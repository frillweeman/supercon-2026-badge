/* IATA Resolution 792 boarding pass barcode (BCBP, format "M") parser. Only the mandatory per-leg fields
   are read; they are what the pass is drawn from. Fixed offsets:
   0 'M' | 1 legs | 2-21 name | 22 e-ticket | then per leg (37 chars, the first leg starting at 23):
   0-6 PNR | 7-9 from | 10-12 to | 13-15 carrier | 16-20 flight | 21-23 julian day | 24 compartment |
   25-28 seat | 29-33 check-in sequence | 34 status | 35-36 size of the conditional data (hex) that follows. */
var BCBP=(function(){
var AP=('ATL Atlanta|LAX Los Angeles|JFK New York|LGA New York|EWR Newark|BOS Boston|MSP Minneapolis|DTW Detroit|SEA Seattle|SLC Salt Lake City|'+
'SFO San Francisco|SAN San Diego|LAS Las Vegas|PHX Phoenix|DEN Denver|DFW Dallas|IAH Houston|AUS Austin|ORD Chicago|MDW Chicago|MCO Orlando|MIA Miami|'+
'FLL Fort Lauderdale|TPA Tampa|RSW Fort Myers|PBI West Palm Beach|JAX Jacksonville|CLT Charlotte|RDU Raleigh|BNA Nashville|MSY New Orleans|MEM Memphis|'+
'STL St. Louis|MCI Kansas City|IND Indianapolis|CVG Cincinnati|CMH Columbus|CLE Cleveland|PIT Pittsburgh|BUF Buffalo|ROC Rochester|SYR Syracuse|ALB Albany|'+
'BWI Baltimore|IAD Washington|DCA Washington|PHL Philadelphia|PVD Providence|BDL Hartford|PWM Portland|BTV Burlington|CHS Charleston|SAV Savannah|'+
'PDX Portland|SMF Sacramento|OAK Oakland|SJC San Jose|BUR Burbank|SNA Orange County|ONT Ontario|PSP Palm Springs|ANC Anchorage|HNL Honolulu|OGG Maui|'+
'KOA Kona|LIH Lihue|BOI Boise|ABQ Albuquerque|TUS Tucson|ELP El Paso|SAT San Antonio|OKC Oklahoma City|TUL Tulsa|OMA Omaha|MKE Milwaukee|'+
'YYZ Toronto|YUL Montreal|YVR Vancouver|YYC Calgary|MEX Mexico City|CUN Cancun|SJD Los Cabos|PVR Puerto Vallarta|SJU San Juan|NAS Nassau|MBJ Montego Bay|PUJ Punta Cana|'+
'LHR London|LGW London|CDG Paris|AMS Amsterdam|FRA Frankfurt|MUC Munich|FCO Rome|MAD Madrid|BCN Barcelona|LIS Lisbon|DUB Dublin|ZRH Zurich|CPH Copenhagen|'+
'ARN Stockholm|OSL Oslo|KEF Reykjavik|ATH Athens|IST Istanbul|TLV Tel Aviv|DXB Dubai|DOH Doha|JNB Johannesburg|NRT Tokyo|HND Tokyo|ICN Seoul|PEK Beijing|PVG Shanghai|'+
'HKG Hong Kong|SIN Singapore|SYD Sydney|MEL Melbourne|AKL Auckland|BOG Bogota|LIM Lima|GRU Sao Paulo|EZE Buenos Aires|SCL Santiago|PTY Panama City|SAL San Salvador').split('|')
 .reduce(function(m,s){m[s.slice(0,3)]=s.slice(4);return m;},{});
var TITLES=/\s+(MR|MRS|MS|MISS|MSTR|MX|DR|PROF|REV|CHD|INF)$/;
function t(s){return s.replace(/\s+$/,'').replace(/^\s+/,'');}
/* Julian day (1..366) -> Date (UTC midnight), year chosen so the result is nearest to today. */
function when(j,now){var y=(now||new Date()).getUTCFullYear(),best=null,i,d;
 for(i=-1;i<=1;i++){d=new Date(Date.UTC(y+i,0,j));if(!best||Math.abs(d-now)<Math.abs(best-now))best=d;}
 return best;}
function name(raw){var s=t(raw),p=s.split('/'),last=p[0],first=p.slice(1).join(' ');
 first=t(first.replace(TITLES,''));return{last:last,first:first,full:first?first+' '+last:last,raw:s};}
function parse(str,now){
 now=now||new Date();
 if(typeof str!='string')throw new Error('No barcode text.');
 var s=str.replace(/[\r\n]+$/,'');
 if(s.charAt(0)!='M'||!/^[1-4]$/.test(s.charAt(1)))throw new Error('Not an airline boarding pass barcode (should start with M1).');
 if(s.length<60)throw new Error('The barcode is too short to be a boarding pass.');
 var n=+s.charAt(1),pos=23,legs=[],i,m,size,j,fl;
 for(i=0;i<n;i++){
  m=s.substr(pos,37);if(m.length<37)throw new Error('The barcode is cut off (leg '+(i+1)+').');
  size=parseInt(m.substr(35,2),16);if(isNaN(size))throw new Error('The barcode data is damaged (leg '+(i+1)+').');
  j=+m.substr(21,3);
  fl=m.substr(16,5);
  legs.push({pnr:t(m.substr(0,7)),from:m.substr(7,3),to:m.substr(10,3),carrier:t(m.substr(13,3)),
   flight:(fl.replace(/\s+/g,'').replace(/^0+(?=\d)/,'')),julian:j,date:when(j,now),cabin:m.charAt(24),
   seat:t(m.substr(25,4)).replace(/^0+/,''),seq:t(m.substr(29,5)).replace(/^0+/,''),status:m.charAt(34),
   fromCity:AP[m.substr(7,3)]||'',toCity:AP[m.substr(10,3)]||''});
  pos+=37+size;}
 return{name:name(s.substr(2,20)),eticket:s.charAt(22)=='E',legs:legs,raw:s};}
/* Cabin from the compartment letter. Airlines map fare letters themselves, so only the clear cases are named. */
function cabin(c){return{F:'FIRST',A:'FIRST',J:'DELTA ONE',C:'DELTA ONE',D:'DELTA ONE',I:'DELTA ONE',Z:'DELTA ONE',
 Y:'MAIN CABIN',B:'MAIN CABIN',M:'MAIN CABIN',H:'MAIN CABIN',Q:'MAIN CABIN',K:'MAIN CABIN',L:'MAIN CABIN',U:'MAIN CABIN',T:'MAIN CABIN',
 X:'MAIN CABIN',V:'MAIN CABIN',E:'MAIN CABIN'}[c]||'';}
var MO=['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
function dstr(d){return d.getUTCDate()+' '+MO[d.getUTCMonth()];}
return{parse:parse,cabin:cabin,dstr:dstr,airports:AP};
})();
if(typeof module!='undefined')module.exports=BCBP;
