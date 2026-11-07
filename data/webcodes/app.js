/* Badge Codes page logic. Draws the whole 960x672 picture in the browser and POSTs 1-bit
   planes to /photo (black plane, optionally followed by a red plane; 120 bytes/row, MSB first). */
(function(){
var W=960,H=672,N=W*H,RED=[200,30,30];
function $(i){return document.getElementById(i);}
var fmt=$('fmt'),txt=$('txt'),cap=$('cap'),ec=$('ec'),pv=$('pv'),pvx=pv.getContext('2d'),msg=$('msg'),go=$('go'),
 ins=$('ins'),border=null,last=null,timer;
/* quiet zone in modules, minimum module counts irrelevant: bars are integer-scaled */
var F={
 qrcode:{qz:4,ec:[['L','L'],['M','M'],['Q','Q'],['H','H']],def:1,k:'eclevel'},
 pdf417:{qz:2,ec:[0,1,2,3,4,5,6,7,8].map(function(v){return[v,'Level '+v];}),def:2,k:'eclevel',tall:1},
 datamatrix:{qz:2},
 azteccode:{qz:1,ec:[[10,'10%'],[23,'23%'],[50,'50%'],[75,'75%']],def:1,k:'eclevel'},
 code128:{qz:10,linear:1},code39:{qz:10,linear:1},
 ean13:{qz:10,linear:1,digits:'EAN-13 needs 12 or 13 digits'},ean8:{qz:10,linear:1,digits:'EAN-8 needs 7 or 8 digits'},
 upca:{qz:10,linear:1,digits:'UPC-A needs 11 or 12 digits'},interleaved2of5:{qz:10,linear:1,digits:'Needs an even number of digits'}};
function say(t,bad){msg.textContent=t;msg.className=bad?'e':'';}
function setEc(){var f=F[fmt.value];ec.innerHTML='';$('ecw').style.display=f.ec?'':'none';
 if(f.ec)f.ec.forEach(function(o,i){var e=document.createElement('option');e.value=o[0];e.textContent=o[1];if(i==f.def)e.selected=true;ec.appendChild(e);});}
/* Render the code at 1 px per module (bwip-js, exact integer geometry), return a canvas. */
function code(f,h){var c=document.createElement('canvas'),o={bcid:fmt.value,text:txt.value,scale:1,includetext:false,
 rotate:$('rot').checked?'R':'N'};
 if(f.k)o[f.k]=f.k=='eclevel'&&fmt.value=='qrcode'?ec.value:+ec.value;
 if(f.linear)o.height=h||10;
 bwipjs.toCanvas(c,o);return c;}
function shown(){var t=txt.value;return cap.value||t;}
/* Draw the code layer to a W x H canvas (black on white). Returns the box it occupies incl. quiet zone. */
function layout(m){
 var f=F[fmt.value],o=document.createElement('canvas');o.width=W;o.height=H;var g=o.getContext('2d');
 g.fillStyle='#fff';g.fillRect(0,0,W,H);
 var showc=$('showcap').checked&&shown(),capH=showc?100:0,
  aw=W-2*m,ah=H-2*m-capH,c=code(f),w=c.width,h=c.height,rot=$('rot').checked,q=f.qz,k;
 if(f.linear){ /* width fixes the scale; then re-render with a bar height that suits the area */
  k=Math.max(1,Math.min(8,Math.floor(aw/((rot?h:w)+2*q))));
  if(!rot){var bh=Math.min(ah-2*q*k,Math.round(w*k*0.4))/k;c=code(f,Math.max(5,bh/2.835));w=c.width;h=c.height;}
  else{var bh2=Math.min(aw-2*q*k,Math.round(h*k*0.4))/k;c=code(f,Math.max(5,bh2/2.835));w=c.width;h=c.height;
   k=Math.max(1,Math.min(8,Math.floor(ah/(h+2*q))));}
  k=Math.max(1,Math.min(k,Math.floor(aw/(w+2*q)),Math.floor(ah/(h+2*q))));
 }else k=Math.floor(Math.min(aw/(w+2*q),ah/(h+2*q)));
 if(k<1)throw new Error('Too much data for the screen: use fewer characters or a lower error correction.');
 var bw=(w+2*q)*k,bhp=(h+2*q)*k,bx=m+Math.floor((aw-bw)/2),by=m+Math.floor((ah-bhp)/2);
 g.imageSmoothingEnabled=false;g.drawImage(c,bx+q*k,by+q*k,w*k,h*k);
 if(showc){var fs=72;g.fillStyle=$('capred').checked?'rgb('+RED+')':'#000';g.textAlign='center';g.textBaseline='middle';
  do{g.font='700 '+fs+'px system-ui,-apple-system,sans-serif';fs-=4;}while(g.measureText(showc).width>aw&&fs>16);
  g.fillText(showc,W/2,H-m-capH/2);}
 return{cv:o,box:[bx,by,bw,bhp],redCap:$('capred').checked&&!!showc,capBox:[0,H-m-capH,W,capH]};}
/* Border layer: stretched to the panel, opaque pixels classified black / red. */
function borderPix(){if(!border)return null;var c=document.createElement('canvas');c.width=W;c.height=H;
 var g=c.getContext('2d');g.drawImage(border,0,0,W,H);return g.getImageData(0,0,W,H).data;}
function compose(){
 var L=layout(+ins.value),p=L.cv.getContext('2d').getImageData(0,0,W,H).data,b=borderPix(),
  blk=new Uint8Array(N/8),red=new Uint8Array(N/8),im=new ImageData(W,H),q=im.data,mode=$('bm').value,
  i,j,r,gc,bl,isRed,any=false,bad=0,bx=L.box;
 for(i=0;i<N;i++){j=4*i;r=p[j];gc=p[j+1];bl=p[j+2];
  /* code layer: red caption pixels come through as r>>g; everything else is thresholded to black */
  isRed=L.redCap&&r>180&&gc<140&&bl<140;var dark=!isRed&&(0.299*r+0.587*gc+0.114*bl)<128;
  if(b&&b[j+3]>128){var br=b[j],bg=b[j+1],bb=b[j+2];
   var rd=br>150&&bg<120&&bb<120,lum=0.299*br+0.587*bg+0.114*bb;
   /* opaque light pixels (white fill inside the border art) stay white and cover the code layer */
   if(lum>200&&!rd){isRed=false;dark=false;}else{isRed=mode=='red'||(mode=='keep'&&rd);dark=!isRed;}
   var x=i%W,y=(i/W)|0;if(x>=bx[0]&&x<bx[0]+bx[2]&&y>=bx[1]&&y<bx[1]+bx[3])bad++;}
  if(isRed){red[i>>3]|=128>>(i&7);any=true;q[j]=RED[0];q[j+1]=RED[1];q[j+2]=RED[2];}
  else if(dark){blk[i>>3]|=128>>(i&7);q[j]=q[j+1]=q[j+2]=0;}
  else q[j]=q[j+1]=q[j+2]=255;
  q[j+3]=255;}
 var out=new Uint8Array(any?N/4:N/8);out.set(blk);if(any)out.set(red,N/8);
 return{out:out,im:im,warn:bad>0};}
function render(){clearTimeout(timer);timer=setTimeout(function(){
 try{
  if(!txt.value){last=null;go.disabled=true;say('Enter some data.');pvx.fillStyle='#fff';pvx.fillRect(0,0,W,H);return;}
  var f=F[fmt.value];if(f.digits&&!/^[0-9]+$/.test(txt.value)){throw new Error(f.digits);}
  last=compose();pvx.putImageData(last.im,0,0);go.disabled=false;
  say(last.warn?'Warning: the border covers the code\'s quiet zone. Raise the distance slider or scanners may fail.':'',last.warn);
 }catch(e){last=null;go.disabled=true;say(String(e.message||e),true);}
},60);}
['fmt','txt','cap','ec','showcap','capred','rot','bm','ins'].forEach(function(i){$(i).oninput=$(i).onchange=function(){
 if(i=='fmt')setEc();if(i=='ins')$('insv').textContent=ins.value;render();};});
$('insv').textContent=ins.value;
$('bf').onchange=function(){var f=this.files[0];if(!f)return;createImageBitmap(f).then(function(b){border=b;
 render();}).catch(function(){say('Could not read that image.',true);});};
$('bx').onclick=function(){border=null;$('bf').value='';render();};
/* ---- send ---- */
function post(body,cb){var r=new XMLHttpRequest();r.open('POST','/photo');r.setRequestHeader('Content-Type','application/octet-stream');
 r.timeout=120000;r.onload=function(){cb(r.status,r.responseText);};r.onerror=r.ontimeout=function(){cb(0,'Could not reach the badge.');};r.send(body);}
function poll(n){fetch('/status',{cache:'no-store'}).then(function(r){return r.json();}).then(function(s){
 if(s.busy&&n<40){say('Panel refreshing...');setTimeout(function(){poll(n+1);},3000);}else say('Done, it is on the badge.');})
 .catch(function(){say('Sent. (Lost the connection while checking.)');});}
go.onclick=function(){if(!last)return;go.disabled=true;
 (function tryit(n){say(n?'Badge is busy, retrying...':'Sending...');
  post(last.out,function(st,t){
   if(st==503&&n<3){setTimeout(function(){tryit(n+1);},5000);return;}
   go.disabled=false;
   if(st==200){say('Sent. Panel refreshing...');setTimeout(function(){poll(0);},1500);}else say(t||'Failed ('+st+')',true);});})(0);};
setEc();
if(window.bwipjs)render();else say('Encoder library failed to load.',true);
})();
