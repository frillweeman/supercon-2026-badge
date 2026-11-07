/* ---- debug log (Bluefy has no console): shown under "Debug log" at the bottom of the page ---- */
var LOG=[],T0=Date.now();
function dbg(){var m=[].map.call(arguments,function(a){return a&&a.name&&a.message!==undefined?a.name+': '+a.message:
 (a&&typeof a=='object'?JSON.stringify(a):String(a));}).join(' ');
 LOG.push(((Date.now()-T0)/1000).toFixed(1)+' '+m);if(LOG.length>300)LOG.shift();
 var e=document.getElementById('dbg');if(e)e.textContent=LOG.join('\n');}
(function(){var o=console.log;console.log=function(){dbg.apply(null,arguments);o.apply(console,arguments);};})();
window.addEventListener('error',function(e){dbg('JS error:',e.message,e.filename?(e.filename.split('/').pop()+':'+e.lineno):'');});
window.addEventListener('unhandledrejection',function(e){dbg('Unhandled rejection:',e.reason);});
dbg('page loaded',navigator.userAgent);
var W=960,H=672,P=W/2,Q=H/2,img,sc,mn,ox,oy,d=null,tab='c',rc=true,dirty=true,last=null,busy=false,tm,tk,pt,pn=0,opener,fo;
function $(i){return document.getElementById(i);}
var f=$('f'),fc=$('fc'),c=$('c'),x=c.getContext('2d'),pv=$('pv'),pvx=pv.getContext('2d'),z=$('z'),rs=$('rs'),
cur=$('cur'),sm=$('sm'),sub=$('sub'),ni=$('ni'),sn=$('sn'),cl=$('cl'),sh=$('sh'),ss=$('ss'),pg=$('pg'),
curName='',curMode='name';
function $$(i,v){$(i).style.display=v;}
/* ---- status ---- */
function note(t,b){sub.className='sub'+(b?' bz':'');sm.textContent=t;}
function show(){cur.textContent=curMode=='photo'?'Photo':(curName||'Nothing yet');
 cur.className='cur'+(curMode!='photo'&&!curName?' e':'');
 sn.disabled=!ni.value||(ni.value==curName&&curMode=='name');
 cl.disabled=curMode=='name'&&!curName;$('cn').textContent=ni.value.length+'/24';}
ni.oninput=show;show();
function poll(){rd().then(function(t){
 curName=t.name;curMode=t.mode;show();
 if(t.busy&&pn++<40){note('Panel refreshing...',1);pt=setTimeout(poll,3000);}
 else{note(pn?'Done, it is on the badge.':'');pn=0;}})
 .catch(function(){if(pn&&pn++<40)pt=setTimeout(poll,3000);else note('');});}
function watch(){clearTimeout(pt);pn=1;note('Panel refreshing...',1);pt=setTimeout(poll,1500);}
/* ---- feedback ---- */
function toast(t){var e=$('toast');e.textContent=t;e.className='on';clearTimeout(tk);tk=setTimeout(function(){e.className='';},4000);}
/* Errors go in a modal (inline text can be scrolled out of view); success is a toast. */
function alertBox(t){$('ovt').textContent=t;fo=document.activeElement;$$('ov','flex');$('ovb').focus();}
function ovc(){$$('ov','none');if(fo&&fo.focus)fo.focus();}
$('ovb').onclick=ovc;$('ov').onclick=function(e){if(e.target==this)ovc();};
function prog(v){pg.className=v<0?'n':'';pg.firstChild.style.width=v<0?'':Math.round(v*100)+'%';}
/* ---- BLE transport ----
   Badge GATT service: CTRL (write, commands), DATA (write w/o response, photo bytes),
   STATUS (read + notify). STATUS = authed,busy,mode,ev (1 byte each), chunk (u16), arg (u32), then the name. */
var SVC='7b1f0001-5c0e-4b6e-9a3d-5e11c0de0001',U_CT='7b1f0002-5c0e-4b6e-9a3d-5e11c0de0001',
 U_DT='7b1f0003-5c0e-4b6e-9a3d-5e11c0de0001',U_ST='7b1f0004-5c0e-4b6e-9a3d-5e11c0de0001',
 EV={AUTH_OK:1,AUTH_BAD:2,PH_READY:3,PH_OK:4,PH_ERR:5,LOCKED:6,BUSY:7,NAME_OK:8,ACK:9,NEED_AUTH:10},
 dev,chC,chD,chS,chunk=20,wt=[],authed=false,MODES=['home','name','photo'];
function parse(v){return{authed:v.getUint8(0),busy:v.getUint8(1),mode:MODES[v.getUint8(2)]||'home',ev:v.getUint8(3),
 chunk:v.getUint16(4),arg:v.getUint32(6),name:v.byteLength>10?new TextDecoder().decode(new DataView(v.buffer,v.byteOffset+10)):''};}
function rd(){return chS.readValue().then(function(v){var t=parse(v);if(t.chunk)chunk=Math.min(t.chunk,180);return t;});}
function onst(e){var t=parse(e.target.value),i;if(t.chunk)chunk=Math.min(t.chunk,180);if(!t.ev)return;dbg('badge event',t.ev,'arg',t.arg,'busy',t.busy,'chunk',t.chunk);
 for(i=0;i<wt.length;i++)if(wt[i].set.indexOf(t.ev)>=0){var w=wt.splice(i,1)[0];clearTimeout(w.t);w.ok(t);return;}}
/* Register BEFORE writing: the notification can beat the write's own completion. */
function wev(set,ms){return new Promise(function(ok,no){var w={set:set,ok:ok};
 w.t=setTimeout(function(){wt.splice(wt.indexOf(w),1);dbg('timeout waiting for event',set);no(new Error('timeout'));},ms||8000);wt.push(w);});}
/* DATA writes: no-response is fast, but if this browser rejects or stalls on it we fall back to acknowledged writes. */
var nr=true;
function dw(a){if(!nr)return chD.writeValueWithResponse(a);
 return chD.writeValueWithoutResponse(a).catch(function(e){dbg('no-response DATA write failed, switching to acknowledged writes:',e);nr=false;return chD.writeValueWithResponse(a);});}
function ctl(bytes){return chC.writeValueWithResponse(bytes);}
function cmd(op,body){dbg('cmd',op,body?body.length:0);var a=new Uint8Array(1+(body?body.length:0));a[0]=op.charCodeAt(0);if(body)a.set(body,1);return ctl(a);}
/* Resolves to [status,text] like the old HTTP calls: 200 ok, 503 badge busy, other = error text. */
function tx(kind,body,cb){
 if(kind=='name'){var w=wev([EV.NAME_OK,EV.BUSY,EV.NEED_AUTH]);
  return cmd('N',new TextEncoder().encode(body)).then(function(){return w;}).then(function(t){
   if(t.ev==EV.BUSY)return[503,'Badge is busy updating its display. Wait a few seconds and try again.'];
   if(t.ev==EV.NEED_AUTH)return[401,'Badge is locked. Enter the code again.'];
   return rd().then(function(s){return[200,s.name];});});}
 dbg('photo: start, bytes',body.length,'chunk',chunk,'noResp',nr);var n=body.length,planes=n/(W*H/8),sent=0,win=Math.max(chunk,Math.floor(3840/chunk)*chunk),w1=wev([EV.PH_READY,EV.BUSY,EV.NEED_AUTH]);
 return cmd('P',new Uint8Array([planes])).then(function(){return w1;}).then(function(t){
  if(t.ev==EV.BUSY)return[503,'Badge is busy updating its display. Wait a few seconds and try again.'];
  if(t.ev==EV.NEED_AUTH)return[401,'Badge is locked. Enter the code again.'];
  function win1(){if(sent>=n){var w3=wev([EV.PH_OK,EV.PH_ERR],15000);
    return cmd('E').then(function(){return w3;}).then(function(e){
     return e.ev==EV.PH_OK?[200,'ok']:[500,'The badge could not use that image. Try again.'];});}
   var end=Math.min(n,sent+win),chain=Promise.resolve(),o;dbg('photo: window',sent,'->',end);
   for(o=sent;o<end;o+=chunk)(function(o){chain=chain.then(function(){
     return dw(body.subarray(o,Math.min(end,o+chunk)));});})(o);
   var w2=wev([EV.ACK,EV.NEED_AUTH],8000);
   return chain.then(function(){dbg('photo: window written, syncing');return cmd('S');}).then(function(){return w2;}).then(function(a){
    if(a.ev!=EV.ACK||a.arg!=end)return[500,'Transfer got out of sync. Try again.'];
    sent=end;if(cb)cb(sent/n);return win1();});}
  return win1();});}
/* The badge answers busy while it is still deploying the last submission: wait 5 s and try once more. */
function rp(kind,body,cb,lb){return tx(kind,body,cb).catch(function(e){
 if(kind=='photo'&&nr&&e&&e.message=='timeout'){nr=false;lb('Retrying more slowly...');return tx(kind,body,cb);}throw e;}).then(function(a){if(a[0]!=503)return a;
 return new Promise(function(ok){var n=5;(function t(){if(n<1)return ok();lb('Badge is busy, retrying in '+n+'s');n--;setTimeout(t,1000);})();})
 .then(function(){lb('Sending...');return tx(kind,body,cb);});});}
/* ---- connect / pairing ---- */
var bsec=$('bsec'),bn=$('bn'),cb=$('cb'),af=$('af'),ci=$('ci'),ch=$('ch');
function gate(on){authed=on;[].forEach.call(document.querySelectorAll('main > section:not(#bsec):not(#dbs)'),function(e){
 e.style.opacity=on?'':'.4';if(on)e.removeAttribute('inert');else e.setAttribute('inert','');});}
function bst(t,cls){bn.textContent=t;bn.className='cur'+(cls?' '+cls:'');}
function lost(){dbg('badge link lost');wt.forEach(function(w){clearTimeout(w.t);});wt=[];gate(false);af.style.display='none';cb.style.display='';
 cb.textContent='Connect';cb.disabled=false;bst('Not connected','e');}
function ckey(){return'code:'+(dev&&dev.name||'');}
function unlock(code){var w=wev([EV.AUTH_OK,EV.AUTH_BAD,EV.LOCKED],6000);
 return cmd('A',new TextEncoder().encode(code)).then(function(){return w;}).then(function(t){
  if(t.ev==EV.AUTH_OK){try{localStorage.setItem(ckey(),code);}catch(e){}gate(true);af.style.display='none';cb.style.display='none';
   bst(dev.name);ch.textContent='';poll();return true;}
  try{localStorage.removeItem(ckey());}catch(e){}
  ch.textContent=t.ev==EV.LOCKED?'Too many wrong codes. Wait 10 seconds and reconnect.':'Wrong code. Look at the number on the badge screen.';
  return false;});}
function connect(){
 if(!navigator.bluetooth){ch.textContent='This browser has no Web Bluetooth. On iPhone, open this page in the Bluefy browser.';return;}
 dbg('connect: requestDevice');cb.disabled=true;cb.textContent='Choosing...';ch.textContent='';
 navigator.bluetooth.requestDevice({filters:[{services:[SVC]},{namePrefix:'Badge-'}],optionalServices:[SVC]}).then(function(d){
  dev=d;dbg('connect: chose',d.name,'; gatt.connect');cb.textContent='Connecting...';bst(d.name||'Badge','e');d.addEventListener('gattserverdisconnected',lost);return d.gatt.connect();})
 .then(function(g){dbg('connect: gatt up');return g.getPrimaryService(SVC);})
 .then(function(sv){return Promise.all([sv.getCharacteristic(U_CT),sv.getCharacteristic(U_DT),sv.getCharacteristic(U_ST)]);})
 .then(function(c){chC=c[0];chD=c[1];chS=c[2];chS.addEventListener('characteristicvaluechanged',onst);dbg('connect: characteristics ok; starting notifications');return chS.startNotifications();})
 .then(rd).then(function(){var saved='';try{saved=localStorage.getItem(ckey())||'';}catch(e){}
  return saved?unlock(saved):false;})
 .then(function(ok){if(!ok){cb.style.display='none';af.style.display='flex';ci.value='';ci.focus();}})
 .catch(function(e){dbg('connect failed:',e);if(e&&e.name=='NotFoundError'){lost();return;}lost();ch.textContent='Could not connect: '+(e&&e.message||e);});}
cb.onclick=function(ev){ev.preventDefault();connect();};
$('af').onsubmit=function(ev){ev.preventDefault();if(ci.value.length==4)unlock(ci.value).then(function(ok){if(!ok)ci.select();}).catch(function(){ch.textContent='No answer from the badge.';});};
gate(false);lost();
/* ---- name ---- */
function sendName(v){sn.disabled=cl.disabled=true;sn.textContent='Sending...';
 rp('name',v,null,function(t){sn.textContent=t;})
 .then(function(a){if(a[0]==200){curName=a[1];curMode='name';ni.value='';ni.blur();toast(a[1]?'Name sent.':'Name cleared.');watch();}
  else alertBox(a[1]);})
 .catch(function(e){dbg('send failed:',e);alertBox('Could not reach the badge'+(e&&e.name?' ('+e.name+': '+(e.message||'')+')':'.'));})
 .then(function(){sn.textContent='Set';show();});}
$('nf').onsubmit=function(ev){ev.preventDefault();if(!sn.disabled)sendName(ni.value);};
cl.onclick=function(){sendName('');};
/* ---- photo editor ---- */
function draw(){
 ox=Math.min(0,Math.max(P-img.width*sc,ox));oy=Math.min(0,Math.max(Q-img.height*sc,oy));
 x.fillStyle='#fff';x.fillRect(0,0,P,Q);x.drawImage(img,ox,oy,img.width*sc,img.height*sc);dirty=true;}
function load(file){return createImageBitmap(file);}
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
  rp('photo',r.red?r.out:r.out.subarray(0,W*H/8),
   function(p){prog(p);ss.textContent='Sending '+Math.round(p*100)+'%';},
   function(t){ss.textContent=t;prog(-1);})
  .then(function(a){if(a[0]==200){reset();curMode='photo';show();toast('Sent! The panel takes a while to refresh.');watch();}
   else alertBox(a[1]);})
  .catch(function(e){dbg('send failed:',e);alertBox('Could not reach the badge'+(e&&e.name?' ('+e.name+': '+(e.message||'')+')':'.'));})
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

$('dbc').onclick=function(){var t=LOG.join('\n');
 (navigator.clipboard&&navigator.clipboard.writeText?navigator.clipboard.writeText(t):Promise.reject()).then(function(){toast('Log copied.');},
  function(){var r=document.createRange();r.selectNodeContents($('dbg'));var g=getSelection();g.removeAllRanges();g.addRange(r);toast('Select and copy the log.');});};
