"""Web Boarding Pass over Bluetooth LE: a Delta style boarding pass drawn by the page
web/webnametag/pass.html from a screenshot/photo of a real pass (the page reads the barcode, draws the pass
in black + red and sends it as a 2-plane photo). All the machinery is in lib/ble_photo.py."""

from lib.ble_photo import BlePhotoApp


class WebPassApp(BlePhotoApp):
    APP_NAME = "Boarding Pass"
    TITLE = "Show a Boarding Pass"
    PAGE_PATH = "pass.html"
