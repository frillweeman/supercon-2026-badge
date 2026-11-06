"""Web Nametag: join WiFi, serve a tiny web page, show a typed name or an uploaded photo.

UserA / UserB toggle the WiFi radio. Test app: nothing is persisted.

The LED bar shows the state: green = WiFi connected, red = WiFi failed, orange chase =
connecting or loading a new name/image, dark = WiFi off. While a submission is being
deployed (upload in progress, redraw pending, or panel refreshing) new POSTs are rejected
with 503 and a wait message; nothing is queued.

A status bar (WiFi status left, hostname / IP right, black text) is always shown across
the top BAR_H rows. The web page crops and dithers photos in the browser for the area
below the bar and POSTs them to /photo as raw 1-bit planes (960 x PHOTO_H, 120 bytes/row,
MSB first). Body = black plane (1 = black), optionally followed by a red plane (1 = red);
a pixel is never set in both. The badge copies the planes into the matching framebuffers
below the bar; LVGL only draws the bar and the name box.
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
from lib.led_bar import NUM_LEDS, leds

# WiFi settings live in data/webnametag_config.py (untracked; see the .example.py).
# A failed import here would break app_switcher's `from apps import ...` for ALL apps at
# boot, so a missing/broken config must not raise: the app just reports "No config".
try:
    from data import webnametag_config as _cfg

    SSID = _cfg.SSID
    PASSWORD = _cfg.PASSWORD
    HOSTNAME = getattr(_cfg, "HOSTNAME", "willboard")
    CONNECT_TIMEOUT_MS = getattr(_cfg, "CONNECT_TIMEOUT_MS", 20000)
except Exception as _ex:
    print("webnametag: no usable data/webnametag_config.py:", _ex)
    SSID = PASSWORD = None
    HOSTNAME = "willboard"
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
_FAILED_STATUSES = ("Failed", "Server failed", "No config")

_WHITE = lvgl.color_hex(0xFFFFFF)
_BLACK = lvgl.color_hex(0x000000)


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

# %NAME%, %MAX%, %H%, %QH%, %HEIC_URL% and %HEIC_SRI% are substituted with str.replace (the JS has braces, so no .format).
_PAGE = """<!DOCTYPE html><html><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'>
<meta name=color-scheme content='light dark'><title>Badge</title>
<style>
:root{--bg:#f4f1ea;--card:#fff;--fg:#111;--mut:#666;--ln:#d6d1c4;--ac:#d4001a;--on:#fff}
@media(prefers-color-scheme:dark){:root{--bg:#0b0b0c;--card:#17171a;--fg:#f2f2f2;--mut:#8d8d95;--ln:#2c2c31;--ac:#ff2b3f;--on:#0b0b0c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.4 system-ui,-apple-system,sans-serif;
padding:max(16px,env(safe-area-inset-top)) 16px 32px}
main{max-width:520px;margin:auto}
header{display:flex;align-items:center;gap:10px;margin:4px 0 18px}
header i{width:12px;height:12px;border-radius:50%;background:var(--ac);flex:none}
h1{font-size:13px;letter-spacing:.14em;text-transform:uppercase;margin:0;color:var(--mut);font-weight:600}
section{background:var(--card);border:1px solid var(--ln);border-radius:14px;padding:18px;margin-bottom:14px}
h2{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);margin:0 0 12px;font-weight:600}
.cur{font-size:26px;font-weight:700;word-break:break-word;margin:0 0 14px;min-height:1.2em}
.row{display:flex;gap:8px}
input[type=text]{flex:1;min-width:0;font:inherit;font-size:16px;color:var(--fg);background:var(--bg);
border:1px solid var(--ln);border-radius:10px;padding:12px 14px;outline:0}
input[type=text]:focus{border-color:var(--ac)}
button,label.b{display:inline-block;font:inherit;font-weight:600;color:var(--on);background:var(--ac);border:0;
border-radius:10px;padding:12px 18px;cursor:pointer;text-align:center;-webkit-appearance:none}
button:active,label.b:active{opacity:.8}
label.b{display:block;background:transparent;color:var(--fg);border:1px dashed var(--mut);padding:18px}
#f{display:none}
#s{width:100%;margin-top:14px}
#m,#nm{display:block;color:var(--mut);font-size:14px;margin-top:10px;min-height:1.2em}
canvas{width:100%;display:block;border-radius:10px;border:1px solid var(--ln);touch-action:none;background:#fff}
input[type=range]{width:100%;accent-color:var(--ac);margin:14px 0 4px}
.hint{color:var(--mut);font-size:13px;margin:6px 0 0}
.opt{display:flex;align-items:center;gap:10px;margin:12px 0 0}
.opt input[type=range]{margin:0;flex:1}
#pv{margin-top:12px}
#ov{position:fixed;top:0;right:0;bottom:0;left:0;background:rgba(0,0,0,.55);display:none;align-items:center;
justify-content:center;padding:20px;z-index:9}
#ov div{background:var(--card);color:var(--fg);border-radius:14px;padding:22px;max-width:360px;width:100%}
#ov p{margin:0 0 16px;font-size:16px}
#ov button{width:100%}
#toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--fg);color:var(--bg);
padding:10px 16px;border-radius:999px;font-size:14px;display:none;z-index:8;max-width:90%;text-align:center}
</style></head><body><main>
<header><i></i><h1>Supercon Badge</h1></header>
<section><h2>Name</h2><p class=cur id=cur>%NAME%</p>
<form id=nf method=POST action=/ class=row><input id=ni type=text name=name maxlength=%MAX% placeholder='Your name' autocomplete=off>
<button>Set</button></form><span id=nm></span></section>
<section><h2>Photo</h2>
<label class=b for=f>Choose a photo</label>
<input type=file id=f accept='image/*,.heic,.heif'><span id=m></span>
<div id=ed style='display:none'>
<canvas id=c width=480 height=%QH%></canvas>
<input type=range id=z min=0 max=100 value=0 aria-label=Zoom>
<p class=hint>Drag to position, slide to zoom. The frame is the area under the badge's status bar.</p>
<label class=opt><input type=checkbox id=rc checked> Use red</label>
<label class=opt>Red strength <input type=range id=rs min=0 max=100 value=50></label>
<canvas id=pv width=960 height=%H%></canvas>
<p class=hint>Preview of what the badge will show.</p>
<button id=s>Send to badge</button>
</div></section></main>
<div id=ov><div role=alertdialog aria-modal=true><p id=ovt></p><button id=ovb>OK</button></div></div>
<div id=toast></div>
<script>
var W=960,H=%H%,P=W/2,Q=H/2,img,sc,mn,ox,oy,d=null;
var f=document.getElementById('f'),c=document.getElementById('c'),x=c.getContext('2d'),
z=document.getElementById('z'),m=document.getElementById('m');
function draw(){
 ox=Math.min(0,Math.max(P-img.width*sc,ox));oy=Math.min(0,Math.max(Q-img.height*sc,oy));
 x.fillStyle='#fff';x.fillRect(0,0,P,Q);x.drawImage(img,ox,oy,img.width*sc,img.height*sc);
 prev();}
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
 .catch(function(){m.textContent='';alertBox('Could not read that image.');});};
z.oninput=function(){var cx=(P/2-ox)/sc,cy=(Q/2-oy)/sc;
 sc=mn*(1+3*z.value/100);ox=P/2-cx*sc;oy=Q/2-cy*sc;draw();};
c.onpointerdown=function(e){d=[e.clientX,e.clientY,ox,oy];c.setPointerCapture(e.pointerId);};
c.onpointermove=function(e){if(!d)return;var k=P/c.getBoundingClientRect().width;
 ox=d[2]+(e.clientX-d[0])*k;oy=d[3]+(e.clientY-d[1])*k;draw();};
c.onpointerup=function(){d=null;};
var rc=document.getElementById('rc'),rs=document.getElementById('rs'),
pvx=document.getElementById('pv').getContext('2d'),tm;
/* Colours as the panel shows them (white/red are estimates; tune by eye). */
var PAL=[[255,255,255],[0,0,0],[200,30,30]];
/* Error-diffusion dither of RGBA bytes p to white/black(/red). Returns the two planes
   (black then red, 1 bit/px, MSB first, never both set) plus a preview ImageData. */
function dither(p,red,k){
 var n=W*H,h=n/8,e=new Float32Array(n*3),out=new Uint8Array(n/4),pv=new ImageData(W,H),
 q=pv.data,nc=red?3:2,i,j,y,X,ci,R,G,B,best,bd,d,pr,er,eg,eb,l;
 for(i=0;i<n;i++){j=3*i;
  if(red){e[j]=p[4*i];e[j+1]=p[4*i+1];e[j+2]=p[4*i+2];}
  else{l=0.299*p[4*i]+0.587*p[4*i+1]+0.114*p[4*i+2];e[j]=e[j+1]=e[j+2]=l;}}
 function sp(t,f){t*=3;e[t]+=er*f;e[t+1]+=eg*f;e[t+2]+=eb*f;}
 for(y=0;y<H;y++)for(X=0;X<W;X++){i=y*W+X;j=3*i;
  R=Math.min(255,Math.max(0,e[j]));G=Math.min(255,Math.max(0,e[j+1]));B=Math.min(255,Math.max(0,e[j+2]));
  best=0;bd=1e12;
  for(ci=0;ci<nc;ci++){pr=PAL[ci];d=(R-pr[0])*(R-pr[0])+(G-pr[1])*(G-pr[1])+(B-pr[2])*(B-pr[2]);
   if(ci==2)d*=k;if(d<bd){bd=d;best=ci;}}
  pr=PAL[best];er=R-pr[0];eg=G-pr[1];eb=B-pr[2];
  if(best==1)out[i>>3]|=128>>(i&7);else if(best==2)out[h+(i>>3)]|=128>>(i&7);
  q[4*i]=pr[0];q[4*i+1]=pr[1];q[4*i+2]=pr[2];q[4*i+3]=255;
  if(X<W-1)sp(i+1,7/16);
  if(y<H-1){if(X>0)sp(i+W-1,3/16);sp(i+W,5/16);if(X<W-1)sp(i+W+1,1/16);}}
 return{out:out,pv:pv,red:red};}
function crop(){var o=document.createElement('canvas');o.width=W;o.height=H;var g=o.getContext('2d');
 g.fillStyle='#fff';g.fillRect(0,0,W,H);
 g.drawImage(img,ox*2,oy*2,img.width*sc*2,img.height*sc*2);return g.getImageData(0,0,W,H).data;}
function run(){return dither(crop(),rc.checked,Math.pow(2,(50-rs.value)/25));}
function prev(){clearTimeout(tm);tm=setTimeout(function(){if(img)pvx.putImageData(run().pv,0,0);},250);}
var sb=document.getElementById('s');
/* Errors go in a modal (inline text can be scrolled out of view); success is a toast. */
var ov=document.getElementById('ov'),ovt=document.getElementById('ovt'),tt=document.getElementById('toast'),tk;
function alertBox(t){ovt.textContent=t;ov.style.display='flex';}
document.getElementById('ovb').onclick=function(){ov.style.display='none';};
ov.onclick=function(e){if(e.target===ov)ov.style.display='none';};
function toast(t){tt.textContent=t;tt.style.display='block';clearTimeout(tk);
 tk=setTimeout(function(){tt.style.display='none';},4000);}
/* Put the photo form back to its starting state after a successful upload. */
function reset(){document.getElementById('ed').style.display='none';f.value='';img=null;
 z.value=0;rc.checked=true;rs.value=50;clearTimeout(tm);pvx.clearRect(0,0,W,H);x.clearRect(0,0,P,Q);}
/* Resolves to [status, text]. The badge answers 503 + a wait message while it is busy. */
function post(url,body,hdr){return fetch(url,{method:'POST',body:body,headers:hdr||{}})
 .then(function(a){return a.text().then(function(t){return[a.status,t];});});}
function send(r){m.textContent='Sending...';sb.disabled=true;
 post('/photo',r.red?r.out:r.out.subarray(0,W*H/8),{'Content-Type':'application/octet-stream'})
 .then(function(a){m.textContent='';
  if(a[0]==200){reset();toast('Sent! The panel is refreshing and takes a while.');}
  else alertBox(a[1]);})
 .catch(function(){m.textContent='';alertBox('Could not reach the badge.');})
 .then(function(){sb.disabled=false;});}
rc.onchange=rs.oninput=prev;
sb.onclick=function(){send(run());};
var nf=document.getElementById('nf'),nm=document.getElementById('nm'),cur=document.getElementById('cur');
nf.onsubmit=function(ev){ev.preventDefault();nm.textContent='Sending...';
 post('/',new URLSearchParams({name:document.getElementById('ni').value}))
 .then(function(a){nm.textContent='';
  if(a[0]==200){cur.textContent=a[1];nf.reset();document.getElementById('ni').blur();toast('Name sent.');}
  else alertBox(a[1]);})
 .catch(function(){nm.textContent='';alertBox('Could not reach the badge.');});};
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
        # bytearray of PHOTO_BYTES (black only) or 2*PHOTO_BYTES (black then red) in photo mode
        self._photo = None
        self._need_full = False
        self._receiving = False  # a /photo body is being read
        self._last_run_ms = None  # when run() last executed (None = never)
        self._led_state = None  # last LED state applied ("red", "green", "orange", "off")
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

    def _busy(self):
        """Is a submission being deployed right now? New submissions are rejected then."""
        if self._receiving or self._display_busy():
            return True
        # Pending redraw only counts while we're on screen: run() is what clears it.
        # While away, a submission is just stored and drawn on re-entry.
        return self._active() and (
            self._need_full or self._drawn != (self.status, self.ip, self.name_text)
        )

    def _led_wanted(self):
        if self.status in _FAILED_STATUSES:
            return "red"
        if self.status == "WiFi off":
            return "off"
        if self.status == "Connecting..." or self._busy():
            return "orange"
        return "green"  # Connected and idle

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

    async def _drain(self, reader, n):
        """Read and discard a request body so the client sees our reply, not a reset."""
        n = min(n, 2 * PHOTO_BYTES)
        while n > 0:
            chunk = await aio.wait_for(reader.read(min(2048, n)), 5)
            if not chunk:
                break
            n -= len(chunk)

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
                print("webnametag: rejected POST, busy")
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
                # The page shows this as the current name.
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n"
                    + self.name_text.encode()
                )
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

        # Status bar: WiFi status left, "hostname.local / ip" right, BAR_H tall, always
        # shown. 300 + 630 + margins fit in 960 px without overlapping.
        self._status_box, self._status_lbl = box(
            300, BAR_H, lvgl.font_montserrat_24, _BLACK, lvgl.ALIGN.TOP_LEFT, 10, 0)
        self._ip_box, self._ip_lbl = box(
            630, BAR_H, lvgl.font_montserrat_24, _BLACK, lvgl.ALIGN.TOP_RIGHT, -10, 0)
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
        self._ip_lbl.set_text((HOSTNAME + ".local / " + self.ip) if self.ip else "")
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
                # Photo goes below the bar. First plane is black; an optional second is red.
                pv = memoryview(self._photo)
                end = PHOTO_OFFSET + PHOTO_BYTES
                display._framebuffer_black[PHOTO_OFFSET:end] = pv[:PHOTO_BYTES]
                if len(pv) > PHOTO_BYTES:
                    display._framebuffer_red[PHOTO_OFFSET:end] = pv[PHOTO_BYTES:]
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
        self._last_run_ms = time.ticks_ms()
        if self.launched:
            if not self._built:
                self._build_screen()
            if not self._wifi_on:  # first launch, or retry after "Failed"/"WiFi off"
                self._wifi_enable()
            self._need_full = True  # draw below, once the panel is idle
            self._led_state = None  # the launcher blanked the LEDs; re-apply ours
            return

        if UserA.pressed() or UserB.pressed():
            if self._wifi_on:
                self._wifi_disable()
            else:
                self._wifi_enable()

        self._poll_wifi()
        self._update_leds()

        # epd.run() clears its "update requested" flag when a refresh finishes, so a
        # display.update() made mid-refresh would be lost. Only draw when idle;
        # anything still dirty is retried on the next tick.
        if self._display_busy():
            return

        if self._need_full:
            self._redraw(full=True)
        elif self._drawn != (self.status, self.ip, self.name_text):
            self._redraw()
