"""WiFi Nametag: the badge hosts its own WiFi access point; join it, open the page, and show a
typed name or an uploaded photo.

UserA toggles the access point; UserB resets the screen to the generated home graphic (which
also clears any name/photo). Nothing is persisted.

The access point (SSID / password / channel from data/webnametag_config.py) needs no existing
network and no internet. The home graphic shows a WiFi-join QR (WIFI:T:WPA;S:..;P:..;;), the
credentials and the URL. The screen is only redrawn when its content changes (new name/photo,
reset, or the AP coming up / going down on the home graphic).

The page is two static files in data/webnametag/ (index.html, app.js) streamed from flash, so
they cost no RAM. The AP has no internet: the page must never load anything from a CDN.

The LED bar shows the state: green = AP up, red = failed / no config, orange chase = starting
or loading a new name/image, dark = off. While a submission is being deployed (upload in
progress, redraw pending, or panel refreshing) new POSTs are rejected with 503; nothing is
queued. The page polls GET /status (busy / mode / name) to show when the panel has finished.

The web page crops and dithers photos in the browser for the whole panel and POSTs them to
/photo as raw 1-bit planes (960 x PHOTO_H, 120 bytes/row, MSB first). Body = black plane
(1 = black), optionally followed by a red plane (1 = red); a pixel is never set in both.
The badge copies the planes into the framebuffers; LVGL only draws text.
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

# Settings live in data/webnametag_config.py (untracked; see the .example.py).
# A failed import here would break app_switcher's `from apps import ...` for ALL apps at
# boot, so a missing/broken config must not raise: the app just reports "No config".
try:
    from data import webnametag_config as _cfg

    AP_SSID = getattr(_cfg, "AP_SSID", "Badge-Name")
    AP_PASSWORD = getattr(_cfg, "AP_PASSWORD", None)
    AP_CHANNEL = getattr(_cfg, "AP_CHANNEL", 6)
except Exception as _ex:
    print("wifinametag: no usable data/webnametag_config.py:", _ex)
    AP_SSID, AP_PASSWORD, AP_CHANNEL = "Badge-Name", None, 6

WEB_DIR = "/data/wifinametag/"
MAX_NAME = 24
ROW_BYTES = 960 // 8  # 120 bytes per framebuffer row
PHOTO_H = 672  # the photo covers the whole panel
PHOTO_BYTES = ROW_BYTES * PHOTO_H  # 80640, one 1-bit plane

ACTIVE_WINDOW_MS = 1000  # run() executed this recently => the app is on screen

# LED colours. The LEDs are very bright; other apps use 0..3 per channel. Tune by eye.
LED_OFF = (0, 0, 0)
LED_RED = (3, 0, 0)
LED_GREEN = (0, 2, 0)
# Orange needs red:green near 3:1, and (1,0,0) rounds the green away and shows as plain red,
# so the chase's dim tail is (3,1,0) and the head is twice that.
LED_ORANGE = (6, 2, 0)
LED_ORANGE_TAIL = (3, 1, 0)
CHASE_STEP_MS = 90
_LED_FILL = {"red": LED_RED, "green": LED_GREEN, "off": LED_OFF}
_FAILED_STATUSES = ("Failed", "No config")

_WHITE = lvgl.color_hex(0xFFFFFF)
_BLACK = lvgl.color_hex(0x000000)
_RED = lvgl.color_hex(0xFF0000)


def _font(*names):
    """First LVGL font in this build from names (sizes beyond 14/16/24/48 are unverified)."""
    for n in names:
        f = getattr(lvgl, n, None)
        if f is not None:
            return f
    return lvgl.font_montserrat_24


F_SMALL = _font("font_montserrat_16")
F_BODY = _font("font_montserrat_24")
F_HEAD = _font("font_montserrat_28", "font_montserrat_24")
F_TITLE = _font("font_montserrat_48")

# Home graphic geometry (px). QR scale is a multiple of 8: rows are copied as whole bytes.
QR_SCALE = 16
QR_X = 24  # byte aligned
QR_Y = 110


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
    """LED sequence (see LedBar.set_sequence): an all-orange comet sweeping along the bar."""
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


# path -> (file, content type). Only these are ever served; streamed from flash, no RAM cost.
_FILES = {
    b"/": ("index.html", "text/html; charset=utf-8"),
    b"/app.js": ("app.js", "application/javascript"),
}


def _urldecode(raw):
    """Decode form-encoded bytes, keeping printable ASCII only."""
    raw = raw.replace(b"+", b" ")
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == 37 and i + 2 < n:  # '%'
            try:
                c = int(raw[i + 1 : i + 3], 16)
                i += 2
            except ValueError:
                pass
        if 32 <= c <= 126:
            out.append(c)
        i += 1
    return out.decode()


class WifiNametagApp(BaseApp):
    def __init__(self, name: str | None = None) -> None:
        self.name = "WiFi Nametag"
        self.sleep_ms = 100
        self.launched = False

        self.name_text = ""
        self.status = "WiFi off"  # "WiFi off", "Ready", "Failed", "No config"
        self.ip = ""
        self.mode = "home"  # "home" (generated graphic), "name" or "photo"
        # bytearray of PHOTO_BYTES (black only) or 2*PHOTO_BYTES (black then red) in photo mode
        self._photo = None
        self._need_full = False
        self._receiving = False  # a /photo body is being read
        self._last_run_ms = None  # when run() last executed (None = never)
        self._led_state = None  # last LED state applied ("red", "green", "orange", "off")
        self._ap = None
        self._server = None
        self._drawn = None  # _key() of what is on screen
        self._scr = None
        self._boxes = []

    # ---- Access point --------------------------------------------------
    def _ap_valid(self):
        return bool(AP_SSID) and AP_PASSWORD is not None and 8 <= len(AP_PASSWORD) <= 63

    def _wifi_enable(self):
        if not self._ap_valid():
            self.status = "No config"
            return
        gc.collect()
        print("wifinametag: free before AP:", gc.mem_free())
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
            print("wifinametag: AP up, ip", self.ip, "free:", gc.mem_free())
            aio.create_task(self._start_server())
        except Exception as ex:  # MemoryError, OSError, ...
            print("wifinametag: AP enable failed")
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

    # ---- Busy tracking and LEDs ----------------------------------------
    def _active(self):
        """Is the app on screen? run() only executes while it is selected."""
        if self._last_run_ms is None:
            return False
        return time.ticks_diff(time.ticks_ms(), self._last_run_ms) < ACTIVE_WINDOW_MS

    def _display_busy(self):
        # epd.update() sets _framebuffer_ready and epd.run() clears it only after the
        # whole refresh, so it covers the refresh itself. is_done_updating() alone has a
        # gap of up to 100 ms between update() and the refresh starting.
        return display._framebuffer_ready.is_set() or not display.is_done_updating()

    def _key(self):
        return (self.mode, self.name_text, self.ip if self.mode == "home" else None)

    def _pending(self):
        """A redraw is due (new content, reset, or the AP IP appeared/disappeared on home)."""
        return self._need_full or self._drawn != self._key()

    def _busy(self):
        """Is a submission being deployed right now? New submissions are rejected then."""
        if self._receiving or self._display_busy():
            return True
        # Pending redraw only counts while we're on screen: run() is what clears it.
        # While away, a submission is just stored and drawn on re-entry.
        return self._active() and self._pending()

    def _led_wanted(self):
        # Busy comes first so a button press shows the loading animation immediately.
        if self._busy():
            return "orange"
        if self.status in _FAILED_STATUSES:
            return "red"
        if self.status == "WiFi off":
            return "off"
        return "green"  # AP up and idle

    def _update_leds(self):
        want = self._led_wanted()
        if want == self._led_state:
            return
        self._led_state = want
        try:
            if want == "orange":
                leds.unpause()  # the launcher's menu pauses the sequencer and never resumes it
                leds.set_sequence(_chase)
            else:
                leds.clear_sequence()
                leds.fill(_LED_FILL[want])
        except Exception as ex:
            sys.print_exception(ex)

    # ---- HTTP server ---------------------------------------------------
    async def _start_server(self):
        try:
            server = await aio.start_server(self._handle, "0.0.0.0", 80)
        except Exception as ex:
            print("wifinametag: server start failed")
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
                raise OSError("photo upload ended early")
            mv[got : got + len(chunk)] = chunk
            got += len(chunk)
        return buf

    async def _drain(self, reader, n):
        """Read and discard a request body so the client sees our reply, not a reset."""
        n = min(n, 2 * PHOTO_BYTES)
        while n > 0:
            chunk = await aio.wait_for(reader.read(min(2048, n)), 5)
            if not chunk:
                break
            n -= len(chunk)

    async def _send_file(self, writer, spec):
        fname, ctype = spec
        path = WEB_DIR + fname
        try:
            size = os.stat(path)[6]
        except OSError:
            writer.write(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\nMissing " + fname.encode())
            return
        writer.write(
            (
                "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
                "Cache-Control: no-cache\r\nConnection: close\r\n\r\n" % (ctype, size)
            ).encode()
        )
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
                # Still deploying the previous submission: reject, never queue.
                await self._drain(reader, clen)
                print("wifinametag: rejected POST, busy")
                writer.write(
                    b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 5\r\n"
                    b"Content-Type: text/plain\r\nConnection: close\r\n\r\n"
                    b"Badge is busy updating its display. Wait a few seconds and try again."
                )
            elif method == b"POST" and path == b"/photo":
                if clen not in (PHOTO_BYTES, 2 * PHOTO_BYTES):
                    writer.write(
                        b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain\r\n"
                        b"Connection: close\r\n\r\nBad photo size"
                    )
                else:
                    # No await between the _busy() check above and this flag, so two
                    # requests can never both pass the check.
                    self._receiving = True
                    try:
                        self._photo = None
                        gc.collect()
                        self._photo = await self._recv_photo(reader, clen)
                        self.mode = "photo"
                        self._need_full = True
                    finally:
                        self._receiving = False
                    print("wifinametag: photo received")
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n"
                        b"Photo received. The panel takes a while to refresh."
                    )
            elif method == b"POST":
                body = await aio.wait_for(reader.readexactly(min(clen, 256)), 5)
                for field in body.split(b"&"):
                    if field.startswith(b"name="):
                        self.name_text = _urldecode(field[5:])[:MAX_NAME]
                        print("wifinametag: name ->", repr(self.name_text))
                self.mode = "name" if self.name_text else "home"
                self._photo = None
                self._need_full = True
                # The page shows this as the current name.
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n"
                    + self.name_text.encode()
                )
            elif path.startswith(b"/status"):
                # Polled by the page to learn the current name and when the panel is done.
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Cache-Control: no-store\r\nConnection: close\r\n\r\n"
                )
                writer.write(
                    json.dumps(
                        {"busy": int(self._busy()), "mode": self.mode, "name": self.name_text}
                    ).encode()
                )
            else:
                spec = _FILES.get(path.split(b"?", 1)[0])
                if spec is None:
                    writer.write(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\nNot found")
                else:
                    await self._send_file(writer, spec)
            await writer.drain()
        except Exception as ex:
            print("wifinametag: request error", ex)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ---- Display -------------------------------------------------------
    def _box(self, parent, x, y, w, h, font, color, bg, text):
        """Fixed-size opaque box with a centred label. LVGL -> framebuffer conversion
        (epd._lv_flush) is a pure-Python per-pixel loop, so only these boxes are ever
        repainted; everything else is drawn straight into the framebuffers."""
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
        self._box(scr, 30, 20, 900, 70, F_TITLE, _RED, _WHITE, "Control Me!")
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
        cx = QR_X + qr_px + 24 if qr_px else 60
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
                  "Left Btn: WiFi on / off\nRight Btn: clear the screen")

    def _redraw(self):
        """Clear the panel, draw the current content, refresh."""
        display.clear()  # framebuffers to white; LVGL only paints our boxes
        fb = display._framebuffer_black
        if self.mode == "photo" and self._photo is not None:
            # First plane is black; an optional second is red.
            pv = memoryview(self._photo)
            fb[0:PHOTO_BYTES] = pv[:PHOTO_BYTES]
            if len(pv) > PHOTO_BYTES:
                display._framebuffer_red[0:PHOTO_BYTES] = pv[PHOTO_BYTES:]
        # Fresh transparent screen each time, holding only the boxes this mode needs.
        old = self._scr
        scr = lvgl.obj()
        scr.remove_style_all()
        scr.set_size(lvgl.pct(100), lvgl.pct(100))
        scr.set_style_bg_opa(lvgl.OPA.TRANSP, 0)
        self._boxes = []
        if self.mode == "home":
            self._draw_home(fb, scr)
        elif self.mode == "name":
            self._box(scr, 30, 301, 900, 70, F_TITLE, _BLACK, _WHITE, self.name_text)
        self._scr = scr
        lvgl.screen_load(scr)
        if old is not None:
            try:
                old.delete()
            except Exception as ex:
                print("wifinametag: old screen delete failed", ex)
        scr.update_layout()
        # screen_load queued a full-panel dirty area; drop it, keep only our boxes.
        display.lv_display.inv_p = 0
        for b in self._boxes:
            b.invalidate()
        lvgl.refr_now(display.lv_display)
        display.update()
        self._drawn = self._key()
        self._need_full = False
        gc.collect()

    # ---- App loop ------------------------------------------------------
    def run(self):
        # The display is only touched from here, and run() only executes while
        # this app is selected, so a name or photo submitted while away is drawn on re-entry.
        self._last_run_ms = time.ticks_ms()
        if self.launched:
            if self.status in ("WiFi off", "Failed", "No config"):
                self._wifi_enable()
            self._need_full = True  # draw below, once the panel is idle
            self._led_state = None  # the launcher blanked the LEDs; re-apply ours
            return

        if UserA.pressed():
            if self.status == "Ready":
                self._wifi_disable()
            else:  # off, or failed: (re)start
                self._wifi_disable()
                self._wifi_enable()
        if UserB.pressed() and not self._busy():  # ignore presses while already working
            self.mode = "home"
            self.name_text = ""
            self._photo = None
            self._need_full = True

        self._update_leds()

        # epd.run() clears its "update requested" flag when a refresh finishes, so a
        # display.update() made mid-refresh would be lost. Only draw when idle;
        # anything still dirty is retried on the next tick.
        if self._display_busy():
            return

        if self._pending():
            self._redraw()
