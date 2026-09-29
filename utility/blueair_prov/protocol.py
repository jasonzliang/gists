"""Transport-agnostic port of service.rs (Service impl).

Every request/response pair is:  write(endpoint, bytes) WITH response, then
read(endpoint).  prov-session and proto-ver are plaintext; prov-scan,
prov-config and custom-endpoint go through the session cipher (whole message).

The Transport protocol lets the same code run over bleak (ble.py) or an
in-memory fake device (tests/).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from google.protobuf.message import DecodeError, Message

from . import constants as C
from .crypto import ClientKeys, KeystreamCipher, NullCipher, derive_session_cipher
from .protos import (
    constants_pb2,
    custom_commands_pb2,
    sec0_pb2,
    sec1_pb2,
    session_pb2,
    wifi_config_pb2,
    wifi_constants_pb2,
    wifi_scan_pb2,
)

log = logging.getLogger("blueair_prov.protocol")


class ProvisioningError(Exception):
    """Protocol-level failure (bad status, unexpected payload, verify mismatch)."""


class SessionDesync(ProvisioningError):
    """A read returned nothing / undecodable data. Per the spec the only safe
    recovery is to reconnect and redo SessionCmd0/Cmd1 (the CTR keystream is
    shared and can no longer be trusted)."""


class Transport(Protocol):
    async def write(self, endpoint: str, data: bytes) -> None: ...
    async def read(self, endpoint: str) -> bytes: ...


@dataclass
class WiFiResult:
    ssid: str
    bssid: str
    channel: int
    rssi: int
    auth: str
    auth_value: int


@dataclass
class Event:
    json: Any
    number_of_events: int
    raw_json: str = ""


@dataclass
class WiFiStatus:
    sta_state: int
    sta_state_name: str
    fail_reason: int | None
    fail_reason_name: str | None
    ip4_addr: str | None = None
    connected: dict[str, Any] = field(default_factory=dict)


def _enum_name(enum_wrapper, value: int) -> str:
    try:
        return enum_wrapper.Name(value)
    except ValueError:
        return f"UNKNOWN({value})"


def _hex(b: bytes) -> str:
    return b.hex() if b else "<empty>"


class Session:
    """Port of `Service` minus the BLE plumbing."""

    def __init__(
        self,
        transport: Transport,
        *,
        sec: int = 1,
        pop: str | None = None,
        counter_bits: int = 128,
        client_keys: ClientKeys | None = None,
    ):
        if sec not in (0, 1):
            raise ValueError("sec must be 0 or 1")
        self.transport = transport
        self.sec = sec
        self.pop = pop
        self.counter_bits = counter_bits
        self._keys = client_keys  # injectable for tests
        self.cipher: KeystreamCipher | NullCipher | None = None
        self.client_pubkey: bytes | None = None
        self.device_pubkey: bytes | None = None
        self.device_random: bytes | None = None
        self.is_configured = False  # Rust: set by set_configuration, checked by wifi_connect
        self.proto_ver_raw: bytes | None = None
        self.proto_ver_json: Any = None

    # ------------------------------------------------------------------ transport
    async def _exchange(self, endpoint: str, request: bytes, *, encrypted: bool) -> bytes:
        """write_characteristic + read_characteristic, with optional cipher."""
        wire = request
        if encrypted:
            if self.cipher is None:
                raise ProvisioningError("Cipher not initialized")
            wire = self.cipher.apply_keystream(request)
            log.info("[%s] -> plaintext (%d B): %s", endpoint, len(request), _hex(request))
        log.info("[%s] -> write   (%d B): %s", endpoint, len(wire), _hex(wire))
        await self.transport.write(endpoint, wire)
        raw = bytes(await self.transport.read(endpoint))
        log.info("[%s] <- read    (%d B): %s", endpoint, len(raw), _hex(raw))
        if not raw:
            raise SessionDesync(f"{endpoint}: empty read after write")
        if encrypted:
            plain = self.cipher.apply_keystream(raw)  # type: ignore[union-attr]
            log.info("[%s] <- plaintext (%d B): %s", endpoint, len(plain), _hex(plain))
            return plain
        return raw

    @staticmethod
    def _parse(msg_cls: type[Message], data: bytes, what: str) -> Message:
        try:
            m = msg_cls.FromString(data)
        except DecodeError as e:
            raise SessionDesync(f"{what}: undecodable response ({e}); raw={data.hex()}") from e
        log.debug("%s decoded: %s", what, str(m).replace("\n", " ").strip() or "<empty message>")
        return m

    @staticmethod
    def _expect_oneof(msg: Message, oneof: str, expected: str, what: str) -> None:
        # proto3 zero-valued enums are absent on the wire, so presence must be
        # judged by the oneof case, never by `msg.status == 0`.
        got = msg.WhichOneof(oneof)
        if got != expected:
            raise ProvisioningError(
                f"{what}: expected payload '{expected}', device sent '{got}' "
                f"(decoded: {str(msg).strip() or '<empty>'})"
            )

    @staticmethod
    def _check_status(status: int, what: str, enum=constants_pb2.Status) -> None:
        if status != 0:  # Success == 0 in both espressif.Status and custom.Status
            raise ProvisioningError(f"{what} status: {_enum_name(enum, status)} ({status})")

    # ------------------------------------------------------------------ proto-ver
    async def get_proto_ver(self) -> bytes:
        """service.rs get_proto_ver(): write "ESP", read back (plaintext).
        ESP-IDF answers with a JSON document (e.g. {"prov":{"ver":"v1.1",...}});
        Rust ignores the content. We parse it opportunistically for display."""
        raw = await self._exchange("proto-ver", C.PROTO_VER_REQUEST, encrypted=False)
        self.proto_ver_raw = raw
        try:
            self.proto_ver_json = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.proto_ver_json = None
        return raw

    # ------------------------------------------------------------------ session
    async def establish(self) -> None:
        """step_session0 + step_session1 (sec1) or the sec0 handshake."""
        if self.sec == 0:
            await self._sec0_handshake()
        else:
            await self._step_session0()
            await self._step_session1()

    async def _sec0_handshake(self) -> None:
        # NOT in the Rust code (which is sec1-only). Standard protocomm Security0:
        # SessionData{sec_ver=SecScheme0, sec0{msg=S0_Session_Command, sc{}}} -> sr{status}.
        req = session_pb2.SessionData(sec_ver=session_pb2.SecScheme0)
        req.sec0.msg = sec0_pb2.S0_Session_Command
        req.sec0.sc.SetInParent()
        resp_raw = await self._exchange("prov-session", req.SerializeToString(), encrypted=False)
        resp = self._parse(session_pb2.SessionData, resp_raw, "S0SessionResp")
        self._expect_oneof(resp, "proto", "sec0", "S0SessionResp")
        self._expect_oneof(resp.sec0, "payload", "sr", "S0SessionResp")
        self._check_status(resp.sec0.sr.status, "Session0 response")
        self.cipher = NullCipher()
        log.info("Security0 session established (plaintext)")

    async def _step_session0(self) -> None:
        keys = self._keys or ClientKeys.generate()
        self._keys = keys

        req = session_pb2.SessionData(sec_ver=session_pb2.SecScheme1)
        req.sec1.msg = sec1_pb2.Session_Command0
        req.sec1.sc0.client_pubkey = keys.public_bytes
        log.debug("client_pubkey: %s", keys.public_bytes.hex())

        resp_raw = await self._exchange("prov-session", req.SerializeToString(), encrypted=False)
        resp = self._parse(session_pb2.SessionData, resp_raw, "SessionResp0")
        self._expect_oneof(resp, "proto", "sec1", "SessionResp0")
        self._expect_oneof(resp.sec1, "payload", "sr0", "SessionResp0")
        sr0 = resp.sec1.sr0
        self._check_status(sr0.status, "Session response")

        device_pubkey = bytes(sr0.device_pubkey)
        device_random = bytes(sr0.device_random)
        if len(device_pubkey) != 32:
            raise ProvisioningError(f"device_pubkey length {len(device_pubkey)} != 32")
        if len(device_random) != 16:
            raise ProvisioningError(f"device_random length {len(device_random)} != 16 (AES-CTR IV)")
        log.debug("device_pubkey: %s", device_pubkey.hex())
        log.debug("device_random: %s", device_random.hex())

        self.cipher = derive_session_cipher(
            keys, device_pubkey, device_random, pop=self.pop, counter_bits=self.counter_bits
        )
        self.client_pubkey = keys.public_bytes
        self.device_pubkey = device_pubkey
        self.device_random = device_random
        log.info(
            "SessionCmd0 OK; AES-256-CTR keyed from X25519 shared secret%s, IV=device_random",
            " XOR SHA256(pop)" if self.pop else " (no PoP, as in Rust)",
        )

    async def _step_session1(self) -> None:
        if self.cipher is None or self.device_pubkey is None or self.client_pubkey is None:
            raise ProvisioningError("Cipher not initialized")
        # client_verify_data = keystream(device_pubkey)  (consumes 32 keystream bytes)
        client_verify = self.cipher.apply_keystream(self.device_pubkey)

        req = session_pb2.SessionData(sec_ver=session_pb2.SecScheme1)
        req.sec1.msg = sec1_pb2.Session_Command1
        req.sec1.sc1.client_verify_data = client_verify

        resp_raw = await self._exchange("prov-session", req.SerializeToString(), encrypted=False)
        resp = self._parse(session_pb2.SessionData, resp_raw, "SessionResp1")
        self._expect_oneof(resp, "proto", "sec1", "SessionResp1")
        self._expect_oneof(resp.sec1, "payload", "sr1", "SessionResp1")
        sr1 = resp.sec1.sr1
        self._check_status(sr1.status, "Session response 1")

        # decrypt device_verify_data with the NEXT 32 keystream bytes; must equal our pubkey
        device_verify = bytes(sr1.device_verify_data)
        if len(device_verify) != 32:
            raise ProvisioningError(f"device_verify_data length {len(device_verify)} != 32")
        check = self.cipher.apply_keystream(device_verify)
        if check != self.client_pubkey:
            raise ProvisioningError(
                "Invalid device verify data (key mismatch). If the device was "
                "provisioned with a proof-of-possession, retry with --pop."
            )
        log.info("SessionCmd1 OK; device verified, secure session established")

    # ------------------------------------------------------------------ custom endpoint
    async def _custom(self, wrapper: custom_commands_pb2.CommandWrapper, expect: str) -> Message:
        raw = await self._exchange("custom-endpoint", wrapper.SerializeToString(), encrypted=True)
        resp = self._parse(custom_commands_pb2.CommandWrapper, raw, f"CommandWrapper/{expect}")
        self._expect_oneof(resp, "payload", expect, f"custom-endpoint {expect}")
        return getattr(resp, expect)

    async def start(self) -> None:
        """step_start()/start(): StartCmd{} -> StartResp{status}."""
        w = custom_commands_pb2.CommandWrapper()
        w.start_cmd.SetInParent()
        resp = await self._custom(w, "start_resp")
        self._check_status(resp.status, "Start response", custom_commands_pb2.Status)
        log.info("StartCmd OK")

    async def stop(self) -> None:
        """stop(): StopCmd{} -> StopResp{status}. (Not called by main.rs.)"""
        w = custom_commands_pb2.CommandWrapper()
        w.stop_cmd.SetInParent()
        resp = await self._custom(w, "stop_resp")
        self._check_status(resp.status, "Stop response", custom_commands_pb2.Status)
        log.info("StopCmd OK")

    async def set_configuration(self, config: dict[str, str]) -> None:
        """set_configuration(): one ConfigCmd per field, Rust order, each must Succeed.
        Rust always sends all six (missing -> empty string). We send only the
        keys present in `config`, in Rust order; the CLI decides completeness."""
        unknown = set(config) - set(C.CONFIG_FIELD_ORDER)
        if unknown:
            raise ValueError(f"unknown configuration fields: {sorted(unknown)}")
        for name in C.CONFIG_FIELD_ORDER:
            if name not in config:
                continue
            w = custom_commands_pb2.CommandWrapper()
            setattr(w.config_cmd, name, config[name])
            log.info("ConfigCmd %s = %r", name, config[name])
            resp = await self._custom(w, "config_resp")
            self._check_status(resp.status, f"Config response ({name})", custom_commands_pb2.Status)
        self.is_configured = True
        log.info("set_configuration OK (%d field(s))", len(config))

    async def set_factory(self, url: str, ssid: str, pwd: str, token: str) -> None:
        """set_factory(): FactoryCmd -> FactoryResp. (Not called by main.rs; not exposed on the CLI.)"""
        w = custom_commands_pb2.CommandWrapper()
        w.factory_cmd.url = url
        w.factory_cmd.ssid = ssid
        w.factory_cmd.pwd = pwd
        w.factory_cmd.token = token
        resp = await self._custom(w, "factory_resp")
        self._check_status(resp.status, "Factory response", custom_commands_pb2.Status)

    async def get_event(self) -> Event:
        """get_event(): EventCmd{cmd=EventGet} -> EventResp{json, number_of_events}."""
        w = custom_commands_pb2.CommandWrapper()
        w.event_cmd.cmd = custom_commands_pb2.EventGet
        resp = await self._custom(w, "event_resp")
        try:
            parsed = json.loads(resp.json) if resp.json else None
        except json.JSONDecodeError:
            parsed = None  # Rust: unwrap_or(Value::Null)
        return Event(json=parsed, number_of_events=resp.number_of_events, raw_json=resp.json)

    async def get_all_event(self, max_events: int = 50) -> list[Event]:
        """get_all_event(): read once, then keep reading while number_of_events > 0.
        `max_events` is a safety cap not present in Rust (its loop is unbounded)."""
        events: list[Event] = []
        ev = await self.get_event()
        events.append(ev)
        while ev.number_of_events > 0:
            if len(events) >= max_events:
                log.warning("get_all_event: stopping at safety cap %d", max_events)
                break
            ev = await self.get_event()
            events.append(ev)
        return events

    # ------------------------------------------------------------------ wifi scan
    async def _scan_exchange(self, payload: wifi_scan_pb2.WiFiScanPayload, expect: str) -> wifi_scan_pb2.WiFiScanPayload:
        raw = await self._exchange("prov-scan", payload.SerializeToString(), encrypted=True)
        resp = self._parse(wifi_scan_pb2.WiFiScanPayload, raw, f"WiFiScanPayload/{expect}")
        return resp

    async def wifi_scan(
        self,
        *,
        blocking: bool = C.SCAN_START_BLOCKING,
        passive: bool = C.SCAN_START_PASSIVE,
        group_channels: int = C.SCAN_START_GROUP_CHANNELS,
        period_ms: int = C.SCAN_START_PERIOD_MS,
        max_status_polls: int = C.SCAN_STATUS_MAX_POLLS,
        poll_interval: float = C.DEFAULT_SCAN_POLL_INTERVAL_S,
        page_size: int = C.WIFI_PACKET_COUNT,
    ) -> list[WiFiResult]:
        """wifi_scan(): CmdScanStart -> poll CmdScanStatus (<=10) -> paged CmdScanResult."""
        if self.cipher is None:
            raise ProvisioningError("Cipher not initialized")

        # --- CmdScanStart
        p = wifi_scan_pb2.WiFiScanPayload(msg=wifi_scan_pb2.TypeCmdScanStart)
        p.cmd_scan_start.blocking = blocking
        p.cmd_scan_start.passive = passive
        p.cmd_scan_start.group_channels = group_channels
        p.cmd_scan_start.period_ms = period_ms
        resp = await self._scan_exchange(p, "resp_scan_start")
        # Rust only checks `resp_scan_start().is_initialized()` (always true for
        # proto3) and ignores `status`; we log rather than fail on mismatches.
        if resp.WhichOneof("payload") != "resp_scan_start":
            log.warning("CmdScanStart: unexpected payload %r (Rust would ignore this)", resp.WhichOneof("payload"))
        if resp.status != 0:
            log.warning("CmdScanStart: status %s (Rust ignores it)", _enum_name(constants_pb2.Status, resp.status))

        # --- CmdScanStatus loop
        scan_finished = False
        result_count = 0
        for i in range(max_status_polls):
            if i > 0 and poll_interval > 0:
                await asyncio.sleep(poll_interval)
            p = wifi_scan_pb2.WiFiScanPayload(msg=wifi_scan_pb2.TypeCmdScanStatus)
            p.cmd_scan_status.SetInParent()
            resp = await self._scan_exchange(p, "resp_scan_status")
            self._check_status(resp.status, "WiFi scan response")
            self._expect_oneof(resp, "payload", "resp_scan_status", "CmdScanStatus")
            scan_finished = resp.resp_scan_status.scan_finished
            result_count = resp.resp_scan_status.result_count
            log.info("scan status poll %d: finished=%s result_count=%d", i + 1, scan_finished, result_count)
            if scan_finished:
                break
        if not scan_finished:
            raise ProvisioningError("WiFi scan did not finish, timeout")

        # --- CmdScanResult pages of WIFI_PACKET_COUNT
        results: list[WiFiResult] = []
        start_index = 0
        while start_index < result_count:
            count = min(result_count - start_index, page_size)
            p = wifi_scan_pb2.WiFiScanPayload(msg=wifi_scan_pb2.TypeCmdScanResult)
            p.cmd_scan_result.start_index = start_index
            p.cmd_scan_result.count = count
            resp = await self._scan_exchange(p, "resp_scan_result")
            self._check_status(resp.status, "WiFi scan response")
            self._expect_oneof(resp, "payload", "resp_scan_result", "CmdScanResult")
            for e in resp.resp_scan_result.entries:
                results.append(
                    WiFiResult(
                        ssid=bytes(e.ssid).decode("utf-8", errors="replace"),
                        bssid=bytes(e.bssid).hex(),
                        channel=e.channel,
                        rssi=e.rssi,
                        auth=_enum_name(wifi_constants_pb2.WifiAuthMode, e.auth),
                        auth_value=e.auth,
                    )
                )
            start_index += count
        return results

    # ------------------------------------------------------------------ wifi config
    async def _config_exchange(self, payload: wifi_config_pb2.WiFiConfigPayload, expect: str) -> Message:
        raw = await self._exchange("prov-config", payload.SerializeToString(), encrypted=True)
        resp = self._parse(wifi_config_pb2.WiFiConfigPayload, raw, f"WiFiConfigPayload/{expect}")
        self._expect_oneof(resp, "payload", expect, f"prov-config {expect}")
        return getattr(resp, expect)

    async def wifi_set_config(self, ssid: str, passphrase: str) -> None:
        p = wifi_config_pb2.WiFiConfigPayload(msg=wifi_config_pb2.TypeCmdSetConfig)
        p.cmd_set_config.ssid = ssid.encode("utf-8")
        p.cmd_set_config.passphrase = passphrase.encode("utf-8")
        resp = await self._config_exchange(p, "resp_set_config")
        self._check_status(resp.status, "WiFi cmd set config response")
        log.info("CmdSetConfig OK")

    async def wifi_apply_config(self) -> None:
        p = wifi_config_pb2.WiFiConfigPayload(msg=wifi_config_pb2.TypeCmdApplyConfig)
        p.cmd_apply_config.SetInParent()
        resp = await self._config_exchange(p, "resp_apply_config")
        self._check_status(resp.status, "WiFi cmd apply response")
        log.info("CmdApplyConfig OK")

    async def wifi_get_status(self) -> WiFiStatus:
        p = wifi_config_pb2.WiFiConfigPayload(msg=wifi_config_pb2.TypeCmdGetStatus)
        p.cmd_get_status.SetInParent()
        resp = await self._config_exchange(p, "resp_get_status")
        self._check_status(resp.status, "WiFi get status response")
        state_case = resp.WhichOneof("state")
        fail = resp.fail_reason if state_case == "fail_reason" else None
        st = WiFiStatus(
            sta_state=resp.sta_state,
            sta_state_name=_enum_name(wifi_constants_pb2.WifiStationState, resp.sta_state),
            fail_reason=fail,
            fail_reason_name=_enum_name(wifi_constants_pb2.WifiConnectFailedReason, fail) if fail is not None else None,
        )
        if state_case == "connected":
            c = resp.connected
            st.ip4_addr = c.ip4_addr
            st.connected = {
                "ip4_addr": c.ip4_addr,
                "auth_mode": _enum_name(wifi_constants_pb2.WifiAuthMode, c.auth_mode),
                "ssid": bytes(c.ssid).decode("utf-8", errors="replace"),
                "bssid": bytes(c.bssid).hex(),
                "channel": c.channel,
            }
        return st

    async def wifi_connect(
        self,
        ssid: str,
        passphrase: str,
        *,
        timeout: float = C.WIFI_STATUS_TIMEOUT_S,
        poll_interval: float = C.DEFAULT_STATUS_POLL_INTERVAL_S,
        require_configured: bool = False,
    ) -> WiFiStatus:
        """wifi_connect(): CmdSetConfig -> CmdApplyConfig -> poll CmdGetStatus.
        Rust refuses to run unless set_configuration() succeeded first
        (`Device not configured.`); we make that a flag (see CLI) because the
        default here is to send NO cloud configuration."""
        if self.cipher is None:
            raise ProvisioningError("Cipher not initialized.")
        if require_configured and not self.is_configured:
            raise ProvisioningError("Device not configured.")
        if not self.is_configured:
            log.warning("wifi_connect without set_configuration (Rust reference always configured first)")

        await self.wifi_set_config(ssid, passphrase)
        await self.wifi_apply_config()

        start = time.monotonic()
        polls = 0
        while True:
            if polls > 0 and poll_interval > 0:
                await asyncio.sleep(poll_interval)
            polls += 1
            st = await self.wifi_get_status()
            log.info(
                "GetStatus poll %d: sta_state=%s fail_reason=%s ip=%s",
                polls, st.sta_state_name, st.fail_reason_name, st.ip4_addr,
            )
            # Rust order: fail_reason (and not Connecting) -> timeout -> Connected
            if st.fail_reason is not None and st.sta_state != wifi_constants_pb2.Connecting:
                raise ProvisioningError(
                    f"WiFi config response has a fail reason: {st.fail_reason_name} (sta_state: {st.sta_state_name})"
                )
            if time.monotonic() - start >= timeout:
                raise ProvisioningError(f"WiFi config timeout (last sta_state: {st.sta_state_name})")
            if st.sta_state == wifi_constants_pb2.Connected:
                return st
