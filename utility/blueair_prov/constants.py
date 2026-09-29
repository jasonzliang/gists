"""Protocol constants. Everything here is taken from the Rust reference
(/tmp/blueairble-rs) unless explicitly marked otherwise."""

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

# Device name seen in the field: "112637_10BDA359C5E0". Used only as a
# display hint; discovery matches on SERVICE_UUID and/or --name/--name-prefix.
KNOWN_DEVICE_NAME = "112637_10BDA359C5E0"

# ---------------------------------------------------------------------------
# BLUEAIR CLOUD CONFIGURATION (ConfigCmd values written to the device)
# ---------------------------------------------------------------------------
# Source: decompiled official Android app, BlueCloudDomain.getDomainForRegion
# (prod), 2026-09-28 -- see /tmp/blueair_cloud_flow.md. Cross-checked against
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
