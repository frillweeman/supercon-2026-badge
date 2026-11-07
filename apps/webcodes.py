"""Web Codes over Bluetooth LE: QR codes / barcodes drawn by the page web/webnametag/codes.html
(which can be opened with ?type=...&content=... to send one automatically). The page sends the
finished bitmap as a photo; all the machinery is in lib/ble_photo.py."""

from lib.ble_photo import BlePhotoApp


class WebCodesApp(BlePhotoApp):
    APP_NAME = "Web Codes"
    TITLE = "Show a Code"
    PAGE_PATH = "codes.html"
