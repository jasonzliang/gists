"""blueair_prov -- BLE provisioning tool for the Blueair Blue Pure 511i Max.

Python port of https://github.com/kovapatrik/blueairble-rs (Rust, btleplug).
Protocol: Espressif protocomm / wifi_provisioning (Security1, no PoP) plus
Blueair custom commands on a fifth "custom-endpoint" characteristic.
"""

__version__ = "0.1.0"
