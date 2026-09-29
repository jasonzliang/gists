#!/usr/bin/env python3
# blueair.py — control and pair Blueair air purifiers from a Mac (no phone needed).
#
# Install:
#   python3 -m venv ~/.local/share/blueair-cli/venv
#   ~/.local/share/blueair-cli/venv/bin/pip install blueair-api aiohttp bleak protobuf cryptography
#   cp -R blueair_prov ~/.local/share/blueair-cli/          # BLE provisioning package (needed only for discover/networks/setup)
#   install -m 755 blueair.py ~/.local/bin/blueair && sed -i "" "1s|.*|#!$HOME/.local/share/blueair-cli/venv/bin/python|" ~/.local/bin/blueair
#   printf '{"username":"you@example.com","password":"...","region":"us"}' > ~/.config/blueair/config.json && chmod 600 ~/.config/blueair/config.json
#
# Notes from the 2026-09-28 Blue Pure 511i Max setup: the purifier only joins 2.4 GHz WiFi; it accepts Bluetooth connections for
# only ~5 s after holding Fan Speed ~5 s (LEDs blink once); a Google-only Blueair account must have a password added (reset email)
# before Blueair's cloud login works; rename goes through PATCH c/cm/update {"uuid","di":{"name"}}; night mode switches auto mode off.
"""blueair — control Blueair purifiers on your account through Blueair's cloud API (uses the blueair-api library).

Usage:
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
Options: --device <uuid-or-name-substring> (default: the only/first device), --json
Credentials: ~/.config/blueair/config.json {"username","password","region"} or env BLUEAIR_USERNAME / BLUEAIR_PASSWORD / BLUEAIR_REGION.
"""
import argparse, asyncio, json, os, sys
from blueair_api import HttpAwsBlueair, DeviceAws
from blueair_api.const import AWS_APIKEYS

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

PROV_DIR = next((d for d in (os.path.dirname(os.path.abspath(__file__)), os.path.expanduser("~/.local/share/blueair-cli"))
                 if os.path.isdir(os.path.join(d, "blueair_prov"))), os.path.expanduser("~/.local/share/blueair-cli"))   # dir containing the blueair_prov package

def run_prov(args, extra_env=None, timeout=None):
    """Run the bundled BLE provisioning tool as a subprocess with live output."""
    import subprocess
    env = dict(os.environ); env.update(extra_env or {})
    cmd = [sys.executable, "-m", "blueair_prov"] + args
    try:
        return subprocess.run(cmd, cwd=PROV_DIR, env=env, timeout=timeout).returncode
    except KeyboardInterrupt:
        return 130

def cmd_discover(seconds):
    return run_prov(["scan", "--duration", str(seconds), "--raw"])

def cmd_networks(timeout):
    print("Hold the purifier's Fan Speed button for ~5 s (LEDs blink once), then release. Waiting up to %d s for it to accept a connection..." % timeout, flush=True)
    return run_prov(["--connect-on-detect", "--scan-timeout", str(timeout), "--connect-timeout", "20", "--retries", "3", "--backoff", "5", "wifi-scan"])

async def cmd_setup(a, u, p, r):
    import getpass
    wifi_pw = a.wifi_password if a.wifi_password is not None else (None if a.open_network else getpass.getpass(f"WiFi password for {a.ssid!r}: "))
    env = {"BLUEAIR_WIFI_SSID": a.ssid}
    prov = ["-v"] if a.verbose else []
    prov += ["--connect-on-detect", "--scan-timeout", str(a.timeout), "--connect-timeout", "20", "--retries", "3", "--backoff", "5",
             "provision", "--yes", "--cloud-region", r, "--gigya-region", r]
    if a.open_network: prov.append("--open-network")
    else: env["BLUEAIR_WIFI_PASS"] = wifi_pw
    if a.no_cloud: prov.append("--no-cloud")
    else:
        prov.append("--cloud"); env["BLUEAIR_USERNAME"] = u; env["BLUEAIR_PASSWORD"] = p
    print("\n== Blueair pairing ==\nThe purifier only joins 2.4 GHz networks and only accepts Bluetooth connections for ~5 s after you\n"
          "hold its Fan Speed button (~5 s, until all LEDs blink once). Get within a few feet, then press it once the tool says it is waiting.\n", flush=True)
    rc = run_prov(prov, env)
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
    a = ap.parse_args()
    if a.cmd == "discover": sys.exit(cmd_discover(a.seconds))
    if a.cmd == "networks": sys.exit(cmd_networks(a.timeout))
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
        out = {"device": di_name or meta.get("name"), "uuid": meta["uuid"], "type": meta.get("type"), **state(dev)}
        print(json.dumps(out, indent=1) if a.json else "\n".join(f"{k:26} {v}" for k, v in out.items()))
    finally:
        await api.cleanup_client_session()

if __name__ == "__main__":
    asyncio.run(main())
