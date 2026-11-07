var W=960,H=672,P=W/2,Q=H/2,img,sc,mn,ox,oy,d=null,tab='c',rc=true,dirty=true,last=null,busy=false,tm,tk,pt,pn=0,opener,fo;
function $(i){return document.getElementById(i);}
var f=$('f'),fc=$('fc'),c=$('c'),x=c.getContext('2d'),pv=$('pv'),pvx=pv.getContext('2d'),z=$('z'),rs=$('rs'),
cur=$('cur'),sm=$('sm'),sub=$('sub'),ni=$('ni'),sn=$('sn'),cl=$('cl'),sh=$('sh'),ss=$('ss'),pg=$('pg'),
curName='',curMode='name';$('hp').textContent=location.host;
function $$(i,v){$(i).style.display=v;}
/* ---- status ---- */
function note(t,b){sub.className='sub'+(b?' bz':'');sm.textContent=t;}
function show(){cur.textContent=curMode=='photo'?'Photo':(curName||'Nothing yet');
 cur.className='cur'+(curMode!='photo'&&!curName?' e':'');
 sn.disabled=!ni.value||(ni.value==curName&&curMode=='name');
 cl.disabled=curMode=='name'&&!curName;$('cn').textContent=ni.value.length+'/24';}
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
