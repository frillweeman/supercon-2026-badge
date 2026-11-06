"""Template for data/webnametag_config.py (which is untracked). Copy it to that name
and fill in the real values. apps/webnametag.py reads SSID, PASSWORD, HOSTNAME and
CONNECT_TIMEOUT_MS from there."""

SSID = "your-network-name"
PASSWORD = "your-password"
HOSTNAME = "willboard"  # announced via DHCP/mDNS as <HOSTNAME>.local
CONNECT_TIMEOUT_MS = 20000
