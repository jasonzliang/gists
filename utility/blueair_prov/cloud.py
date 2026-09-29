"""Blueair cloud side of onboarding (NOT in the Rust reference; from the
decompiled Android app, see /tmp/blueair_cloud_flow.md).

Auth chain (Gigya -> accounts.getJWT -> POST {api_url}/c/login) is reused from
dahlb/blueair_api (HttpAwsBlueair). This module adds the three onboarding
calls the app makes around the BLE ConfigCmd writes:

  1. POST {api_url}/c/register-for-onboarding  {"secure-random", "random-text"}   (BEFORE ConfigCmd)
  2. POST {api_url}/c/device-status {"deviceId": uuid} -> {"online": bool}          (after DeviceBound)
  3. GET  {api_url}/c/registered-devices -> {"devices": [{uuid, mac, name, userId}]}
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import string
from dataclasses import dataclass
from typing import Any

from . import constants as C

log = logging.getLogger("blueair_prov.cloud")

_ALNUM = string.ascii_letters + string.digits


def generate_random_text(length: int = C.RANDOM_TEXT_LEN) -> str:
    """128 random [A-Za-z0-9] characters (app: random_text)."""
    return "".join(secrets.choice(_ALNUM) for _ in range(length))


def android_base64_default(data: bytes) -> str:
    """Android `Base64.encodeToString(data, Base64.DEFAULT)`: standard alphabet
    with padding, a "\\n" after every 76 output chars and a trailing "\\n"."""
    b64 = base64.b64encode(data).decode("ascii")
    lines = [b64[i : i + 76] for i in range(0, len(b64), 76)]
    return "".join(line + "\n" for line in lines)


def generate_secure_random(nbytes: int = C.SECURE_RANDOM_BYTES) -> str:
    """Base64(64 random bytes) in Android Base64.DEFAULT format (90 bytes)."""
    return android_base64_default(secrets.token_bytes(nbytes))


# ------------------------------------------------------------------ device events

# et states reported by the device via EventResp.json (from the app's DeviceEvent enum).
KNOWN_EVENT_STATES = (
    "NotConnected", "DeviceUnbound", "LinkConnect", "LinkReconnect", "LinkConnected",
    "ObtainingIPAddress", "IPAddressObtained", "Authenticating", "Authenticated",
    "BrokerConnecting", "BrokerConnected", "RegisterDevice", "SettingPassword",
    "DeviceBound", "BrokerDisconnected",
)


@dataclass
class EventVerdict:
    kind: str            # "success" | "wait" | "error" | "ignore"
    message: str
    device_uuid: str | None = None


def classify_device_event(ev: Any) -> EventVerdict:
    """App logic: success when et == "DeviceBound" and ec >= 0; ec < 0 is an
    error except (BrokerConnecting, -5) which means keep waiting."""
    if not isinstance(ev, dict):
        return EventVerdict("ignore", "no event / not a JSON object")
    et = ev.get("et")
    ec = ev.get("ec")
    o = ev.get("o")
    try:
        ec_i = int(ec) if ec is not None else None
    except (TypeError, ValueError):
        ec_i = None
    if et == "DeviceBound" and ec_i is not None and ec_i >= 0:
        return EventVerdict("success", f"DeviceBound (ec={ec_i}) uuid={o}", device_uuid=str(o) if o else None)
    if ec_i is not None and ec_i < 0:
        if et == "BrokerConnecting" and ec_i == -5:
            return EventVerdict("wait", "BrokerConnecting ec=-5 (transient, keep waiting)")
        return EventVerdict("error", f"{et} ec={ec_i}: {describe_error(et, ec_i)}")
    if et and et not in KNOWN_EVENT_STATES and not str(et).startswith("ImageProvision"):
        log.warning("unknown device event state et=%r", et)
    return EventVerdict("wait", f"{et} ec={ec_i}")


def describe_error(et: str | None, ec: int) -> str:
    """Error map from the app (see cloud flow write-up)."""
    if ec == -99:
        return "internet error (device cannot reach the internet)"
    if et == "LinkConnect" and ec == -4:
        return "JWT error (cloud rejected the device's token; check region/api_url)"
    if (et in ("LinkConnect", "LinkReconnect", "Authenticating") and ec == -3) or (et == "BrokerConnected" and ec == -8):
        return "password error (WiFi passphrase or cloud auth rejected)"
    if et == "ObtainingIPAddress" and -3 <= ec <= -1:
        return "router error (no DHCP lease)"
    if (et in ("LinkConnect", "LinkReconnect") and ec in (-1, -2)) or (et in ("BrokerConnected", "RegisterDevice", "SettingPassword") and ec == -1):
        return "signal error (weak WiFi / lost link)"
    return "unmapped error code"


# ------------------------------------------------------------------ cloud client


class CloudError(Exception):
    pass


class BlueairCloud:
    """Thin wrapper over blueair_api.HttpAwsBlueair for the onboarding calls."""

    def __init__(self, username: str, password: str, *, cloud_region: str, gigya_region: str | None = None):
        from blueair_api import HttpAwsBlueair  # imported lazily so BLE-only commands need no aiohttp

        if cloud_region not in C.BLUEAIR_CLOUD_REGIONS:
            raise ValueError(f"unknown cloud region {cloud_region!r}")
        self.cloud_region = cloud_region
        self.api_url = C.BLUEAIR_CLOUD_REGIONS[cloud_region]["api_url"]
        self._api = HttpAwsBlueair(
            username, password, gigya_region=gigya_region or cloud_region, cloud_region=cloud_region
        )
        self.access_token: str | None = None
        self.user_id: str | None = None

    async def login(self) -> str:
        """Gigya login -> getJWT -> /c/login. Returns the BlueCloud access token."""
        self.access_token = await self._api.get_access_token()
        self.user_id = self._api.user_id
        log.info("cloud login OK (region=%s, userId=%s)", self.cloud_region, self.user_id)
        return self.access_token

    async def close(self) -> None:
        await self._api.cleanup_client_session()

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise CloudError("not logged in")
        return {"Authorization": f"Bearer {self.access_token}", "X-Source": C.CLOUD_HEADERS_SOURCE}

    async def _request(self, method: str, path: str, json_body: dict | None = None) -> tuple[int, Any, str]:
        url = f"{self.api_url}{path}"
        redacted = {k: ("<%d chars>" % len(v) if isinstance(v, str) and len(v) > 40 else v) for k, v in (json_body or {}).items()}
        log.info("cloud %s %s %s", method, url, json.dumps(redacted) if json_body else "")
        async with self._api.api_session.request(method, url, json=json_body, headers=self._headers()) as resp:
            text = await resp.text()
            try:
                body = json.loads(text) if text else None
            except json.JSONDecodeError:
                body = None
            log.info("cloud <- %d %s", resp.status, text[:500])
            return resp.status, body, text

    async def register_for_onboarding(self, random_text: str, secure_random: str) -> None:
        """POST /c/register-for-onboarding; response ignored; 2 retries, 3 s apart."""
        body = {"secure-random": secure_random, "random-text": random_text}
        last: Exception | None = None
        for attempt in range(1 + C.CLOUD_REGISTER_RETRIES):
            try:
                status, _, text = await self._request("POST", "/c/register-for-onboarding", body)
                if 200 <= status < 300:
                    log.info("register-for-onboarding OK (attempt %d)", attempt + 1)
                    return
                last = CloudError(f"register-for-onboarding HTTP {status}: {text[:200]}")
            except Exception as e:  # noqa: BLE001
                last = e
            log.warning("register-for-onboarding attempt %d failed: %s", attempt + 1, last)
            if attempt < C.CLOUD_REGISTER_RETRIES:
                await asyncio.sleep(C.CLOUD_REGISTER_RETRY_DELAY_S)
        raise CloudError(f"register-for-onboarding failed: {last}")

    async def device_status(self, device_uuid: str) -> dict[str, Any]:
        status, body, text = await self._request("POST", "/c/device-status", {"deviceId": device_uuid})
        if not (200 <= status < 300) or not isinstance(body, dict):
            raise CloudError(f"device-status HTTP {status}: {text[:200]}")
        return body

    async def registered_devices(self) -> list[dict[str, Any]]:
        status, body, text = await self._request("GET", "/c/registered-devices")
        if not (200 <= status < 300):
            raise CloudError(f"registered-devices HTTP {status}: {text[:200]}")
        if isinstance(body, dict) and "devices" in body:
            return list(body["devices"])
        if isinstance(body, list):
            return body
        raise CloudError(f"registered-devices: unexpected body {text[:200]}")

    async def wait_online(self, device_uuid: str) -> bool:
        """Wait 10 s, then poll device-status up to 6x every 5 s."""
        log.info("waiting %.0fs before polling device-status", C.CLOUD_STATUS_INITIAL_WAIT_S)
        await asyncio.sleep(C.CLOUD_STATUS_INITIAL_WAIT_S)
        for i in range(C.CLOUD_STATUS_POLLS):
            try:
                st = await self.device_status(device_uuid)
                if st.get("online") is True:
                    return True
                log.info("device-status poll %d: %s", i + 1, st)
            except CloudError as e:
                log.warning("device-status poll %d failed: %s", i + 1, e)
            if i < C.CLOUD_STATUS_POLLS - 1:
                await asyncio.sleep(C.CLOUD_STATUS_POLL_INTERVAL_S)
        return False
