/* Badge Boarding Pass page: reads the barcode from a boarding pass image (or pasted text), parses it (bcbp.js),
   draws a Delta style pass (passdraw.js) and sends it over BLE (ble.js) as a two-plane photo (black + red). */
var cv=$('cv'),cx=cv.getContext('2d'),msg=$('msg'),pm=$('pm'),go=$('go'),pf=$('pf'),raw=$('raw'),lg=$('lg'),bt=$('bt'),
 zn=$('zn'),gt=$('gt'),bd=$('bd'),pass=null,last=null,up=false,timer,seq=0;
function say(t,bad){msg.textContent=t;msg.className=bad?'e':'';}
function psay(t,bad){pm.textContent=t;pm.style.color=bad?'var(--ac)':'';}
function blank(){cx.fillStyle='#fff';cx.fillRect(0,0,960,672);}
blank();
if(window.ZXingWASM)ZXingWASM.setZXingModuleOverrides({locateFile:function(p,pre){return/\.wasm$/.test(p)?'zxing_reader.wasm':pre+p;}});
/* ---- render ---- */
function render(){clearTimeout(timer);last=null;go.disabled=true;
 if(!pass){blank();return;}
 try{var leg=pass.legs[+lg.value||0],
  r=renderPass({name:pass.name.full,leg:leg,zone:zn.value.trim().toUpperCase(),gate:gt.value.trim().toUpperCase(),boards:bd.value.trim().toUpperCase(),raw:pass.raw},bt.value);
  cx.drawImage(r.canvas,0,0);last=r;say('');go.disabled=!up;
 }catch(e){blank();say(String(e.message||e),true);}}
function later(){clearTimeout(timer);timer=setTimeout(render,120);}
[zn,gt,bd].forEach(function(e){e.oninput=later;});bt.onchange=lg.onchange=render;
/* ---- barcode text -> pass ---- */
function use(text,fmt){
 var p=BCBP.parse(text);pass=p;
 lg.innerHTML='';p.legs.forEach(function(l,i){var o=document.createElement('option');o.value=i;
  o.textContent=l.from+' to '+l.to+' · DL '+l.flight+' · '+BCBP.dstr(l.date);lg.appendChild(o);});
 $$('lgw',p.legs.length>1?'':'none');
 if(fmt)bt.value=/aztec/i.test(fmt)?'azteccode':'pdf417';
 psay('Found a pass for '+p.name.full+(p.legs.length>1?' ('+p.legs.length+' flights).':'.'));
 render();}
raw.oninput=function(){var t=raw.value.trim();if(!t){return;}
 try{use(t);}catch(e){pass=null;lg.innerHTML='';render();psay(String(e.message||e),true);}};
/* ---- image -> barcode text ---- */
function loadImg(file){return new Promise(function(ok,no){var u=URL.createObjectURL(file),im=new Image();
 im.onload=function(){URL.revokeObjectURL(u);ok(im);};im.onerror=function(){URL.revokeObjectURL(u);no(new Error('Could not open that image.'));};im.src=u;});}
function scan(im,maxSide){var s=Math.min(1,maxSide/Math.max(im.naturalWidth,im.naturalHeight)),w=Math.round(im.naturalWidth*s),h=Math.round(im.naturalHeight*s),
 c=document.createElement('canvas');c.width=w;c.height=h;var g=c.getContext('2d');g.fillStyle='#fff';g.fillRect(0,0,w,h);g.drawImage(im,0,0,w,h);
 return ZXingWASM.readBarcodesFromImageData(g.getImageData(0,0,w,h),{formats:['PDF417','Aztec'],tryHarder:true,tryRotate:true,tryInvert:true,tryDownscale:true,maxNumberOfSymbols:4});}
pf.onchange=function(){var f=pf.files&&pf.files[0],id=++seq;if(!f)return;
 if(!window.ZXingWASM){psay('The barcode reader failed to load. Paste the barcode text instead.',true);return;}
 psay('Reading the barcode...');dbg('image',f.type,f.size);
 loadImg(f).then(function(im){var sizes=[1600,2600,4200],bad='';
  function next(i){if(i>=sizes.length)throw new Error(bad||'No boarding pass barcode found. Use a clearer, larger image of the whole code.');
   return scan(im,sizes[i]).then(function(rs){dbg('scan',sizes[i],rs.length);
    for(var k=0;k<rs.length;k++){try{return{p:BCBP.parse(rs[k].text),t:rs[k].text,f:rs[k].format};}catch(e){bad=String(e.message);}}
    return next(i+1);});}
  return next(0);})
 .then(function(r){if(id!=seq)return;raw.value=r.t;use(r.t,r.f);})
 .catch(function(e){if(id!=seq)return;dbg('read failed',e);pass=null;lg.innerHTML='';render();psay(String(e.message||e),true);});};
/* ---- send ---- */
function send(){if(!last||!up)return;go.disabled=true;
 rp('photo',last.planes,function(p){say('Sending '+Math.round(p*100)+'%');},say)
 .then(function(a){if(a[0]==200){say('Sent! The panel takes a while to refresh.');toast('Sent to the badge.');}else{say(a[1],true);alertBox(a[1]);}})
 .catch(function(e){dbg('send failed:',e);say('Could not reach the badge.',true);})
 .then(function(){go.disabled=!(last&&up);});}
go.onclick=send;
function onBadgeUp(){up=true;go.disabled=!last;}
function onBadgeDown(){up=false;go.disabled=true;}
if(!window.bwipjs)say('Barcode library failed to load.',true);
