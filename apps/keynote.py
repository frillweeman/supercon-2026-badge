"""Keynote Remote: use the badge as a BLE clicker for Keynote on a Mac.

The badge is a BLE peripheral advertising one GATT service. A small bridge on the Mac
(tools/keynote_bridge.py) connects to it, subscribes to BTN_UUID, drives Keynote with
AppleScript, and writes the slide state back to STATE_UUID.

  Left Btn  (UserA) = next slide         Right Btn (UserB) = previous slide
  (If Keynote is not presenting, the bridge starts the show on "next".)
  Root Access SAO: on start the app probes the shared SAO I2C bus for address 0x50. If it
  answers, GPIO1 of any SAO port = B1 and GPIO2 = B2 (custom commands, not slide control; active high, low at rest, internal pull-downs);
  if not, the SAO is ignored. (I2C is shared, so it can't tell which port the SAO is in.)

The e-paper is never touched on launch (like webnametag, which waits for WiFi before its
first draw): it is written only once a Mac is connected AND has a Keynote document open,
and again only if that document changes. Disconnecting does not redraw.

LED bar: orange chase (webnametag's loading bar) until ready; solid green = ready and
idle (green is used for nothing else). While presenting, slide position is one bright LED
at (slide - 1) % 8, wrapping around, with a different colour each lap of 8 slides (blue,
magenta, white; never green or orange). The other LEDs are dim where a slide exists in that
lap's window of 8, and off past the end of the deck.

BLE is only on while this app is open: the launcher calls on_exit() when you go to the menu.

Wire format (little-endian):
  BTN_UUID   badge -> Mac, notify : b"N" (next), b"P" (previous), b"1" (B1) or b"2" (B2)
  STATE_UUID Mac -> badge, write  : struct "<BHH" = playing(0/1), current slide, slide count
  NAME_UUID  Mac -> badge, write  : UTF-8 name of the front Keynote document (max 20 bytes)
  ROOT_UUID  Mac -> badge, write  : 1 byte, 0-49 = Root Access SAO animation, 50 = all off.
                                    Sent by the bridge from a "root:N" field in the slide's
                                    presenter notes; written to the SAO over I2C (0x50) if one
                                    was detected, otherwise dropped. Slides without the field
                                    send nothing, so the lights stay as they were.
"""

import gc
import struct
import sys
import time

import bluetooth
import lvgl
from machine import I2C, Pin

from apps.base_app import BaseApp
from lib.button import UserA, UserB
from lib.epd import display
from lib.led_bar import NUM_LEDS, leds

# Reuse webnametag's LVGL box + display-busy helpers (unbound, so its app class is not
# imported into this namespace and the launcher does not instantiate it twice).
from apps import webnametag as _wn

SVC_UUID = bluetooth.UUID("9f1c0001-4b1d-4e6f-8a52-4b6579524d31")
BTN_UUID = bluetooth.UUID("9f1c0002-4b1d-4e6f-8a52-4b6579524d31")
STATE_UUID = bluetooth.UUID("9f1c0003-4b1d-4e6f-8a52-4b6579524d31")
NAME_UUID = bluetooth.UUID("9f1c0004-4b1d-4e6f-8a52-4b6579524d31")
ROOT_UUID = bluetooth.UUID("9f1c0005-4b1d-4e6f-8a52-4b6579524d31")
ADV_NAME = b"Keynote Remote"

_IRQ_CENTRAL_CONNECT = 1
_IRQ_CENTRAL_DISCONNECT = 2
_IRQ_GATTS_WRITE = 3

_SAO_ADDR = 0x50  # Root Access SAO (I2C slave)
_SAO_PORTS = ((2, 42), (4, 5), (7, 6))  # (GPIO1, GPIO2) of SAO1..3, see lib/sao.py
_SAO_CMDS = (b"1", b"2")  # SAO GPIO1 = B1, GPIO2 = B2 (custom commands, handled by the bridge)

_WHITE = lvgl.color_hex(0xFFFFFF)
_BLACK = lvgl.color_hex(0x000000)
_RED = lvgl.color_hex(0xFF0000)
F_BODY = lvgl.font_montserrat_24
F_BIG = lvgl.font_montserrat_48
_DIM = (0, 0, 1)  # an existing, non-current slide (1 is the lowest non-zero level)
_OFF = (0, 0, 0)  # no slide here (past the end of the deck)
_LAP_COLORS = ((0, 4, 14), (12, 0, 8), (8, 8, 8))  # blue, magenta, white


def _adv_payload():
    flags = b"\x02\x01\x06"
    svc = bytes(SVC_UUID)  # 16 bytes, little-endian
    return flags + bytes((len(svc) + 1, 0x07)) + svc  # 0x07 = complete 128-bit UUID list


def _scan_resp():
    return bytes((len(ADV_NAME) + 1, 0x09)) + ADV_NAME  # 0x09 = complete local name


class KeynoteApp(BaseApp):
    def __init__(self, name: str | None = None) -> None:
        self.name = "Keynote Remote"
        self.sleep_ms = 50
        self.launched = False
        self._ble = None
        self._conn = None
        self._btn_h = None
        self._state_h = None
        self._name_h = None
        self._root_h = None
        self._root_pending = None  # last "root" value from the Mac, not yet sent to the SAO
        self._sao_i2c = None
        # Latest state from the Mac: (playing, current, total)
        self._state = (0, 0, 0)
        self._doc = ""
        self._drawn = None
        self._scr = None
        self._boxes = []
        self._led_key = None
        self._sao_pins = []
        self._sao_prev = []

    # ---- BLE -----------------------------------------------------------
    def _start_ble(self):
        if self._ble is not None:
            return
        gc.collect()
        ble = bluetooth.BLE()
        ble.active(True)
        ble.irq(self._irq)
        ((self._btn_h, self._state_h, self._name_h, self._root_h),) = ble.gatts_register_services((
            (SVC_UUID, (
                (BTN_UUID, bluetooth.FLAG_NOTIFY),
                (STATE_UUID, bluetooth.FLAG_WRITE | bluetooth.FLAG_WRITE_NO_RESPONSE),
                (NAME_UUID, bluetooth.FLAG_WRITE | bluetooth.FLAG_WRITE_NO_RESPONSE),
                (ROOT_UUID, bluetooth.FLAG_WRITE | bluetooth.FLAG_WRITE_NO_RESPONSE),
            )),
        ))
        self._ble = ble
        self._conn = None
        self._advertise()

    def _stop_ble(self):
        ble, self._ble = self._ble, None
        self._conn = None
        if ble is None:
            return
        try:
            ble.gap_advertise(None)
            ble.irq(None)
            ble.active(False)
        except Exception as ex:
            print("keynote: ble stop failed", ex)
        gc.collect()

    def _advertise(self):
        self._ble.gap_advertise(100_000, adv_data=_adv_payload(), resp_data=_scan_resp())

    def _irq(self, event, data):
        # Keep this tiny and allocation-light; it runs in the BLE scheduler context.
        if event == _IRQ_CENTRAL_CONNECT:
            self._conn = data[0]
        elif event == _IRQ_CENTRAL_DISCONNECT:
            self._conn = None
            self._state = (0, 0, 0)
            self._doc = ""
            if self._ble is not None:
                self._advertise()
        elif event == _IRQ_GATTS_WRITE:
            _, attr = data
            if attr == self._state_h:
                raw = self._ble.gatts_read(attr)
                if len(raw) >= 5:
                    self._state = struct.unpack("<BHH", raw[:5])
            elif attr == self._root_h:
                raw = self._ble.gatts_read(attr)
                if len(raw) >= 1:
                    self._root_pending = raw[0]  # I2C happens in run(), not in the IRQ
            elif attr == self._name_h:
                self._doc = bytes(self._ble.gatts_read(attr)).decode("utf-8", "ignore").strip("\x00")

    def _send(self, cmd: bytes):
        if self._ble is None or self._conn is None:
            return
        try:
            self._ble.gatts_notify(self._conn, self._btn_h, cmd)
        except Exception as ex:
            print("keynote: notify failed", ex)

    def _detect_sao(self):
        """Probe the shared SAO I2C bus for the Root Access board (0x50). If it answers, watch
        the GPIO pair of every SAO port (the bus is shared, so I2C can't say which port it is
        in); otherwise the SAO is ignored. Run once per app start."""
        self._sao_pins = []
        self._sao_prev = []
        found = False
        self._sao_i2c = None
        try:
            # Own bus at 100 kHz (the only speed the SAO README says it was verified at;
            # lib.sao runs 400 kHz). Retry a few times in case the SAO is still booting.
            i2c = I2C(0, scl=Pin(18), sda=Pin(17), freq=100_000)
            for _ in range(3):
                devs = i2c.scan()
                print("keynote: SAO i2c scan", [hex(a) for a in devs])
                if _SAO_ADDR in devs:
                    i2c.readfrom(_SAO_ADDR, 2)  # README: a 2-byte read has no side effects
                    found = True
                    break
                time.sleep_ms(100)
        except Exception as ex:
            print("keynote: SAO probe error")
            sys.print_exception(ex)
        if not found:
            print("keynote: no SAO at 0x50, ignoring SAO buttons")
            return
        print("keynote: SAO found at 0x50")
        self._sao_i2c = i2c
        for pair in _SAO_PORTS:
            for p in pair:
                self._sao_pins.append(Pin(p, Pin.IN, Pin.PULL_DOWN))
                self._sao_prev.append(0)

    def _apply_root(self):
        """Send the latest root value (0-49 = animation, 50 = all off) to the SAO, once."""
        val, self._root_pending = self._root_pending, None
        if val is None or self._sao_i2c is None or val > 50:
            return
        try:
            self._sao_i2c.writeto(_SAO_ADDR, bytes((val,)))
        except Exception as ex:
            print("keynote: SAO write failed", ex)

    def _poll_sao(self):
        """GPIO1 of any port = B1, GPIO2 = B2. Active high (low at rest); fires on the
        low->high edge. The 50 ms loop tick doubles as the debounce."""
        for i, pin in enumerate(self._sao_pins):
            v = pin.value()
            if v == 1 and self._sao_prev[i] == 0:
                self._send(_SAO_CMDS[i & 1])  # even index = GPIO1, odd = GPIO2
            self._sao_prev[i] = v

    def on_exit(self):
        """Called by the launcher when the user leaves this app for the menu."""
        self._stop_ble()
        self._drawn = None
        self._led_key = None

    # ---- Display ---------------------------------------------------------
    def _view(self):
        """What the screen should show, or None until a Mac is connected with a Keynote
        document open (nothing is drawn then). Slide changes deliberately do not change this."""
        if self._conn is None or not self._doc:
            return None
        return self._doc

    def _display_busy(self):
        return _wn.WebNametagApp._display_busy(self)

    def _box(self, parent, x, y, w, h, font, color, text):
        _wn.WebNametagApp._box(self, parent, x, y, w, h, font, color, _BLACK, text)

    def _redraw(self, doc):
        display.clear()
        display.black.fill(1)  # black background; text boxes below are white/red on black
        old = self._scr
        scr = lvgl.obj()
        scr.remove_style_all()
        scr.set_size(lvgl.pct(100), lvgl.pct(100))
        scr.set_style_bg_opa(lvgl.OPA.TRANSP, 0)
        self._boxes = []
        self._box(scr, 180, 60, 600, 60, F_BIG, _RED, "Keynote Remote")
        self._box(scr, 130, 170, 700, 40, F_BODY, _WHITE, "Connected to Mac")
        self._box(scr, 130, 220, 700, 60, F_BIG, _WHITE, doc)
        self._box(scr, 130, 320, 700, 40, F_BODY, _WHITE, "LED bar = slide position (new colour each lap of 8)")
        self._box(scr, 130, 440, 700, 40, F_BODY, _WHITE, "Left Button starts the show.")
        self._box(scr, 24, 580, 300, 40, F_BODY, _WHITE, "Left Btn: Next")
        self._box(scr, 636, 580, 300, 40, F_BODY, _WHITE, "Right Btn: Prev")
        self._scr = scr
        lvgl.screen_load(scr)
        if old is not None:
            try:
                old.delete()
            except Exception as ex:
                print("keynote: old screen delete failed", ex)
        scr.update_layout()
        display.lv_display.inv_p = 0  # screen_load dirtied everything; paint only our boxes
        for b in self._boxes:
            b.invalidate()
        lvgl.refr_now(display.lv_display)
        display.update()
        self._drawn = doc
        gc.collect()

    def _leds(self):
        """Loading chase until a Mac is connected with a Keynote document; then solid green
        (ready), or while presenting one bright LED at (slide - 1) % 8 with a colour per lap."""
        playing, cur, total = self._state
        if self._view() is None:
            key = "wait"
        elif not playing or cur < 1:
            key = "ready"
        else:
            lap = (cur - 1) // NUM_LEDS
            # (marker LED, lap colour, how many slides exist in this lap's window of 8)
            key = (cur - 1) % NUM_LEDS, lap % len(_LAP_COLORS), max(0, min(NUM_LEDS, total - lap * NUM_LEDS))
        if key == self._led_key:
            return
        self._led_key = key
        try:
            if key == "wait":
                leds.unpause()  # the launcher's menu pauses the sequencer and never resumes it
                leds.set_sequence(_wn._chase)
                return
            leds.clear_sequence()
            if key == "ready":
                leds.fill((0, 2, 0))
            else:
                # LED i = slide (lap * 8 + i + 1): dim if that slide exists, off past the end.
                frame = [_DIM if i < key[2] else _OFF for i in range(NUM_LEDS)]
                frame[key[0]] = _LAP_COLORS[key[1]]
                leds.set(frame)
                leds.write()
        except Exception as ex:
            sys.print_exception(ex)

    # ---- App loop --------------------------------------------------------
    def run(self):
        if self.launched:
            try:
                self._start_ble()
            except Exception as ex:
                print("keynote: ble start failed")
                sys.print_exception(ex)
            self._drawn = None
            self._led_key = None
            self._detect_sao()
            self._leds()
            return

        if UserA.pressed():
            self._send(b"N")
        if UserB.pressed():
            self._send(b"P")
        self._poll_sao()
        self._apply_root()

        self._leds()
        view = self._view()
        if view is not None and view != self._drawn:
            # epd.run() drops an update() requested mid-refresh, so only draw when idle.
            # A newer state simply replaces this one on the next tick.
            if not self._display_busy():
                self._redraw(view)
