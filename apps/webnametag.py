"""Web Nametag: join WiFi, serve a tiny web page, show a typed name or an uploaded photo.

UserA / UserB toggle the WiFi radio. Test app: nothing is persisted.

A status bar (WiFi status left, IP right) is always shown across the top BAR_H rows.
The web page crops and dithers photos to 1-bit black/white in the browser for the area
below the bar and POSTs the raw black plane (960 x PHOTO_H, 120 bytes/row, MSB first,
1 = black) to /photo. The badge copies it into the black framebuffer below the bar;
LVGL only draws the bar and the name box.
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

# WiFi settings live in data/webnametag_config.py (untracked; see the .example.py).
# A failed import here would break app_switcher's `from apps import ...` for ALL apps at
# boot, so a missing/broken config must not raise: the app just reports "No config".
try:
    from data import webnametag_config as _cfg

    SSID = _cfg.SSID
    PASSWORD = _cfg.PASSWORD
    HOSTNAME = getattr(_cfg, "HOSTNAME", "billboard")
    CONNECT_TIMEOUT_MS = getattr(_cfg, "CONNECT_TIMEOUT_MS", 20000)
except Exception as _ex:
    print("webnametag: no usable data/webnametag_config.py:", _ex)
    SSID = PASSWORD = None
    HOSTNAME = "billboard"
    CONNECT_TIMEOUT_MS = 20000
# Optional HEIC fallback: only fetched if the browser can't decode a HEIC/HEIF photo itself.
# Pinned with a Subresource Integrity hash (heic2any 0.0.4, MIT, ~1.3 MB).
HEIC_URL = "https://cdn.jsdelivr.net/npm/heic2any@0.0.4/dist/heic2any.min.js"
HEIC_SRI = "sha384-OTofQ0MEeiSgh62havBcemCIK0gqj809wX6UA0uPISNMRnR6NZyCdGzX3SbLrgwL"
MAX_NAME = 24
BAR_H = 32  # status bar height in px, always drawn at the top
ROW_BYTES = 960 // 8  # 120 bytes per framebuffer row
PHOTO_H = 672 - BAR_H  # 640 rows left for the photo
PHOTO_BYTES = ROW_BYTES * PHOTO_H  # 76800, one 1-bit plane
PHOTO_OFFSET = ROW_BYTES * BAR_H  # where the photo starts in the framebuffer

_WHITE = lvgl.color_hex(0xFFFFFF)
_BLACK = lvgl.color_hex(0x000000)
_RED = lvgl.color_hex(0xFF0000)

# %NAME%, %MAX%, %H%, %QH%, %HEIC_URL% and %HEIC_SRI% are substituted with str.replace (the JS has braces, so no .format).
_PAGE = """<!DOCTYPE html><html><head>
<meta name=viewport content='width=device-width,initial-scale=1'><title>Badge</title></head>
<body style='font-family:sans-serif;max-width:520px;margin:auto;padding:8px'>
<h2>Badge name</h2><p>Current: <b>%NAME%</b></p>
<form method=POST action=/><input name=name maxlength=%MAX% autofocus> <button>Set</button></form>
<h2>Photo (black &amp; white)</h2>
<input type=file id=f accept='image/*,.heic,.heif'> <span id=m></span>
<div id=ed style='display:none'>
<canvas id=c width=480 height=%QH% style='width:100%;border:1px solid #000;touch-action:none'></canvas>
<p>Zoom <input type=range id=z min=0 max=100 value=0 style='width:70%'></p>
<p>Drag to position. The frame is the area under the badge's status bar; nothing is stretched.</p>
<button id=s>Send to badge</button>
</div>
<script>
var W=960,H=%H%,P=W/2,Q=H/2,img,sc,mn,ox,oy,d=null;
var f=document.getElementById('f'),c=document.getElementById('c'),x=c.getContext('2d'),
z=document.getElementById('z'),m=document.getElementById('m');
function draw(){
 ox=Math.min(0,Math.max(P-img.width*sc,ox));oy=Math.min(0,Math.max(Q-img.height*sc,oy));
 x.fillStyle='#fff';x.fillRect(0,0,P,Q);x.drawImage(img,ox,oy,img.width*sc,img.height*sc);}
function heic(){return window.heic2any?Promise.resolve():new Promise(function(ok,no){
 var s=document.createElement('script');s.src='%HEIC_URL%';s.integrity='%HEIC_SRI%';
 s.crossOrigin='anonymous';s.onload=ok;s.onerror=no;document.head.appendChild(s);});}
function load(file){return createImageBitmap(file).catch(function(err){
 if(!/[.]hei[cf]$/i.test(file.name)&&!/hei[cf]/i.test(file.type))throw err;
 m.textContent='Loading HEIC converter (needs internet)...';
 return heic().then(function(){return heic2any({blob:file,toType:'image/jpeg',quality:0.9});})
 .then(function(r){return createImageBitmap(Array.isArray(r)?r[0]:r);});});}
f.onchange=function(){if(!f.files[0])return;m.textContent='Loading...';
 load(f.files[0]).then(function(b){
 img=b;mn=Math.max(P/b.width,Q/b.height);sc=mn;z.value=0;
 ox=(P-b.width*sc)/2;oy=(Q-b.height*sc)/2;
 document.getElementById('ed').style.display='block';m.textContent='';draw();})
 .catch(function(){m.textContent='Could not read that image.';});};
z.oninput=function(){var cx=(P/2-ox)/sc,cy=(Q/2-oy)/sc;
 sc=mn*(1+3*z.value/100);ox=P/2-cx*sc;oy=Q/2-cy*sc;draw();};
c.onpointerdown=function(e){d=[e.clientX,e.clientY,ox,oy];c.setPointerCapture(e.pointerId);};
c.onpointermove=function(e){if(!d)return;var k=P/c.getBoundingClientRect().width;
 ox=d[2]+(e.clientX-d[0])*k;oy=d[3]+(e.clientY-d[1])*k;draw();};
c.onpointerup=function(){d=null;};
document.getElementById('s').onclick=function(){
 var o=document.createElement('canvas');o.width=W;o.height=H;var g=o.getContext('2d');
 g.fillStyle='#fff';g.fillRect(0,0,W,H);
 g.drawImage(img,ox*2,oy*2,img.width*sc*2,img.height*sc*2);
 var p=g.getImageData(0,0,W,H).data,n=W*H,e=new Float32Array(n),b=new Uint8Array(n/8),i,y,X;
 for(i=0;i<n;i++)e[i]=0.299*p[4*i]+0.587*p[4*i+1]+0.114*p[4*i+2];
 for(y=0;y<H;y++)for(X=0;X<W;X++){i=y*W+X;var v=e[i],q=v<128?0:255,r=v-q;
  if(q==0)b[i>>3]|=128>>(i&7);
  if(X<W-1)e[i+1]+=r*7/16;
  if(y<H-1){if(X>0)e[i+W-1]+=r*3/16;e[i+W]+=r*5/16;if(X<W-1)e[i+W+1]+=r/16;}}
 m.textContent='Sending...';
 fetch('/photo',{method:'POST',body:b,headers:{'Content-Type':'application/octet-stream'}})
 .then(function(r){return r.text();}).then(function(t){m.textContent=t;})
 .catch(function(){m.textContent='Failed';});};
</script></body></html>"""


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
        self.mode = "name"  # "name" or "photo"
        self._photo = None  # bytearray(PHOTO_BYTES) while in photo mode
        self._need_full = False
        self._wifi_on = False
        self._connect_start = 0
        self._sta = None
        self._server = None
        self._drawn = None  # snapshot of what is on screen
        self._built = False

    # ---- WiFi ----------------------------------------------------------
    def _wifi_enable(self):
        if not SSID:
            self._wifi_on = False
            self.status = "No config"
            return
        gc.collect()
        print("webnametag: free before wifi:", gc.mem_free())
        try:
            if self._sta is None:
                self._sta = network.WLAN(network.STA_IF)
            try:  # must be set before connect(); also what mDNS announces
                network.hostname(HOSTNAME)
            except Exception as ex:
                print("webnametag: could not set hostname:", ex)
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
            if method == b"POST" and path == b"/photo":
                if clen != PHOTO_BYTES:
                    msg = b"Bad photo size"
                    writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n" + msg)
                else:
                    self._photo = None
                    gc.collect()
                    self._photo = await self._recv_photo(reader, clen)
                    self.mode = "photo"
                    self._need_full = True
                    print("webnametag: photo received")
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n"
                        b"Photo received. The panel takes a while to refresh."
                    )
            elif method == b"POST":
                body = await aio.wait_for(reader.readexactly(min(clen, 256)), 5)
                for field in body.split(b"&"):
                    if field.startswith(b"name="):
                        self.name_text = _urldecode(field[5:])[:MAX_NAME]
                        print("webnametag: name ->", repr(self.name_text))
                if self.mode == "photo":
                    self.mode = "name"
                    self._photo = None
                    self._need_full = True
                writer.write(b"HTTP/1.1 303 See Other\r\nLocation: /\r\nConnection: close\r\n\r\n")
            else:
                page = (
                    _PAGE.replace("%NAME%", _escape(self.name_text))
                    .replace("%MAX%", str(MAX_NAME))
                    .replace("%H%", str(PHOTO_H))
                    .replace("%QH%", str(PHOTO_H // 2))
                    .replace("%HEIC_URL%", HEIC_URL)
                    .replace("%HEIC_SRI%", HEIC_SRI)
                )
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
        # LVGL -> framebuffer conversion (epd._lv_flush) is a pure-Python per-pixel
        # loop, so repainting the full 960x672 panel takes ages. Keep the screen
        # transparent over a pre-cleared white framebuffer, and draw text only in
        # small fixed-size OPAQUE white boxes so stale pixels get covered.
        scr = lvgl.obj()
        scr.remove_style_all()
        scr.set_size(lvgl.pct(100), lvgl.pct(100))
        scr.set_style_bg_opa(lvgl.OPA.TRANSP, 0)

        def box(w, h, font, color, align, x, y):
            b = lvgl.obj(scr)
            b.remove_style_all()
            b.set_size(w, h)
            b.set_style_bg_color(_WHITE, 0)
            b.set_style_bg_opa(lvgl.OPA.COVER, 0)
            b.align(align, x, y)
            lbl = lvgl.label(b)
            lbl.set_style_text_font(font, 0)
            lbl.set_style_text_color(color, 0)
            lbl.set_text("")
            return b, lbl

        # Status bar (red text): WiFi status left, "hostname.local  ip" right, BAR_H
        # tall, always shown. 300 + 630 + margins fit in 960 px without overlapping.
        self._status_box, self._status_lbl = box(
            300, BAR_H, lvgl.font_montserrat_24, _RED, lvgl.ALIGN.TOP_LEFT, 10, 0)
        self._ip_box, self._ip_lbl = box(
            630, BAR_H, lvgl.font_montserrat_24, _RED, lvgl.ALIGN.TOP_RIGHT, -10, 0)
        # Name box is centred in the area below the bar; only used in name mode.
        self._name_box, self._name_lbl = box(
            900, 70, lvgl.font_montserrat_48, _BLACK, lvgl.ALIGN.CENTER, 0, BAR_H // 2)

        self._scr = scr
        self._built = True

    def _set_texts(self):
        # When connected, show which network we're on instead of the word "Connected".
        shown = SSID[:16] if self.status == "Connected" and SSID else self.status
        self._status_lbl.set_text("WiFi: " + shown)
        self._status_lbl.align(lvgl.ALIGN.LEFT_MID, 0, 0)
        self._ip_lbl.set_text((HOSTNAME + ".local  " + self.ip) if self.ip else "")
        self._ip_lbl.align(lvgl.ALIGN.RIGHT_MID, 0, 0)
        if self.mode == "name":
            self._name_lbl.set_text(self.name_text)
            self._name_lbl.align(lvgl.ALIGN.CENTER, 0, 0)

    def _redraw(self, full=False):
        """Render only what changed. full=True clears the panel and redraws everything."""
        new = (self.status, self.ip, self.name_text)
        if full:
            display.clear()  # framebuffers to white; LVGL only paints our boxes
            if self.mode == "photo" and self._photo is not None:
                # Photo goes below the bar; red plane stays empty (black & white only).
                display._framebuffer_black[PHOTO_OFFSET : PHOTO_OFFSET + PHOTO_BYTES] = self._photo
            lvgl.screen_load(self._scr)
        self._set_texts()
        if full:
            self._scr.update_layout()
            # screen_load queued a full-panel dirty area; drop it, keep only our boxes.
            display.lv_display.inv_p = 0
            boxes = [self._status_box, self._ip_box]
            if self.mode == "name":
                boxes.append(self._name_box)
            for b in boxes:
                b.invalidate()
        lvgl.refr_now(display.lv_display)
        display.update()
        self._drawn = new
        self._need_full = False

    # ---- App loop ------------------------------------------------------
    def run(self):
        # The display is only touched from here, and run() only executes while
        # this app is selected, so a name or photo submitted while away is drawn on re-entry.
        if self.launched:
            if not self._built:
                self._build_screen()
            if not self._wifi_on:  # first launch, or retry after "Failed"/"WiFi off"
                self._wifi_enable()
            self._need_full = True  # draw below, once the panel is idle
            return

        if UserA.pressed() or UserB.pressed():
            if self._wifi_on:
                self._wifi_disable()
            else:
                self._wifi_enable()

        self._poll_wifi()

        # epd.run() clears its "update requested" flag when a refresh finishes, so a
        # display.update() made mid-refresh would be lost. Only draw when idle;
        # anything still dirty is retried on the next tick.
        if not display.is_done_updating():
            return

        if self._need_full:
            self._redraw(full=True)
        elif self._drawn != (self.status, self.ip, self.name_text):
            self._redraw()
