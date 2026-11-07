"""Template for data/webnametag_config.py (which is untracked). Copy it to that name
and fill in the real values. apps/webnametag.py hosts its own WiFi access point (no existing
network, no internet) and reads these three settings. The password is required (8-63 chars);
without it the app shows "No config" and never starts an open network."""

AP_SSID = "Badge-Name"
AP_PASSWORD = "choose-a-password"
AP_CHANNEL = 6
