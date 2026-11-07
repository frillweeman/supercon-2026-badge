"""Template for data/webnametag_config.py (which is untracked). Copy it to that name
and fill in the real values. apps/webnametag.py and apps/webcodes.py both host their own WiFi
access point (no existing network, no internet) and read these three settings. The password is
required (8-63 chars); without it the apps show "No config" and never start an open network.
Only one of the two apps should have its AP on at a time (turn it off with the left button)."""

AP_SSID = "Badge-Codes"
AP_PASSWORD = "choose-a-password"
AP_CHANNEL = 6
