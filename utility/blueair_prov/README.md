# blueair_prov — BLE provisioning for the Blueair Blue Pure 511i Max (macOS, Python 3.13)

Python port of the Rust tool `blueairble-rs` (`/tmp/blueairble-rs`, btleplug + protobuf-rs).
The purifier speaks Espressif **protocomm / wifi_provisioning** over BLE (Security1, *empty*
proof-of-possession) plus a fifth Blueair "custom-endpoint" characteristic. The cloud half of
onboarding (from the decompiled Android app, `/tmp/blueair_cloud_flow.md`) is optional (`--cloud`).

```
/tmp/blueair_prov/
  .venv/                       python3.13 venv: bleak 3.0.2, protobuf 7.x, grpcio-tools, cryptography, blueair-api
  build_protos.sh              copies the .proto files and compiles them into blueair_prov/protos (relative imports fixed)
  blueair_prov/
    constants.py               UUIDs, packetization constants, VERIFIED cloud region table, timing knobs
    crypto.py                  X25519 + AES-256-CTR keystream (Security1), optional PoP mixing
    protocol.py                transport-agnostic port of service.rs (Session: proto-ver, handshake, scan, config, wifi, events)
    ble.py                     bleak/CoreBluetooth: scan, robust connect (retry/backoff/connect-on-detect/connectable gating), GATT, transport
    cloud.py                   random_text/secure_random generators, device-event classifier, BlueCloud onboarding calls
    cli.py                     subcommands scan / info / wifi-scan / events / provision
    protos/                    *.proto (verbatim copies) + generated *_pb2.py/.pyi
  tests/                       60 unit tests incl. an independent in-memory fake device (tests/fake_device.py)
  README.md
```

## Setup

```bash
cd /tmp/blueair_prov
# already done: python3.13 -m venv .venv && .venv/bin/pip install bleak protobuf grpcio-tools cryptography blueair-api
./build_protos.sh                       # regenerate protos if the .proto files change
.venv/bin/python -m unittest discover -s tests -t .      # 60 tests, ~0.3 s, no hardware needed
alias bp='/tmp/blueair_prov/.venv/bin/python -m blueair_prov'
```

macOS will prompt for Bluetooth permission for the terminal app on first use.

## Commands (add `-v` for hex dumps of every write/read, `-vv` for protobuf decodes + bleak debug)

| command | what it does | writes to the device? |
|---|---|---|
| `bp scan [--duration 5] [--all] [--raw]` | passive scan; lists Blueair devices (service UUID `4772911e-…` or `--name-prefix`); `--raw` dumps CoreBluetooth's advertisement dict incl. **`kCBAdvDataIsConnectable`** | no |
| `bp info` | connect, enumerate GATT (reads the 0x2901 user-description descriptors), write `"ESP"` to **proto-ver**, read it back | proto-ver only |
| `bp wifi-scan [--start]` | proto-ver → Security1 handshake (**prov-session**) → `CmdScanStart/Status/Result` on **prov-scan**; `--start` additionally sends `StartCmd` on custom-endpoint (Rust does; off by default) | prov-session, prov-scan (+custom-endpoint with `--start`) |
| `bp events [--start] [--watch 60]` | handshake → `EventCmd{EventGet}` loop on **custom-endpoint**; `--watch` keeps polling every 2 s | prov-session, custom-endpoint |
| `bp provision …` | full flow (below). Refuses without WiFi credentials and without `--yes`. | everything |
| `bp provision --dry-run` | proto-ver + handshake + WiFi scan, then stops; **no StartCmd, no ConfigCmd, no WiFi config, no cloud writes** | prov-session, prov-scan |

Connection knobs (all commands): `--address <CoreBluetooth UUID>` / `--name` / `--name-prefix`, `--scan-timeout 15`,
`--connect-timeout 20`, `--retries 3 --backoff 5 --backoff-factor 1.5 --max-backoff 30`, `--connect-on-detect`
(connect from the scanner's detection callback using that exact CBPeripheral), `--keep-scanning`, `--no-service-filter`,
`--allow-nonconnectable`. Session knobs: `--sec {0,1}` (default 1), `--pop` (default none, like Rust/app), `--ctr-bits {32,128}`.

### provision

WiFi credentials come **only** from `BLUEAIR_WIFI_SSID` / `BLUEAIR_WIFI_PASS` (or `--ssid/--password`; `--open-network`
for an open SSID). Nothing is hard-coded. The command prints exactly what it will write and exits unless `--yes` is given.

```bash
export BLUEAIR_WIFI_SSID='MyNetwork' BLUEAIR_WIFI_PASS='…'

# 1. safest first step: handshake + WiFi scan only, confirms the target SSID is visible to the purifier
bp -v provision --dry-run

# 2. device-only (Rust main.rs shape) WITHOUT any cloud configuration -> the purifier joins WiFi but is bound to nothing
bp -v provision --yes --no-cloud

# 3. device + verified production cloud values, generated random_text/secure_random, NO cloud API calls
bp -v provision --yes --cloud-region us --no-cloud

# 4. the full official-app flow (needs a Blueair account)
export BLUEAIR_USERNAME='me@example.com' BLUEAIR_PASSWORD='…'
bp -v provision --yes --cloud --cloud-region us          # eu / cn / au also in the table
```

Step order with `--cloud`: connect → proto-ver → SessionCmd0/1 → `StartCmd` → cloud login (Gigya → getJWT → `/c/login`,
via `blueair_api.HttpAwsBlueair`) → `POST /c/register-for-onboarding {"secure-random","random-text"}` (2 retries, 3 s) →
6 × `ConfigCmd` (api_url, auth_url, broker_url, region, random_text, secure_random) → `CmdSetConfig` → `CmdApplyConfig` →
**one** `CmdGetStatus` → `EventGet` every 2 s until `et=="DeviceBound" && ec>=0` (≤120 s; `(BrokerConnecting,-5)` = keep
waiting; other `ec<0` = mapped error) → BLE disconnect → wait 10 s → `POST /c/device-status` ≤6× / 5 s → `GET /c/registered-devices`.
`--wifi-status-mode rust` instead reproduces `service.rs::wifi_connect` (poll `CmdGetStatus` for 10 s until `Connected`, then one `get_all_event`).

Configuration is **never sent unless asked**: `--cloud-region` fills the four URLs/region from the verified table and
generates the two random strings; individual `--api-url … --secure-random` flags override; `--skip-configuration` forces
none; partial sets are refused unless `--allow-partial-config` (Rust always sends all six).

## How the Rust code was mapped

| Rust (`service.rs` / `discovery.rs` / `main.rs`) | Python |
|---|---|
| `ScanFilter{services:[4772911e-…]}`, 3 s scan | `ble.scan/find_device` with `BleakScanner(service_uuids=[…])`; match on service UUID or `--name-prefix` |
| characteristics keyed by first descriptor string (`prov-scan`, …) | hard-coded UUID map `constants.ENDPOINT_UUIDS` (ff50…ff54); descriptors only read for `info`/`--verify-descriptors` (Rust panics on descriptor-less chars) |
| `write(WithResponse)` then `read()` same char | `BleakTransport.write(response=True)` + `read_gatt_char`; no notifications, no framing |
| `get_proto_ver`: write `"ESP"`, read | `Session.get_proto_ver` (JSON parsed opportunistically) |
| `step_session0`: X25519 ephemeral, `Aes256Ctr::new(shared_secret, device_random)` — **no PoP** | `crypto.derive_session_cipher(pop=None)`; `--pop` XORs SHA-256(pop) (ESP-IDF rule, not used by Rust/app) |
| one `Ctr32BE<Aes256>` reused for every `apply_keystream` (encrypt **and** decrypt) | one `KeystreamCipher` per session; `Session._exchange` runs request then response through it in wire order. Counter default 128-bit (= mbedtls on the device); `--ctr-bits 32` = Rust bit-for-bit; identical unless the IV's low word wraps |
| `step_session1`: verify = ks(device_pubkey); check ks(device_verify_data) == client_pubkey | `Session._step_session1` |
| `step_start`: `StartCmd` on custom-endpoint | `Session.start()` — sent by `provision`, optional (`--start`) for `wifi-scan`/`events` because it is a custom-endpoint write |
| `set_configuration`: 6 `ConfigCmd` round trips in fixed order, each `Success` | `Session.set_configuration` (same order) |
| `wifi_connect`: SetConfig → ApplyConfig → poll GetStatus 10 s (fail_reason && !Connecting → error; timeout; Connected) | `Session.wifi_connect` (same order of checks, `--status-poll-interval` default 1 s, Rust = 0) |
| `wifi_scan`: `CmdScanStart{blocking,!passive,0,120}`, ≤10 status polls, `WIFI_PACKET_COUNT=4` pages | `Session.wifi_scan` (`--scan-poll-interval` default 1 s, Rust = 0) |
| `get_event`/`get_all_event` (loop while `number_of_events>0`) | same, plus `--max-events` safety cap |
| `set_factory`, `stop` (never called by main.rs) | ported in `protocol.py`, not exposed on the CLI |
| proto3 zero enums absent on the wire | all response checks use `WhichOneof(...)` presence, then status |
| lost/duplicate read | `SessionDesync`; read-only flows reconnect + redo Cmd0/Cmd1 (`--session-retries`), `provision` aborts |

Rust `main.rs` pointed the device at LOCAL servers (`http://192.168.0.4:8080` …); those are **not** defaults anywhere.

## Test results (2026-09-28)

* `unittest`: **60 passed** — NIST SP 800-38A CTR-AES256 vector, RFC 7748 X25519 vector, PoP XOR, streaming vs one-shot
  keystream, 32- vs 128-bit counter equivalence/divergence, hand-derived wire bytes for SessionCmd0/StartCmd/ConfigCmd/
  CmdScanStart, proto3 oneof presence with zero enums, full handshake + StartCmd + 6×ConfigCmd + WiFi + events against an
  **independent** fake device (its own X25519 key and `cryptography` CTR encryptor — a client keystream bug would surface as
  a decode failure), scan pagination (9 results → pages 4/4/1), auth-error/timeout paths, cloud region table cross-checked
  against `blueair_api.const`, Android `Base64.DEFAULT` format (90 bytes), event classification, CLI refusal guards.
* Live `scan`: device found immediately — `79B790D6-D9EB-5C10-7B01-B84D0C404781`, name `112637_10BDA359C5E0`,
  service UUID advertised, RSSI −52…−58, ~1.4 adv/s.
* Live `info`: plain connect (20 s) **timed out**; `--connect-on-detect` with 45 s **timed out** (CoreBluetooth issued
  `connectPeripheral`, never got `didConnect`/`didFailToConnect`; cancel → `didDisconnect`).
* **Root cause (passive, `scan --raw`): `kCBAdvDataIsConnectable=0` on every one of 28 advertisements over 20 s** (legacy
  PHY, no secondary PHY). The purifier is beaconing non-connectably, so macOS parks the connect request forever. This is
  a device-state problem (not in BLE pairing mode / provisioning window closed / already connected to another central),
  not a bleak or tool problem. The tool now detects this and fails fast with that message; `--allow-nonconnectable` forces
  the old behaviour. **Nothing beyond `scan`/`info` could be exercised live; no session was established with the device.**

## What is still unknown / open

1. **Device state**: how to put the 511i Max into connectable pairing mode (likely a long-press of the WiFi/Bluetooth
   button until the LED blinks). Until `scan --raw` shows `kCBAdvDataIsConnectable=1`, nothing else can be tested.
2. **Untested live**: the Security1 handshake, WiFi scan, `StartCmd`, configuration, WiFi config, events, cloud calls.
   Everything is validated only against the fake device and the Rust/app logic. First live step should be `provision --dry-run`.
3. **Cloud values**: US/EU/CN `api_url`/`auth_url`/`broker_url`/`region` come from the decompiled app and agree with
   `blueair_api`; `au` is an assumption (EU cloud). `random_text`/`secure_random` semantics beyond "same string to cloud and
   device" are unknown. Whether the device accepts WiFi config *without* any `ConfigCmd` (Rust refused client-side) is unknown.
4. **Timings** not recoverable from the decompile: EventGet poll interval (~2 s used), whether `StartCmd` is required
   before `prov-scan` (Rust always sent it; `wifi-scan` omits it unless `--start`).
5. `proto-ver` JSON format is ESP-IDF convention, only displayed. `sec0` path is standard protocomm, not exercised by Rust.

## Safety notes

* `provision` is the **only** command that writes to prov-config or sends `ConfigCmd`; it requires `--yes` and prints
  SSID (passphrase masked unless `--show-password`) and every configuration value first.
* `--cloud` performs account login and `register-for-onboarding` against Blueair production; it binds the device to that
  account. Use `--no-cloud` (default) to keep everything local.
* Never pass the Rust example LAN URLs; a wrong `api_url`/`broker_url` leaves the device trying to reach a dead host.
* Do not spam connects: each failed `connectPeripheral` sits for the whole timeout. Check `scan --raw` first.
* Read-only flows re-handshake at most `--session-retries` (1) times on keystream desync; `provision` aborts instead of
  re-sending configuration.
