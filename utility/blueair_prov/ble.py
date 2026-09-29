"""BLE plumbing on top of bleak 3.x / CoreBluetooth: discovery, robust connect,
GATT enumeration and the Transport used by protocol.Session.

Port of discovery.rs (service-UUID scan filter) and the connect/characteristic
part of service.rs. macOS notes:
  * BLEDevice.address is a CoreBluetooth UUID, not a MAC.
  * A BLEDevice from a *fresh* scan is the most reliable thing to hand to
    BleakClient; connecting by address string makes bleak re-scan internally.
  * `BleakClient.connect()` has been timing out on this Mac, so connect() below
    retries with backoff, can connect straight from the scanner's detection
    callback (--connect-on-detect), and has an outer watchdog.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

from . import constants as C

log = logging.getLogger("blueair_prov.ble")


@dataclass
class ConnectOptions:
    name: str | None = None            # exact advertised/OS name
    name_prefix: str | None = None     # prefix match on name
    address: str | None = None         # CoreBluetooth UUID string
    service_filter: bool = True        # pass SERVICE_UUID to CoreBluetooth scan (as Rust did)
    scan_timeout: float = 15.0
    connect_timeout: float = 20.0
    retries: int = 3
    backoff: float = 5.0
    backoff_factor: float = 1.5
    max_backoff: float = 30.0
    connect_on_detect: bool = False
    keep_scanning: bool = False        # with connect_on_detect: leave the scanner running during connect
    watchdog_margin: float = 15.0      # extra seconds before we abandon a hung connect()
    allow_nonconnectable: bool = False # issue connectPeripheral even if the adv says non-connectable


@dataclass
class Seen:
    device: BLEDevice
    adv: AdvertisementData
    first_seen: float = field(default_factory=time.monotonic)
    last_seen: float = field(default_factory=time.monotonic)
    count: int = 1
    connectable_values: set = field(default_factory=set)


def is_connectable(adv: AdvertisementData) -> bool | None:
    """CoreBluetooth's kCBAdvDataIsConnectable (None if unavailable, e.g. other OS).
    A peripheral advertising ADV_NONCONN_IND / ADV_SCAN_IND can never be connected
    to: macOS queues connectPeripheral: until a connectable advertisement shows up,
    which looks exactly like an endless connect timeout."""
    try:
        adv_dict = adv.platform_data[1]
        v = adv_dict.get("kCBAdvDataIsConnectable") if adv_dict is not None else None
        return None if v is None else bool(int(v))
    except Exception:  # noqa: BLE001
        return None


def is_blueair(device: BLEDevice, adv: AdvertisementData, opts: ConnectOptions | None = None) -> bool:
    """Match on advertised service UUID (Rust's filter) OR the name criteria."""
    opts = opts or ConnectOptions()
    name = adv.local_name or device.name or ""
    if opts.address and device.address.lower() == opts.address.lower():
        return True
    if opts.name and name == opts.name:
        return True
    if opts.name_prefix and name.startswith(opts.name_prefix):
        return True
    if opts.address or opts.name or opts.name_prefix:
        # explicit criteria given but not matched -> still accept the service UUID
        # only if no address/name was pinned
        if opts.address or opts.name:
            return False
    return C.SERVICE_UUID in [u.lower() for u in adv.service_uuids]


def describe(device: BLEDevice, adv: AdvertisementData) -> str:
    svc = ",".join(adv.service_uuids) or "-"
    mfg = ",".join(f"0x{k:04x}:{v.hex()}" for k, v in adv.manufacturer_data.items()) or "-"
    c = is_connectable(adv)
    return (
        f"{device.address}  name={adv.local_name or device.name!r}  rssi={adv.rssi}  "
        f"connectable={'?' if c is None else ('yes' if c else 'NO')}  tx={adv.tx_power}  services=[{svc}]  mfg=[{mfg}]"
    )


def raw_advertisement(adv: AdvertisementData) -> str:
    """CoreBluetooth's own advertisement dictionary (platform_data[1]) plus the
    peripheral state; useful when connects time out (is it connectable at all?)."""
    try:
        peripheral, adv_dict, rssi = adv.platform_data
        d = {str(k): (bytes(v).hex() if hasattr(v, "bytes") or isinstance(v, (bytes, bytearray)) else str(v)) for k, v in dict(adv_dict).items()}
        d["peripheral.state"] = str(peripheral.state())
        d["peripheral.name"] = str(peripheral.name())
        return " ".join(f"{k}={v}" for k, v in sorted(d.items()))
    except Exception as e:  # noqa: BLE001
        return f"<unavailable: {e}>"


async def scan(duration: float, service_filter: bool, opts: ConnectOptions | None = None) -> dict[str, Seen]:
    """discovery.rs discover_devices(): start_scan(filter) -> sleep -> stop_scan -> peripherals()."""
    seen: dict[str, Seen] = {}
    opts = opts or ConnectOptions()

    def cb(device: BLEDevice, adv: AdvertisementData) -> None:
        s = seen.get(device.address)
        if s is None:
            s = seen[device.address] = Seen(device, adv)
            if is_blueair(device, adv, opts):
                log.info("detected Blueair candidate: %s", describe(device, adv))
        else:
            s.adv, s.last_seen, s.count = adv, time.monotonic(), s.count + 1
        c = is_connectable(adv)
        if c is not None:
            s.connectable_values.add(c)

    scanner = BleakScanner(cb, service_uuids=[C.SERVICE_UUID] if service_filter else None)
    log.info("scanning for %.1fs (CoreBluetooth service filter %s)", duration, "ON" if service_filter else "OFF")
    await scanner.start()
    try:
        await asyncio.sleep(duration)
    finally:
        await scanner.stop()
    return seen


class _Matcher:
    """Detection-callback state shared by find_device / _connect_on_detect:
    resolves the future on the first matching *connectable* advertisement and
    counts the non-connectable ones so the timeout message can explain itself."""

    def __init__(self, opts: ConnectOptions):
        self.opts = opts
        self.fut: asyncio.Future[tuple[BLEDevice, AdvertisementData]] = asyncio.get_running_loop().create_future()
        self.nonconnectable_seen = 0
        self.last_nonconnectable: tuple[BLEDevice, AdvertisementData] | None = None

    def __call__(self, device: BLEDevice, adv: AdvertisementData) -> None:
        if self.fut.done() or not is_blueair(device, adv, self.opts):
            return
        if is_connectable(adv) is False and not self.opts.allow_nonconnectable:
            self.nonconnectable_seen += 1
            self.last_nonconnectable = (device, adv)
            if self.nonconnectable_seen == 1:
                log.warning(
                    "%s is advertising NON-connectable (kCBAdvDataIsConnectable=0); waiting for a "
                    "connectable advertisement instead of issuing a doomed connect. Put the purifier in "
                    "pairing mode, or force with --allow-nonconnectable.",
                    device.address,
                )
            return
        self.fut.set_result((device, adv))

    def timeout_error(self) -> BleakError:
        if self.nonconnectable_seen:
            dev, adv = self.last_nonconnectable  # type: ignore[misc]
            return BleakError(
                f"{dev.address} ({adv.local_name or dev.name}) sent {self.nonconnectable_seen} advertisement(s) in "
                f"{self.opts.scan_timeout:.0f}s, ALL non-connectable (kCBAdvDataIsConnectable=0). macOS cannot connect to it "
                f"in this state; a connect would time out. Put the device into BLE pairing mode and retry."
            )
        return BleakError(f"no matching device seen within {self.opts.scan_timeout:.0f}s")


async def find_device(opts: ConnectOptions) -> tuple[BLEDevice, AdvertisementData]:
    """Fresh scan until the first matching (connectable) advertisement, or timeout."""
    m = _Matcher(opts)
    scanner = BleakScanner(m, service_uuids=[C.SERVICE_UUID] if opts.service_filter else None)
    await scanner.start()
    try:
        return await asyncio.wait_for(m.fut, opts.scan_timeout)
    except asyncio.TimeoutError:
        raise m.timeout_error() from None
    finally:
        await scanner.stop()


async def _connect_device(device: BLEDevice, opts: ConnectOptions, on_disconnect: Callable[[BleakClient], None]) -> BleakClient:
    client = BleakClient(device, disconnected_callback=on_disconnect, timeout=opts.connect_timeout)
    t0 = time.monotonic()
    log.info("connecting to %s (timeout %.0fs, adv connectable=%s)...", device.address, opts.connect_timeout, is_connectable_str(device))
    # Outer watchdog: bleak's CoreBluetooth timeout path awaits a disconnect
    # future with no bound of its own; do not let a hung attempt stall the loop.
    await asyncio.wait_for(client.connect(timeout=opts.connect_timeout), opts.connect_timeout + opts.watchdog_margin)
    log.info("connected in %.2fs; mtu=%s", time.monotonic() - t0, _safe_mtu(client))
    return client


def is_connectable_str(device: BLEDevice) -> str:
    try:
        return str(device.details[0].state())  # CBPeripheral state at connect time (0 = disconnected)
    except Exception:  # noqa: BLE001
        return "?"


def _safe_mtu(client: BleakClient) -> Any:
    try:
        return client.mtu_size
    except Exception:  # noqa: BLE001
        return "?"


async def _connect_on_detect(opts: ConnectOptions, on_disconnect: Callable[[BleakClient], None]) -> BleakClient:
    """Connect using the BLEDevice handed to the detection callback, immediately
    (optionally while the scanner is still running)."""
    m = _Matcher(opts)
    scanner = BleakScanner(m, service_uuids=[C.SERVICE_UUID] if opts.service_filter else None)
    await scanner.start()
    stopped = False
    try:
        try:
            device, adv = await asyncio.wait_for(m.fut, opts.scan_timeout)
        except asyncio.TimeoutError:
            raise m.timeout_error() from None
        log.info("detected: %s", describe(device, adv))
        if not opts.keep_scanning:
            await scanner.stop()
            stopped = True
        return await _connect_device(device, opts, on_disconnect)
    finally:
        if not stopped:
            try:
                await scanner.stop()
            except Exception:  # noqa: BLE001
                pass


async def connect(opts: ConnectOptions, on_disconnect: Callable[[BleakClient], None] | None = None) -> BleakClient:
    """Retry loop with backoff around (scan -> connect). Raises the last error."""
    on_disconnect = on_disconnect or (lambda c: log.warning("device disconnected: %s", c.address))
    attempts = 1 + max(0, opts.retries)
    delay = opts.backoff
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            log.info("connect attempt %d/%d (%s)", attempt, attempts, "connect-on-detect" if opts.connect_on_detect else "scan-then-connect")
            if opts.connect_on_detect:
                return await _connect_on_detect(opts, on_disconnect)
            device, adv = await find_device(opts)
            log.info("found: %s", describe(device, adv))
            return await _connect_device(device, opts, on_disconnect)
        except (asyncio.TimeoutError, BleakError, OSError) as e:
            last = e
            kind = "timeout" if isinstance(e, asyncio.TimeoutError) else type(e).__name__
            log.warning("connect attempt %d failed: %s: %s", attempt, kind, e or "(no message)")
            if attempt < attempts:
                log.info("backing off %.1fs before retry", delay)
                await asyncio.sleep(delay)
                delay = min(delay * opts.backoff_factor, opts.max_backoff)
    assert last is not None
    raise last


# ---------------------------------------------------------------------------- GATT


@dataclass
class GattChar:
    name: str | None
    uuid: str
    handle: int
    properties: list[str]
    descriptors: list[tuple[str, int, str]]  # (uuid, handle, value-as-text-or-hex)
    user_description: str | None


async def enumerate_gatt(client: BleakClient, read_descriptors: bool = True) -> dict[str, list[GattChar]]:
    """Walk services/characteristics/descriptors; read 0x2901 user descriptions
    (that is how service.rs names its endpoints)."""
    out: dict[str, list[GattChar]] = {}
    for service in client.services:
        chars: list[GattChar] = []
        for ch in service.characteristics:
            descs: list[tuple[str, int, str]] = []
            user_desc: str | None = None
            for d in ch.descriptors:
                val = ""
                if read_descriptors:
                    try:
                        raw = bytes(await client.read_gatt_descriptor(d))
                        try:
                            val = raw.decode("utf-8")
                        except UnicodeDecodeError:
                            val = raw.hex()
                        if d.uuid.lower() == C.USER_DESCRIPTION_UUID:
                            user_desc = val
                    except Exception as e:  # noqa: BLE001
                        val = f"<read failed: {e}>"
                descs.append((d.uuid, d.handle, val))
            chars.append(
                GattChar(
                    name=C.UUID_TO_ENDPOINT.get(ch.uuid.lower()),
                    uuid=ch.uuid,
                    handle=ch.handle,
                    properties=list(ch.properties),
                    descriptors=descs,
                    user_description=user_desc,
                )
            )
        out[service.uuid] = chars
    return out


def map_endpoints(client: BleakClient) -> dict[str, BleakGATTCharacteristic]:
    """Hard-coded UUID -> endpoint mapping (spec item 4). Missing endpoints raise."""
    found: dict[str, BleakGATTCharacteristic] = {}
    for service in client.services:
        for ch in service.characteristics:
            name = C.UUID_TO_ENDPOINT.get(ch.uuid.lower())
            if name:
                found[name] = ch
    missing = [n for n in C.ENDPOINT_NAMES if n not in found]
    if missing:
        raise BleakError(f"Blueair endpoints not found on device: {missing}; services={[s.uuid for s in client.services]}")
    return found


async def verify_endpoint_names(client: BleakClient, chars: dict[str, BleakGATTCharacteristic]) -> None:
    """Cross-check the user-description descriptors against the hard-coded map
    (what Rust relies on exclusively). Logs mismatches; never fatal."""
    for name, ch in chars.items():
        for d in ch.descriptors:
            if d.uuid.lower() != C.USER_DESCRIPTION_UUID:
                continue
            try:
                val = bytes(await client.read_gatt_descriptor(d)).decode("utf-8", errors="replace")
            except Exception as e:  # noqa: BLE001
                log.warning("could not read user description of %s: %s", name, e)
                continue
            if val != name:
                log.warning("descriptor name %r != expected %r for %s", val, name, ch.uuid)
            else:
                log.debug("descriptor confirms %s = %s", ch.uuid, name)


class BleakTransport:
    """service.rs write_characteristic (WriteType::WithResponse) / read_characteristic."""

    def __init__(self, client: BleakClient, chars: dict[str, BleakGATTCharacteristic]):
        self.client = client
        self.chars = chars

    async def write(self, endpoint: str, data: bytes) -> None:
        await self.client.write_gatt_char(self.chars[endpoint], data, response=True)

    async def read(self, endpoint: str) -> bytes:
        return bytes(await self.client.read_gatt_char(self.chars[endpoint]))
