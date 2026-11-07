/* Fake navigator.bluetooth that behaves like apps/webnametag.py (pairing code 1234, 2 s panel refresh).
   Used only by tools/preview.py. */
(function(){
var T=1234+'',st={authed:0,busy:0,mode:0,name:'',chunk:180},buf=null,got=0,total=0,ls=[],bad=0;
function val(ev,arg,full){var n=full?new TextEncoder().encode(st.name):[],b=new Uint8Array(10+n.length),v=new DataView(b.buffer);
 v.setUint8(0,st.authed);v.setUint8(1,st.busy);v.setUint8(2,st.mode);v.setUint8(3,ev||0);v.setUint16(4,st.chunk);v.setUint32(6,arg||0);b.set(n,10);return new DataView(b.buffer);}
function ev(e,a){setTimeout(function(){ls.forEach(function(f){f({target:{value:val(e,a,false)}});});},20);}
function work(){st.busy=1;setTimeout(function(){st.busy=0;},2000);}
var C={writeValueWithResponse:function(a){var op=String.fromCharCode(a[0]),rest=a.slice(1);return new Promise(function(ok){setTimeout(ok,10);
 if(op=='A'){if(new TextDecoder().decode(rest)==T){st.authed=1;ev(1);}else{bad++;ev(bad>=3?6:2);}return;}
 if(!st.authed)return ev(10);
 if(op=='N'){if(st.busy)return ev(7);st.name=new TextDecoder().decode(rest);st.mode=st.name?1:0;work();return ev(8);}
 if(op=='P'){if(st.busy)return ev(7);total=rest[0]*80640;got=0;buf=1;return ev(3);}
 if(op=='S')return ev(9,got);
 if(op=='E'){var good=buf&&got==total;buf=null;if(good){st.mode=2;work();}return ev(good?4:5);}});}};
var D={writeValueWithoutResponse:function(a){if(buf)got+=a.length;return new Promise(function(ok){setTimeout(ok,1);});}};
D.writeValueWithResponse=D.writeValueWithoutResponse;
var S={readValue:function(){return Promise.resolve(val(0,0,true));},startNotifications:function(){return Promise.resolve(S);},
 addEventListener:function(n,f){ls.push(f);}};
var chars={};chars['7b1f0002-5c0e-4b6e-9a3d-5e11c0de0001']=C;chars['7b1f0003-5c0e-4b6e-9a3d-5e11c0de0001']=D;chars['7b1f0004-5c0e-4b6e-9a3d-5e11c0de0001']=S;
var dev={name:'Badge-MOCK',addEventListener:function(){},gatt:{connect:function(){return Promise.resolve({getPrimaryService:function(){
 return Promise.resolve({getCharacteristic:function(u){return Promise.resolve(chars[u]);}});}});}}};
Object.defineProperty(navigator,'bluetooth',{value:{requestDevice:function(){return Promise.resolve(dev);}},configurable:true});
})();
