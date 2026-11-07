"""Web Nametag over Bluetooth LE: a web page (hosted on GitHub Pages, opened in a Web Bluetooth
browser such as Bluefy on iPhone) connects to the badge, and shows a typed name or an uploaded
photo on the panel. No WiFi is used.

UserA toggles Bluetooth; UserB resets the screen to the generated home graphic (which also
clears any name/photo and picks a NEW pairing code). Nothing is persisted.

Identity: the badge advertises as "Badge-<BLE_NAME>" (BLE_NAME from data/webnametag_config.py),
or "Badge-<last 2 MAC bytes>" if unset, so several badges are told apart in the browser's chooser.
The home graphic shows the page URL (text + QR), the advertised name and a random 4-digit pairing
code. The page must send that code first (AUTH); until then every other write is refused. Three
wrong codes disconnect and lock out for 10 s. This keeps neighbours from overwriting the nametag;
it is not strong security.

GATT (one 128-bit service, see UUIDs below; the page, web/webnametag/app.js, mirrors them):
  CTRL   write           b"A"+code | b"N"+name | b"P"+planes(1|2) | b"D"+bytes | b"S" (sync) | b"E" (end photo)
  DATA   (unused; stalled writes under Bluefy. Photo bytes now go in CTRL as b"D"+bytes)
  STATUS read + notify   authed, busy, mode, ev (1 byte each), chunk (u16), arg (u32), then the name.
                         ev != 0 is a one-shot event (see EV_*); arg is the received byte count for EV_ACK.
A photo is sent as P, then D windows of <= 3840 bytes each followed by S (the badge answers with
EV_ACK and its byte count, so the sender never runs far ahead), then E. Body =
black plane (1 = black), optionally followed by a red plane (1 = red), 960 x 672 at 120 bytes/row,
MSB first; a pixel is never set in both. The badge copies the planes into the framebuffers; LVGL
only draws text.

The LED bar shows the state: green = Bluetooth on, red = failed, orange chase = loading a new
name/image, dark = off. While a submission is being deployed (receiving, redraw pending, or panel
refreshing) new commands get EV_BUSY; nothing is queued.
"""

import gc
import os
import struct
import sys
import time

import bluetooth
import lvgl

from apps.base_app import BaseApp
from lib.button import UserA, UserB
from lib.epd import display
from lib.led_bar import NUM_LEDS, leds
from lib.qr import make as qr_make

# Settings live in data/webnametag_config.py (untracked; see the .example.py).
# A failed import here would break app_switcher's `from apps import ...` for ALL apps at
# boot, so a missing/broken config must not raise.
try:
    from data import webnametag_config as _cfg

    BLE_NAME = getattr(_cfg, "BLE_NAME", "")
    PAGE_URL = getattr(_cfg, "PAGE_URL", "https://frillweeman.github.io/supercon-2026-badge/")
except Exception as _ex:
    print("webnametag: no usable data/webnametag_config.py:", _ex)
    BLE_NAME = ""
    PAGE_URL = "https://frillweeman.github.io/supercon-2026-badge/"

_SVC = bluetooth.UUID("7b1f0001-5c0e-4b6e-9a3d-5e11c0de0001")
_CTRL = bluetooth.UUID("7b1f0002-5c0e-4b6e-9a3d-5e11c0de0001")
_DATA = bluetooth.UUID("7b1f0003-5c0e-4b6e-9a3d-5e11c0de0001")
_STAT = bluetooth.UUID("7b1f0004-5c0e-4b6e-9a3d-5e11c0de0001")
_IRQ_CONNECT, _IRQ_DISCONNECT, _IRQ_WRITE, _IRQ_MTU = 1, 2, 3, 21
EV_AUTH_OK, EV_AUTH_BAD, EV_PH_READY, EV_PH_OK, EV_PH_ERR = 1, 2, 3, 4, 5
EV_LOCKED, EV_BUSY, EV_NAME_OK, EV_ACK, EV_NEED_AUTH = 6, 7, 8, 9, 10
_MODE_IDX = {"home": 0, "name": 1, "photo": 2}
DATA_BUF = 4096
MAX_BAD_CODES = 3
LOCKOUT_MS = 10000
RX_TIMEOUT_MS = 10000

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
_FAILED_STATUSES = ("Failed",)

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


def _ascii(raw):
    """Printable ASCII only, as str."""
    return bytes(c for c in raw if 32 <= c <= 126).decode()


def _uuid_le(u):
    return bytes(reversed(bytes.fromhex(u.replace("-", ""))))  # MicroPython has no [::-1] on bytes


class WebNametagApp(BaseApp):
    def __init__(self, name: str | None = None) -> None:
        self.name = "Web Nametag"
        self.sleep_ms = 100
        self.launched = False

        self.name_text = ""
        self.status = "BLE off"  # "BLE off", "Ready", "Failed"
        self.mode = "home"  # "home" (generated graphic), "name" or "photo"
        # bytearray of PHOTO_BYTES (black only) or 2*PHOTO_BYTES (black then red) in photo mode
        self._photo = None
        self._need_full = False
        self._last_run_ms = None  # when run() last executed (None = never)
        self._led_state = None  # last LED state applied ("red", "green", "orange", "off")
        self._drawn = None  # _key() of what is on screen
        self._scr = None
        self._boxes = []
        # BLE
        self._ble = None
        self._h_ctrl = self._h_data = self._h_stat = 0
        self._adv_name = self._make_adv_name()
        self._code = ""  # current 4-digit pairing code
        self._conn = None
        self._authed = False
        self._bad = 0
        self._lock_until = None  # ticks_ms until which AUTH is refused
        self._mtu = 23
        self._receiving = False  # a photo is being received
        self._buf = None  # photo bytes received so far
        self._total = 0
        self._got = 0
        self._rx_ms = 0
        self._stat_key = None

    # ---- Bluetooth -------------------------------------------------------
    @staticmethod
    def _make_adv_name():
        import machine

        suffix = _ascii(BLE_NAME.encode())[:18] if BLE_NAME else "%02X%02X" % tuple(machine.unique_id()[-2:])
        return "Badge-" + suffix

    def _new_code(self):
        self._code = "%04d" % (int.from_bytes(os.urandom(2), "big") % 10000)

    def _ble_enable(self):
        gc.collect()
        print("webnametag: free before BLE:", gc.mem_free())
        try:
            ble = self._ble = bluetooth.BLE()
            ble.active(True)
            for kw in ({"gap_name": self._adv_name}, {"mtu": 512}):
                try:
                    ble.config(**kw)
                except Exception as ex:
                    print("webnametag: ble.config", kw, ex)
            ble.irq(self._irq)
            rw = bluetooth.FLAG_WRITE | bluetooth.FLAG_WRITE_NO_RESPONSE
            ((self._h_ctrl, self._h_data, self._h_stat),) = ble.gatts_register_services((
                (_SVC, (
                    (_CTRL, rw),
                    (_DATA, rw),
                    (_STAT, bluetooth.FLAG_READ | bluetooth.FLAG_NOTIFY),
                )),
            ))
            ble.gatts_set_buffer(self._h_ctrl, 256)  # fits b"D" + a 180-byte photo chunk
            ble.gatts_set_buffer(self._h_data, DATA_BUF, True)  # append: bursts are not lost
            ble.gatts_set_buffer(self._h_stat, 64)
            self._new_code()
            self._conn = None
            self._authed = False
            self.status = "Ready"
            self._stat_key = None
            self._advertise()
            print("webnametag: BLE up as", self._adv_name, "free:", gc.mem_free())
        except Exception as ex:  # MemoryError, OSError, ...
            print("webnametag: BLE enable failed")
            sys.print_exception(ex)
            self.status = "Failed"
            try:
                self._ble.active(False)
            except Exception:
                pass
            gc.collect()

    def _ble_disable(self):
        try:
            if self._ble is not None:
                if self._conn is not None:
                    self._ble.gap_disconnect(self._conn)
                self._ble.gap_advertise(None)
                self._ble.active(False)
        except Exception as ex:
            sys.print_exception(ex)
        self._conn = None
        self._authed = False
        self._abort_rx()
        self.status = "BLE off"
        gc.collect()

    def _advertise(self):
        name = self._adv_name.encode()
        adv = b"\x02\x01\x06" + bytes((len(name) + 1, 0x09)) + name
        resp = bytes((17, 0x07)) + _uuid_le("7b1f0001-5c0e-4b6e-9a3d-5e11c0de0001")
        self._ble.gap_advertise(100000, adv_data=adv, resp_data=resp, connectable=True)

    def _abort_rx(self):
        self._receiving = False
        self._buf = None
        self._got = self._total = 0

    def _chunk(self):
        return min(self._mtu - 3, 244)

    def _status_bytes(self, ev=0, arg=0):
        hdr = struct.pack(
            ">BBBBHI", int(self._authed), int(self._busy()), _MODE_IDX[self.mode], ev, self._chunk(), arg
        )
        return hdr

    def _publish(self, ev=0, arg=0):
        """Update the readable STATUS value; with an event, also notify the (short) header."""
        hdr = self._status_bytes(ev, arg)
        try:
            self._ble.gatts_write(self._h_stat, hdr + self.name_text.encode())
            if ev and self._conn is not None:
                self._ble.gatts_notify(self._conn, self._h_stat, hdr)
        except Exception as ex:
            print("webnametag: status publish failed", ex)

    def _refresh_status(self):
        """Keep the readable STATUS fresh (busy/mode/name change from run(), not from IRQs)."""
        if self.status != "Ready":
            return
        key = (self._authed, self._busy(), self.mode, self.name_text, self._chunk())
        if key != self._stat_key:
            self._stat_key = key
            self._publish()

    def _irq(self, event, data):
        try:
            if event == _IRQ_CONNECT:
                print("webnametag: connect")
                self._conn = data[0]
                self._authed = False
                self._bad = 0
                self._stat_key = None
            elif event == _IRQ_DISCONNECT:
                print("webnametag: disconnect (rx %d/%d)" % (self._got, self._total))
                self._conn = None
                self._authed = False
                self._mtu = 23
                self._abort_rx()
                self._stat_key = None
                if self.status == "Ready":
                    self._advertise()
            elif event == _IRQ_MTU:
                print("webnametag: mtu", data[1])
                self._mtu = data[1]
                self._stat_key = None
            elif event == _IRQ_WRITE:
                h = data[1]
                if h == self._h_ctrl:
                    self._on_ctrl(bytes(self._ble.gatts_read(h)))
                elif h == self._h_data:
                    self._on_data(self._ble.gatts_read(h))
        except Exception as ex:
            print("webnametag: irq error")
            sys.print_exception(ex)

    def _locked(self):
        return self._lock_until is not None and time.ticks_diff(self._lock_until, time.ticks_ms()) > 0

    def _on_ctrl(self, msg):
        op = msg[:1]
        if op == b"D":  # photo bytes (acknowledged write; the DATA characteristic is unused)
            if self._authed:
                self._on_data(memoryview(msg)[1:])
            return
        print("webnametag: ctrl", op, len(msg), "authed" if self._authed else "locked-out" if self._locked() else "unauthed")
        if op == b"A":
            if self._locked():
                return self._publish(EV_LOCKED)
            if msg[1:] == self._code.encode():
                self._authed = True
                self._bad = 0
                self._stat_key = None
                return self._publish(EV_AUTH_OK)
            self._bad += 1
            if self._bad >= MAX_BAD_CODES:
                self._lock_until = time.ticks_add(time.ticks_ms(), LOCKOUT_MS)
                self._publish(EV_LOCKED)
                self._ble.gap_disconnect(self._conn)
                return
            return self._publish(EV_AUTH_BAD)
        if not self._authed:
            return self._publish(EV_NEED_AUTH)
        if op == b"N":
            if self._busy():
                return self._publish(EV_BUSY)
            self.name_text = _ascii(msg[1:])[:MAX_NAME]
            print("webnametag: name ->", repr(self.name_text))
            self.mode = "name" if self.name_text else "home"
            self._photo = None
            self._need_full = True
            self._stat_key = None
            return self._publish(EV_NAME_OK)
        if op == b"P":
            planes = msg[1] if len(msg) > 1 else 0
            # A new P while receiving restarts the upload (the page retries after a stall).
            if self._display_busy() or (not self._receiving and self._busy()):
                return self._publish(EV_BUSY)
            if planes not in (1, 2):
                return self._publish(EV_PH_ERR)
            try:
                self._photo = None
                self._buf = None
                gc.collect()
                self._buf = bytearray(planes * PHOTO_BYTES)
            except MemoryError:
                self._abort_rx()
                return self._publish(EV_PH_ERR)
            self._total = planes * PHOTO_BYTES
            self._got = 0
            self._rx_ms = time.ticks_ms()
            self._receiving = True
            return self._publish(EV_PH_READY)
        if op == b"S":
            print("webnametag: ack", self._got)
            return self._publish(EV_ACK, self._got)
        if op == b"E":
            ok = self._receiving and self._got == self._total
            if ok:
                self._photo = self._buf
                self.mode = "photo"
                self._need_full = True
                self._stat_key = None
            self._abort_rx()  # drops _buf; _photo (if set above) keeps the data
            return self._publish(EV_PH_OK if ok else EV_PH_ERR)

    def _on_data(self, d):
        if not (self._authed and self._receiving and self._buf is not None):
            return
        n = len(d)
        if self._got == 0:
            print("webnametag: first data", n)
        if self._got + n > self._total:
            self._abort_rx()
            return self._publish(EV_PH_ERR)
        self._buf[self._got : self._got + n] = d
        self._got += n
        self._rx_ms = time.ticks_ms()

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
        return (self.mode, self.name_text, (self.status, self._code) if self.mode == "home" else None)

    def _pending(self):
        """A redraw is due (new content, reset, or Bluetooth state/code changed on home)."""
        return self._need_full or self._drawn != self._key()

    def _busy(self):
        """Is a submission being deployed right now? New submissions are refused then."""
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
        if self.status == "BLE off":
            return "off"
        return "green"  # Bluetooth on and idle

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
        if self.status == "Ready":
            qr_px = 0
            try:
                rows = qr_make(PAGE_URL)
            except ValueError:  # URL too long for the on-badge encoder
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
            url = PAGE_URL.split("://", 1)[-1].rstrip("/")
            self._box(scr, cx, QR_Y, cw, 40, F_HEAD, _BLACK, _WHITE, "1. Open in Bluefy")
            self._box(scr, cx, QR_Y + 44, cw, 28, F_SMALL, _RED, _WHITE, url)
            self._box(scr, cx, QR_Y + 96, cw, 40, F_HEAD, _BLACK, _WHITE, "2. Tap Connect, pick")
            self._box(scr, cx, QR_Y + 140, cw, 34, F_BODY, _RED, _WHITE, self._adv_name)
            self._box(scr, cx, QR_Y + 196, cw, 40, F_HEAD, _BLACK, _WHITE, "3. Enter code")
            self._box(scr, cx, QR_Y + 240, cw, 60, F_TITLE, _RED, _WHITE, self._code)
        else:
            cx, cw = 60, 960 - 24 - 60
            _frame(fb, cx, QR_Y, cw, 240)
            msg = {
                "Failed": "Bluetooth failed\n\nPress Left Btn\nto retry",
            }.get(self.status, "Bluetooth is off\n\nPress Left Btn\nto turn it on")
            self._box(scr, cx + 10, QR_Y + 20, cw - 20, 200, F_BODY, _BLACK, _WHITE, msg)
        self._box(scr, cx, 560, cw, 60, F_SMALL, _BLACK, _WHITE,
                  "Left Btn: Bluetooth on / off\nRight Btn: clear the screen, new code")

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
                print("webnametag: old screen delete failed", ex)
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
            if self.status != "Ready":  # first launch, or retry after "Failed"/"BLE off"
                self._ble_enable()
            self._need_full = True  # draw below, once the panel is idle
            self._led_state = None  # the launcher blanked the LEDs; re-apply ours
            return

        if UserA.pressed():
            if self.status == "Ready":
                self._ble_disable()
            else:  # off, or failed: (re)start
                self._ble_disable()
                self._ble_enable()
        if UserB.pressed() and not self._busy():  # ignore presses while already working
            self.mode = "home"
            self.name_text = ""
            self._photo = None
            self._need_full = True
            if self.status == "Ready":
                self._new_code()  # a fresh code; an already-paired phone stays connected

        # A phone that vanished mid-upload must not wedge the badge in "busy".
        if self._receiving and time.ticks_diff(time.ticks_ms(), self._rx_ms) > RX_TIMEOUT_MS:
            print("webnametag: photo upload timed out")
            self._abort_rx()

        self._refresh_status()
        self._update_leds()

        # epd.run() clears its "update requested" flag when a refresh finishes, so a
        # display.update() made mid-refresh would be lost. Only draw when idle;
        # anything still dirty is retried on the next tick.
        if self._display_busy():
            return

        if self._pending():
            self._redraw()
