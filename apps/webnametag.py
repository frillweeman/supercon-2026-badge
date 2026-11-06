"""Web Nametag: join WiFi, serve a tiny web page, show a typed name or an uploaded photo.

UserA toggles the WiFi radio; UserB resets the screen to the generated "Control me!" home
graphic (which also clears any name/photo). Nothing is persisted.

The home graphic (drawn in code, no image file) has a legend for the LED bar above the screen,
shows a QR code for http://<ip> with "hostname / ip" underneath, and explains
the two buttons. There is no status bar: the screen is only redrawn when its content
changes (new name/photo, reset, or the IP appearing/disappearing on the home graphic), and
the first home draw waits until WiFi has connected or failed.

The LED bar shows the state: green = WiFi connected, red = WiFi failed, orange chase =
connecting or loading a new name/image, dark = WiFi off. While a submission is being
deployed (upload in progress, redraw pending, or panel refreshing) new POSTs are rejected
with 503 and a wait message; nothing is queued.

The page also polls GET /status (busy / mode / name) to show when the panel has finished.

The web page crops and dithers photos in the browser for the whole panel and POSTs them to
/photo as raw 1-bit planes (960 x PHOTO_H, 120 bytes/row, MSB first). Body = black plane
(1 = black), optionally followed by a red plane (1 = red); a pixel is never set in both.
The badge copies the planes into the framebuffers; LVGL only draws text.
"""

import asyncio as aio
import gc
import json
import sys
import time

import lvgl
import network

from apps.base_app import BaseApp
from lib.button import UserA, UserB
from lib.epd import display
from lib.led_bar import NUM_LEDS, leds
from lib.qr import make as qr_make

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
_FAILED_STATUSES = ("Failed", "Server failed", "No config")

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
F_CAP = _font("font_montserrat_20", "font_montserrat_24")
F_HEAD = _font("font_montserrat_28", "font_montserrat_24")
F_TITLE = _font("font_montserrat_48")

# Home graphic geometry (px). QR is 25 modules * 16 px = 400, x origin byte-aligned.
QR_SCALE = 16
QR_PX = 25 * QR_SCALE
QR_X = 280
QR_Y = 190
PANEL_W, PANEL_H, PANEL_Y = 200, 240, 270


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

# %NAME%, %HOST%, %MAX%, %H%, %QH%, %HEIC_URL% and %HEIC_SRI% are substituted with str.replace (the JS has braces, so no .format).
# Keep this page ASCII and free of backslashes (it is a plain, non-raw Python string).
_PAGE = """<!DOCTYPE html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>
<meta name=color-scheme content='light dark'><title>Badge</title>
<style>
:root{--bg:#f4f1ea;--card:#fff;--fg:#111;--mut:#666;--ln:#d6d1c4;--ac:#d4001a;--on:#fff;--ok:#1a9d4b}
@media(prefers-color-scheme:dark){:root{--bg:#0b0b0c;--card:#17171a;--fg:#f2f2f2;--mut:#8d8d95;--ln:#2c2c31;--ac:#ff2b3f;--on:#0b0b0c;--ok:#3ccf70}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.4 system-ui,-apple-system,sans-serif;
padding:max(16px,env(safe-area-inset-top)) 16px max(32px,env(safe-area-inset-bottom))}
main{max-width:520px;margin:auto}
header{display:flex;align-items:center;gap:12px;margin:4px 2px 18px}
#dot{width:12px;height:12px;border-radius:50%;background:var(--ok);flex:none}
h1{font-size:17px;margin:0;font-weight:700}
header p{margin:0;color:var(--mut);font-size:13px}
section{background:var(--card);border:1px solid var(--ln);border-radius:14px;padding:18px;margin-bottom:14px}
section.dr{border-color:var(--ac)}
h2{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);margin:0 0 12px;font-weight:600}
.cur{font-size:28px;font-weight:700;word-break:break-word;margin:0}
.cur.e{color:var(--mut);font-weight:500;font-size:20px}
.sub{display:flex;align-items:center;gap:8px;color:var(--mut);font-size:13px;margin:8px 0 0;min-height:20px}
.sp{width:14px;height:14px;border:2px solid var(--ln);border-top-color:var(--ac);border-radius:50%;display:none;
animation:r .8s linear infinite}
.bz .sp{display:block}
@keyframes r{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){*{animation-duration:3s!important;transition:none!important}}
.row{display:flex;gap:8px}
.hint{color:var(--mut);font-size:13px;margin:0}
.nr{display:flex;justify-content:space-between;align-items:center;margin-top:6px}
input[type=text]{flex:1;min-width:0;min-height:48px;font:inherit;font-size:16px;color:var(--fg);background:var(--bg);
border:1px solid var(--ln);border-radius:10px;padding:0 14px;outline:0}
input[type=text]:focus{border-color:var(--ac)}
button{font:inherit;font-weight:600;min-height:44px;color:var(--on);background:var(--ac);border:0;border-radius:10px;
padding:0 18px;cursor:pointer;-webkit-appearance:none}
button:active{opacity:.8}
button:disabled{opacity:.4;cursor:default}
button.g{background:transparent;color:var(--fg);border:1px solid var(--ln)}
button.t{background:none;color:var(--mut);padding:0 8px;font-weight:500;font-size:14px}
#sn{min-width:92px}
:focus-visible{outline:3px solid var(--ac);outline-offset:2px}
.v{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden}
.pk{display:flex;gap:10px}
.pk label{flex:1;display:flex;flex-direction:column;align-items:center;gap:6px;padding:18px 8px;cursor:pointer;
border:1px dashed var(--mut);border-radius:12px;font-weight:600;font-size:15px}
.pk label:active{background:var(--bg)}
.v:focus-visible+label{outline:3px solid var(--ac);outline-offset:2px}
.pk svg{width:28px;height:28px;stroke:var(--ac);fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.dh{margin-top:12px}
@media(hover:hover){#lc,#fc{display:none}}
@media(hover:none){.dh{display:none}}
#sh{position:fixed;top:0;right:0;bottom:0;left:0;background:var(--bg);display:none;flex-direction:column;z-index:7;
touch-action:none;overscroll-behavior:contain;padding-top:env(safe-area-inset-top)}
#sh.o{display:flex}
.bar{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 12px;background:var(--card)}
#pg{height:3px;background:var(--ln);overflow:hidden}
#pg i{display:block;height:100%;width:0;background:var(--ac);transition:width .15s}
#pg.n i{width:35%;animation:n 1s ease-in-out infinite alternate}
@keyframes n{from{margin-left:0}to{margin-left:65%}}
.bar b{font-size:15px}
#cs{display:none;gap:12px}
.seg{display:flex;background:var(--bg);border:1px solid var(--ln);border-radius:10px;padding:3px;gap:3px}
.seg button{flex:1;min-height:38px;background:transparent;color:var(--fg);padding:0 12px;font-size:14px;white-space:nowrap}
.seg button[aria-pressed=true]{background:var(--ac);color:var(--on)}
#stg{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;padding:12px;position:relative}
canvas{max-width:100%;max-height:100%;display:block;border-radius:10px;border:1px solid var(--ln);background:#fff}
#c{touch-action:none;cursor:grab}
#ld{position:absolute;top:12px;right:12px;bottom:12px;left:12px;display:none;align-items:center;justify-content:center;
background:rgba(0,0,0,.45);color:#fff;border-radius:10px;font-weight:600}
.ctl{padding:12px 16px max(14px,env(safe-area-inset-bottom));background:var(--card);border-top:1px solid var(--ln);
display:grid;gap:12px}
.ctl label{display:flex;align-items:center;gap:12px;font-size:14px;color:var(--mut)}
.ctl label span{width:84px;flex:none}
.ctl input[type=range]{flex:1;min-width:0;accent-color:var(--ac);min-height:32px;margin:0}
#ov{position:fixed;top:0;right:0;bottom:0;left:0;background:rgba(0,0,0,.55);display:none;align-items:center;
justify-content:center;padding:20px;z-index:9}
#ov div{background:var(--card);color:var(--fg);border-radius:14px;padding:22px;max-width:360px;width:100%}
#ov p{margin:0 0 16px;font-size:16px}
#ov button{width:100%}
#toast{position:fixed;left:50%;bottom:max(24px,env(safe-area-inset-bottom));transform:translateX(-50%);background:var(--fg);
color:var(--bg);padding:10px 16px;border-radius:999px;font-size:14px;opacity:0;pointer-events:none;transition:opacity .2s;
z-index:8;max-width:90%;text-align:center}
#toast.on{opacity:1}
</style></head><body><main>
<header><i id=dot></i><div><h1>Supercon Badge</h1><p>%HOST%</p></div></header>
<section><h2>On the badge</h2><p class=cur id=cur>%NAME%</p>
<p class=sub id=sub aria-live=polite><i class=sp></i><span id=sm></span></p></section>
<section><h2>Name</h2>
<form id=nf method=POST action=/ class=row><input id=ni type=text name=name maxlength=%MAX% placeholder='Your name'
autocomplete=off enterkeyhint=send aria-label=Name><button id=sn>Set</button></form>
<div class=nr><span class=hint id=cn></span><button type=button id=cl class=t>Clear name</button></div></section>
<section id=ps><h2>Photo</h2><div class=pk>
<input id=f class=v type=file accept='image/*,.heic,.heif'><label for=f><svg viewBox='0 0 24 24'><rect x='3' y='4' width='18' height='16' rx='2'/><circle cx='9' cy='10' r='1.5'/><path d='M21 16l-5-5-9 9'/></svg>Choose photo</label>
<input id=fc class=v type=file accept='image/*' capture=environment><label for=fc id=lc><svg viewBox='0 0 24 24'><path d='M4 8h3l2-3h6l2 3h3v12H4z'/><circle cx='12' cy='13' r='3.5'/></svg>Take photo</label></div>
<p class='hint dh'>Or drop an image here. HEIC works too.</p></section></main>
<div id=sh role=dialog aria-modal=true aria-label='Photo editor'>
<div class=bar><button id=cx type=button class=g>Cancel</button>
<b id=ttl></b>
<button id=ss type=button>Next</button></div>
<div id=pg><i></i></div>
<div id=stg><canvas id=c width=480 height=%QH%></canvas><canvas id=pv width=960 height=%H% style='display:none'></canvas>
<div id=ld>Rendering preview...</div></div>
<div class=ctl id=ctl><p class=hint id=hn></p>
<label id=zr><span>Zoom</span><input type=range id=z min=0 max=100 value=0></label>
<div id=cs><div class=seg><button id=bw type=button aria-pressed=false>Black &amp; white</button><button id=br type=button aria-pressed=true>B/W + red</button></div>
<label id=rr><span>Red amount</span><input type=range id=rs min=0 max=100 value=50></label></div></div></div>
<div id=ov><div role=alertdialog aria-modal=true><p id=ovt></p><button id=ovb>OK</button></div></div>
<div id=toast role=status></div>
<script>
var W=960,H=%H%,P=W/2,Q=H/2,img,sc,mn,ox,oy,d=null,tab='c',rc=true,dirty=true,last=null,busy=false,tm,tk,pt,pn=0,opener,fo;
function $(i){return document.getElementById(i);}
var f=$('f'),fc=$('fc'),c=$('c'),x=c.getContext('2d'),pv=$('pv'),pvx=pv.getContext('2d'),z=$('z'),rs=$('rs'),
cur=$('cur'),sm=$('sm'),sub=$('sub'),ni=$('ni'),sn=$('sn'),cl=$('cl'),sh=$('sh'),ss=$('ss'),pg=$('pg'),
curName=cur.textContent,curMode='name';
function $$(i,v){$(i).style.display=v;}
/* ---- status ---- */
function note(t,b){sub.className='sub'+(b?' bz':'');sm.textContent=t;}
function show(){cur.textContent=curMode=='photo'?'Photo':(curName||'Nothing yet');
 cur.className='cur'+(curMode!='photo'&&!curName?' e':'');
 sn.disabled=!ni.value||(ni.value==curName&&curMode=='name');
 cl.disabled=curMode=='name'&&!curName;$('cn').textContent=ni.value.length+'/%MAX%';}
ni.oninput=show;show();
function poll(){fetch('/status',{cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
 curName=s.name;curMode=s.mode;show();
 if(s.busy&&pn++<40){note('Panel refreshing...',1);pt=setTimeout(poll,3000);}
 else{note(pn?'Done, it is on the badge.':'');pn=0;}})
 .catch(function(){if(pn&&pn++<40)pt=setTimeout(poll,3000);else note('');});}
function watch(){clearTimeout(pt);pn=1;note('Panel refreshing...',1);pt=setTimeout(poll,1500);}
poll();
/* ---- feedback ---- */
function toast(t){var e=$('toast');e.textContent=t;e.className='on';clearTimeout(tk);tk=setTimeout(function(){e.className='';},4000);}
/* Errors go in a modal (inline text can be scrolled out of view); success is a toast. */
function alertBox(t){$('ovt').textContent=t;fo=document.activeElement;$$('ov','flex');$('ovb').focus();}
function ovc(){$$('ov','none');if(fo&&fo.focus)fo.focus();}
$('ovb').onclick=ovc;$('ov').onclick=function(e){if(e.target==this)ovc();};
function prog(v){pg.className=v<0?'n':'';pg.firstChild.style.width=v<0?'':Math.round(v*100)+'%';}
/* XHR (fetch cannot report upload progress). Resolves to [status, text]. */
function xp(url,body,hdr,cb){return new Promise(function(ok,no){var r=new XMLHttpRequest(),k;r.open('POST',url);
 for(k in hdr)r.setRequestHeader(k,hdr[k]);r.timeout=120000;
 if(cb)r.upload.onprogress=function(e){if(e.lengthComputable)cb(e.loaded/e.total);};
 r.onload=function(){ok([r.status,r.responseText]);};r.onerror=r.ontimeout=function(){no();};r.send(body);});}
/* The badge answers 503 while it is still deploying the last submission: wait 5 s and try once more. */
function rp(url,body,hdr,cb,lb){return xp(url,body,hdr,cb).then(function(a){if(a[0]!=503)return a;
 return new Promise(function(ok){var n=5;(function t(){if(n<1)return ok();lb('Badge is busy, retrying in '+n+'s');n--;setTimeout(t,1000);})();})
 .then(function(){lb('Sending...');return xp(url,body,hdr,cb);});});}
/* ---- name ---- */
function sendName(v){sn.disabled=cl.disabled=true;sn.textContent='Sending...';
 rp('/',new URLSearchParams({name:v}),{},null,function(t){sn.textContent=t;})
 .then(function(a){if(a[0]==200){curName=a[1];curMode='name';ni.value='';ni.blur();toast(a[1]?'Name sent.':'Name cleared.');watch();}
  else alertBox(a[1]);})
 .catch(function(){alertBox('Could not reach the badge.');})
 .then(function(){sn.textContent='Set';show();});}
$('nf').onsubmit=function(ev){ev.preventDefault();if(!sn.disabled)sendName(ni.value);};
cl.onclick=function(){sendName('');};
/* ---- photo editor ---- */
function draw(){
 ox=Math.min(0,Math.max(P-img.width*sc,ox));oy=Math.min(0,Math.max(Q-img.height*sc,oy));
 x.fillStyle='#fff';x.fillRect(0,0,P,Q);x.drawImage(img,ox,oy,img.width*sc,img.height*sc);dirty=true;}
function heic(){return window.heic2any?Promise.resolve():new Promise(function(ok,no){
 var s=document.createElement('script');s.src='%HEIC_URL%';s.integrity='%HEIC_SRI%';
 s.crossOrigin='anonymous';s.onload=ok;s.onerror=no;document.head.appendChild(s);});}
function load(file){return createImageBitmap(file).catch(function(err){
 if(!/[.]hei[cf]$/i.test(file.name)&&!/hei[cf]/i.test(file.type))throw err;
 note('Loading HEIC converter (needs internet)...',1);
 return heic().then(function(){return heic2any({blob:file,toType:'image/jpeg',quality:0.9});})
 .then(function(r){return createImageBitmap(Array.isArray(r)?r[0]:r);});});}
function pick(file){if(!file)return;note('Loading photo...',1);
 load(file).then(function(b){img=b;mn=Math.max(P/b.width,Q/b.height);sc=mn;z.value=0;
  ox=(P-b.width*sc)/2;oy=(Q-b.height*sc)/2;note('');opener=document.activeElement;sh.className='o';
  document.body.style.overflow='hidden';tabTo('c');draw();$('cx').focus();})
 .catch(function(){note('');alertBox('Could not read that image.');});}
f.onchange=fc.onchange=function(){pick(this.files[0]);this.value='';};
var ps=$('ps');
ps.ondragover=function(e){e.preventDefault();ps.className='dr';};
ps.ondragleave=function(){ps.className='';};
ps.ondrop=function(e){e.preventDefault();ps.className='';pick(e.dataTransfer.files[0]);};
z.oninput=function(){var cx=(P/2-ox)/sc,cy=(Q/2-oy)/sc;
 sc=mn*(1+3*z.value/100);ox=P/2-cx*sc;oy=Q/2-cy*sc;draw();};
c.onpointerdown=function(e){d=[e.clientX,e.clientY,ox,oy];c.setPointerCapture(e.pointerId);};
c.onpointermove=function(e){if(!d)return;var k=P/c.getBoundingClientRect().width;
 ox=d[2]+(e.clientX-d[0])*k;oy=d[3]+(e.clientY-d[1])*k;draw();};
c.onpointerup=c.onpointercancel=function(){d=null;};
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
/* The dither is heavy, so it only runs for the Preview tab or Send, and the result is cached until the crop or colour changes. */
function res(){if(dirty||!last){last=dither(crop(),rc,Math.pow(2,(50-rs.value)/25));dirty=false;}return last;}
function render(){clearTimeout(tm);if(tab!='p'||!img)return;
 if(!dirty&&last){pvx.putImageData(last.pv,0,0);return;}
 $$('ld','flex');tm=setTimeout(function(){pvx.putImageData(res().pv,0,0);$$('ld','none');},60);}
function tabTo(t){tab=t; $$('c',t=='c'?'block':'none');$$('pv',t=='p'?'block':'none');$$('zr',t=='c'?'flex':'none');$$('ld','none');$$('cs',t=='p'?'grid':'none');
 $('ttl').textContent='Step '+(t=='c'?1:2)+' of 2';$('cx').textContent=t=='c'?'Cancel':'Back';ss.textContent=t=='c'?'Next':'Send';
 $('hn').textContent=t=='c'?'Drag the photo to position it and use Zoom to size it. The frame is the whole badge screen.':
  'This is how the badge will show it. Pick black and white or add red, then send.';
 render();}
function setRed(v){rc=v;$('bw').setAttribute('aria-pressed',!v);$('br').setAttribute('aria-pressed',v);$$('rr',v?'flex':'none');
 dirty=true;render();}
$('bw').onclick=function(){setRed(false);};$('br').onclick=function(){setRed(true);};
rs.oninput=function(){dirty=true;clearTimeout(tm);tm=setTimeout(render,120);};
function lock(on){busy=on;ss.disabled=on;$('stg').inert=$('ctl').inert=$('cx').inert=on;
 if(!on){ss.textContent=tab=='c'?'Next':'Send';prog(0);}}
function closeSheet(){sh.className='';document.body.style.overflow='';if(opener&&opener.focus)opener.focus();}
/* Put the photo form back to its starting state (after Cancel or a successful upload). */
function reset(){closeSheet();img=null;last=null;dirty=true;f.value=fc.value='';z.value=0;rs.value=50;setRed(true);
 clearTimeout(tm);pvx.clearRect(0,0,W,H);x.clearRect(0,0,P,Q);}
$('cx').onclick=function(){if(tab=='p')tabTo('c');else reset();};
ss.onclick=function(){if(busy||!img)return;if(tab=='c'){tabTo('p');return;}lock(true);ss.textContent='Preparing...';prog(-1);
 setTimeout(function(){var r=res();ss.textContent='Sending...';prog(0);
  rp('/photo',r.red?r.out:r.out.subarray(0,W*H/8),{'Content-Type':'application/octet-stream'},
   function(p){prog(p);ss.textContent='Sending '+Math.round(p*100)+'%';},
   function(t){ss.textContent=t;prog(-1);})
  .then(function(a){if(a[0]==200){reset();curMode='photo';show();toast('Sent! The panel takes a while to refresh.');watch();}
   else alertBox(a[1]);})
  .catch(function(){alertBox('Could not reach the badge.');})
  .then(function(){lock(false);});},40);};
/* Escape closes the topmost layer; Tab stays inside it. */
document.onkeydown=function(e){var ovo=$('ov').style.display=='flex';
 if(e.key=='Escape'){if(ovo)ovc();else if(sh.className&&!busy)$('cx').onclick();return;}
 if(e.key!='Tab'||!(ovo||sh.className))return;
 var l=[].filter.call((ovo?$('ov'):sh).querySelectorAll('button,input'),function(n){
  return !n.disabled&&!n.closest('[inert]')&&n.offsetParent!==null;});
 if(!l.length){e.preventDefault();return;}
 var a=l.indexOf(document.activeElement);
 if(e.shiftKey&&a<=0){l[l.length-1].focus();e.preventDefault();}
 else if(!e.shiftKey&&a==l.length-1){l[0].focus();e.preventDefault();}};
setRed(true);
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
        self.mode = "home"  # "home" (generated graphic), "name" or "photo"
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
        self._drawn = None  # (mode, name) on screen
        self._drawn_ip = None  # IP in the home QR (None = placeholder or not home)
        self._scr = None

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

    def _pending(self):
        """A redraw is due. The home graphic waits while WiFi is still connecting.
        Turning WiFi off never redraws; the home QR is only refreshed when an IP appears
        or changes, and the WiFi-off placeholder only shows on an explicit reset."""
        if self.mode == "home":
            if self.status == "Connecting...":
                return False
            return self._need_full or (bool(self.ip) and self.ip != self._drawn_ip)
        return self._need_full or self._drawn != (self.mode, self.name_text)

    def _busy(self):
        """Is a submission being deployed right now? New submissions are rejected then."""
        if self._receiving or self._display_busy():
            return True
        # Pending redraw only counts while we're on screen: run() is what clears it.
        # While away, a submission is just stored and drawn on re-entry.
        return self._active() and self._pending()

    def _led_wanted(self):
        # Busy comes first so a button press shows the loading animation immediately,
        # whatever the WiFi state is.
        if self.status == "Connecting..." or self._busy():
            return "orange"
        if self.status in _FAILED_STATUSES:
            return "red"
        if self.status == "WiFi off":
            return "off"
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
                self.mode = "name" if self.name_text else "home"
                self._photo = None
                self._need_full = True
                # The page shows this as the current name.
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n"
                    + self.name_text.encode()
                )
            elif path.startswith(b"/status"):
                # Polled by the page after a submission to learn when the panel is done.
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
                page = (
                    _PAGE.replace("%NAME%", _escape(self.name_text))
                    .replace("%HOST%", HOSTNAME + ".local")
                    .replace("%MAX%", str(MAX_NAME))
                    .replace("%H%", str(PHOTO_H))
                    .replace("%QH%", str(PHOTO_H // 2))
                    .replace("%HEIC_URL%", HEIC_URL)
                    .replace("%HEIC_SRI%", HEIC_SRI)
                )
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n"
                )
                # Send in small pieces: encoding the whole page at once needs another ~18 KB.
                for i in range(0, len(page), 1024):
                    writer.write(page[i : i + 1024].encode())
                    await writer.drain()
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

    def _home_placeholder(self):
        st = self.status
        if st == "No config":
            return "No WiFi config\non this badge"
        if st in _FAILED_STATUSES:
            return "WiFi failed\n\nPress Left Btn\nto retry"
        return "WiFi is off\n\nPress Left Btn\nto turn it on"

    def _draw_home(self, fb, scr):
        # Legend table: header above, meaning below, one column per LED colour.
        _fill(fb, 180, 56, 600, 2)
        for n, (head, val) in enumerate((
            ("Green", "Wi-Fi Connected"),
            ("Red", "Wi-Fi Failed"),
            ("Orange", "Loading"),
            ("Off", "Wi-Fi Off"),
        )):
            x = 180 + n * 150
            self._box(scr, x, 22, 150, 32, F_BODY, _BLACK, _WHITE, head)
            self._box(scr, x, 62, 150, 24, F_SMALL, _BLACK, _WHITE, val)
        # --- title ---
        self._box(scr, 180, 100, 600, 70, F_TITLE, _RED, _WHITE, "Control Me!")
        # --- QR or placeholder ---
        if self.ip:
            rows = qr_make("http://" + self.ip)
            on, off = b"\xff" * (QR_SCALE // 8), b"\x00" * (QR_SCALE // 8)
            for r, row in enumerate(rows):
                line = b"".join([on if m else off for m in row])
                for sl in range(QR_SCALE):
                    o = (QR_Y + r * QR_SCALE + sl) * ROW_BYTES + (QR_X >> 3)
                    fb[o : o + len(line)] = line
            cap = HOSTNAME + ".local  /  " + self.ip
        else:
            _frame(fb, QR_X, QR_Y, QR_PX, QR_PX)
            self._box(scr, QR_X + 20, QR_Y + 100, QR_PX - 40, 200, F_BODY, _BLACK, _WHITE,
                      self._home_placeholder())
            cap = HOSTNAME + ".local"
        self._box(scr, 180, QR_Y + QR_PX + 14, 600, 34, F_CAP, _BLACK, _WHITE, cap)
        # --- button tutorial: black header bar, outlined panel ---
        for x, head, body in (
            (24, "Left Btn", "Toggle the\nWiFi radio\non / off"),
            (960 - 24 - PANEL_W, "Right Btn", "Clear\nthe Image"),
        ):
            _frame(fb, x, PANEL_Y, PANEL_W, PANEL_H)
            _fill(fb, x, PANEL_Y, PANEL_W, 52)
            self._box(scr, x, PANEL_Y, PANEL_W, 52, F_HEAD, _WHITE, _BLACK, head)
            self._box(scr, x + 6, PANEL_Y + 60, PANEL_W - 12, PANEL_H - 70, F_BODY,
                      _BLACK, _WHITE, body)

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
        self._drawn = (self.mode, self.name_text)
        self._drawn_ip = self.ip if self.mode == "home" and self.ip else None
        self._need_full = False
        gc.collect()

    # ---- App loop ------------------------------------------------------
    def run(self):
        # The display is only touched from here, and run() only executes while
        # this app is selected, so a name or photo submitted while away is drawn on re-entry.
        self._last_run_ms = time.ticks_ms()
        if self.launched:
            if not self._wifi_on:  # first launch, or retry after "Failed"/"WiFi off"
                self._wifi_enable()
            self._need_full = True  # draw below, once the panel is idle
            self._led_state = None  # the launcher blanked the LEDs; re-apply ours
            return

        if UserA.pressed():
            if self._wifi_on and self.status not in _FAILED_STATUSES:
                self._wifi_disable()
            else:  # off, or failed: (re)start
                if self._wifi_on:
                    self._wifi_disable()
                self._wifi_enable()
        if UserB.pressed() and not self._busy():  # ignore presses while already working
            self.mode = "home"
            self.name_text = ""
            self._photo = None
            self._need_full = True

        self._poll_wifi()
        self._update_leds()

        # epd.run() clears its "update requested" flag when a refresh finishes, so a
        # display.update() made mid-refresh would be lost. Only draw when idle;
        # anything still dirty is retried on the next tick.
        if self._display_busy():
            return

        if self._pending():
            self._redraw()
