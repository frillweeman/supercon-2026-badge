/* Badge Codes page: draws a QR code / barcode as a 960x672 1-bit picture and sends it over BLE (ble.js) as a
   one-plane photo. Query params ?type=qrcode&content=hello (optional &ec=) skip the form: connect + send at once. */
var N=W*H,M=40,
 fmt=$('fmt'),txt=$('txt'),ec=$('ec'),sz=$('sz'),cv=$('cv'),cx=cv.getContext('2d'),msg=$('msg'),go=$('go'),
 last=null,timer,sent=false,auto=false,up=false;
/* qz = quiet zone in modules (the canvas is white, so it is just empty margin) */
var F={
 qrcode:{qz:4,ec:[['L','L'],['M','M'],['Q','Q'],['H','H']],def:1},
 pdf417:{qz:2,ec:[0,1,2,3,4,5,6,7,8].map(function(v){return[v,'Level '+v];}),def:2},
 datamatrix:{qz:2},
 azteccode:{qz:1,ec:[[10,'10%'],[23,'23%'],[50,'50%'],[75,'75%']],def:1},
 code128:{qz:10,lin:1},code39:{qz:10,lin:1},
 ean13:{qz:10,lin:1,digits:'EAN-13 needs 12 or 13 digits'},ean8:{qz:10,lin:1,digits:'EAN-8 needs 7 or 8 digits'},
 upca:{qz:10,lin:1,digits:'UPC-A needs 11 or 12 digits'},interleaved2of5:{qz:10,lin:1,digits:'Needs an even number of digits'}};
function say(t,bad){msg.textContent=t;msg.className=bad?'e':'';}
function setEc(){var f=F[fmt.value];ec.innerHTML='';$('ecw').style.display=f.ec?'':'none';
 if(f.ec)f.ec.forEach(function(o,i){var e=document.createElement('option');e.value=o[0];e.textContent=o[1];if(i==f.def)e.selected=true;ec.appendChild(e);});}
/* The code at 1 px per module (bwip-js gives exact geometry); h = bar height in mm for linear codes. */
function enc(f,h){var c=document.createElement('canvas'),o={bcid:fmt.value,text:txt.value,scale:1,includetext:false};
 if(f.ec)o.eclevel=fmt.value=='qrcode'?ec.value:+ec.value;
 if(f.lin)o.height=h||10;
 bwipjs.toCanvas(c,o);return c;}
/* Picture = code at integer scale k (the slider; 0 = largest that fits), centred on white. */
function build(k){
 var f=F[fmt.value],q=f.qz,aw=W-2*M,ah=H-2*M,c=enc(f),w=c.width,h=c.height,max;
 if(f.lin){max=Math.max(1,Math.min(8,Math.floor(aw/(w+2*q))));
  k=Math.min(k||max,max); /* bar height follows the scale: tall, but within the screen */
  c=enc(f,Math.max(5,Math.min(ah-2*q*k,Math.round(w*k*0.4))/k/2.835));w=c.width;h=c.height;
  max=Math.max(1,Math.min(max,Math.floor(ah/(h+2*q))));k=Math.min(k,max);}
 else{max=Math.floor(Math.min(aw/(w+2*q),ah/(h+2*q)));k=Math.min(k||max,max);}
 if(max<1)throw new Error('Too much data for the screen: use less content or a lower error correction.');
 var o=document.createElement('canvas');o.width=W;o.height=H;var g=o.getContext('2d');
 g.fillStyle='#fff';g.fillRect(0,0,W,H);g.imageSmoothingEnabled=false;
 g.drawImage(c,Math.floor((W-w*k)/2),Math.floor((H-h*k)/2),w*k,h*k);
 var p=g.getImageData(0,0,W,H),d=p.data,out=new Uint8Array(N/8),i;
 for(i=0;i<N;i++){var v=d[4*i]<128?0:255;if(!v)out[i>>3]|=128>>(i&7);d[4*i]=d[4*i+1]=d[4*i+2]=v;}
 return{out:out,im:p,k:k,max:max};}
function render(fit){clearTimeout(timer);
 try{
  if(!txt.value)throw new Error('Enter some content.');
  var f=F[fmt.value];if(f&&f.digits&&!/^[0-9]+$/.test(txt.value))throw new Error(f.digits);
  var r=build(fit?0:+sz.value);last=r;cx.putImageData(r.im,0,0);
  sz.max=r.max;sz.value=r.k;$('szv').textContent=r.k+' px per module';say('');
  go.disabled=!up;
 }catch(e){last=null;go.disabled=true;cx.fillStyle='#fff';cx.fillRect(0,0,W,H);say(String(e.message||e),true);}
 return !!last;}
function later(fit){clearTimeout(timer);timer=setTimeout(function(){render(fit);},80);}
fmt.onchange=function(){setEc();later(true);};
ec.onchange=txt.oninput=function(){later(true);};
sz.oninput=function(){later(false);};
/* ---- send ---- */
function send(){if(!last||!up)return;go.disabled=true;
 rp('photo',last.out,function(p){say('Sending '+Math.round(p*100)+'%');},say)
 .then(function(a){if(a[0]==200){sent=true;say('Sent! The panel takes a while to refresh.');toast('Sent to the badge.');}else{say(a[1],true);alertBox(a[1]);}})
 .catch(function(e){dbg('send failed:',e);say('Could not reach the badge.',true);})
 .then(function(){go.disabled=!(last&&up);});}
go.onclick=send;
function onBadgeUp(){up=true;go.disabled=!last;if(auto){auto=false;send();}}
function onBadgeDown(){up=false;go.disabled=true;}
/* ---- start-up: ?type=&content= (aliases format, text, data) = automatic mode ---- */
var qs=new URLSearchParams(location.search),qt=qs.get('type')||qs.get('format'),qc=qs.get('content');
if(qc===null)qc=qs.get('text');if(qc===null)qc=qs.get('data');
setEc();
if(qt||qc!==null){
 if(qt&&!F[qt])say('Unknown type "'+qt+'". Choose one below.',true);
 else{if(qt)fmt.value=qt;if(qc!==null)txt.value=qc;setEc();
  if(qs.get('ec')!==null&&F[fmt.value].ec)ec.value=qs.get('ec');
  if(qt&&qc!==null){ /* complete: connect and send without further input */
   window.CONNECT_LABEL='Connect & send';
   if(window.bwipjs&&render(true)){auto=true;say('Connecting to the badge...');
    autoConnect().then(function(ok){if(!ok&&auto){say('Tap to connect and send.');cb.textContent=CONNECT_LABEL;}});}
  }}
}
if(!auto){if(window.bwipjs)render(true);else say('Encoder library failed to load.',true);}
