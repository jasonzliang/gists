"""Command-line front end: scan / info / wifi-scan / events / provision."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakError

from . import __version__
from . import ble
from . import constants as C
from .protocol import ProvisioningError, Session, SessionDesync, WiFiResult
from .protos import wifi_constants_pb2

log = logging.getLogger("blueair_prov")

ENV_SSID = "BLUEAIR_WIFI_SSID"
ENV_PASS = "BLUEAIR_WIFI_PASS"
ENV_USER = "BLUEAIR_USERNAME"
ENV_PWD = "BLUEAIR_PASSWORD"


# ---------------------------------------------------------------------------- args


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="blueair-prov",
        description="Blueair Blue Pure 511i Max BLE provisioning (Python port of blueairble-rs).",
    )
    p.add_argument("-v", "--verbose", action="count", default=0, help="-v: hex dumps of every write/read; -vv: protobuf decode + bleak debug")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    d = p.add_argument_group("discovery / connection")
    d.add_argument("--name", help="exact advertised name (e.g. 112637_10BDA359C5E0)")
    d.add_argument("--name-prefix", help="match devices whose name starts with this (e.g. 112637_)")
    d.add_argument("--address", help="CoreBluetooth UUID of the device (from `scan`)")
    d.add_argument("--no-service-filter", action="store_true", help="do not pass the Blueair service UUID to CoreBluetooth when scanning")
    d.add_argument("--scan-timeout", type=float, default=15.0, help="seconds to wait for an advertisement per attempt (default 15)")
    d.add_argument("--connect-timeout", type=float, default=20.0, help="BleakClient connect timeout in seconds (default 20)")
    d.add_argument("--retries", type=int, default=3, help="connect retries after the first attempt (default 3)")
    d.add_argument("--backoff", type=float, default=5.0, help="initial delay between attempts (default 5 s)")
    d.add_argument("--backoff-factor", type=float, default=1.5)
    d.add_argument("--max-backoff", type=float, default=30.0)
    d.add_argument("--connect-on-detect", action="store_true", help="connect immediately from the scanner detection callback")
    d.add_argument("--keep-scanning", action="store_true", help="with --connect-on-detect: keep the scanner running while connecting")
    d.add_argument("--allow-nonconnectable", action="store_true", help="issue connectPeripheral even when the advertisement is flagged non-connectable (it will time out)")

    s = p.add_argument_group("session")
    s.add_argument("--sec", type=int, choices=(0, 1), default=1, help="protocomm security scheme (Rust/app: 1)")
    s.add_argument("--pop", default=None, help="proof-of-possession; NOT used by Rust or the app (empty PoP). Only try if verify fails")
    s.add_argument("--ctr-bits", type=int, choices=(32, 128), default=128, help="AES-CTR counter width: 128 = device/mbedtls, 32 = Rust Ctr32BE (identical unless the IV wraps)")
    s.add_argument("--verify-descriptors", action="store_true", help="read the 0x2901 user descriptions and compare with the hard-coded UUID map")
    s.add_argument("--session-retries", type=int, default=1, help="on keystream desync in read-only flows: reconnect and redo the handshake this many times")

    sub = p.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scan", help="list Blueair devices (advertisements only, no connection)")
    sc.add_argument("--duration", type=float, default=5.0, help="seconds (Rust used 3)")
    sc.add_argument("--all", action="store_true", help="list every BLE device seen, not only Blueair matches")
    sc.add_argument("--raw", action="store_true", help="also dump the raw CoreBluetooth advertisement dictionary (connectable flag, PHY, ...)")

    inf = sub.add_parser("info", help="connect, enumerate GATT, write 'ESP' to proto-ver and read it back")
    inf.add_argument("--no-proto-ver", action="store_true", help="skip the proto-ver write/read (pure GATT dump)")

    ws = sub.add_parser("wifi-scan", help="secure session + WiFi scan via prov-scan (no config written)")
    ws.add_argument("--start", action="store_true", help="also send StartCmd on custom-endpoint first (Rust connect() does; a WRITE to custom-endpoint)")
    ws.add_argument("--scan-poll-interval", type=float, default=C.DEFAULT_SCAN_POLL_INTERVAL_S, help="sleep between CmdScanStatus polls (Rust: 0)")
    ws.add_argument("--scan-poll-max", type=int, default=C.SCAN_STATUS_MAX_POLLS)
    ws.add_argument("--period-ms", type=int, default=C.SCAN_START_PERIOD_MS)
    ws.add_argument("--passive", action="store_true")
    ws.add_argument("--group-channels", type=int, default=C.SCAN_START_GROUP_CHANNELS)
    ws.add_argument("--json", action="store_true")

    ev = sub.add_parser("events", help="secure session + EventGet loop on custom-endpoint")
    ev.add_argument("--start", action="store_true", help="send StartCmd before reading events")
    ev.add_argument("--max-events", type=int, default=50)
    ev.add_argument("--watch", type=float, default=0.0, help="keep polling for this many seconds (0 = one get_all_event)")
    ev.add_argument("--event-poll-interval", type=float, default=C.EVENT_POLL_INTERVAL_S)
    ev.add_argument("--json", action="store_true")

    pr = sub.add_parser("provision", help="full flow: session -> StartCmd -> [cloud register] -> [ConfigCmd x6] -> WiFi config -> status -> events")
    w = pr.add_argument_group("wifi credentials (or env BLUEAIR_WIFI_SSID / BLUEAIR_WIFI_PASS)")
    w.add_argument("--ssid")
    w.add_argument("--password", help="WiFi passphrase (prefer the env var)")
    w.add_argument("--open-network", action="store_true", help="network has no passphrase (app omits the passphrase for Open auth)")
    w.add_argument("--show-password", action="store_true", help="print the passphrase in the confirmation instead of masking it")
    pr.add_argument("--yes", action="store_true", help="REQUIRED to write anything to the device")
    pr.add_argument("--dry-run", action="store_true", help="stop after proto-ver + session handshake + WiFi scan (no StartCmd, no config, no cloud writes)")
    cfg = pr.add_argument_group("device configuration (ConfigCmd) -- nothing is sent unless one of these is given")
    cfg.add_argument("--cloud-region", choices=sorted(C.BLUEAIR_CLOUD_REGIONS), help="fill api_url/auth_url/broker_url/region from the verified production table and generate random_text/secure_random")
    for f in C.CONFIG_FIELD_ORDER:
        cfg.add_argument(f"--{f.replace('_', '-')}", dest=f, help=f"ConfigCmd.{f} (overrides --cloud-region)")
    cfg.add_argument("--skip-configuration", action="store_true", help="send no ConfigCmd even if config flags are given")
    cfg.add_argument("--allow-partial-config", action="store_true", help="allow sending fewer than all six fields (Rust always sends six)")
    cl = pr.add_argument_group("cloud onboarding (decompiled app flow)")
    cx = cl.add_mutually_exclusive_group()
    cx.add_argument("--cloud", action="store_true", help="log in to BlueCloud, POST register-for-onboarding before ConfigCmd, verify device-status/registered-devices after DeviceBound")
    cx.add_argument("--no-cloud", action="store_true", help="explicitly skip all cloud calls (default)")
    cl.add_argument("--account-username", help=f"Blueair account e-mail (or env {ENV_USER})")
    cl.add_argument("--account-password", help=f"Blueair account password (or env {ENV_PWD})")
    cl.add_argument("--gigya-region", choices=("us", "eu", "cn", "au"), help="account login region if different from --cloud-region")
    fl = pr.add_argument_group("post-apply behaviour")
    fl.add_argument("--wifi-status-mode", choices=("app", "rust"), default="app",
                    help="app: ONE CmdGetStatus then poll EventGet until DeviceBound (default); rust: poll CmdGetStatus until Connected (10 s) then one get_all_event")
    fl.add_argument("--wifi-timeout", type=float, default=C.WIFI_STATUS_TIMEOUT_S, help="rust mode: CmdGetStatus poll budget (default 10 s)")
    fl.add_argument("--status-poll-interval", type=float, default=C.DEFAULT_STATUS_POLL_INTERVAL_S, help="rust mode: sleep between CmdGetStatus polls (Rust: 0)")
    fl.add_argument("--event-poll-interval", type=float, default=C.EVENT_POLL_INTERVAL_S)
    fl.add_argument("--event-timeout", type=float, default=C.EVENT_POLL_TIMEOUT_S)
    fl.add_argument("--max-events", type=int, default=50)
    fl.add_argument("--no-events", action="store_true", help="skip the EventGet phase entirely")
    fl.add_argument("--require-configured", action="store_true", help="reproduce Rust's guard: refuse WiFi config unless ConfigCmd was sent")
    pr.add_argument("--json", action="store_true", help="print a JSON summary at the end")
    return p


def setup_logging(verbosity: int) -> None:
    level = logging.WARNING if verbosity == 0 else logging.INFO if verbosity == 1 else logging.DEBUG
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    logging.getLogger("bleak").setLevel(logging.DEBUG if verbosity >= 2 else logging.WARNING)
    logging.getLogger("blueair_api").setLevel(logging.DEBUG if verbosity >= 2 else logging.WARNING)
    if verbosity >= 3:
        logging.getLogger("asyncio").setLevel(logging.DEBUG)


def connect_options(args: argparse.Namespace) -> ble.ConnectOptions:
    return ble.ConnectOptions(
        name=args.name,
        name_prefix=args.name_prefix,
        address=args.address,
        service_filter=not args.no_service_filter,
        scan_timeout=args.scan_timeout,
        connect_timeout=args.connect_timeout,
        retries=args.retries,
        backoff=args.backoff,
        backoff_factor=args.backoff_factor,
        max_backoff=args.max_backoff,
        connect_on_detect=args.connect_on_detect,
        keep_scanning=args.keep_scanning,
        allow_nonconnectable=args.allow_nonconnectable,
    )


# ---------------------------------------------------------------------------- helpers


def out(msg: str = "") -> None:
    print(msg, flush=True)


async def open_client(args: argparse.Namespace) -> BleakClient:
    client = await ble.connect(connect_options(args))
    out(f"connected: {client.address} name={_client_name(client)!r} mtu={ble._safe_mtu(client)}")
    return client


def _client_name(client: BleakClient) -> str | None:
    try:
        return client.name
    except Exception:  # noqa: BLE001
        return None


async def open_session(args: argparse.Namespace, client: BleakClient) -> Session:
    """map endpoints -> proto-ver -> SessionCmd0/Cmd1 (Rust connect() minus StartCmd)."""
    chars = ble.map_endpoints(client)
    if args.verify_descriptors:
        await ble.verify_endpoint_names(client, chars)
    session = Session(ble.BleakTransport(client, chars), sec=args.sec, pop=args.pop, counter_bits=args.ctr_bits)
    raw = await session.get_proto_ver()
    out(f"proto-ver: {raw.decode('utf-8', errors='replace')!r}")
    await session.establish()
    out(f"session: sec{args.sec} established" + (" (PoP mixed in)" if args.pop else ""))
    return session


async def safe_disconnect(client: BleakClient | None) -> None:
    if client is None:
        return
    try:
        if client.is_connected:
            await client.disconnect()
            log.info("disconnected")
    except Exception as e:  # noqa: BLE001
        log.debug("disconnect error ignored: %s", e)


def print_wifi_results(results: list[WiFiResult]) -> None:
    if not results:
        out("no networks found")
        return
    out(f"{'SSID':<32} {'BSSID':<14} {'CH':>3} {'RSSI':>5}  AUTH")
    for r in sorted(results, key=lambda r: -r.rssi):
        out(f"{r.ssid[:32]:<32} {r.bssid:<14} {r.channel:>3} {r.rssi:>5}  {r.auth}")


def print_events(events, prefix: str = "") -> None:
    for i, ev in enumerate(events):
        out(f"{prefix}event[{i}] remaining={ev.number_of_events} json={ev.raw_json or '<empty>'}")


# ---------------------------------------------------------------------------- commands


async def cmd_scan(args: argparse.Namespace) -> int:
    opts = connect_options(args)
    seen = await ble.scan(args.duration, service_filter=not args.no_service_filter, opts=opts)
    rows = []
    for s in seen.values():
        match = ble.is_blueair(s.device, s.adv, opts)
        if match or args.all:
            rows.append((match, s))
    rows.sort(key=lambda t: (not t[0], -(t[1].adv.rssi or -999)))
    if not rows:
        out(f"no {'devices' if args.all else 'Blueair devices'} seen in {args.duration:.0f}s "
            f"(service filter {'OFF' if args.no_service_filter else 'ON'}; try --no-service-filter, --all, or move closer)")
        return 1
    for match, s in rows:
        cv = ",".join(str(v) for v in sorted(s.connectable_values)) or "?"
        out(("[BLUEAIR] " if match else "          ") + ble.describe(s.device, s.adv) + f"  adv_count={s.count}  connectable_values_seen={{{cv}}}")
        if args.raw and (match or args.all):
            out("           raw: " + ble.raw_advertisement(s.adv))
    return 0


async def cmd_info(args: argparse.Namespace) -> int:
    client = None
    try:
        client = await open_client(args)
        gatt = await ble.enumerate_gatt(client, read_descriptors=True)
        for svc_uuid, chars in gatt.items():
            tag = "  <- Blueair provisioning service" if svc_uuid.lower() == C.SERVICE_UUID else ""
            out(f"service {svc_uuid}{tag}")
            for ch in chars:
                nm = f" ({ch.name})" if ch.name else ""
                out(f"  char {ch.uuid}{nm} handle={ch.handle} props={','.join(ch.properties)}")
                for du, dh, dv in ch.descriptors:
                    out(f"      desc {du} handle={dh} value={dv!r}")
                if ch.name and ch.user_description is not None and ch.user_description != ch.name:
                    out(f"      !! user description {ch.user_description!r} differs from expected {ch.name!r}")
        try:
            chars = ble.map_endpoints(client)
            out(f"endpoint map OK: {', '.join(sorted(chars))}")
        except BleakError as e:
            out(f"endpoint map FAILED: {e}")
            return 2
        if not args.no_proto_ver:
            session = Session(ble.BleakTransport(client, chars), sec=args.sec)
            raw = await session.get_proto_ver()
            out(f"proto-ver raw: {raw!r}")
            if session.proto_ver_json is not None:
                out("proto-ver json: " + json.dumps(session.proto_ver_json, indent=2))
        return 0
    finally:
        await safe_disconnect(client)


async def with_session_retry(args: argparse.Namespace, body) -> int:
    """Run `body(client, session)`; on SessionDesync reconnect + re-handshake (read-only flows only)."""
    attempts = 1 + max(0, args.session_retries)
    for attempt in range(1, attempts + 1):
        client = None
        try:
            client = await open_client(args)
            session = await open_session(args, client)
            return await body(client, session)
        except SessionDesync as e:
            log.warning("session desync (attempt %d/%d): %s", attempt, attempts, e)
            if attempt == attempts:
                out(f"ERROR: {e}")
                return 3
            await safe_disconnect(client)
            client = None
            await asyncio.sleep(2.0)
        finally:
            await safe_disconnect(client)
    return 3


async def cmd_wifi_scan(args: argparse.Namespace) -> int:
    async def body(client: BleakClient, session: Session) -> int:
        if args.start:
            await session.start()
            out("StartCmd: OK")
        results = await session.wifi_scan(
            passive=args.passive,
            group_channels=args.group_channels,
            period_ms=args.period_ms,
            max_status_polls=args.scan_poll_max,
            poll_interval=args.scan_poll_interval,
        )
        if args.json:
            out(json.dumps([asdict(r) for r in results], indent=2))
        else:
            print_wifi_results(results)
        return 0

    return await with_session_retry(args, body)


async def cmd_events(args: argparse.Namespace) -> int:
    async def body(client: BleakClient, session: Session) -> int:
        if args.start:
            await session.start()
            out("StartCmd: OK")
        deadline = time.monotonic() + args.watch
        rounds = 0
        while True:
            events = await session.get_all_event(max_events=args.max_events)
            rounds += 1
            if args.json:
                out(json.dumps([{"json": e.json, "raw": e.raw_json, "number_of_events": e.number_of_events} for e in events]))
            else:
                print_events(events, prefix=f"[{time.strftime('%H:%M:%S')}] ")
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(args.event_poll_interval)
        return 0

    return await with_session_retry(args, body)


# ---------------------------------------------------------------------------- provision


def resolve_config(args: argparse.Namespace) -> tuple[dict[str, str] | None, dict[str, str]]:
    """Return (config-to-send or None, generated-values). Nothing is sent by default."""
    if args.skip_configuration:
        return None, {}
    cfg: dict[str, str] = {}
    generated: dict[str, str] = {}
    if args.cloud_region:
        cfg.update(C.BLUEAIR_CLOUD_REGIONS[args.cloud_region])
    for f in C.CONFIG_FIELD_ORDER:
        v = getattr(args, f)
        if v is not None:
            cfg[f] = v
    if not cfg:
        return None, {}
    if args.cloud_region or args.cloud:
        from .cloud import generate_random_text, generate_secure_random

        if "random_text" not in cfg:
            cfg["random_text"] = generated["random_text"] = generate_random_text()
        if "secure_random" not in cfg:
            cfg["secure_random"] = generated["secure_random"] = generate_secure_random()
    missing = [f for f in C.CONFIG_FIELD_ORDER if f not in cfg]
    if missing and not args.allow_partial_config:
        raise SystemExit(
            f"refusing to send a partial configuration (missing {missing}); Rust always sends all six fields. "
            f"Add --cloud-region, the missing flags, or --allow-partial-config."
        )
    return cfg, generated


def mask(pw: str, show: bool) -> str:
    if show:
        return repr(pw)
    return f"{'*' * min(len(pw), 12)} ({len(pw)} bytes)" if pw else "<empty> (open network)"


async def cmd_provision(args: argparse.Namespace) -> int:
    # ---- credentials: env or flags only, never hard-coded
    ssid = args.ssid or os.environ.get(ENV_SSID)
    password = args.password if args.password is not None else os.environ.get(ENV_PASS)
    if not ssid:
        out(f"ERROR: WiFi SSID missing. Set {ENV_SSID} or pass --ssid.")
        return 2
    if password is None:
        if args.open_network:
            password = ""
        else:
            out(f"ERROR: WiFi passphrase missing. Set {ENV_PASS}, pass --password, or use --open-network.")
            return 2
    if password == "" and not args.open_network:
        out("ERROR: empty passphrase; pass --open-network if the network really is open.")
        return 2

    try:
        config, generated = resolve_config(args)
    except SystemExit as e:
        out(f"ERROR: {e}")
        return 2

    cloud = None
    if args.cloud:
        if not args.cloud_region and not config:
            out("ERROR: --cloud needs --cloud-region (or explicit --api-url ...).")
            return 2
        user = args.account_username or os.environ.get(ENV_USER)
        pwd = args.account_password or os.environ.get(ENV_PWD)
        if not user or not pwd:
            out(f"ERROR: --cloud needs Blueair account credentials: env {ENV_USER}/{ENV_PWD} or --account-username/--account-password.")
            return 2
        region = args.cloud_region or _region_from_api_url(config["api_url"] if config else "")
        if region is None:
            out("ERROR: cannot infer the cloud region from --api-url; pass --cloud-region.")
            return 2
        from .cloud import BlueairCloud

        cloud = BlueairCloud(user, pwd, cloud_region=region, gigya_region=args.gigya_region)

    # ---- confirmation
    out("=" * 78)
    out("PROVISION PLAN" + ("  (DRY RUN: no StartCmd / ConfigCmd / WiFi config / cloud writes)" if args.dry_run else ""))
    out(f"  target        : {args.address or args.name or (args.name_prefix + '*' if args.name_prefix else 'first device advertising ' + C.SERVICE_UUID)}")
    out(f"  WiFi SSID     : {ssid!r} ({len(ssid.encode())} bytes)")
    out(f"  WiFi pass     : {mask(password, args.show_password)}" + ("" if args.show_password else "   [--show-password to reveal]"))
    if config:
        out(f"  ConfigCmd x{len(config)}   :")
        for f in C.CONFIG_FIELD_ORDER:
            if f in config:
                v = config[f]
                shown = v if len(v) <= 60 else f"{v[:24]}...{v[-12:]!r}".replace("'", "")
                out(f"     {f:<14}= {shown!s}{'  (generated)' if f in generated else ''}")
    else:
        out("  ConfigCmd     : NONE (no config flags given; Rust reference always sent six fields)")
    out(f"  cloud         : {'YES -> ' + cloud.api_url if cloud else 'no cloud calls'}")
    steps = ["connect", "proto-ver", f"sec{args.sec} session"]
    if args.dry_run:
        steps += ["wifi-scan", "STOP"]
    else:
        steps += ["StartCmd"]
        if cloud:
            steps += ["cloud login", "POST /c/register-for-onboarding"]
        if config:
            steps += [f"ConfigCmd x{len(config)}"]
        steps += ["CmdSetConfig", "CmdApplyConfig"]
        steps += ["CmdGetStatus x1", "EventGet poll -> DeviceBound"] if args.wifi_status_mode == "app" else ["CmdGetStatus poll", "get_all_event"]
        if args.no_events:
            steps = [s for s in steps if "Event" not in s]
        if cloud:
            steps += ["cloud device-status", "cloud registered-devices"]
    out("  steps         : " + " -> ".join(steps))
    out("=" * 78)
    if not args.dry_run and not args.yes:
        out("Refusing to write: re-run with --yes to proceed.")
        return 2

    summary: dict[str, Any] = {"ssid": ssid, "config_sent": bool(config) and not args.dry_run, "dry_run": args.dry_run}
    client = None
    try:
        if cloud:
            await cloud.login()
            summary["cloud_user_id"] = cloud.user_id
            out(f"cloud login OK: userId={cloud.user_id}")

        client = await open_client(args)
        session = await open_session(args, client)
        summary["proto_ver"] = session.proto_ver_json or (session.proto_ver_raw or b"").decode("utf-8", "replace")

        if args.dry_run:
            results = await session.wifi_scan()
            print_wifi_results(results)
            summary["wifi_scan"] = [asdict(r) for r in results]
            target = [r for r in results if r.ssid == ssid]
            out(f"target SSID {ssid!r}: {'FOUND ' + str(target[0].auth) + ' rssi=' + str(target[0].rssi) if target else 'NOT SEEN in scan'}")
            out("dry run complete; nothing written.")
            if args.json:
                out(json.dumps(summary, indent=2))
            return 0

        await session.start()
        out("StartCmd: OK")

        if cloud and config:
            await cloud.register_for_onboarding(config["random_text"], config["secure_random"])
            out("cloud register-for-onboarding: OK")

        if config:
            await session.set_configuration(config)
            out(f"ConfigCmd x{len(config)}: OK")

        # ---- WiFi
        if args.wifi_status_mode == "rust":
            st = await session.wifi_connect(ssid, password, timeout=args.wifi_timeout, poll_interval=args.status_poll_interval,
                                            require_configured=args.require_configured)
            out(f"WiFi: {st.sta_state_name} ip={st.ip4_addr} {st.connected or ''}")
            summary["wifi_status"] = asdict(st)
            if not args.no_events:
                events = await session.get_all_event(max_events=args.max_events)
                print_events(events)
                summary["events"] = [e.raw_json for e in events]
            return 0

        # app mode
        if args.require_configured and not session.is_configured:
            raise ProvisioningError("Device not configured.")
        await session.wifi_set_config(ssid, password)
        await session.wifi_apply_config()
        out("CmdSetConfig + CmdApplyConfig: OK")
        st = await session.wifi_get_status()
        out(f"CmdGetStatus: sta_state={st.sta_state_name} fail_reason={st.fail_reason_name} ip={st.ip4_addr}")
        summary["wifi_status"] = asdict(st)
        if st.sta_state in (wifi_constants_pb2.ConnectionFailed, wifi_constants_pb2.Disconnected):
            hint = {"AuthError": "wrong WiFi passphrase", "NetworkNotFound": "SSID not found"}.get(st.fail_reason_name or "", "")
            raise ProvisioningError(f"WiFi {st.sta_state_name}: {st.fail_reason_name} {hint}".strip())
        if args.no_events:
            out("skipping EventGet phase (--no-events)")
            return 0

        from .cloud import classify_device_event

        deadline = time.monotonic() + args.event_timeout
        device_uuid: str | None = None
        seen_states: list[str] = []
        while True:
            events = await session.get_all_event(max_events=args.max_events)
            verdict = None
            for ev in events:
                v = classify_device_event(ev.json)
                if v.kind != "ignore":
                    label = f"{ev.json.get('et')}({ev.json.get('ec')})" if isinstance(ev.json, dict) else "?"
                    if not seen_states or seen_states[-1] != label:
                        seen_states.append(label)
                        out(f"  device event: {label}  {ev.raw_json}")
                if v.kind == "success":
                    device_uuid = v.device_uuid
                    verdict = v
                    break
                if v.kind == "error":
                    raise ProvisioningError(f"device reported failure: {v.message}")
            if verdict is not None:
                break
            if time.monotonic() >= deadline:
                raise ProvisioningError(f"timed out after {args.event_timeout:.0f}s waiting for DeviceBound; states seen: {seen_states}")
            await asyncio.sleep(args.event_poll_interval)
        out(f"DeviceBound: device uuid = {device_uuid}")
        summary["device_uuid"] = device_uuid
        summary["event_states"] = seen_states

        # BLE part done; release the link before the cloud polls.
        await safe_disconnect(client)
        client = None

        if cloud:
            if device_uuid:
                online = await cloud.wait_online(device_uuid)
                out(f"cloud device-status online: {online}")
                summary["cloud_online"] = online
            devices = await cloud.registered_devices()
            match = [d for d in devices if device_uuid and str(d.get("uuid")) == device_uuid]
            out(f"cloud registered-devices: {len(devices)} device(s); target {'FOUND: ' + json.dumps(match[0]) if match else 'NOT listed'}")
            summary["cloud_registered"] = bool(match)
        return 0
    except (ProvisioningError, BleakError, asyncio.TimeoutError) as e:
        out(f"ERROR: {type(e).__name__}: {e}")
        summary["error"] = str(e)
        return 3
    finally:
        await safe_disconnect(client)
        if cloud:
            await cloud.close()
        if args.json:
            out(json.dumps(summary, indent=2, default=str))


def _region_from_api_url(api_url: str) -> str | None:
    for key, vals in C.BLUEAIR_CLOUD_REGIONS.items():
        if vals["api_url"] == api_url.rstrip("/"):
            return key
    return None


# ---------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    handlers = {
        "scan": cmd_scan,
        "info": cmd_info,
        "wifi-scan": cmd_wifi_scan,
        "events": cmd_events,
        "provision": cmd_provision,
    }
    try:
        return asyncio.run(handlers[args.cmd](args))
    except KeyboardInterrupt:
        out("interrupted")
        return 130
    except (BleakError, asyncio.TimeoutError, ProvisioningError) as e:
        out(f"ERROR: {type(e).__name__}: {e or '(timeout)'}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
