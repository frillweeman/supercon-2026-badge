"""Template for data/webnametag_config.py (which is untracked). Copy it to that name.
apps/webnametag.py advertises over Bluetooth LE as "Badge-<BLE_NAME>"; if BLE_NAME is empty it
uses "Badge-<last 2 MAC bytes>". Pick something short and recognisable, since it is what you
choose in the browser's device list when several badges are nearby."""

BLE_NAME = "Will"

# Optional: where the web page is hosted (shown on the badge as text + QR). Default is the
# GitHub Pages site for this repo.
# PAGE_URL = "https://frillweeman.github.io/supercon-2026-badge/"
