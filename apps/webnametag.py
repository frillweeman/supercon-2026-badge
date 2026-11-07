"""Web Nametag over Bluetooth LE: a typed name or an uploaded photo, from the page
web/webnametag/index.html. All the machinery is in lib/ble_photo.py."""

from lib.ble_photo import BlePhotoApp


class WebNametagApp(BlePhotoApp):
    APP_NAME = "Web Nametag"
    TITLE = "Control Me!"
    PAGE_PATH = ""
    NAME_MODE = True
