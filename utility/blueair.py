#!/usr/bin/env python3
# blueair.py — control and pair Blueair air purifiers from a Mac (no phone needed). ONE file: the cloud CLI (blueair-api) plus the
# BLE provisioning tool that used to be the blueair_prov package (Espressif protocomm Security1 + Blueair custom endpoint,
# Python port of github.com/kovapatrik/blueairble-rs; verified live on a Blue Pure 511i Max on 2026-09-28).
#
# Install (Python 3.10+; pip dependencies only, no sibling files):
#   python3 blueair.py install        # creates the venv, installs deps + the `blueair` command, asks for your account
# or by hand:
#   python3 -m venv ~/.local/share/blueair-cli/venv
#   ~/.local/share/blueair-cli/venv/bin/pip install blueair-api aiohttp bleak protobuf cryptography
#   install -m 755 blueair.py ~/.local/bin/blueair
#   perl -pi -e '$_ = "#!$ENV{HOME}/.local/share/blueair-cli/venv/bin/python\n" if $. == 1' ~/.local/bin/blueair   # shebang -> venv (BSD+GNU safe)
#   printf '{"username":"you@example.com","password":"...","region":"us"}' > ~/.config/blueair/config.json && chmod 600 ~/.config/blueair/config.json
#
# Notes from the 2026-09-28 Blue Pure 511i Max setup: the purifier only joins 2.4 GHz WiFi; it accepts Bluetooth connections for
# only ~5 s after holding Fan Speed ~5 s (LEDs blink once); a Google-only Blueair account must have a password added (reset email)
# before Blueair's cloud login works; rename goes through PATCH c/cm/update {"uuid","di":{"name"}}; night mode switches auto mode off.
"""blueair — control Blueair purifiers on your account through Blueair's cloud API (uses the blueair-api library).

Usage:
  python3 blueair.py install          one-time setup on a new machine (venv, dependencies, command, credentials)
  blueair status                      show every device: online, standby, fan, auto, brightness, night, lock, filter, sensors
  blueair fan <0-100>                 set fan speed (percent; 511i Max uses 3 steps, values map to the nearest)
  blueair on | off                    leave / enter standby
  blueair auto on|off                 auto mode
  blueair night on|off                night mode
  blueair lock on|off                 child lock
  blueair brightness <0-100>          LED brightness
  blueair rename "<name>"             rename the device (raw cloud call, same as the app)
  blueair raw                         dump the raw device_info JSON

Pairing a NEW purifier (Bluetooth, from this Mac; no phone needed):
  blueair discover [--seconds 10]     list unpaired Blueair units advertising over Bluetooth and whether they are accepting connections
  blueair networks                    ask the purifier to scan WiFi (2.4 GHz only) so you can pick the right SSID; no settings written
  blueair setup --ssid <2.4GHz SSID> [--wifi-password ...] [--name "Bedroom"] [--no-cloud] [--timeout 900]
                                      full pairing: joins WiFi and binds the unit to your account (like the app). Prompts for the WiFi
                                      password if not given. When told to, hold the unit's Fan Speed button ~5 s until the LEDs blink once;
                                      the purifier accepts Bluetooth connections only for ~5 s after that, and the tool connects instantly.
Low-level provisioning (the former blueair_prov tool, for debugging):
  blueair prov [--connect-on-detect --scan-timeout N ...] scan|info|wifi-scan|events|provision ...     see `blueair prov --help`
Options: --device <uuid-or-name-substring> (default: the only/first device), --json
Credentials: ~/.config/blueair/config.json {"username","password","region"} or env BLUEAIR_USERNAME / BLUEAIR_PASSWORD / BLUEAIR_REGION.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import string
import sys
import time
import types
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol

# --------------------------------------------------------------------------------------------------------------------
# SECTION: install — `python3 blueair.py install` (stdlib only; runs BEFORE the third-party imports below)
# --------------------------------------------------------------------------------------------------------------------
INSTALL_DEPS = ["blueair-api", "aiohttp", "bleak", "protobuf", "cryptography"]
DEFAULT_VENV = os.path.expanduser("~/.local/share/blueair-cli/venv")
DEFAULT_BIN = os.path.expanduser("~/.local/bin")
DEFAULT_CFG = os.path.expanduser("~/.config/blueair/config.json")


def _install(argv):
    """Create the virtualenv, install dependencies, install this file as `blueair`, and write the credentials file."""
    import getpass
    import shutil
    import subprocess

    ap = argparse.ArgumentParser(prog="blueair.py install", description="Install blueair as a command (venv + deps + config).")
    ap.add_argument("--venv", default=DEFAULT_VENV, help=f"virtualenv location (default {DEFAULT_VENV})")
    ap.add_argument("--bin", default=DEFAULT_BIN, help=f"directory for the `blueair` command (default {DEFAULT_BIN})")
    ap.add_argument("--no-config", action="store_true", help="do not prompt for / write account credentials")
    ap.add_argument("--reconfigure", action="store_true", help="overwrite an existing credentials file")
    a = ap.parse_args(argv)

    if sys.version_info < (3, 10):
        sys.exit(f"Python 3.10+ is required (this is {sys.version.split()[0]}). Install a newer python3 (e.g. `brew install python`) and rerun with it.")
    if sys.platform == "darwin":
        print("Note: Bluetooth pairing commands (discover/networks/setup) need macOS Bluetooth permission for your terminal app; "
              "macOS asks on first use.", flush=True)

    py = os.path.join(a.venv, "bin", "python")
    if not os.path.exists(py):
        print(f"[1/4] creating virtualenv {a.venv}", flush=True)
        os.makedirs(os.path.dirname(a.venv), exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", a.venv], check=True)
    else:
        print(f"[1/4] virtualenv exists: {a.venv}", flush=True)
    print(f"[2/4] installing {' '.join(INSTALL_DEPS)}", flush=True)
    subprocess.run([py, "-m", "pip", "install", "-q", "--upgrade", *INSTALL_DEPS], check=True)

    os.makedirs(a.bin, exist_ok=True)
    target = os.path.join(a.bin, "blueair")
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        body = f.read().split("\n", 1)[1]
    with open(target, "w", encoding="utf-8") as f:
        f.write(f"#!{py}\n" + body)
    os.chmod(target, 0o755)
    print(f"[3/4] installed command: {target}", flush=True)

    if a.no_config:
        print("[4/4] skipping credentials (--no-config)", flush=True)
    elif os.path.exists(DEFAULT_CFG) and not a.reconfigure:
        print(f"[4/4] credentials already present: {DEFAULT_CFG} (use --reconfigure to replace)", flush=True)
    else:
        print("[4/4] Blueair account (the same login as the phone app; a Google-only account needs a password added first "
              "via 'Forgot password')", flush=True)
        user = input("  e-mail: ").strip()
        pw = getpass.getpass("  password: ")
        region = (input("  region [us/eu/cn/au] (default us): ").strip().lower() or "us")
        if region not in ("us", "eu", "cn", "au"):
            sys.exit("region must be one of us, eu, cn, au")
        os.makedirs(os.path.dirname(DEFAULT_CFG), mode=0o700, exist_ok=True)
        fd = os.open(DEFAULT_CFG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"username": user, "password": pw, "region": region}, f)
        print(f"  written {DEFAULT_CFG} (mode 600)", flush=True)

    rc = subprocess.run([py, target, "--help"], capture_output=True).returncode
    print("self-test:", "OK" if rc == 0 else f"FAILED (exit {rc})", flush=True)
    if not any(os.path.realpath(p) == os.path.realpath(a.bin) for p in os.environ.get("PATH", "").split(os.pathsep) if p):
        print(f"\nAdd the command directory to your PATH, e.g.:  echo 'export PATH=\"{a.bin}:$PATH\"' >> ~/.zshrc && source ~/.zshrc", flush=True)
    print("\nNext:  blueair status        (cloud)\n       blueair setup --ssid <2.4GHz network>   (pair a new purifier over Bluetooth)", flush=True)
    return 0 if rc == 0 else 1


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "install":
    sys.exit(_install(sys.argv[2:]))

try:
    from bleak import BleakClient, BleakScanner
    from bleak.backends.characteristic import BleakGATTCharacteristic
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    from bleak.exc import BleakError
    from blueair_api import DeviceAws, HttpAwsBlueair
    from blueair_api.const import AWS_APIKEYS
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from google.protobuf import descriptor_pool
    from google.protobuf.message import DecodeError, Message
except ImportError as _e:   # fresh machine: only `install` (above) and this message work without the dependencies
    sys.exit(f"missing dependency ({_e.name}). Run:  python3 {os.path.basename(__file__)} install   "
             f"(or: pip install {' '.join(INSTALL_DEPS)})")

__version__ = "0.1.0"  # provisioning tool version (`blueair prov --version`)

log = logging.getLogger("blueair_prov")            # provisioning CLI
log_proto = logging.getLogger("blueair_prov.protocol")
log_ble = logging.getLogger("blueair_prov.ble")
log_cloud = logging.getLogger("blueair_prov.cloud")


# ======================================================================================================================
# SECTION: constants — protocol constants and the Blueair cloud region table   (formerly blueair_prov/constants.py)
# ======================================================================================================================
# Protocol constants. Everything here is taken from the Rust reference
# (github.com/kovapatrik/blueairble-rs) unless explicitly marked otherwise.

# discovery.rs: ScanFilter{services: [BLUEAIR_CHARACTERISTIC]} (it is really the service UUID)
SERVICE_UUID = "4772911e-d07c-4617-8241-f4d10948d6ae"

# service.rs header comments ("Discovered characteristic: <name>, uuid: <uuid>").
# The Rust code maps characteristics by reading each one's first descriptor
# (Characteristic User Description, 0x2901) and using that string as the key.
# We hard-code the UUID->name map (per the verified spec) and only read the
# descriptors for display / cross-checking.
ENDPOINT_UUIDS = {
    "prov-scan": "4772ff50-d07c-4617-8241-f4d10948d6ae",
    "prov-session": "4772ff51-d07c-4617-8241-f4d10948d6ae",
    "prov-config": "4772ff52-d07c-4617-8241-f4d10948d6ae",
    "proto-ver": "4772ff53-d07c-4617-8241-f4d10948d6ae",
    "custom-endpoint": "4772ff54-d07c-4617-8241-f4d10948d6ae",
}
UUID_TO_ENDPOINT = {v: k for k, v in ENDPOINT_UUIDS.items()}
ENDPOINT_NAMES = tuple(ENDPOINT_UUIDS)

# Endpoints written in plaintext vs. through the session cipher (service.rs).
PLAINTEXT_ENDPOINTS = frozenset({"proto-ver", "prov-session"})
ENCRYPTED_ENDPOINTS = frozenset({"prov-scan", "prov-config", "custom-endpoint"})

# GATT user-description descriptor UUID (Bluetooth SIG assigned number 0x2901).
USER_DESCRIPTION_UUID = "00002901-0000-1000-8000-00805f9b34fb"

# service.rs: const WIFI_PACKET_COUNT: u32 = 4;  (page size for CmdScanResult)
WIFI_PACKET_COUNT = 4

# service.rs get_proto_ver(): writes "ESP" then reads.
PROTO_VER_REQUEST = b"ESP"

# service.rs wifi_scan(): CmdScanStart parameters.
SCAN_START_BLOCKING = True
SCAN_START_PASSIVE = False
SCAN_START_GROUP_CHANNELS = 0
SCAN_START_PERIOD_MS = 120
# service.rs: `while !scan_finished && iter_count < 10`
SCAN_STATUS_MAX_POLLS = 10

# service.rs wifi_connect(): `Duration::from_secs(10)` overall status-poll budget.
WIFI_STATUS_TIMEOUT_S = 10.0

# The Rust loops have NO sleep between polls. The verified spec asks for a
# 1-2 s sleep; both defaults are CLI-overridable (0 reproduces Rust exactly).
DEFAULT_SCAN_POLL_INTERVAL_S = 1.0
DEFAULT_STATUS_POLL_INTERVAL_S = 1.0

# service.rs set_configuration(): six ConfigCmd round trips in this exact order.
CONFIG_FIELD_ORDER = (
    "api_url",
    "auth_url",
    "broker_url",
    "region",
    "random_text",
    "secure_random",
)

# Example advertised name: "<6-digit SKU>_<MAC without colons>" (e.g. 112637_AABBCCDDEEFF). Used only as a
# display hint; discovery matches on SERVICE_UUID and/or --name/--name-prefix.
KNOWN_DEVICE_NAME = "112637_AABBCCDDEEFF"

# ---------------------------------------------------------------------------
# BLUEAIR CLOUD CONFIGURATION (ConfigCmd values written to the device)
# ---------------------------------------------------------------------------
# Source: decompiled official Android app, BlueCloudDomain.getDomainForRegion
# (prod), extracted from the decompiled Blueair Android app on 2026-09-28. Cross-checked against
# dahlb/blueair_api const.AWS_APIKEYS / AWS_MQTT_BROKERS (tests/test_cloud.py).
# The Rust example main.rs used LOCAL test servers (http://192.168.0.4:8080 ...)
# which must never be used as defaults.
#
#   api_url    = https://<restApiId>.execute-api.<awsRegion>/prod
#   auth_url   = api_url + "/c/authenticate"
#   broker_url = AWS IoT ATS endpoint host (no scheme, as the app sends it)
#   region     = AWS region string
#   random_text   = 128 random [A-Za-z0-9] chars              (generated per run)
#   secure_random = Base64(64 random bytes), Android Base64.DEFAULT
#                   (76-char lines + trailing "\n", 90 bytes)  (generated per run)
# The SAME random_text / secure_random strings are POSTed to the cloud
# (/c/register-for-onboarding) and written to the device.
BLUEAIR_CLOUD_REGIONS: dict[str, dict[str, str]] = {
    "us": {
        "api_url": "https://on1keymlmh.execute-api.us-east-2.amazonaws.com/prod",
        "auth_url": "https://on1keymlmh.execute-api.us-east-2.amazonaws.com/prod/c/authenticate",
        "broker_url": "a3tpdpjvxk6yog-ats.iot.us-east-2.amazonaws.com",
        "region": "us-east-2",
    },
    "eu": {
        "api_url": "https://hkgmr8v960.execute-api.eu-west-1.amazonaws.com/prod",
        "auth_url": "https://hkgmr8v960.execute-api.eu-west-1.amazonaws.com/prod/c/authenticate",
        "broker_url": "a3tpdpjvxk6yog-ats.iot.eu-west-1.amazonaws.com",
        "region": "eu-west-1",
    },
    "cn": {
        "api_url": "https://ftbkyp79si.execute-api.cn-north-1.amazonaws.com.cn/prod",
        "auth_url": "https://ftbkyp79si.execute-api.cn-north-1.amazonaws.com.cn/prod/c/authenticate",
        "broker_url": "a2du5f95w7oz2a.ats.iot.cn-north-1.amazonaws.com.cn",
        "region": "cn-north-1",
    },
    # "au" accounts log in via accounts.au1.gigya.com but use the EU BlueCloud
    # (per blueair_api const.py). Not in the app table; ASSUMPTION.
    "au": {
        "api_url": "https://hkgmr8v960.execute-api.eu-west-1.amazonaws.com/prod",
        "auth_url": "https://hkgmr8v960.execute-api.eu-west-1.amazonaws.com/prod/c/authenticate",
        "broker_url": "a3tpdpjvxk6yog-ats.iot.eu-west-1.amazonaws.com",
        "region": "eu-west-1",
    },
}

# Cloud onboarding flow timings (from the decompiled app; poll interval is an estimate).
CLOUD_REGISTER_RETRIES = 2          # register-for-onboarding: retry twice ...
CLOUD_REGISTER_RETRY_DELAY_S = 3.0  # ... with 3 s delay
CLOUD_HEADERS_SOURCE = "android"    # X-Source header
EVENT_POLL_INTERVAL_S = 2.0         # EventGet poll cadence (not recoverable from decompile; ~2 s)
EVENT_POLL_TIMEOUT_S = 120.0        # give up waiting for DeviceBound
CLOUD_STATUS_INITIAL_WAIT_S = 10.0  # after DeviceBound, wait 10 s ...
CLOUD_STATUS_POLLS = 6              # ... then poll device-status <= 6x ...
CLOUD_STATUS_POLL_INTERVAL_S = 5.0  # ... every 5 s
RANDOM_TEXT_LEN = 128
SECURE_RANDOM_BYTES = 64
# ---------------------------------------------------------------------------


# ======================================================================================================================
# SECTION: protos — embedded protobuf descriptors (private pool)   (formerly blueair_prov/protos/*_pb2.py)
# ======================================================================================================================
# The eight FileDescriptorProto blobs are the exact `AddSerializedFile(...)` bytes protoc emitted into the former
# blueair_prov/protos/*_pb2.py (sources: blueairble-rs/src/protos/*.proto). They are registered, in dependency order, into a
# PRIVATE DescriptorPool (not google.protobuf's default pool, so nothing else that loads e.g. a "constants.proto" can collide),
# and message classes are built with message_factory.GetMessageClass. Each `*_pb2` name below is a stand-in for the generated
# module: message classes, EnumTypeWrapper objects (`.Name()` / `.Value()`) and module-level enum value constants.
_PROTO_FILES: tuple[tuple[str, bytes], ...] = (
    ('constants.proto', b'\n\x0fconstants.proto\x12\tespressif*\x9f\x01\n\x06Status\x12\x0b\n\x07Success\x10\x00\x12\x14\n\x10InvalidSecScheme\x10\x01\x12\x10\n\x0cInvalidProto\x10\x02\x12\x13\n\x0fTooManySessions\x10\x03\x12\x13\n\x0fInvalidArgument\x10\x04\x12\x11\n\rInternalError\x10\x05\x12\x0f\n\x0bCryptoError\x10\x06\x12\x12\n\x0eInvalidSession\x10\x07b\x06proto3'),
    ('wifi_constants.proto', b'\n\x14wifi_constants.proto\x12\tespressif"\x80\x01\n\x12WifiConnectedState\x12\x10\n\x08ip4_addr\x18\x01 \x01(\t\x12*\n\tauth_mode\x18\x02 \x01(\x0e2\x17.espressif.WifiAuthMode\x12\x0c\n\x04ssid\x18\x03 \x01(\x0c\x12\r\n\x05bssid\x18\x04 \x01(\x0c\x12\x0f\n\x07channel\x18\x05 \x01(\x05*Y\n\x10WifiStationState\x12\r\n\tConnected\x10\x00\x12\x0e\n\nConnecting\x10\x01\x12\x10\n\x0cDisconnected\x10\x02\x12\x14\n\x10ConnectionFailed\x10\x03*=\n\x17WifiConnectFailedReason\x12\r\n\tAuthError\x10\x00\x12\x13\n\x0fNetworkNotFound\x10\x01*\x84\x01\n\x0cWifiAuthMode\x12\x08\n\x04Open\x10\x00\x12\x07\n\x03WEP\x10\x01\x12\x0b\n\x07WPA_PSK\x10\x02\x12\x0c\n\x08WPA2_PSK\x10\x03\x12\x10\n\x0cWPA_WPA2_PSK\x10\x04\x12\x13\n\x0fWPA2_ENTERPRISE\x10\x05\x12\x0c\n\x08WPA3_PSK\x10\x06\x12\x11\n\rWPA2_WPA3_PSK\x10\x07b\x06proto3'),
    ('sec0.proto', b'\n\nsec0.proto\x12\tespressif\x1a\x0fconstants.proto"\x0e\n\x0cS0SessionCmd"2\n\rS0SessionResp\x12!\n\x06status\x18\x01 \x01(\x0e2\x11.espressif.Status"\x8c\x01\n\x0bSec0Payload\x12#\n\x03msg\x18\x01 \x01(\x0e2\x16.espressif.Sec0MsgType\x12%\n\x02sc\x18\x14 \x01(\x0b2\x17.espressif.S0SessionCmdH\x00\x12&\n\x02sr\x18\x15 \x01(\x0b2\x18.espressif.S0SessionRespH\x00B\t\n\x07payload*>\n\x0bSec0MsgType\x12\x16\n\x12S0_Session_Command\x10\x00\x12\x17\n\x13S0_Session_Response\x10\x01b\x06proto3'),
    ('sec1.proto', b'\n\nsec1.proto\x12\tespressif\x1a\x0fconstants.proto")\n\x0bSessionCmd1\x12\x1a\n\x12client_verify_data\x18\x02 \x01(\x0c"M\n\x0cSessionResp1\x12!\n\x06status\x18\x01 \x01(\x0e2\x11.espressif.Status\x12\x1a\n\x12device_verify_data\x18\x03 \x01(\x0c"$\n\x0bSessionCmd0\x12\x15\n\rclient_pubkey\x18\x01 \x01(\x0c"_\n\x0cSessionResp0\x12!\n\x06status\x18\x01 \x01(\x0e2\x11.espressif.Status\x12\x15\n\rdevice_pubkey\x18\x02 \x01(\x0c\x12\x15\n\rdevice_random\x18\x03 \x01(\x0c"\xdb\x01\n\x0bSec1Payload\x12#\n\x03msg\x18\x01 \x01(\x0e2\x16.espressif.Sec1MsgType\x12%\n\x03sc0\x18\x14 \x01(\x0b2\x16.espressif.SessionCmd0H\x00\x12&\n\x03sr0\x18\x15 \x01(\x0b2\x17.espressif.SessionResp0H\x00\x12%\n\x03sc1\x18\x16 \x01(\x0b2\x16.espressif.SessionCmd1H\x00\x12&\n\x03sr1\x18\x17 \x01(\x0b2\x17.espressif.SessionResp1H\x00B\t\n\x07payload*g\n\x0bSec1MsgType\x12\x14\n\x10Session_Command0\x10\x00\x12\x15\n\x11Session_Response0\x10\x01\x12\x14\n\x10Session_Command1\x10\x02\x12\x15\n\x11Session_Response1\x10\x03b\x06proto3'),
    ('session.proto', b'\n\rsession.proto\x12\tespressif\x1a\nsec0.proto\x1a\nsec1.proto"\x94\x01\n\x0bSessionData\x12,\n\x07sec_ver\x18\x02 \x01(\x0e2\x1b.espressif.SecSchemeVersion\x12&\n\x04sec0\x18\n \x01(\x0b2\x16.espressif.Sec0PayloadH\x00\x12&\n\x04sec1\x18\x0b \x01(\x0b2\x16.espressif.Sec1PayloadH\x00B\x07\n\x05proto*2\n\x10SecSchemeVersion\x12\x0e\n\nSecScheme0\x10\x00\x12\x0e\n\nSecScheme1\x10\x01b\x06proto3'),
    ('wifi_config.proto', b'\n\x11wifi_config.proto\x12\tespressif\x1a\x0fconstants.proto\x1a\x14wifi_constants.proto"\x0e\n\x0cCmdGetStatus"\xda\x01\n\rRespGetStatus\x12!\n\x06status\x18\x01 \x01(\x0e2\x11.espressif.Status\x12.\n\tsta_state\x18\x02 \x01(\x0e2\x1b.espressif.WifiStationState\x129\n\x0bfail_reason\x18\n \x01(\x0e2".espressif.WifiConnectFailedReasonH\x00\x122\n\tconnected\x18\x0b \x01(\x0b2\x1d.espressif.WifiConnectedStateH\x00B\x07\n\x05state"P\n\x0cCmdSetConfig\x12\x0c\n\x04ssid\x18\x01 \x01(\x0c\x12\x12\n\npassphrase\x18\x02 \x01(\x0c\x12\r\n\x05bssid\x18\x03 \x01(\x0c\x12\x0f\n\x07channel\x18\x04 \x01(\x05"2\n\rRespSetConfig\x12!\n\x06status\x18\x01 \x01(\x0e2\x11.espressif.Status"\x10\n\x0eCmdApplyConfig"4\n\x0fRespApplyConfig\x12!\n\x06status\x18\x01 \x01(\x0e2\x11.espressif.Status"\x89\x03\n\x11WiFiConfigPayload\x12)\n\x03msg\x18\x01 \x01(\x0e2\x1c.espressif.WiFiConfigMsgType\x121\n\x0ecmd_get_status\x18\n \x01(\x0b2\x17.espressif.CmdGetStatusH\x00\x123\n\x0fresp_get_status\x18\x0b \x01(\x0b2\x18.espressif.RespGetStatusH\x00\x121\n\x0ecmd_set_config\x18\x0c \x01(\x0b2\x17.espressif.CmdSetConfigH\x00\x123\n\x0fresp_set_config\x18\r \x01(\x0b2\x18.espressif.RespSetConfigH\x00\x125\n\x10cmd_apply_config\x18\x0e \x01(\x0b2\x19.espressif.CmdApplyConfigH\x00\x127\n\x11resp_apply_config\x18\x0f \x01(\x0b2\x1a.espressif.RespApplyConfigH\x00B\t\n\x07payload*\x9e\x01\n\x11WiFiConfigMsgType\x12\x14\n\x10TypeCmdGetStatus\x10\x00\x12\x15\n\x11TypeRespGetStatus\x10\x01\x12\x14\n\x10TypeCmdSetConfig\x10\x02\x12\x15\n\x11TypeRespSetConfig\x10\x03\x12\x16\n\x12TypeCmdApplyConfig\x10\x04\x12\x17\n\x13TypeRespApplyConfig\x10\x05b\x06proto3'),
    ('wifi_scan.proto', b'\n\x0fwifi_scan.proto\x12\tespressif\x1a\x0fconstants.proto\x1a\x14wifi_constants.proto"\\\n\x0cCmdScanStart\x12\x10\n\x08blocking\x18\x01 \x01(\x08\x12\x0f\n\x07passive\x18\x02 \x01(\x08\x12\x16\n\x0egroup_channels\x18\x03 \x01(\r\x12\x11\n\tperiod_ms\x18\x04 \x01(\r"\x0f\n\rRespScanStart"\x0f\n\rCmdScanStatus"=\n\x0eRespScanStatus\x12\x15\n\rscan_finished\x18\x01 \x01(\x08\x12\x14\n\x0cresult_count\x18\x02 \x01(\r"3\n\rCmdScanResult\x12\x13\n\x0bstart_index\x18\x01 \x01(\r\x12\r\n\x05count\x18\x02 \x01(\r"s\n\x0eWiFiScanResult\x12\x0c\n\x04ssid\x18\x01 \x01(\x0c\x12\x0f\n\x07channel\x18\x02 \x01(\r\x12\x0c\n\x04rssi\x18\x03 \x01(\x05\x12\r\n\x05bssid\x18\x04 \x01(\x0c\x12%\n\x04auth\x18\x05 \x01(\x0e2\x17.espressif.WifiAuthMode"<\n\x0eRespScanResult\x12*\n\x07entries\x18\x01 \x03(\x0b2\x19.espressif.WiFiScanResult"\xa8\x03\n\x0fWiFiScanPayload\x12\'\n\x03msg\x18\x01 \x01(\x0e2\x1a.espressif.WiFiScanMsgType\x12!\n\x06status\x18\x02 \x01(\x0e2\x11.espressif.Status\x121\n\x0ecmd_scan_start\x18\n \x01(\x0b2\x17.espressif.CmdScanStartH\x00\x123\n\x0fresp_scan_start\x18\x0b \x01(\x0b2\x18.espressif.RespScanStartH\x00\x123\n\x0fcmd_scan_status\x18\x0c \x01(\x0b2\x18.espressif.CmdScanStatusH\x00\x125\n\x10resp_scan_status\x18\r \x01(\x0b2\x19.espressif.RespScanStatusH\x00\x123\n\x0fcmd_scan_result\x18\x0e \x01(\x0b2\x18.espressif.CmdScanResultH\x00\x125\n\x10resp_scan_result\x18\x0f \x01(\x0b2\x19.espressif.RespScanResultH\x00B\t\n\x07payload*\x9c\x01\n\x0fWiFiScanMsgType\x12\x14\n\x10TypeCmdScanStart\x10\x00\x12\x15\n\x11TypeRespScanStart\x10\x01\x12\x15\n\x11TypeCmdScanStatus\x10\x02\x12\x16\n\x12TypeRespScanStatus\x10\x03\x12\x15\n\x11TypeCmdScanResult\x10\x04\x12\x16\n\x12TypeRespScanResult\x10\x05b\x06proto3'),
    ('custom_commands.proto', b'\n\x15custom_commands.proto\x12\x06custom"\n\n\x08StartCmd"+\n\tStartResp\x12\x1e\n\x06status\x18\x01 \x01(\x0e2\x0e.custom.Status"\x95\x01\n\tConfigCmd\x12\x11\n\x07api_url\x18\x01 \x01(\tH\x00\x12\x12\n\x08auth_url\x18\x02 \x01(\tH\x00\x12\x14\n\nbroker_url\x18\x03 \x01(\tH\x00\x12\x10\n\x06region\x18\x04 \x01(\tH\x00\x12\x15\n\x0brandom_text\x18\x05 \x01(\tH\x00\x12\x17\n\rsecure_random\x18\x06 \x01(\tH\x00B\t\n\x07payload",\n\nConfigResp\x12\x1e\n\x06status\x18\x01 \x01(\x0e2\x0e.custom.Status".\n\x08EventCmd\x12"\n\x03cmd\x18\x01 \x01(\x0e2\x15.custom.EventCommands"3\n\tEventResp\x12\x0c\n\x04json\x18\x01 \x01(\t\x12\x18\n\x10number_of_events\x18\x02 \x01(\x05"\x0c\n\nAddressCmd""\n\x0bAddressResp\x12\x13\n\x0bmac_address\x18\x01 \x01(\t"\t\n\x07StopCmd"*\n\x08StopResp\x12\x1e\n\x06status\x18\x01 \x01(\x0e2\x0e.custom.Status"C\n\nFactoryCmd\x12\x0b\n\x03url\x18\x01 \x01(\t\x12\x0c\n\x04ssid\x18\x02 \x01(\t\x12\x0b\n\x03pwd\x18\x03 \x01(\t\x12\r\n\x05token\x18\x04 \x01(\t"-\n\x0bFactoryResp\x12\x1e\n\x06status\x18\x01 \x01(\x0e2\x0e.custom.Status"\x1d\n\rFilterReadCmd\x12\x0c\n\x04type\x18\x01 \x01(\t" \n\x0eFilterReadResp\x12\x0e\n\x06filter\x18\x01 \x01(\r"\x1e\n\x0cFilterSetCmd\x12\x0e\n\x06filter\x18\x01 \x01(\t"/\n\rFilterSetResp\x12\x1e\n\x06status\x18\x01 \x01(\x0e2\x0e.custom.Status"\xd3\x05\n\x0eCommandWrapper\x12%\n\tstart_cmd\x18\x01 \x01(\x0b2\x10.custom.StartCmdH\x00\x12\'\n\nstart_resp\x18\x02 \x01(\x0b2\x11.custom.StartRespH\x00\x12\'\n\nconfig_cmd\x18\x03 \x01(\x0b2\x11.custom.ConfigCmdH\x00\x12)\n\x0bconfig_resp\x18\x04 \x01(\x0b2\x12.custom.ConfigRespH\x00\x12%\n\tevent_cmd\x18\x05 \x01(\x0b2\x10.custom.EventCmdH\x00\x12\'\n\nevent_resp\x18\x06 \x01(\x0b2\x11.custom.EventRespH\x00\x12)\n\x0baddress_cmd\x18\x07 \x01(\x0b2\x12.custom.AddressCmdH\x00\x12+\n\x0caddress_resp\x18\x08 \x01(\x0b2\x13.custom.AddressRespH\x00\x12#\n\x08stop_cmd\x18\t \x01(\x0b2\x0f.custom.StopCmdH\x00\x12%\n\tstop_resp\x18\n \x01(\x0b2\x10.custom.StopRespH\x00\x12)\n\x0bfactory_cmd\x18\x0b \x01(\x0b2\x12.custom.FactoryCmdH\x00\x12+\n\x0cfactory_resp\x18\x0c \x01(\x0b2\x13.custom.FactoryRespH\x00\x120\n\x0ffilter_read_cmd\x18\r \x01(\x0b2\x15.custom.FilterReadCmdH\x00\x122\n\x10filter_read_resp\x18\x0e \x01(\x0b2\x16.custom.FilterReadRespH\x00\x12.\n\x0efilter_set_cmd\x18\x0f \x01(\x0b2\x14.custom.FilterSetCmdH\x00\x120\n\x0ffilter_set_resp\x18\x10 \x01(\x0b2\x15.custom.FilterSetRespH\x00B\t\n\x07payload*\x1f\n\x06Status\x12\x0b\n\x07Success\x10\x00\x12\x08\n\x04Fail\x10\x01*0\n\rEventCommands\x12\x0c\n\x08EventGet\x10\x00\x12\x11\n\rEventClearAll\x10\x01b\x06proto3'),
)

_PROTO_POOL = descriptor_pool.DescriptorPool()

try:
    from google.protobuf.message_factory import GetMessageClass as _get_message_class
except ImportError:  # protobuf < 4.22
    from google.protobuf.message_factory import MessageFactory as _MessageFactory

    _get_message_class = _MessageFactory(pool=_PROTO_POOL).GetPrototype
try:
    from google.protobuf.internal.enum_type_wrapper import EnumTypeWrapper as _EnumTypeWrapper
except ImportError:  # pragma: no cover - very old protobuf

    class _EnumTypeWrapper:
        def __init__(self, enum_type):
            self.DESCRIPTOR = enum_type
            self._names = {v.number: v.name for v in enum_type.values}
            self._values = {v.name: v.number for v in enum_type.values}

        def Name(self, number):
            try:
                return self._names[number]
            except KeyError:
                raise ValueError(f"Enum {self.DESCRIPTOR.name} has no name defined for value {number}") from None

        def Value(self, name):
            try:
                return self._values[name]
            except KeyError:
                raise ValueError(f"Enum {self.DESCRIPTOR.name} has no value defined for name {name}") from None

        def keys(self):
            return list(self._values)

        def values(self):
            return list(self._values.values())

        def items(self):
            return list(self._values.items())


class _ProtoModule(types.SimpleNamespace):
    """Stand-in for one generated *_pb2 module (DESCRIPTOR, message classes, enum wrappers, enum value constants)."""


def _load_proto(name: str, serialized: bytes) -> _ProtoModule:
    _PROTO_POOL.AddSerializedFile(serialized)
    fd = _PROTO_POOL.FindFileByName(name)
    ns = _ProtoModule(DESCRIPTOR=fd)
    for enum_desc in fd.enum_types_by_name.values():
        setattr(ns, enum_desc.name, _EnumTypeWrapper(enum_desc))
        for value in enum_desc.values:  # module-level constants, as protoc's builder emits them (e.g. wifi_constants_pb2.WPA2_PSK)
            setattr(ns, value.name, value.number)
    for msg_desc in fd.message_types_by_name.values():
        setattr(ns, msg_desc.name, _get_message_class(msg_desc))
    return ns


_protos = {name: _load_proto(name, blob) for name, blob in _PROTO_FILES}
constants_pb2 = _protos["constants.proto"]              # espressif.Status
wifi_constants_pb2 = _protos["wifi_constants.proto"]    # WifiStationState / WifiConnectFailedReason / WifiAuthMode / WifiConnectedState
sec0_pb2 = _protos["sec0.proto"]                        # Security0 handshake
sec1_pb2 = _protos["sec1.proto"]                        # Security1 handshake (SessionCmd0/Resp0/Cmd1/Resp1)
session_pb2 = _protos["session.proto"]                  # SessionData envelope
wifi_config_pb2 = _protos["wifi_config.proto"]          # WiFiConfigPayload (set/apply/get-status)
wifi_scan_pb2 = _protos["wifi_scan.proto"]              # WiFiScanPayload (scan start/status/result)
custom_commands_pb2 = _protos["custom_commands.proto"]  # Blueair CommandWrapper on custom-endpoint
del _protos


# ======================================================================================================================
# SECTION: crypto — Security1 primitives (X25519 + AES-256-CTR keystream)   (formerly blueair_prov/crypto.py)
# ======================================================================================================================
# Security1 primitives, ported from service.rs step_session0/step_session1.
#
# Rust:
#     let secret_key = EphemeralSecret::random();        // x25519-dalek
#     let shared_secret = secret_key.diffie_hellman(&device_pubkey);
#     type Aes256Ctr = ctr::Ctr32BE<aes::Aes256>;
#     self.cipher = Some(Aes256Ctr::new(shared_secret.as_bytes().into(), device_random.into()));
#     ... self.cipher.apply_keystream(buf)  // ONE cipher, used for every encrypt AND decrypt
#
# Key facts reproduced here:
#   * Key = the raw 32-byte X25519 shared secret. The Rust code never mixes a
#     proof-of-possession (PoP) in, i.e. the device runs protocomm Security1 with
#     pop == NULL. `pop=` below is an OPTIONAL extension implementing the
#     standard ESP-IDF rule key = shared_secret XOR SHA256(pop); it is off by
#     default and is NOT exercised by the Rust reference.
#   * IV = device_random (16 bytes from SessionResp0).
#   * A single continuous keystream is shared by all encrypt and decrypt calls
#     in wire order (CTR is symmetric, so "decrypt" is the same XOR).
#   * Counter width: Rust uses Ctr32BE (32-bit big-endian counter in the last
#     4 IV bytes). The device (ESP-IDF/mbedtls `mbedtls_aes_crypt_ctr`) increments
#     the whole 128-bit block big-endian. The two agree unless the low 32 bits of
#     the IV wrap during the session (probability ~ session_bytes/16 / 2^32).
#     We implement CTR by hand on top of AES-ECB so both widths are available;
#     default 128 (= device), `counter_bits=32` reproduces Rust bit-for-bit.

X25519_KEY_LEN = 32
AES_BLOCK = 16


class KeystreamCipher:
    """AES-256-CTR as a stateful keystream (mirrors ctr::Ctr32BE<Aes256>::apply_keystream)."""

    def __init__(self, key: bytes, iv: bytes, counter_bits: int = 128):
        if len(key) != 32:
            raise ValueError(f"AES-256 key must be 32 bytes, got {len(key)}")
        if len(iv) != AES_BLOCK:
            raise ValueError(f"CTR IV must be 16 bytes, got {len(iv)}")
        if counter_bits not in (32, 128):
            raise ValueError("counter_bits must be 32 or 128")
        self._ecb = Cipher(algorithms.AES(key), modes.ECB()).encryptor()  # noqa: S305 (CTR built on ECB)
        self._iv = bytes(iv)
        self._counter_bits = counter_bits
        self._block_index = 0  # number of counter blocks consumed
        self._pending = b""    # unused keystream bytes from the current block
        self.bytes_processed = 0

    def _counter_block(self, n: int) -> bytes:
        if self._counter_bits == 128:
            v = (int.from_bytes(self._iv, "big") + n) % (1 << 128)
            return v.to_bytes(16, "big")
        # Ctr32BE: only the last 4 bytes increment, wrapping mod 2^32.
        prefix, ctr = self._iv[:12], int.from_bytes(self._iv[12:], "big")
        return prefix + ((ctr + n) % (1 << 32)).to_bytes(4, "big")

    def apply_keystream(self, data: bytes) -> bytes:
        """XOR `data` with the next len(data) keystream bytes (encrypt == decrypt)."""
        data = bytes(data)
        need = len(data) - len(self._pending)
        chunks = [self._pending]
        while need > 0:
            block = self._ecb.update(self._counter_block(self._block_index))
            self._block_index += 1
            chunks.append(block)
            need -= AES_BLOCK
        ks = b"".join(chunks)
        out = bytes(a ^ b for a, b in zip(data, ks, strict=False))
        self._pending = ks[len(data):]
        self.bytes_processed += len(data)
        return out


class NullCipher:
    """Security0: plaintext. Same interface as KeystreamCipher."""

    bytes_processed = 0

    def apply_keystream(self, data: bytes) -> bytes:
        return bytes(data)


def pop_mix(shared_secret: bytes, pop: str | bytes | None) -> bytes:
    """ESP-IDF security1: key = shared_secret XOR SHA256(pop) when a PoP is set.
    Returns shared_secret unchanged for pop=None/"" (the Rust behaviour)."""
    if not pop:
        return bytes(shared_secret)
    if isinstance(pop, str):
        pop = pop.encode("utf-8")
    digest = hashlib.sha256(pop).digest()
    return bytes(a ^ b for a, b in zip(shared_secret, digest, strict=False))


@dataclass
class ClientKeys:
    private: X25519PrivateKey
    public_bytes: bytes

    @classmethod
    def generate(cls) -> "ClientKeys":
        priv = X25519PrivateKey.generate()
        pub = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return cls(priv, pub)

    @classmethod
    def from_private_bytes(cls, raw: bytes) -> "ClientKeys":
        priv = X25519PrivateKey.from_private_bytes(raw)
        pub = priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return cls(priv, pub)

    def shared_secret(self, device_pubkey: bytes) -> bytes:
        if len(device_pubkey) != X25519_KEY_LEN:
            raise ValueError(f"device_pubkey must be 32 bytes, got {len(device_pubkey)}")
        return self.private.exchange(X25519PublicKey.from_public_bytes(device_pubkey))


def derive_session_cipher(
    keys: ClientKeys,
    device_pubkey: bytes,
    device_random: bytes,
    pop: str | bytes | None = None,
    counter_bits: int = 128,
) -> KeystreamCipher:
    """step_session0 tail: Aes256Ctr::new(shared_secret, device_random)."""
    key = pop_mix(keys.shared_secret(device_pubkey), pop)
    return KeystreamCipher(key, device_random, counter_bits=counter_bits)


# ======================================================================================================================
# SECTION: protocol — transport-agnostic protocomm session   (formerly blueair_prov/protocol.py)
# ======================================================================================================================
# Transport-agnostic port of service.rs (Service impl).
#
# Every request/response pair is:  write(endpoint, bytes) WITH response, then
# read(endpoint).  prov-session and proto-ver are plaintext; prov-scan,
# prov-config and custom-endpoint go through the session cipher (whole message).
#
# The Transport protocol lets the same code run over bleak (ble.py) or an
# in-memory fake device (tests/).

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
            log_proto.info("[%s] -> plaintext (%d B): %s", endpoint, len(request), _hex(request))
        log_proto.info("[%s] -> write   (%d B): %s", endpoint, len(wire), _hex(wire))
        await self.transport.write(endpoint, wire)
        raw = bytes(await self.transport.read(endpoint))
        log_proto.info("[%s] <- read    (%d B): %s", endpoint, len(raw), _hex(raw))
        if not raw:
            raise SessionDesync(f"{endpoint}: empty read after write")
        if encrypted:
            plain = self.cipher.apply_keystream(raw)  # type: ignore[union-attr]
            log_proto.info("[%s] <- plaintext (%d B): %s", endpoint, len(plain), _hex(plain))
            return plain
        return raw

    @staticmethod
    def _parse(msg_cls: type[Message], data: bytes, what: str) -> Message:
        try:
            m = msg_cls.FromString(data)
        except DecodeError as e:
            raise SessionDesync(f"{what}: undecodable response ({e}); raw={data.hex()}") from e
        log_proto.debug("%s decoded: %s", what, str(m).replace("\n", " ").strip() or "<empty message>")
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
        raw = await self._exchange("proto-ver", PROTO_VER_REQUEST, encrypted=False)
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
        log_proto.info("Security0 session established (plaintext)")

    async def _step_session0(self) -> None:
        keys = self._keys or ClientKeys.generate()
        self._keys = keys

        req = session_pb2.SessionData(sec_ver=session_pb2.SecScheme1)
        req.sec1.msg = sec1_pb2.Session_Command0
        req.sec1.sc0.client_pubkey = keys.public_bytes
        log_proto.debug("client_pubkey: %s", keys.public_bytes.hex())

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
        log_proto.debug("device_pubkey: %s", device_pubkey.hex())
        log_proto.debug("device_random: %s", device_random.hex())

        self.cipher = derive_session_cipher(
            keys, device_pubkey, device_random, pop=self.pop, counter_bits=self.counter_bits
        )
        self.client_pubkey = keys.public_bytes
        self.device_pubkey = device_pubkey
        self.device_random = device_random
        log_proto.info(
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
        log_proto.info("SessionCmd1 OK; device verified, secure session established")

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
        log_proto.info("StartCmd OK")

    async def stop(self) -> None:
        """stop(): StopCmd{} -> StopResp{status}. (Not called by main.rs.)"""
        w = custom_commands_pb2.CommandWrapper()
        w.stop_cmd.SetInParent()
        resp = await self._custom(w, "stop_resp")
        self._check_status(resp.status, "Stop response", custom_commands_pb2.Status)
        log_proto.info("StopCmd OK")

    async def set_configuration(self, config: dict[str, str]) -> None:
        """set_configuration(): one ConfigCmd per field, Rust order, each must Succeed.
        Rust always sends all six (missing -> empty string). We send only the
        keys present in `config`, in Rust order; the CLI decides completeness."""
        unknown = set(config) - set(CONFIG_FIELD_ORDER)
        if unknown:
            raise ValueError(f"unknown configuration fields: {sorted(unknown)}")
        for name in CONFIG_FIELD_ORDER:
            if name not in config:
                continue
            w = custom_commands_pb2.CommandWrapper()
            setattr(w.config_cmd, name, config[name])
            log_proto.info("ConfigCmd %s = %r", name, config[name])
            resp = await self._custom(w, "config_resp")
            self._check_status(resp.status, f"Config response ({name})", custom_commands_pb2.Status)
        self.is_configured = True
        log_proto.info("set_configuration OK (%d field(s))", len(config))

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
                log_proto.warning("get_all_event: stopping at safety cap %d", max_events)
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
        blocking: bool = SCAN_START_BLOCKING,
        passive: bool = SCAN_START_PASSIVE,
        group_channels: int = SCAN_START_GROUP_CHANNELS,
        period_ms: int = SCAN_START_PERIOD_MS,
        max_status_polls: int = SCAN_STATUS_MAX_POLLS,
        poll_interval: float = DEFAULT_SCAN_POLL_INTERVAL_S,
        page_size: int = WIFI_PACKET_COUNT,
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
            log_proto.warning("CmdScanStart: unexpected payload %r (Rust would ignore this)", resp.WhichOneof("payload"))
        if resp.status != 0:
            log_proto.warning("CmdScanStart: status %s (Rust ignores it)", _enum_name(constants_pb2.Status, resp.status))

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
            log_proto.info("scan status poll %d: finished=%s result_count=%d", i + 1, scan_finished, result_count)
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
        log_proto.info("CmdSetConfig OK")

    async def wifi_apply_config(self) -> None:
        p = wifi_config_pb2.WiFiConfigPayload(msg=wifi_config_pb2.TypeCmdApplyConfig)
        p.cmd_apply_config.SetInParent()
        resp = await self._config_exchange(p, "resp_apply_config")
        self._check_status(resp.status, "WiFi cmd apply response")
        log_proto.info("CmdApplyConfig OK")

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
        timeout: float = WIFI_STATUS_TIMEOUT_S,
        poll_interval: float = DEFAULT_STATUS_POLL_INTERVAL_S,
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
            log_proto.warning("wifi_connect without set_configuration (Rust reference always configured first)")

        await self.wifi_set_config(ssid, passphrase)
        await self.wifi_apply_config()

        start = time.monotonic()
        polls = 0
        while True:
            if polls > 0 and poll_interval > 0:
                await asyncio.sleep(poll_interval)
            polls += 1
            st = await self.wifi_get_status()
            log_proto.info(
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


# ======================================================================================================================
# SECTION: ble — bleak/CoreBluetooth discovery, connect and GATT transport   (formerly blueair_prov/ble.py)
# ======================================================================================================================
# BLE plumbing on top of bleak 3.x / CoreBluetooth: discovery, robust connect,
# GATT enumeration and the Transport used by protocol.Session.
#
# Port of discovery.rs (service-UUID scan filter) and the connect/characteristic
# part of service.rs. macOS notes:
#   * BLEDevice.address is a CoreBluetooth UUID, not a MAC.
#   * A BLEDevice from a *fresh* scan is the most reliable thing to hand to
#     BleakClient; connecting by address string makes bleak re-scan internally.
#   * `BleakClient.connect()` has been timing out on this Mac, so connect() below
#     retries with backoff, can connect straight from the scanner's detection
#     callback (--connect-on-detect), and has an outer watchdog.

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
    return SERVICE_UUID in [u.lower() for u in adv.service_uuids]


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
                log_ble.info("detected Blueair candidate: %s", describe(device, adv))
        else:
            s.adv, s.last_seen, s.count = adv, time.monotonic(), s.count + 1
        c = is_connectable(adv)
        if c is not None:
            s.connectable_values.add(c)

    scanner = BleakScanner(cb, service_uuids=[SERVICE_UUID] if service_filter else None)
    log_ble.info("scanning for %.1fs (CoreBluetooth service filter %s)", duration, "ON" if service_filter else "OFF")
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
                log_ble.warning(
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
    scanner = BleakScanner(m, service_uuids=[SERVICE_UUID] if opts.service_filter else None)
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
    log_ble.info("connecting to %s (timeout %.0fs, adv connectable=%s)...", device.address, opts.connect_timeout, is_connectable_str(device))
    # Outer watchdog: bleak's CoreBluetooth timeout path awaits a disconnect
    # future with no bound of its own; do not let a hung attempt stall the loop.
    await asyncio.wait_for(client.connect(timeout=opts.connect_timeout), opts.connect_timeout + opts.watchdog_margin)
    log_ble.info("connected in %.2fs; mtu=%s", time.monotonic() - t0, _safe_mtu(client))
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
    scanner = BleakScanner(m, service_uuids=[SERVICE_UUID] if opts.service_filter else None)
    await scanner.start()
    stopped = False
    try:
        try:
            device, adv = await asyncio.wait_for(m.fut, opts.scan_timeout)
        except asyncio.TimeoutError:
            raise m.timeout_error() from None
        log_ble.info("detected: %s", describe(device, adv))
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
    on_disconnect = on_disconnect or (lambda c: log_ble.warning("device disconnected: %s", c.address))
    attempts = 1 + max(0, opts.retries)
    delay = opts.backoff
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            log_ble.info("connect attempt %d/%d (%s)", attempt, attempts, "connect-on-detect" if opts.connect_on_detect else "scan-then-connect")
            if opts.connect_on_detect:
                return await _connect_on_detect(opts, on_disconnect)
            device, adv = await find_device(opts)
            log_ble.info("found: %s", describe(device, adv))
            return await _connect_device(device, opts, on_disconnect)
        except (asyncio.TimeoutError, BleakError, OSError) as e:
            last = e
            kind = "timeout" if isinstance(e, asyncio.TimeoutError) else type(e).__name__
            log_ble.warning("connect attempt %d failed: %s: %s", attempt, kind, e or "(no message)")
            if attempt < attempts:
                log_ble.info("backing off %.1fs before retry", delay)
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
                        if d.uuid.lower() == USER_DESCRIPTION_UUID:
                            user_desc = val
                    except Exception as e:  # noqa: BLE001
                        val = f"<read failed: {e}>"
                descs.append((d.uuid, d.handle, val))
            chars.append(
                GattChar(
                    name=UUID_TO_ENDPOINT.get(ch.uuid.lower()),
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
            name = UUID_TO_ENDPOINT.get(ch.uuid.lower())
            if name:
                found[name] = ch
    missing = [n for n in ENDPOINT_NAMES if n not in found]
    if missing:
        raise BleakError(f"Blueair endpoints not found on device: {missing}; services={[s.uuid for s in client.services]}")
    return found


async def verify_endpoint_names(client: BleakClient, chars: dict[str, BleakGATTCharacteristic]) -> None:
    """Cross-check the user-description descriptors against the hard-coded map
    (what Rust relies on exclusively). Logs mismatches; never fatal."""
    for name, ch in chars.items():
        for d in ch.descriptors:
            if d.uuid.lower() != USER_DESCRIPTION_UUID:
                continue
            try:
                val = bytes(await client.read_gatt_descriptor(d)).decode("utf-8", errors="replace")
            except Exception as e:  # noqa: BLE001
                log_ble.warning("could not read user description of %s: %s", name, e)
                continue
            if val != name:
                log_ble.warning("descriptor name %r != expected %r for %s", val, name, ch.uuid)
            else:
                log_ble.debug("descriptor confirms %s = %s", ch.uuid, name)


class BleakTransport:
    """service.rs write_characteristic (WriteType::WithResponse) / read_characteristic."""

    def __init__(self, client: BleakClient, chars: dict[str, BleakGATTCharacteristic]):
        self.client = client
        self.chars = chars

    async def write(self, endpoint: str, data: bytes) -> None:
        await self.client.write_gatt_char(self.chars[endpoint], data, response=True)

    async def read(self, endpoint: str) -> bytes:
        return bytes(await self.client.read_gatt_char(self.chars[endpoint]))


# ======================================================================================================================
# SECTION: cloud — BlueCloud onboarding calls and device-event classifier   (formerly blueair_prov/cloud.py)
# ======================================================================================================================
# Blueair cloud side of onboarding (NOT in the Rust reference; from the
# decompiled Blueair Android app).
#
# Auth chain (Gigya -> accounts.getJWT -> POST {api_url}/c/login) is reused from
# dahlb/blueair_api (HttpAwsBlueair). This module adds the three onboarding
# calls the app makes around the BLE ConfigCmd writes:
#
#   1. POST {api_url}/c/register-for-onboarding  {"secure-random", "random-text"}   (BEFORE ConfigCmd)
#   2. POST {api_url}/c/device-status {"deviceId": uuid} -> {"online": bool}          (after DeviceBound)
#   3. GET  {api_url}/c/registered-devices -> {"devices": [{uuid, mac, name, userId}]}

_ALNUM = string.ascii_letters + string.digits


def generate_random_text(length: int = RANDOM_TEXT_LEN) -> str:
    """128 random [A-Za-z0-9] characters (app: random_text)."""
    return "".join(secrets.choice(_ALNUM) for _ in range(length))


def android_base64_default(data: bytes) -> str:
    """Android `Base64.encodeToString(data, Base64.DEFAULT)`: standard alphabet
    with padding, a "\\n" after every 76 output chars and a trailing "\\n"."""
    b64 = base64.b64encode(data).decode("ascii")
    lines = [b64[i : i + 76] for i in range(0, len(b64), 76)]
    return "".join(line + "\n" for line in lines)


def generate_secure_random(nbytes: int = SECURE_RANDOM_BYTES) -> str:
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
        log_cloud.warning("unknown device event state et=%r", et)
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

        if cloud_region not in BLUEAIR_CLOUD_REGIONS:
            raise ValueError(f"unknown cloud region {cloud_region!r}")
        self.cloud_region = cloud_region
        self.api_url = BLUEAIR_CLOUD_REGIONS[cloud_region]["api_url"]
        self._api = HttpAwsBlueair(
            username, password, gigya_region=gigya_region or cloud_region, cloud_region=cloud_region
        )
        self.access_token: str | None = None
        self.user_id: str | None = None

    async def login(self) -> str:
        """Gigya login -> getJWT -> /c/login. Returns the BlueCloud access token."""
        self.access_token = await self._api.get_access_token()
        self.user_id = self._api.user_id
        log_cloud.info("cloud login OK (region=%s, userId=%s)", self.cloud_region, self.user_id)
        return self.access_token

    async def close(self) -> None:
        await self._api.cleanup_client_session()

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise CloudError("not logged in")
        return {"Authorization": f"Bearer {self.access_token}", "X-Source": CLOUD_HEADERS_SOURCE}

    async def _request(self, method: str, path: str, json_body: dict | None = None) -> tuple[int, Any, str]:
        url = f"{self.api_url}{path}"
        redacted = {k: ("<%d chars>" % len(v) if isinstance(v, str) and len(v) > 40 else v) for k, v in (json_body or {}).items()}
        log_cloud.info("cloud %s %s %s", method, url, json.dumps(redacted) if json_body else "")
        async with self._api.api_session.request(method, url, json=json_body, headers=self._headers()) as resp:
            text = await resp.text()
            try:
                body = json.loads(text) if text else None
            except json.JSONDecodeError:
                body = None
            log_cloud.info("cloud <- %d %s", resp.status, text[:500])
            return resp.status, body, text

    async def register_for_onboarding(self, random_text: str, secure_random: str) -> None:
        """POST /c/register-for-onboarding; response ignored; 2 retries, 3 s apart."""
        body = {"secure-random": secure_random, "random-text": random_text}
        last: Exception | None = None
        for attempt in range(1 + CLOUD_REGISTER_RETRIES):
            try:
                status, _, text = await self._request("POST", "/c/register-for-onboarding", body)
                if 200 <= status < 300:
                    log_cloud.info("register-for-onboarding OK (attempt %d)", attempt + 1)
                    return
                last = CloudError(f"register-for-onboarding HTTP {status}: {text[:200]}")
            except Exception as e:  # noqa: BLE001
                last = e
            log_cloud.warning("register-for-onboarding attempt %d failed: %s", attempt + 1, last)
            if attempt < CLOUD_REGISTER_RETRIES:
                await asyncio.sleep(CLOUD_REGISTER_RETRY_DELAY_S)
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
        log_cloud.info("waiting %.0fs before polling device-status", CLOUD_STATUS_INITIAL_WAIT_S)
        await asyncio.sleep(CLOUD_STATUS_INITIAL_WAIT_S)
        for i in range(CLOUD_STATUS_POLLS):
            try:
                st = await self.device_status(device_uuid)
                if st.get("online") is True:
                    return True
                log_cloud.info("device-status poll %d: %s", i + 1, st)
            except CloudError as e:
                log_cloud.warning("device-status poll %d failed: %s", i + 1, e)
            if i < CLOUD_STATUS_POLLS - 1:
                await asyncio.sleep(CLOUD_STATUS_POLL_INTERVAL_S)
        return False


# ======================================================================================================================
# SECTION: provisioning CLI — `blueair prov scan|info|wifi-scan|events|provision`   (formerly blueair_prov/cli.py)
# ======================================================================================================================
# Command-line front end: scan / info / wifi-scan / events / provision.

ENV_SSID = "BLUEAIR_WIFI_SSID"
ENV_PASS = "BLUEAIR_WIFI_PASS"
ENV_USER = "BLUEAIR_USERNAME"
ENV_PWD = "BLUEAIR_PASSWORD"


# ---------------------------------------------------------------------------- args


PROV_DESCRIPTION = "Blueair Blue Pure 511i Max BLE provisioning (Python port of blueairble-rs)."


def build_prov_parser(p: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    """Add the provisioning CLI to `p` (the `blueair prov` subparser); with p=None build it standalone (prov_main, tests).
    The former global connection/session knobs are options of `prov` itself: `blueair prov --connect-on-detect ... wifi-scan`."""
    if p is None:
        p = argparse.ArgumentParser(prog="blueair prov", description=PROV_DESCRIPTION)
    p.add_argument("-v", "--verbose", action="count", default=0, help="-v: hex dumps of every write/read; -vv: protobuf decode + bleak debug")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    d = p.add_argument_group("discovery / connection")
    d.add_argument("--name", help="exact advertised name (e.g. 112637_AABBCCDDEEFF)")
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

    sub = p.add_subparsers(dest="prov_cmd", required=True)

    sc = sub.add_parser("scan", help="list Blueair devices (advertisements only, no connection)")
    sc.add_argument("--duration", type=float, default=5.0, help="seconds (Rust used 3)")
    sc.add_argument("--all", action="store_true", help="list every BLE device seen, not only Blueair matches")
    sc.add_argument("--raw", action="store_true", help="also dump the raw CoreBluetooth advertisement dictionary (connectable flag, PHY, ...)")

    inf = sub.add_parser("info", help="connect, enumerate GATT, write 'ESP' to proto-ver and read it back")
    inf.add_argument("--no-proto-ver", action="store_true", help="skip the proto-ver write/read (pure GATT dump)")

    ws = sub.add_parser("wifi-scan", help="secure session + WiFi scan via prov-scan (no config written)")
    ws.add_argument("--start", action="store_true", help="also send StartCmd on custom-endpoint first (Rust connect() does; a WRITE to custom-endpoint)")
    ws.add_argument("--scan-poll-interval", type=float, default=DEFAULT_SCAN_POLL_INTERVAL_S, help="sleep between CmdScanStatus polls (Rust: 0)")
    ws.add_argument("--scan-poll-max", type=int, default=SCAN_STATUS_MAX_POLLS)
    ws.add_argument("--period-ms", type=int, default=SCAN_START_PERIOD_MS)
    ws.add_argument("--passive", action="store_true")
    ws.add_argument("--group-channels", type=int, default=SCAN_START_GROUP_CHANNELS)
    ws.add_argument("--json", action="store_true")

    ev = sub.add_parser("events", help="secure session + EventGet loop on custom-endpoint")
    ev.add_argument("--start", action="store_true", help="send StartCmd before reading events")
    ev.add_argument("--max-events", type=int, default=50)
    ev.add_argument("--watch", type=float, default=0.0, help="keep polling for this many seconds (0 = one get_all_event)")
    ev.add_argument("--event-poll-interval", type=float, default=EVENT_POLL_INTERVAL_S)
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
    cfg.add_argument("--cloud-region", choices=sorted(BLUEAIR_CLOUD_REGIONS), help="fill api_url/auth_url/broker_url/region from the verified production table and generate random_text/secure_random")
    for f in CONFIG_FIELD_ORDER:
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
    fl.add_argument("--wifi-timeout", type=float, default=WIFI_STATUS_TIMEOUT_S, help="rust mode: CmdGetStatus poll budget (default 10 s)")
    fl.add_argument("--status-poll-interval", type=float, default=DEFAULT_STATUS_POLL_INTERVAL_S, help="rust mode: sleep between CmdGetStatus polls (Rust: 0)")
    fl.add_argument("--event-poll-interval", type=float, default=EVENT_POLL_INTERVAL_S)
    fl.add_argument("--event-timeout", type=float, default=EVENT_POLL_TIMEOUT_S)
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


def connect_options(args: argparse.Namespace) -> ConnectOptions:
    return ConnectOptions(
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
    client = await connect(connect_options(args))
    out(f"connected: {client.address} name={_client_name(client)!r} mtu={_safe_mtu(client)}")
    return client


def _client_name(client: BleakClient) -> str | None:
    try:
        return client.name
    except Exception:  # noqa: BLE001
        return None


async def open_session(args: argparse.Namespace, client: BleakClient) -> Session:
    """map endpoints -> proto-ver -> SessionCmd0/Cmd1 (Rust connect() minus StartCmd)."""
    chars = map_endpoints(client)
    if args.verify_descriptors:
        await verify_endpoint_names(client, chars)
    session = Session(BleakTransport(client, chars), sec=args.sec, pop=args.pop, counter_bits=args.ctr_bits)
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
    seen = await scan(args.duration, service_filter=not args.no_service_filter, opts=opts)
    rows = []
    for s in seen.values():
        match = is_blueair(s.device, s.adv, opts)
        if match or args.all:
            rows.append((match, s))
    rows.sort(key=lambda t: (not t[0], -(t[1].adv.rssi or -999)))
    if not rows:
        out(f"no {'devices' if args.all else 'Blueair devices'} seen in {args.duration:.0f}s "
            f"(service filter {'OFF' if args.no_service_filter else 'ON'}; try --no-service-filter, --all, or move closer)")
        return 1
    for match, s in rows:
        cv = ",".join(str(v) for v in sorted(s.connectable_values)) or "?"
        out(("[BLUEAIR] " if match else "          ") + describe(s.device, s.adv) + f"  adv_count={s.count}  connectable_values_seen={{{cv}}}")
        if args.raw and (match or args.all):
            out("           raw: " + raw_advertisement(s.adv))
    return 0


async def cmd_info(args: argparse.Namespace) -> int:
    client = None
    try:
        client = await open_client(args)
        gatt = await enumerate_gatt(client, read_descriptors=True)
        for svc_uuid, chars in gatt.items():
            tag = "  <- Blueair provisioning service" if svc_uuid.lower() == SERVICE_UUID else ""
            out(f"service {svc_uuid}{tag}")
            for ch in chars:
                nm = f" ({ch.name})" if ch.name else ""
                out(f"  char {ch.uuid}{nm} handle={ch.handle} props={','.join(ch.properties)}")
                for du, dh, dv in ch.descriptors:
                    out(f"      desc {du} handle={dh} value={dv!r}")
                if ch.name and ch.user_description is not None and ch.user_description != ch.name:
                    out(f"      !! user description {ch.user_description!r} differs from expected {ch.name!r}")
        try:
            chars = map_endpoints(client)
            out(f"endpoint map OK: {', '.join(sorted(chars))}")
        except BleakError as e:
            out(f"endpoint map FAILED: {e}")
            return 2
        if not args.no_proto_ver:
            session = Session(BleakTransport(client, chars), sec=args.sec)
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
        cfg.update(BLUEAIR_CLOUD_REGIONS[args.cloud_region])
    for f in CONFIG_FIELD_ORDER:
        v = getattr(args, f)
        if v is not None:
            cfg[f] = v
    if not cfg:
        return None, {}
    if args.cloud_region or args.cloud:

        if "random_text" not in cfg:
            cfg["random_text"] = generated["random_text"] = generate_random_text()
        if "secure_random" not in cfg:
            cfg["secure_random"] = generated["secure_random"] = generate_secure_random()
    missing = [f for f in CONFIG_FIELD_ORDER if f not in cfg]
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

        cloud = BlueairCloud(user, pwd, cloud_region=region, gigya_region=args.gigya_region)

    # ---- confirmation
    out("=" * 78)
    out("PROVISION PLAN" + ("  (DRY RUN: no StartCmd / ConfigCmd / WiFi config / cloud writes)" if args.dry_run else ""))
    out(f"  target        : {args.address or args.name or (args.name_prefix + '*' if args.name_prefix else 'first device advertising ' + SERVICE_UUID)}")
    out(f"  WiFi SSID     : {ssid!r} ({len(ssid.encode())} bytes)")
    out(f"  WiFi pass     : {mask(password, args.show_password)}" + ("" if args.show_password else "   [--show-password to reveal]"))
    if config:
        out(f"  ConfigCmd x{len(config)}   :")
        for f in CONFIG_FIELD_ORDER:
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
    for key, vals in BLUEAIR_CLOUD_REGIONS.items():
        if vals["api_url"] == api_url.rstrip("/"):
            return key
    return None


# ---------------------------------------------------------------------------- entry points

PROV_HANDLERS = {
    "scan": cmd_scan,
    "info": cmd_info,
    "wifi-scan": cmd_wifi_scan,
    "events": cmd_events,
    "provision": cmd_provision,
}


async def prov_dispatch(args: argparse.Namespace) -> int:
    """Run one provisioning subcommand on the current event loop (used by `blueair prov ...`, discover/networks/setup, prov_main)."""
    setup_logging(args.verbose)
    try:
        return await PROV_HANDLERS[args.prov_cmd](args)
    except (BleakError, asyncio.TimeoutError, ProvisioningError) as e:
        out(f"ERROR: {type(e).__name__}: {e or '(timeout)'}")
        return 3


def prov_main(argv: list[str] | None = None) -> int:
    """Standalone provisioning entry point (what `python -m blueair_prov` used to be)."""
    args = build_prov_parser().parse_args(argv)
    try:
        return asyncio.run(prov_dispatch(args))
    except KeyboardInterrupt:
        out("interrupted")
        return 130


# ======================================================================================================================
# SECTION: blueair — cloud control CLI; discover/networks/setup run the provisioning code in-process   (formerly blueair_prov/../blueair.py)
# ======================================================================================================================
CFG = os.path.expanduser("~/.config/blueair/config.json")

def load_creds():
    c = {}
    if os.path.exists(CFG):
        c = json.load(open(CFG))
    u = os.environ.get("BLUEAIR_USERNAME") or c.get("username"); p = os.environ.get("BLUEAIR_PASSWORD") or c.get("password")
    r = os.environ.get("BLUEAIR_REGION") or c.get("region", "us")
    if not u or not p:
        sys.exit(f"no credentials: create {CFG} or set BLUEAIR_USERNAME/BLUEAIR_PASSWORD")
    return u, p, r

def onoff(s):
    s = s.lower()
    if s in ("on", "1", "true", "yes"): return True
    if s in ("off", "0", "false", "no"): return False
    raise argparse.ArgumentTypeError("expected on|off")

async def pick_device(api, sel):
    raw = await api.devices()
    devs = raw.get("devices") if isinstance(raw, dict) else raw
    if not devs: sys.exit("no devices registered to this account")
    if sel:
        devs = [d for d in devs if sel.lower() in (d.get("uuid", "") + " " + d.get("name", "")).lower()]
        if not devs: sys.exit(f"no device matches {sel!r}")
    d = devs[0]
    return await DeviceAws.create_device(api, d["uuid"], d.get("name"), d.get("mac"), d.get("type"), refresh=True), d

def state(dev):
    keys = ["wifi_working", "standby", "fan_speed", "fan_auto_mode", "brightness", "night_mode", "child_lock",
            "filter_usage_percentage", "pm1", "pm2_5", "pm10", "voc", "total_voc", "temperature", "humidity", "rssi", "hw"]
    return {k: getattr(dev, k, None) for k in keys if getattr(dev, k, None) is not None and getattr(dev, k, None) is not NotImplemented}

async def rename(api, uuid, name):
    url = f"https://{AWS_APIKEYS[api.cloud_region]['restApiId']}.execute-api.{AWS_APIKEYS[api.cloud_region]['awsRegion']}/prod/c/cm/update"
    headers = {"Authorization": f"Bearer {await api.get_access_token()}", "X-Source": "android", "Content-Type": "application/json"}
    body = {"uuid": uuid, "di": {"name": name}}   # app: UpdateWrapper(uuid, da=null omitted, di=NameConfiguration{name}) via PATCH c/cm/update
    resp = await api.api_session.patch(url, json=body, headers=headers)
    return resp.status, (await resp.text())[:300]


async def run_prov(argv: list[str]) -> int:
    """Run the bundled provisioning CLI in-process (formerly `python -m blueair_prov <argv>` in a subprocess)."""
    return await prov_dispatch(build_prov_parser().parse_args(argv))

async def cmd_discover(seconds):
    return await run_prov(["scan", "--duration", str(seconds), "--raw"])

async def cmd_networks(timeout):
    print("Hold the purifier's Fan Speed button for ~5 s (LEDs blink once), then release. Waiting up to %d s for it to accept a connection..." % timeout, flush=True)
    return await run_prov(["--connect-on-detect", "--scan-timeout", str(timeout), "--connect-timeout", "20", "--retries", "3", "--backoff", "5", "wifi-scan"])

async def cmd_setup(a, u, p, r):
    import getpass
    wifi_pw = a.wifi_password if a.wifi_password is not None else (None if a.open_network else getpass.getpass(f"WiFi password for {a.ssid!r}: "))
    prov = ["-v"] if a.verbose else []
    prov += ["--connect-on-detect", "--scan-timeout", str(a.timeout), "--connect-timeout", "20", "--retries", "3", "--backoff", "5",
             "provision", "--yes", "--cloud-region", r, "--gigya-region", r, f"--ssid={a.ssid}"]   # --opt=value: values may start with "-"
    if a.open_network: prov.append("--open-network")
    else: prov.append(f"--password={wifi_pw}")
    if a.no_cloud: prov.append("--no-cloud")
    else: prov += ["--cloud", f"--account-username={u}", f"--account-password={p}"]
    print("\n== Blueair pairing ==\nThe purifier only joins 2.4 GHz networks and only accepts Bluetooth connections for ~5 s after you\n"
          "hold its Fan Speed button (~5 s, until all LEDs blink once). Get within a few feet, then press it once the tool says it is waiting.\n", flush=True)
    rc = await run_prov(prov)
    if rc != 0:
        print(f"\nsetup did not complete (exit {rc}). Common causes: wrong 2.4 GHz SSID/password (run `blueair networks`), no button press within the wait, or the unit already paired (factory reset: hold On/Standby ~15 s).")
        return rc
    if a.name and not a.no_cloud:
        api = HttpAwsBlueair(username=u, password=p, region=r)
        try:
            raw = await api.devices(); devs = raw.get("devices") if isinstance(raw, dict) else raw
            # the newest device is the one whose name is still the account id placeholder
            target = next((d for d in devs if d.get("name") == d.get("uuid") or (d.get("name") or "").count("-") == 4), devs[-1])
            st, body = await rename(api, target["uuid"], a.name); print(f"renamed {target['uuid']} -> {a.name!r}: HTTP {st}")
        finally:
            await api.cleanup_client_session()
    print("\nPairing complete. `blueair status` shows the unit; the Blueair app on your phone will list it under the same account.")
    return 0

async def main():
    ap = argparse.ArgumentParser(prog="blueair", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device"); ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status"); sub.add_parser("raw"); sub.add_parser("on"); sub.add_parser("off")
    sub.add_parser("fan").add_argument("percent", type=int)
    for name in ("auto", "night", "lock"): sub.add_parser(name).add_argument("value", type=onoff)
    sub.add_parser("brightness").add_argument("percent", type=int)
    sub.add_parser("rename").add_argument("name")
    d = sub.add_parser("discover"); d.add_argument("--seconds", type=int, default=10)
    n = sub.add_parser("networks"); n.add_argument("--timeout", type=int, default=600)
    st = sub.add_parser("setup"); st.add_argument("--ssid", required=True); st.add_argument("--wifi-password"); st.add_argument("--open-network", action="store_true")
    st.add_argument("--name"); st.add_argument("--no-cloud", action="store_true"); st.add_argument("--timeout", type=int, default=900); st.add_argument("-v", "--verbose", action="store_true")
    pv = sub.add_parser("prov", description=PROV_DESCRIPTION,
                        help="low-level BLE provisioning tool: prov [connection/session options] scan|info|wifi-scan|events|provision ...")
    build_prov_parser(pv)
    a = ap.parse_args()
    if a.cmd == "discover": sys.exit(await cmd_discover(a.seconds))
    if a.cmd == "networks": sys.exit(await cmd_networks(a.timeout))
    if a.cmd == "prov": sys.exit(await prov_dispatch(a))
    u, p, r = load_creds()
    if a.cmd == "setup": sys.exit(await cmd_setup(a, u, p, r))
    api = HttpAwsBlueair(username=u, password=p, region=r)
    try:
        dev, meta = await pick_device(api, a.device)
        if a.cmd == "raw":
            print(json.dumps(await api.device_info(meta.get("name"), meta["uuid"]), indent=1)); return
        if a.cmd == "status":
            pass
        elif a.cmd == "fan":
            if not 0 <= a.percent <= 100: sys.exit("percent must be 0-100")
            await dev.set_fan_speed(a.percent)
        elif a.cmd == "on": await dev.set_standby(False)
        elif a.cmd == "off": await dev.set_standby(True)
        elif a.cmd == "auto": await dev.set_fan_auto_mode(a.value)
        elif a.cmd == "night": await dev.set_night_mode(a.value)
        elif a.cmd == "lock": await dev.set_child_lock(a.value)
        elif a.cmd == "brightness":
            if not 0 <= a.percent <= 100: sys.exit("percent must be 0-100")
            await dev.set_brightness(a.percent)
        elif a.cmd == "rename":
            st, body = await rename(api, meta["uuid"], a.name); print(f"rename -> HTTP {st} {body}")
        if a.cmd != "status":
            await asyncio.sleep(2)
        await dev.refresh()
        try:   # the user-facing name lives in device_info.configuration.di.name (set by rename); the registered-devices list keeps a placeholder
            info = await api.device_info(meta.get("name"), meta["uuid"]); di_name = ((info or {}).get("configuration") or {}).get("di", {}).get("name")
        except Exception:
            di_name = None
        result = {"device": di_name or meta.get("name"), "uuid": meta["uuid"], "type": meta.get("type"), **state(dev)}
        print(json.dumps(result, indent=1) if a.json else "\n".join(f"{k:26} {v}" for k, v in result.items()))
    finally:
        await api.cleanup_client_session()

if __name__ == "__main__":
    import signal
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)   # let `| head` end the process quietly instead of a BrokenPipeError traceback
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("interrupted", flush=True)
        sys.exit(130)
