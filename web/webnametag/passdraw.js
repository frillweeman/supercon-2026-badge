/* Draws a boarding pass as a 960x672 picture in pure black / pure red / white and splits it into the badge's
   two 1-bit planes (black first, then red; 1 = ink). No DOM access except creating canvases, so the layout can
   be tested outside a browser. Everything shown comes from the parsed barcode (bcbp.js) plus the optional
   zone / gate / boarding time the user types, which the barcode does not carry. */
var PW=960,PH=672,RED='#ff0000',BLK='#000000',FONT='"Helvetica Neue",Helvetica,Arial,sans-serif';
function mk(w,h){var c=document.createElement('canvas');c.width=w;c.height=h;return c;}
/* Text with letter spacing, shrunk to fit maxW. align: l | r. */
function txt(g,s,x,y,size,col,o){o=o||{};var w=o.weight||'700',sp=o.sp||0,t=o.upper===false?s:String(s).toUpperCase(),wd;
 for(;;){g.font=w+' '+size+'px '+FONT;wd=g.measureText(t).width+sp*Math.max(0,t.length-1);
  if(!o.maxW||wd<=o.maxW||size<=12)break;size-=2;}
 g.fillStyle=col;g.textBaseline='alphabetic';g.textAlign='left';
 var sx=o.align=='r'?x-wd:o.align=='c'?x-wd/2:x,i;
 if(!sp)g.fillText(t,sx,y);else for(i=0;i<t.length;i++){g.fillText(t[i],sx,y);sx+=g.measureText(t[i]).width+sp;}
 return wd;}
function tri(g,x,y,w,h,col){g.fillStyle=col;g.beginPath();g.moveTo(x,y+h);g.lineTo(x+w/2,y);g.lineTo(x+w,y+h);g.closePath();g.fill();}
/* The barcode re-encoded from the pass's own text, scaled by whole pixels to fit bw x bh. type: pdf417 | azteccode. */
function makeBarcode(raw,type,bw,bh){
 function enc(o){var c=mk(1,1);bwipjs.toCanvas(c,Object.assign({bcid:type,text:raw,scale:1,includetext:false},o));return c;}
 var c,k,r;
 if(type=='pdf417'){
  var best=null,cols;
  for(cols=6;cols<=14;cols++){try{c=enc({columns:cols,rowmult:1,eclevel:4});}catch(e){continue;}
   k=Math.floor(bw/c.width);if(k>=1&&(!best||k>best.k||(k==best.k&&cols<best.cols)))best={k:k,cols:cols,w:c.width,h:c.height};}
  if(!best)throw new Error('The barcode is too big for the pass.');
  /* rows must be at least 1 px tall at this scale; stretch them to fill the box height */
  var rm=Math.max(1,Math.min(10,Math.floor(bh/(best.h*best.k))));
  c=enc({columns:best.cols,rowmult:rm,eclevel:4});k=best.k;
  while(k>1&&c.height*k>bh)k--;
 }else{
  c=enc({});k=Math.floor(Math.min(bw/c.width,bh/c.height));
 }
 if(k<1)throw new Error('The barcode is too big for the pass.');
 r=mk(c.width*k,c.height*k);var g=r.getContext('2d');g.imageSmoothingEnabled=false;g.drawImage(c,0,0,r.width,r.height);
 return r;}
/* d = {name,leg,zone,gate,boards,raw}. Returns the box left for the barcode. */
function drawPass(cv,d,side){
 var g=cv.getContext('2d'),L=d.leg,i,fields=[],cab=BCBP.cabin(L.cabin);
 g.fillStyle='#fff';g.fillRect(0,0,PW,PH);
 /* header band */
 g.fillStyle=RED;g.fillRect(0,0,PW,88);
 tri(g,36,16,58,54,'#fff');tri(g,49,38,32,26,RED);
 txt(g,'DELTA',108,62,46,'#fff',{sp:4});
 txt(g,'BOARDING PASS',924,58,28,'#fff',{align:'r',sp:3,maxW:420});
 g.strokeStyle=BLK;g.lineWidth=4;g.beginPath();g.moveTo(2,88);g.lineTo(2,PH-2);g.lineTo(PW-2,PH-2);g.lineTo(PW-2,88);g.stroke();
 /* passenger */
 txt(g,'PASSENGER',36,126,18,RED,{sp:3});
 txt(g,d.name,36,170,44,BLK,{maxW:cab?600:888});
 if(cab){txt(g,'CABIN',924,126,18,RED,{align:'r',sp:3});txt(g,cab,924,170,30,BLK,{align:'r',maxW:260});}
 /* route */
 txt(g,L.from,32,308,128,BLK,{sp:2});txt(g,L.to,928,308,128,BLK,{align:'r',sp:2});
 g.strokeStyle=RED;g.lineWidth=6;g.setLineDash([16,10]);g.beginPath();g.moveTo(350,262);g.lineTo(590,262);g.stroke();g.setLineDash([]);
 g.fillStyle=RED;g.beginPath();g.moveTo(612,262);g.lineTo(582,244);g.lineTo(582,280);g.closePath();g.fill();
 if(L.fromCity)txt(g,L.fromCity,36,346,26,BLK,{maxW:400});
 if(L.toCity)txt(g,L.toCity,924,346,26,BLK,{align:'r',maxW:400});
 /* fields: 4 per row (3 beside a square code when side) */
 fields.push(['FLIGHT','DL '+L.flight]);
 fields.push(['DATE',BCBP.dstr(L.date)]);
 fields.push(['SEAT',L.seat||'—']);
 if(d.zone)fields.push(['ZONE',d.zone]);
 if(d.gate)fields.push(['GATE',d.gate]);
 if(d.boards)fields.push(['BOARDS',d.boards]);
 if(L.seq)fields.push(['SEQ',L.seq]);
 fields.push(['CONFIRMATION',L.pnr]);
 fields=fields.slice(0,8);
 var y0=376,rh=86,end=y0,nc=side?3:4,cw=side?204:228;
 fields.forEach(function(f,n){var cx=36+(n%nc)*cw,ry=y0+Math.floor(n/nc)*rh;
  txt(g,f[0],cx,ry+18,17,RED,{sp:2});txt(g,f[1],cx,ry+62,46,BLK,{maxW:cw-16});end=ry+rh;});
 if(side)return{top:y0,left:672,w:252,h:PH-24-y0};
 /* tear line, then the barcode below */
 var top=end+14;
 g.strokeStyle=BLK;g.lineWidth=2;g.setLineDash([12,9]);g.beginPath();g.moveTo(24,top);g.lineTo(PW-24,top);g.stroke();g.setLineDash([]);
 return{top:top+16,left:36,w:888,h:PH-24-(top+16)};}
/* Compose the whole picture; returns {canvas,planes} (planes = black bytes then red bytes). */
function renderPass(d,type){
 var cv=mk(PW,PH),b=drawPass(cv,d,type=='azteccode'),bar=makeBarcode(d.raw,type,b.w,b.h);
 cv.getContext('2d').drawImage(bar,b.left+Math.floor((b.w-bar.width)/2),b.top+Math.floor((b.h-bar.height)/2));
 return quantize(cv);}
function quantize(cv){
 var g=cv.getContext('2d'),im=g.getImageData(0,0,PW,PH),p=im.data,N=PW*PH,blk=new Uint8Array(N/8),red=new Uint8Array(N/8),i,r,gr,b,o;
 for(i=0;i<N;i++){r=p[4*i];gr=p[4*i+1];b=p[4*i+2];o=0;
  if(r<128&&gr<128&&b<128){blk[i>>3]|=128>>(i&7);o=0;}
  else if(r-gr>100&&r-b>100){red[i>>3]|=128>>(i&7);o=1;}else o=2;
  p[4*i]=o==2?255:o==1?255:0;p[4*i+1]=o==2?255:0;p[4*i+2]=o==2?255:0;}
 g.putImageData(im,0,0);
 var out=new Uint8Array(N/4);out.set(blk,0);out.set(red,N/8);
 return{canvas:cv,planes:out};}
if(typeof module!='undefined')module.exports={renderPass:renderPass,PW:PW,PH:PH};
