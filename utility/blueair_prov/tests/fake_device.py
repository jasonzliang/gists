"""In-memory stand-in for the purifier's protocomm server.

Deliberately implemented WITHOUT blueair_prov.crypto: it uses cryptography's
own AES-CTR mode (128-bit counter, like mbedtls on the device) and a separate
X25519 key, so a keystream-bookkeeping bug on the client side shows up as a
protobuf decode failure here rather than being masked by shared code.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from blueair_prov.protos import (
    constants_pb2,
    custom_commands_pb2,
    sec0_pb2,
    sec1_pb2,
    session_pb2,
    wifi_config_pb2,
    wifi_constants_pb2,
    wifi_scan_pb2,
)


@dataclass
class FakeNetwork:
    ssid: str
    bssid: bytes
    channel: int
    rssi: int
    auth: int = wifi_constants_pb2.WPA2_PSK


@dataclass
class FakeDevice:
    pop: str | None = None
    proto_ver: bytes = b'{"prov":{"ver":"v1.1","sec_ver":1,"cap":["wifi_scan"]}}'
    networks: list[FakeNetwork] = field(default_factory=list)
    scan_polls_needed: int = 2
    # sequence of (sta_state, fail_reason|None, ip|None) returned by successive CmdGetStatus
    status_sequence: list[tuple[int, int | None, str | None]] = field(
        default_factory=lambda: [(wifi_constants_pb2.Connecting, None, None), (wifi_constants_pb2.Connected, None, "192.168.1.50")]
    )
    events: list[dict] = field(default_factory=list)
    device_random: bytes | None = None
    fail_session1: bool = False
    require_start_for_wifi: bool = False

    def __post_init__(self):
        self.pending: dict[str, bytes] = {}
        self.requests: list[tuple[str, object]] = []  # (endpoint, decoded plaintext message)
        self.config: dict[str, str] = {}
        self.wifi: dict[str, bytes] = {}
        self.started = False
        self.stopped = False
        self.applied = False
        self._enc = None
        self._client_pub: bytes | None = None
        self._dev_pub: bytes | None = None
        self._status_i = 0
        self._scan_polls = 0
        self._event_i = 0
        self.plaintext = False

    # ------------------------------------------------------------- transport
    async def write(self, endpoint: str, data: bytes) -> None:
        handler = {
            "proto-ver": self._h_proto_ver,
            "prov-session": self._h_session,
            "prov-scan": self._h_scan,
            "prov-config": self._h_config,
            "custom-endpoint": self._h_custom,
        }[endpoint]
        self.pending[endpoint] = handler(bytes(data))

    async def read(self, endpoint: str) -> bytes:
        return self.pending.pop(endpoint, b"")

    # ------------------------------------------------------------- crypto
    def _crypt(self, data: bytes) -> bytes:
        if self.plaintext:
            return data
        assert self._enc is not None, "session not established"
        return self._enc.update(data)

    def _h_proto_ver(self, data: bytes) -> bytes:
        self.requests.append(("proto-ver", data))
        return self.proto_ver

    def _h_session(self, data: bytes) -> bytes:
        req = session_pb2.SessionData.FromString(data)
        self.requests.append(("prov-session", req))
        resp = session_pb2.SessionData()
        if req.WhichOneof("proto") == "sec0":
            resp.sec_ver = session_pb2.SecScheme0
            resp.sec0.msg = sec0_pb2.S0_Session_Response
            resp.sec0.sr.status = constants_pb2.Success
            self.plaintext = True
            return resp.SerializeToString()
        resp.sec_ver = session_pb2.SecScheme1
        case = req.sec1.WhichOneof("payload")
        if case == "sc0":
            priv = X25519PrivateKey.generate()
            self._dev_pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            self._client_pub = bytes(req.sec1.sc0.client_pubkey)
            shared = priv.exchange(X25519PublicKey.from_public_bytes(self._client_pub))
            key = shared
            if self.pop:
                d = hashlib.sha256(self.pop.encode()).digest()
                key = bytes(a ^ b for a, b in zip(shared, d))
            self.device_random = self.device_random or os.urandom(16)
            self._enc = Cipher(algorithms.AES(key), modes.CTR(self.device_random)).encryptor()
            resp.sec1.msg = sec1_pb2.Session_Response0
            resp.sec1.sr0.status = constants_pb2.Success
            resp.sec1.sr0.device_pubkey = self._dev_pub
            resp.sec1.sr0.device_random = self.device_random
            return resp.SerializeToString()
        if case == "sc1":
            check = self._crypt(bytes(req.sec1.sc1.client_verify_data))
            resp.sec1.msg = sec1_pb2.Session_Response1
            if check != self._dev_pub or self.fail_session1:
                resp.sec1.sr1.status = constants_pb2.CryptoError
                return resp.SerializeToString()
            resp.sec1.sr1.status = constants_pb2.Success
            resp.sec1.sr1.device_verify_data = self._crypt(self._client_pub)
            return resp.SerializeToString()
        resp.sec1.sr0.status = constants_pb2.InvalidArgument
        return resp.SerializeToString()

    def _h_scan(self, data: bytes) -> bytes:
        req = wifi_scan_pb2.WiFiScanPayload.FromString(self._crypt(data))
        self.requests.append(("prov-scan", req))
        resp = wifi_scan_pb2.WiFiScanPayload(status=constants_pb2.Success)
        case = req.WhichOneof("payload")
        if case == "cmd_scan_start":
            self._scan_polls = 0
            resp.msg = wifi_scan_pb2.TypeRespScanStart
            resp.resp_scan_start.SetInParent()
        elif case == "cmd_scan_status":
            self._scan_polls += 1
            resp.msg = wifi_scan_pb2.TypeRespScanStatus
            done = self._scan_polls >= self.scan_polls_needed
            resp.resp_scan_status.scan_finished = done
            resp.resp_scan_status.result_count = len(self.networks) if done else 0
        elif case == "cmd_scan_result":
            resp.msg = wifi_scan_pb2.TypeRespScanResult
            s, n = req.cmd_scan_result.start_index, req.cmd_scan_result.count
            for net in self.networks[s : s + n]:
                e = resp.resp_scan_result.entries.add()
                e.ssid, e.bssid, e.channel, e.rssi, e.auth = net.ssid.encode(), net.bssid, net.channel, net.rssi, net.auth
        else:
            resp.status = constants_pb2.InvalidArgument
        return self._crypt(resp.SerializeToString())

    def _h_config(self, data: bytes) -> bytes:
        req = wifi_config_pb2.WiFiConfigPayload.FromString(self._crypt(data))
        self.requests.append(("prov-config", req))
        resp = wifi_config_pb2.WiFiConfigPayload()
        case = req.WhichOneof("payload")
        if self.require_start_for_wifi and not self.started:
            resp.msg = wifi_config_pb2.TypeRespSetConfig
            resp.resp_set_config.status = constants_pb2.InvalidSession
            return self._crypt(resp.SerializeToString())
        if case == "cmd_set_config":
            self.wifi = {"ssid": bytes(req.cmd_set_config.ssid), "passphrase": bytes(req.cmd_set_config.passphrase)}
            resp.msg = wifi_config_pb2.TypeRespSetConfig
            resp.resp_set_config.status = constants_pb2.Success
        elif case == "cmd_apply_config":
            self.applied = True
            resp.msg = wifi_config_pb2.TypeRespApplyConfig
            resp.resp_apply_config.status = constants_pb2.Success
        elif case == "cmd_get_status":
            resp.msg = wifi_config_pb2.TypeRespGetStatus
            st, fail, ip = self.status_sequence[min(self._status_i, len(self.status_sequence) - 1)]
            self._status_i += 1
            resp.resp_get_status.status = constants_pb2.Success
            resp.resp_get_status.sta_state = st
            if fail is not None:
                resp.resp_get_status.fail_reason = fail
            elif ip is not None:
                resp.resp_get_status.connected.ip4_addr = ip
                resp.resp_get_status.connected.ssid = self.wifi.get("ssid", b"")
                resp.resp_get_status.connected.channel = 6
        return self._crypt(resp.SerializeToString())

    def _h_custom(self, data: bytes) -> bytes:
        req = custom_commands_pb2.CommandWrapper.FromString(self._crypt(data))
        self.requests.append(("custom-endpoint", req))
        resp = custom_commands_pb2.CommandWrapper()
        case = req.WhichOneof("payload")
        if case == "start_cmd":
            self.started = True
            resp.start_resp.status = custom_commands_pb2.Success
        elif case == "stop_cmd":
            self.stopped = True
            resp.stop_resp.status = custom_commands_pb2.Success
        elif case == "config_cmd":
            f = req.config_cmd.WhichOneof("payload")
            self.config[f] = getattr(req.config_cmd, f)
            resp.config_resp.status = custom_commands_pb2.Success
        elif case == "event_cmd":
            if self._event_i < len(self.events):
                ev = self.events[self._event_i]
                self._event_i += 1
                resp.event_resp.json = json.dumps(ev)
            else:
                resp.event_resp.json = ""
            resp.event_resp.number_of_events = max(0, len(self.events) - self._event_i)
        elif case == "factory_cmd":
            resp.factory_resp.status = custom_commands_pb2.Success
        else:
            resp.start_resp.status = custom_commands_pb2.Fail
        return self._crypt(resp.SerializeToString())
