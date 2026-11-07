"""Web Codes: the badge hosts its own WiFi access point and shows QR codes / barcodes.

UserA toggles the access point, UserB resets the screen to the home graphic.

The home graphic shows a WiFi-join QR (WIFI:T:WPA;S:..;P:..;;) and the credentials. Join,
open http://192.168.4.1 and the page (static files in data/webcodes/, streamed from flash
so they cost no RAM) encodes the code in the browser, draws the whole 960 x 672 picture
(code, caption, optional border) and POSTs it to /photo as raw 1-bit planes, the same format
as apps/webnametag.py: body = black plane (1 = black), optionally followed by a red plane
(1 = red), 120 bytes/row, MSB first. A pixel is never set in both.

The access point has no internet, so the page cannot load anything from a CDN: the encoder
library is vendored as data/webcodes/bwip.js.gz and sent with Content-Encoding: gzip.

LED bar: green = AP up, red = failed / no config, orange chase = starting or deploying,
dark = off. While a submission is deploying new POSTs get 503; nothing is queued.
"""

import asyncio as aio
import gc
import json
import os
import sys
import time

import lvgl
import network

from apps.base_app import BaseApp
from lib.button import UserA, UserB
from lib.epd import display
from lib.led_bar import NUM_LEDS, leds
from lib.qr import make as qr_make

# Settings live in data/webnametag_config.py (untracked). A failed import here would break
# app_switcher's `from apps import ...` for ALL apps at boot, so it must never raise.
try:
    from data import webnametag_config as _cfg

    AP_SSID = getattr(_cfg, "AP_SSID", "Badge-Codes")
    AP_PASSWORD = getattr(_cfg, "AP_PASSWORD", None)
    AP_CHANNEL = getattr(_cfg, "AP_CHANNEL", 6)
except Exception as _ex:
    print("webcodes: no usable data/webnametag_config.py:", _ex)
    AP_SSID, AP_PASSWORD, AP_CHANNEL = "Badge-Codes", None, 6

WEB_DIR = "/data/webcodes/"
ROW_BYTES = 960 // 8
PHOTO_H = 672
PHOTO_BYTES = ROW_BYTES * PHOTO_H  # 80640, one 1-bit plane
ACTIVE_WINDOW_MS = 1000

LED_OFF = (0, 0, 0)
LED_RED = (3, 0, 0)
LED_GREEN = (0, 2, 0)
LED_ORANGE = (6, 2, 0)
LED_ORANGE_TAIL = (3, 1, 0)
CHASE_STEP_MS = 90
_LED_FILL = {"red": LED_RED, "green": LED_GREEN, "off": LED_OFF}
_FAILED_STATUSES = ("Failed", "No config")

_WHITE = lvgl.color_hex(0xFFFFFF)
_BLACK = lvgl.color_hex(0x000000)
_RED = lvgl.color_hex(0xFF0000)


def _font(*names):
    for n in names:
        f = getattr(lvgl, n, None)
        if f is not None:
            return f
    return lvgl.font_montserrat_24


F_SMALL = _font("font_montserrat_16")
F_BODY = _font("font_montserrat_24")
F_HEAD = _font("font_montserrat_28", "font_montserrat_24")
F_TITLE = _font("font_montserrat_48")

QR_SCALE = 16  # multiple of 8: rows are copied as whole bytes
QR_X = 24  # byte aligned
QR_Y = 110
COL_X = 0  # right column x, set once the QR size is known


def _fill(buf, x, y, w, h):
    """Set a w x h rectangle of bits in a 1-bit framebuffer plane."""
    x2 = x + w - 1
    b0, b1 = x >> 3, x2 >> 3
    m0 = 0xFF >> (x & 7)
    m1 = (0xFF << (7 - (x2 & 7))) & 0xFF
    mid = b"\xff" * (b1 - b0 - 1) if b1 - b0 > 1 else b""
    for row in range(y, y + h):
        base = row * ROW_BYTES
        if b0 == b1:
            buf[base + b0] |= m0 & m1
        else:
            buf[base + b0] |= m0
            buf[base + b1] |= m1
            if mid:
                buf[base + b0 + 1 : base + b1] = mid


def _frame(buf, x, y, w, h, t=4):
    _fill(buf, x, y, w, t)
    _fill(buf, x, y + h - t, w, t)
    _fill(buf, x, y, t, h)
    _fill(buf, x + w - t, y, t, h)


def _chase():
    """LED sequence: an orange comet sweeping along the bar."""
    i = 0
    while True:
        frame = [LED_OFF] * NUM_LEDS
        frame[(i - 2) % NUM_LEDS] = LED_ORANGE_TAIL
        frame[(i - 1) % NUM_LEDS] = LED_ORANGE_TAIL
        frame[i % NUM_LEDS] = LED_ORANGE
        yield frame, CHASE_STEP_MS
        i += 1


def _wifi_esc(s):
    for ch in "\\;,:\"":
        s = s.replace(ch, "\\" + ch)
    return s


# path -> (file, content type, gzip, cache). Only these are ever served.
_FILES = {
    b"/": ("index.html", "text/html; charset=utf-8", False, False),
    b"/app.js": ("app.js", "application/javascript", False, False),
    b"/bwip.js": ("bwip.js.gz", "application/javascript", True, True),
}


class WebCodesApp(BaseApp):
    def __init__(self, name: str | None = None) -> None:
        self.name = "Web Codes"
        self.sleep_ms = 100
        self.launched = False

        self.status = "WiFi off"  # "WiFi off", "Ready", "Failed", "No config"
        self.ip = ""
        self.mode = "home"  # "home" (generated graphic) or "photo"
        self._photo = None
        self._need_full = False
        self._receiving = False
        self._last_run_ms = None
        self._led_state = None
        self._ap = None
        self._server = None
        self._drawn = None
        self._scr = None
        self._boxes = []

    # ---- Access point ----------------------------------------------------
    def _ap_valid(self):
        return bool(AP_SSID) and AP_PASSWORD is not None and 8 <= len(AP_PASSWORD) <= 63

    def _wifi_enable(self):
        if not self._ap_valid():
            self.status = "No config"
            return
        gc.collect()
        print("webcodes: free before AP:", gc.mem_free())
        try:
            if self._ap is None:
                self._ap = network.WLAN(network.AP_IF)
            self._ap.active(True)
            self._ap.config(
                essid=AP_SSID,
                password=AP_PASSWORD,
                authmode=network.AUTH_WPA2_PSK,
                channel=AP_CHANNEL,
            )
            self.ip = self._ap.ifconfig()[0]
            self.status = "Ready"
            print("webcodes: AP up, ip", self.ip, "free:", gc.mem_free())
            aio.create_task(self._start_server())
        except Exception as ex:
            print("webcodes: AP enable failed")
            sys.print_exception(ex)
            self.status = "Failed"
            self.ip = ""
            gc.collect()

    def _wifi_disable(self):
        if self._server is not None:
            self._server.close()
            self._server = None
        try:
            if self._ap is not None:
                self._ap.active(False)
        except Exception as ex:
            sys.print_exception(ex)
        self.ip = ""
        self.status = "WiFi off"
        gc.collect()

    # ---- Busy tracking and LEDs -----------------------------------------
    def _active(self):
        if self._last_run_ms is None:
            return False
        return time.ticks_diff(time.ticks_ms(), self._last_run_ms) < ACTIVE_WINDOW_MS

    def _display_busy(self):
        return display._framebuffer_ready.is_set() or not display.is_done_updating()

    def _key(self):
        return (self.mode, self.ip if self.mode == "home" else None)

    def _pending(self):
        return self._need_full or self._drawn != self._key()

    def _busy(self):
        if self._receiving or self._display_busy():
            return True
        return self._active() and self._pending()

    def _led_wanted(self):
        if self._busy():
            return "orange"
        if self.status in _FAILED_STATUSES:
            return "red"
        if self.status == "WiFi off":
            return "off"
        return "green"

    def _update_leds(self):
        want = self._led_wanted()
        if want == self._led_state:
            return
        self._led_state = want
        try:
            if want == "orange":
                leds.unpause()
                leds.set_sequence(_chase)
            else:
                leds.clear_sequence()
                leds.fill(_LED_FILL[want])
        except Exception as ex:
            sys.print_exception(ex)

    # ---- HTTP server ------------------------------------------------------
    async def _start_server(self):
        try:
            server = await aio.start_server(self._handle, "0.0.0.0", 80)
        except Exception as ex:
            print("webcodes: server start failed")
            sys.print_exception(ex)
            self.status = "Failed"
            return
        if self.status == "Ready":
            self._server = server
        else:  # toggled off while starting
            server.close()

    async def _recv_photo(self, reader, n):
        buf = bytearray(n)
        mv = memoryview(buf)
        got = 0
        while got < n:
            chunk = await aio.wait_for(reader.read(min(2048, n - got)), 10)
            if not chunk:
                raise OSError("upload ended early")
            mv[got : got + len(chunk)] = chunk
            got += len(chunk)
        return buf

    async def _drain(self, reader, n):
        n = min(n, 2 * PHOTO_BYTES)
        while n > 0:
            chunk = await aio.wait_for(reader.read(min(2048, n)), 5)
            if not chunk:
                break
            n -= len(chunk)

    async def _send_file(self, writer, spec):
        fname, ctype, gz, cache = spec
        path = WEB_DIR + fname
        try:
            size = os.stat(path)[6]
        except OSError:
            writer.write(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\nMissing " + fname.encode())
            return
        head = "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\nConnection: close\r\n" % (ctype, size)
        if gz:
            head += "Content-Encoding: gzip\r\n"
        head += "Cache-Control: max-age=86400\r\n\r\n" if cache else "Cache-Control: no-cache\r\n\r\n"
        writer.write(head.encode())
        buf = bytearray(1024)
        with open(path, "rb") as f:
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                writer.write(memoryview(buf)[:n])
                await writer.drain()

    async def _handle(self, reader, writer):
        try:
            line = await aio.wait_for(reader.readline(), 5)
            parts = line.split()
            method = parts[0] if parts else b""
            path = parts[1] if len(parts) > 1 else b"/"
            clen = 0
            for _ in range(40):
                h = await aio.wait_for(reader.readline(), 5)
                if h in (b"\r\n", b"\n", b""):
                    break
                if h.lower().startswith(b"content-length:"):
                    clen = int(h.split(b":", 1)[1])
            if method == b"POST" and self._busy():
                await self._drain(reader, clen)
                print("webcodes: rejected POST, busy")
                writer.write(
                    b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 5\r\n"
                    b"Content-Type: text/plain\r\nConnection: close\r\n\r\n"
                    b"Badge is busy updating its display. Wait a few seconds and try again."
                )
            elif method == b"POST" and path == b"/photo":
                if clen not in (PHOTO_BYTES, 2 * PHOTO_BYTES):
                    writer.write(
                        b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain\r\n"
                        b"Connection: close\r\n\r\nBad image size"
                    )
                else:
                    # No await between the _busy() check and this flag.
                    self._receiving = True
                    try:
                        self._photo = None
                        gc.collect()
                        self._photo = await self._recv_photo(reader, clen)
                        self.mode = "photo"
                        self._need_full = True
                    finally:
                        self._receiving = False
                    print("webcodes: image received")
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n"
                        b"Received. The panel takes a while to refresh."
                    )
            elif method == b"POST":
                writer.write(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
            elif path.startswith(b"/status"):
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Cache-Control: no-store\r\nConnection: close\r\n\r\n"
                )
                writer.write(json.dumps({"busy": int(self._busy()), "mode": self.mode}).encode())
            else:
                spec = _FILES.get(path.split(b"?", 1)[0])
                if spec is None:
                    writer.write(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\nNot found")
                else:
                    await self._send_file(writer, spec)
            await writer.drain()
        except Exception as ex:
            print("webcodes: request error", ex)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ---- Display ------------------------------------------------------------
    def _box(self, parent, x, y, w, h, font, color, bg, text):
        """Fixed-size opaque box with a centred label (see webnametag for why boxes)."""
        b = lvgl.obj(parent)
        b.remove_style_all()
        b.set_size(w, h)
        b.set_style_bg_color(bg, 0)
        b.set_style_bg_opa(lvgl.OPA.COVER, 0)
        b.align(lvgl.ALIGN.TOP_LEFT, x, y)
        lbl = lvgl.label(b)
        lbl.set_style_text_font(font, 0)
        lbl.set_style_text_color(color, 0)
        try:
            lbl.set_style_text_align(lvgl.TEXT_ALIGN.CENTER, 0)
        except Exception:
            pass
        lbl.set_text(text)
        lbl.align(lvgl.ALIGN.CENTER, 0, 0)
        self._boxes.append(b)

    def _draw_home(self, fb, scr):
        self._box(scr, 30, 20, 900, 70, F_TITLE, _RED, _WHITE, "Web Codes")
        qr_px = 0
        if self.ip:
            payload = "WIFI:T:WPA;S:%s;P:%s;;" % (_wifi_esc(AP_SSID), _wifi_esc(AP_PASSWORD))
            try:
                rows = qr_make(payload)
            except ValueError:  # credentials too long for the on-badge encoder
                rows = None
            if rows:
                qr_px = len(rows) * QR_SCALE
                on, off = b"\xff" * (QR_SCALE // 8), b"\x00" * (QR_SCALE // 8)
                for r, row in enumerate(rows):
                    line = b"".join([on if m else off for m in row])
                    for sl in range(QR_SCALE):
                        o = (QR_Y + r * QR_SCALE + sl) * ROW_BYTES + (QR_X >> 3)
                        fb[o : o + len(line)] = line
        if qr_px:
            cx = QR_X + qr_px + 24
        else:
            cx = 60
        cw = 960 - 24 - cx
        if self.ip:
            self._box(scr, cx, QR_Y, cw, 40, F_HEAD, _BLACK, _WHITE,
                      "1. Scan to join" if qr_px else "1. Join this WiFi")
            self._box(scr, cx, QR_Y + 46, cw, 28, F_SMALL, _BLACK, _WHITE, "Network")
            self._box(scr, cx, QR_Y + 74, cw, 34, F_BODY, _RED, _WHITE, AP_SSID)
            self._box(scr, cx, QR_Y + 112, cw, 28, F_SMALL, _BLACK, _WHITE, "Password")
            self._box(scr, cx, QR_Y + 140, cw, 34, F_BODY, _RED, _WHITE, AP_PASSWORD)
            self._box(scr, cx, QR_Y + 196, cw, 40, F_HEAD, _BLACK, _WHITE, "2. Open")
            self._box(scr, cx, QR_Y + 240, cw, 34, F_BODY, _RED, _WHITE, "http://" + self.ip)
        else:
            _frame(fb, cx, QR_Y, cw, 240)
            msg = {
                "No config": "No AP password\nin the config",
                "Failed": "WiFi failed\n\nPress Left Btn\nto retry",
            }.get(self.status, "WiFi is off\n\nPress Left Btn\nto turn it on")
            self._box(scr, cx + 10, QR_Y + 20, cw - 20, 200, F_BODY, _BLACK, _WHITE, msg)
        self._box(scr, cx, 560, cw, 60, F_SMALL, _BLACK, _WHITE,
                  "Left Btn: AP on / off\nRight Btn: clear the screen")

    def _redraw(self):
        display.clear()
        fb = display._framebuffer_black
        if self.mode == "photo" and self._photo is not None:
            pv = memoryview(self._photo)
            fb[0:PHOTO_BYTES] = pv[:PHOTO_BYTES]
            if len(pv) > PHOTO_BYTES:
                display._framebuffer_red[0:PHOTO_BYTES] = pv[PHOTO_BYTES:]
        old = self._scr
        scr = lvgl.obj()
        scr.remove_style_all()
        scr.set_size(lvgl.pct(100), lvgl.pct(100))
        scr.set_style_bg_opa(lvgl.OPA.TRANSP, 0)
        self._boxes = []
        if self.mode == "home":
            self._draw_home(fb, scr)
        self._scr = scr
        lvgl.screen_load(scr)
        if old is not None:
            try:
                old.delete()
            except Exception as ex:
                print("webcodes: old screen delete failed", ex)
        scr.update_layout()
        display.lv_display.inv_p = 0
        for b in self._boxes:
            b.invalidate()
        lvgl.refr_now(display.lv_display)
        display.update()
        self._drawn = self._key()
        self._need_full = False
        gc.collect()

    # ---- App loop -------------------------------------------------------------
    def run(self):
        self._last_run_ms = time.ticks_ms()
        if self.launched:
            if self.status in ("WiFi off", "Failed", "No config"):
                self._wifi_enable()
            self._need_full = True
            self._led_state = None
            return

        if UserA.pressed():
            if self.status == "Ready":
                self._wifi_disable()
            else:
                self._wifi_disable()
                self._wifi_enable()
        if UserB.pressed() and not self._busy():
            self.mode = "home"
            self._photo = None
            self._need_full = True

        self._update_leds()
        if self._display_busy():
            return
        if self._pending():
            self._redraw()
