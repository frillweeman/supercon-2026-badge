"""Web Nametag: join WiFi, serve a tiny form, show the submitted name.

UserA / UserB toggle the WiFi radio. Test app: no persistence, no images.
"""

import asyncio as aio
import gc
import sys
import time

import lvgl
import network

from apps.base_app import BaseApp
from lib.button import UserA, UserB
from lib.epd import display

SSID = "Supercon"  # assumption: change if the real SSID differs
PASSWORD = "your-password"
MAX_NAME = 24
CONNECT_TIMEOUT_MS = 20000

_WHITE = lvgl.color_hex(0xFFFFFF)
_BLACK = lvgl.color_hex(0x000000)
_RED = lvgl.color_hex(0xFF0000)

_PAGE = (
    "<!DOCTYPE html><html><head><meta name=viewport content='width=device-width'>"
    "<title>Badge name</title></head><body style='font-family:sans-serif'>"
    "<h2>Badge name</h2><p>Current: <b>{name}</b></p>"
    "<form method=POST action=/><input name=name maxlength={max} autofocus>"
    " <button>Set</button></form></body></html>"
)


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


def _escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class WebNametagApp(BaseApp):
    def __init__(self, name: str | None = None) -> None:
        self.name = "Web Nametag"
        self.sleep_ms = 100
        self.launched = False

        self.name_text = ""
        self.status = "WiFi off"
        self.ip = ""
        self._wifi_on = False
        self._connect_start = 0
        self._sta = None
        self._server = None
        self._drawn = None  # snapshot of what is on screen
        self._built = False

    # ---- WiFi ----------------------------------------------------------
    def _wifi_enable(self):
        gc.collect()
        print("webnametag: free before wifi:", gc.mem_free())
        try:
            if self._sta is None:
                self._sta = network.WLAN(network.STA_IF)
            self._sta.active(True)
            self._sta.connect(SSID, PASSWORD)
            self._wifi_on = True
            self._connect_start = time.ticks_ms()
            self.status = "Connecting..."
            print("webnametag: free after wifi:", gc.mem_free())
        except Exception as ex:  # MemoryError, OSError, ...
            print("webnametag: wifi enable failed")
            sys.print_exception(ex)
            self._wifi_on = False
            self.status = "Failed"
            gc.collect()

    def _wifi_disable(self):
        if self._server is not None:
            self._server.close()
            self._server = None
        try:
            if self._sta is not None:
                self._sta.disconnect()
                self._sta.active(False)
        except Exception as ex:
            sys.print_exception(ex)
        self._wifi_on = False
        self.ip = ""
        self.status = "WiFi off"
        gc.collect()

    def _poll_wifi(self):
        if not self._wifi_on or self._sta is None:
            return
        if self.status == "Connecting...":
            if self._sta.isconnected():
                self.ip = self._sta.ifconfig()[0]
                self.status = "Connected"
                print("webnametag: connected, ip", self.ip)
                aio.create_task(self._start_server())
            elif time.ticks_diff(time.ticks_ms(), self._connect_start) > CONNECT_TIMEOUT_MS:
                print("webnametag: connect timed out")
                self.status = "Failed"
                try:
                    self._sta.disconnect()
                except Exception:
                    pass
        elif self.status == "Connected" and not self._sta.isconnected():
            self.ip = ""
            self.status = "Connecting..."
            self._connect_start = time.ticks_ms()

    # ---- HTTP server ---------------------------------------------------
    async def _start_server(self):
        try:
            server = await aio.start_server(self._handle, "0.0.0.0", 80)
        except Exception as ex:
            print("webnametag: server start failed")
            sys.print_exception(ex)
            self.status = "Server failed"
            return
        if self._wifi_on:
            self._server = server
        else:  # toggled off while starting
            server.close()

    async def _handle(self, reader, writer):
        try:
            line = await aio.wait_for(reader.readline(), 5)
            parts = line.split()
            method = parts[0] if parts else b""
            clen = 0
            for _ in range(40):
                h = await aio.wait_for(reader.readline(), 5)
                if h in (b"\r\n", b"\n", b""):
                    break
                if h.lower().startswith(b"content-length:"):
                    clen = int(h.split(b":", 1)[1])
            if method == b"POST":
                body = await aio.wait_for(reader.readexactly(min(clen, 256)), 5)
                for field in body.split(b"&"):
                    if field.startswith(b"name="):
                        self.name_text = _urldecode(field[5:])[:MAX_NAME]
                        print("webnametag: name ->", repr(self.name_text))
                writer.write(b"HTTP/1.1 303 See Other\r\nLocation: /\r\nConnection: close\r\n\r\n")
            else:
                page = _PAGE.format(name=_escape(self.name_text), max=MAX_NAME)
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n"
                )
                writer.write(page.encode())
            await writer.drain()
        except Exception as ex:
            print("webnametag: request error", ex)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ---- Display -------------------------------------------------------
    def _build_screen(self):
        scr = lvgl.obj()
        scr.remove_style_all()
        scr.set_size(lvgl.pct(100), lvgl.pct(100))
        scr.set_style_bg_color(_WHITE, 0)
        scr.set_style_bg_opa(lvgl.OPA.COVER, 0)

        self._status_lbl = lvgl.label(scr)
        self._status_lbl.set_style_text_font(lvgl.font_montserrat_24, 0)
        self._status_lbl.set_style_text_color(_BLACK, 0)
        self._status_lbl.align(lvgl.ALIGN.TOP_LEFT, 20, 20)

        self._ip_lbl = lvgl.label(scr)
        self._ip_lbl.set_style_text_font(lvgl.font_montserrat_24, 0)
        self._ip_lbl.set_style_text_color(_RED, 0)
        self._ip_lbl.align(lvgl.ALIGN.TOP_LEFT, 20, 60)

        self._name_lbl = lvgl.label(scr)
        self._name_lbl.set_style_text_font(lvgl.font_montserrat_48, 0)
        self._name_lbl.set_style_text_color(_BLACK, 0)
        self._name_lbl.align(lvgl.ALIGN.CENTER, 0, 0)

        hint = lvgl.label(scr)
        hint.set_style_text_font(lvgl.font_montserrat_16, 0)
        hint.set_style_text_color(_BLACK, 0)
        hint.set_text("User A/B: WiFi on/off")
        hint.align(lvgl.ALIGN.BOTTOM_MID, 0, -10)

        self._scr = scr
        self._built = True

    def _redraw(self):
        snap = (self.status, self.ip, self.name_text)
        self._status_lbl.set_text("WiFi: " + self.status)
        self._ip_lbl.set_text(("http://" + self.ip) if self.ip else "")
        self._name_lbl.set_text(self.name_text)
        lvgl.refr_now(display.lv_display)
        display.update()
        self._drawn = snap

    # ---- App loop ------------------------------------------------------
    def run(self):
        # The display is only touched from here, and run() only executes while
        # this app is selected, so a name submitted while away is drawn on re-entry.
        if self.launched:
            if not self._built:
                self._build_screen()
            lvgl.screen_load(self._scr)
            if not self._wifi_on:  # first launch, or retry after "Failed"/"WiFi off"
                self._wifi_enable()
            self._redraw()
            return

        if UserA.pressed() or UserB.pressed():
            if self._wifi_on:
                self._wifi_disable()
            else:
                self._wifi_enable()

        self._poll_wifi()

        if self._drawn != (self.status, self.ip, self.name_text):
            self._redraw()
