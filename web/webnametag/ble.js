/* Shared by index.html (name + photo) and codes.html (QR codes / barcodes): debug log, small UI helpers and the
   BLE transport to the badge (apps/webnametag.py). A page defines onBadgeUp() / onBadgeDown() to react to the link. */
/* ---- debug log (Bluefy has no console): shown under "Debug log" at the bottom of the page ---- */
var LOG=[],T0=Date.now();
function dbg(){var m=[].map.call(arguments,function(a){return a&&a.name&&a.message!==undefined?a.name+': '+a.message:
 (a&&typeof a=='object'?JSON.stringify(a):String(a));}).join(' ');
 LOG.push(((Date.now()-T0)/1000).toFixed(1)+' '+m);if(LOG.length>300)LOG.shift();
 var e=document.getElementById('dbg');if(e)e.textContent=LOG.join('\n');}
(function(){var o=console.log;console.log=function(){dbg.apply(null,arguments);o.apply(console,arguments);};})();
window.addEventListener('error',function(e){dbg('JS error:',e.message,e.filename?(e.filename.split('/').pop()+':'+e.lineno):'');});
window.addEventListener('unhandledrejection',function(e){dbg('Unhandled rejection:',e.reason);});
var BUILD=6,W=960,H=672;
dbg('page loaded, build',BUILD,navigator.userAgent);
function $(i){return document.getElementById(i);}
function $$(i,v){$(i).style.display=v;}
var tk,fo,BUSY_R=[503,'Badge is busy updating its display. Wait a few seconds and try again.'];
/* ---- feedback ---- */
function toast(t){var e=$('toast');e.textContent=t;e.className='on';clearTimeout(tk);tk=setTimeout(function(){e.className='';},4000);}
/* Errors go in a modal (inline text can be scrolled out of view); success is a toast. */
function alertBox(t){$('ovt').textContent=t;fo=document.activeElement;$$('ov','flex');$('ovb').focus();}
function ovc(){$$('ov','none');if(fo&&fo.focus)fo.focus();}
$('ovb').onclick=ovc;$('ov').onclick=function(e){if(e.target==this)ovc();};
$('dbc').onclick=function(){var t=LOG.join('\n');
 (navigator.clipboard&&navigator.clipboard.writeText?navigator.clipboard.writeText(t):Promise.reject()).then(function(){toast('Log copied.');},
  function(){var r=document.createRange();r.selectNodeContents($('dbg'));var g=getSelection();g.removeAllRanges();g.addRange(r);toast('Select and copy the log.');});};
/* ---- BLE transport ----
   Badge GATT service: CTRL (write, commands), DATA (write w/o response, photo bytes),
   STATUS (read + notify). STATUS = busy,mode,ev (1 byte each), chunk (u16), arg (u32), then the name. */
var SVC='7b1f0001-5c0e-4b6e-9a3d-5e11c0de0001',U_CT='7b1f0002-5c0e-4b6e-9a3d-5e11c0de0001',
 U_DT='7b1f0003-5c0e-4b6e-9a3d-5e11c0de0001',U_ST='7b1f0004-5c0e-4b6e-9a3d-5e11c0de0001',
 EV={PH_READY:1,PH_OK:2,PH_ERR:3,BUSY:4,NAME_OK:5,ACK:6},
 dev,chC,chD,chS,chunk=20,wt=[],MODES=['home','name','photo'];
function parse(v){return{busy:v.getUint8(0),mode:MODES[v.getUint8(1)]||'home',ev:v.getUint8(2),
 chunk:v.getUint16(3),arg:v.getUint32(5),name:v.byteLength>9?new TextDecoder().decode(new DataView(v.buffer,v.byteOffset+9)):''};}
function rd(){return chS.readValue().then(function(v){var t=parse(v);if(t.chunk)chunk=Math.min(t.chunk,CAP);return t;});}
function onst(e){var t=parse(e.target.value),i;if(t.chunk)chunk=Math.min(t.chunk,CAP);if(!t.ev)return;dbg('badge event',t.ev,'arg',t.arg,'busy',t.busy,'chunk',t.chunk);
 for(i=0;i<wt.length;i++)if(wt[i].set.indexOf(t.ev)>=0){var w=wt.splice(i,1)[0];clearTimeout(w.t);w.ok(t);return;}}
/* Register BEFORE writing: the notification can beat the write's own completion. */
function wev(set,ms){return new Promise(function(ok,no){var w={set:set,ok:ok};
 w.t=setTimeout(function(){wt.splice(wt.indexOf(w),1);dbg('timeout waiting for event',set);no(new Error('timeout'));},ms||8000);wt.push(w);});}
/* DATA writes are acknowledged by default (one event per write on the badge, no burst). */
var nr=/[?&]nr=1/.test(location.search),CAP=+((location.search.match(/[?&]chunk=(\d+)/)||[])[1]||180); /* fast no-response DATA writes are opt-in (?nr=1): a 21-write burst wedged the badge's BLE under Bluefy */
function dw(a){if(!nr){var b=new Uint8Array(1+a.length);b[0]=68;b.set(a,1);return chC.writeValueWithResponse(b);} /* 'D'+bytes on CTRL */
 return chD.writeValueWithoutResponse(a).catch(function(e){dbg('no-response DATA write failed, switching to acknowledged writes:',e);nr=false;return chD.writeValueWithResponse(a);});}
function ctl(bytes){return chC.writeValueWithResponse(bytes);}
function cmd(op,body){dbg('cmd',op,body?body.length:0);var a=new Uint8Array(1+(body?body.length:0));a[0]=op.charCodeAt(0);if(body)a.set(body,1);return ctl(a);}
/* Resolves to [status,text] like the old HTTP calls: 200 ok, 503 badge busy, other = error text. */
function tx(kind,body,cb){
 if(kind=='name'){var w=wev([EV.NAME_OK,EV.BUSY]);
  return cmd('N',new TextEncoder().encode(body)).then(function(){return w;}).then(function(t){
   if(t.ev==EV.BUSY)return BUSY_R;
   return rd().then(function(s){return[200,s.name];});});}
 dbg('photo: start, bytes',body.length,'chunk',chunk,'noResp',nr,'cap',CAP);var t1=Date.now();var n=body.length,planes=n/(W*H/8),sent=0,win=Math.max(chunk,Math.floor(3840/chunk)*chunk),w1=wev([EV.PH_READY,EV.BUSY]);
 return cmd('P',new Uint8Array([planes])).then(function(){return w1;}).then(function(t){
  if(t.ev==EV.BUSY)return BUSY_R;
  function win1(){if(sent>=n){var w3=wev([EV.PH_OK,EV.PH_ERR],15000);
    return cmd('E').then(function(){return w3;}).then(function(e){
     dbg('photo: finished in',((Date.now()-t1)/1000).toFixed(1),'s, ev',e.ev);return e.ev==EV.PH_OK?[200,'ok']:[500,'The badge could not use that image. Try again.'];});}
   var end=Math.min(n,sent+win),chain=Promise.resolve(),o;dbg('photo: window',sent,'->',end);
   for(o=sent;o<end;o+=chunk)(function(o){chain=chain.then(function(){
     return dw(body.subarray(o,Math.min(end,o+chunk)));});})(o);
   var w2=wev([EV.ACK],8000);
   return chain.then(function(){dbg('photo: window written, syncing');return cmd('S');}).then(function(){return w2;}).then(function(a){
    if(a.ev!=EV.ACK||a.arg!=end)return[500,'Transfer got out of sync. Try again.'];
    sent=end;if(cb)cb(sent/n);return win1();});}
  return win1();});}
/* The badge answers busy while it is still deploying the last submission: wait 5 s and try once more. */
function rp(kind,body,cb,lb){return tx(kind,body,cb).catch(function(e){
 if(kind=='photo'&&nr&&e&&e.message=='timeout'){nr=false;lb('Retrying more slowly...');return tx(kind,body,cb);}throw e;}).then(function(a){if(a[0]!=503)return a;
 return new Promise(function(ok){var n=5;(function t(){if(n<1)return ok();lb('Badge is busy, retrying in '+n+'s');n--;setTimeout(t,1000);})();})
 .then(function(){lb('Sending...');return tx(kind,body,cb);});});}
/* ---- connect ---- */
var bn=$('bn'),cb=$('cb'),ch=$('ch');
/* Elements marked data-gate are dimmed and inert until the badge is connected. */
function gate(on){[].forEach.call(document.querySelectorAll('[data-gate]'),function(e){
 e.style.opacity=on?'':'.4';if(on)e.removeAttribute('inert');else e.setAttribute('inert','');});}
function bst(t,cls){bn.textContent=t;bn.className='cur'+(cls?' '+cls:'');}
function lost(){dbg('badge link lost');wt.forEach(function(w){clearTimeout(w.t);});wt=[];gate(false);
 cb.textContent=window.CONNECT_LABEL||'Connect';cb.disabled=false;bst('Not connected','e');if(window.onBadgeDown)onBadgeDown();}
function link(d){ /* GATT setup for a chosen device */
 dev=d;dbg('connect: chose',d.name,'; gatt.connect');cb.disabled=true;cb.textContent='Connecting...';bst(d.name||'Badge','e');
 d.addEventListener('gattserverdisconnected',lost);
 return d.gatt.connect()
 .then(function(g){dbg('connect: gatt up');return g.getPrimaryService(SVC);})
 .then(function(sv){return Promise.all([sv.getCharacteristic(U_CT),sv.getCharacteristic(U_DT),sv.getCharacteristic(U_ST)]);})
 .then(function(c){chC=c[0];chD=c[1];chS=c[2];chS.addEventListener('characteristicvaluechanged',onst);dbg('connect: characteristics ok; starting notifications');return chS.startNotifications();})
 .then(rd).then(function(){gate(true);cb.style.display='none';bst(dev.name);if(window.onBadgeUp)onBadgeUp();});}
function fail(e){dbg('connect failed:',e);lost();if(!(e&&e.name=='NotFoundError'))ch.textContent='Could not connect: '+(e&&e.message||e);}
function connect(){
 if(!navigator.bluetooth){ch.textContent='This browser has no Web Bluetooth. On iPhone, open this page in the Bluefy browser.';return;}
 dbg('connect: requestDevice');cb.disabled=true;cb.textContent='Choosing...';ch.textContent='';
 navigator.bluetooth.requestDevice({filters:[{services:[SVC]},{namePrefix:'Badge-'}],optionalServices:[SVC]}).then(link).catch(fail);}
/* Reconnect without a tap to a badge this browser already has permission for (getDevices needs browser support).
   Resolves false when that is not possible; the caller then shows the normal Connect button. */
function autoConnect(){
 if(!navigator.bluetooth||!navigator.bluetooth.getDevices)return Promise.resolve(false);
 return navigator.bluetooth.getDevices().then(function(ds){
  ds=ds.filter(function(d){return(d.name||'').indexOf('Badge-')==0;});dbg('autoConnect: known badges',ds.length);
  if(ds.length!=1)return false; /* none, or several: let the user choose */
  return link(ds[0]).then(function(){return true;},function(e){dbg('autoConnect failed:',e);lost();return false;});
 }).catch(function(e){dbg('autoConnect unavailable:',e);return false;});}
cb.onclick=function(ev){ev.preventDefault();cb.style.display='';connect();};
lost();
