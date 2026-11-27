"""Vault in-store size match: an installable web app (PWA).

A shelf-tag QR code opens /scan on any phone. The shopper picks a tee they
already own and gets a recommended size for the brand on the tag. It uses
the same /meta, /get_fit and /sync_state endpoints as the Shopify widget
and the Chrome extension, so events land in the same tracking_events table.

URL parameters
  b  target brand, as named in the database (for example Snitch). Optional:
     when missing or unknown the shopper picks it first.
  s  store code, for example the store or mall name. Optional.
  p  product or rack label. Optional.

Routes added: /scan, /manifest.webmanifest, /sw.js and the icon files.

Wire it up in api.py with two lines:
  from scan import router as scan_router
  app.include_router(scan_router)
"""
import os

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

router = APIRouter()
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan_assets")

MANIFEST = {
    "name": "Vault Size Match",
    "short_name": "Vault",
    "description": "Find your size in any brand from a tee you already own.",
    "start_url": "/scan?src=pwa",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#0b1210",
    "theme_color": "#0b1210",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {"src": "/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}

SW = r"""const V = 'vault-scan-v1';
const SHELL = ['/scan', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png'];
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(V).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== V).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname === '/meta') {
    e.respondWith(fetch(req).then((r) => { const c = r.clone(); caches.open(V).then((ch) => ch.put(req, c)); return r; }).catch(() => caches.match(req)));
    return;
  }
  if (SHELL.indexOf(url.pathname) > -1) {
    e.respondWith(caches.match(req, { ignoreSearch: true }).then((hit) => {
      const net = fetch(req).then((r) => { const c = r.clone(); caches.open(V).then((ch) => ch.put(req, c)); return r; }).catch(() => hit);
      return hit || net;
    }));
  }
});
"""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b1210">
<meta name="robots" content="noindex">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<title>Find your size</title>
<style>
:root{--h:160;--bg:#0b1210;--fg:#e8f0ec;--muted:#8fa399;--line:rgba(255,255,255,.1);--card:rgba(255,255,255,.05);
 --ac:hsl(var(--h) 62% 58%);--ac2:hsl(var(--h) 62% 30%);--acs:hsl(var(--h) 55% 58% / .16);--err:#f0a06a}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;min-height:100%}
body{background:var(--bg);color:var(--fg);font-family:ui-rounded,"SF Pro Rounded",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;line-height:1.45;
 background-image:radial-gradient(120% 60% at 50% -10%,var(--acs),transparent 70%);background-repeat:no-repeat;
 padding:max(18px,env(safe-area-inset-top)) max(18px,env(safe-area-inset-right)) max(22px,env(safe-area-inset-bottom)) max(18px,env(safe-area-inset-left))}
#app{max-width:440px;margin:0 auto;min-height:calc(100vh - 40px);display:flex;flex-direction:column}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:26px}
.mark{display:flex;align-items:center;gap:9px;font-weight:700;letter-spacing:.14em;font-size:13px}
.mark i{width:26px;height:26px;border:2px solid var(--ac);border-radius:50%;display:grid;place-items:center;font-style:normal;font-size:12px;letter-spacing:0;color:var(--fg)}
.dots{display:flex;gap:6px}.dots b{width:22px;height:4px;background:var(--line);transition:background .3s}.dots b.on{background:var(--ac)}
.eyebrow{margin:0 0 6px;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--ac);font-weight:700}
h1{margin:0 0 6px;font-size:30px;line-height:1.1;letter-spacing:-.02em}
.lead{margin:0 0 20px;color:var(--muted);font-size:15px}
.panel{animation:rise .35s ease both;flex:1;display:flex;flex-direction:column}
@keyframes rise{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
.search{width:100%;font:inherit;font-size:16px;padding:14px 16px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:14px;margin-bottom:12px}
.search::placeholder{color:var(--muted)}
.search:focus-visible,.chip:focus-visible,.btn:focus-visible,.ghost:focus-visible{outline:2px solid var(--ac);outline-offset:2px}
.chips{display:flex;flex-wrap:wrap;gap:9px;max-height:46vh;overflow:auto;padding:2px 2px 8px;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
.chip{font:inherit;font-size:15px;font-weight:600;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:999px;padding:11px 17px;cursor:pointer;transition:transform .12s,background .2s,border-color .2s}
.chip:active{transform:scale(.96)}
.chip.sel{background:var(--ac);border-color:var(--ac);color:#06100c}
.chip.big{min-width:62px;text-align:center;padding:14px 18px;font-size:17px}
.lab{margin:18px 0 9px;font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-weight:700}
.back{background:none;border:0;color:var(--muted);font:inherit;font-size:14px;padding:6px 0;margin-bottom:10px;cursor:pointer;align-self:flex-start}
.reveal{text-align:center;margin:8px 0 18px}
.ring{width:210px;height:210px;margin:6px auto 18px;border-radius:50%;display:grid;place-items:center;position:relative;background:radial-gradient(closest-side,var(--ac2),transparent 72%)}
.ring svg{position:absolute;inset:0;width:100%;height:100%;transform:rotate(-90deg)}
.ring circle{fill:none;stroke-width:5;stroke-linecap:round}
.ring .tr{stroke:var(--line)}.ring .pr{stroke:var(--ac);stroke-dasharray:597;stroke-dashoffset:597;animation:draw 1s .1s ease forwards}
@keyframes draw{to{stroke-dashoffset:0}}
.sz{font-size:86px;font-weight:800;letter-spacing:-.03em;line-height:1;animation:pop .5s .45s cubic-bezier(.2,1.4,.4,1) both}
@keyframes pop{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:none}}
.based{color:var(--muted);font-size:14px;margin:0 0 6px}
.note{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:13px 15px;font-size:14px;margin:12px 0 0;text-align:left}
.btn{font:inherit;font-weight:700;font-size:16px;width:100%;padding:16px;border:0;border-radius:14px;background:var(--ac);color:#06100c;cursor:pointer;margin-top:auto}
.ghost{font:inherit;font-weight:600;font-size:15px;width:100%;padding:14px;border-radius:14px;border:1px solid var(--line);background:none;color:var(--fg);cursor:pointer;margin-top:10px}
.spacer{height:16px}
.load{text-align:center;margin:auto 0;color:var(--muted)}
.spin{width:38px;height:38px;border:3px solid var(--line);border-top-color:var(--ac);border-radius:50%;margin:0 auto 16px;animation:sp 1s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.err{color:var(--err);font-size:14px;margin-top:12px}
.fine{margin:22px 0 0;font-size:11.5px;color:var(--muted);text-align:center}
[hidden]{display:none!important}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.ring .pr{stroke-dashoffset:0}}
</style>
</head>
<body>
<div id="app">
  <div class="top"><div class="mark"><i>V</i>VAULT</div><div class="dots" id="dots" aria-hidden="true"></div></div>
  <div id="view"></div>
  <p class="fine">We save the brand, fit and size you pick, not your name or contact details. The brand on the tag owns this data and Vault uses it to show your size.</p>
</div>
<script>
(function(){
  var q=new URLSearchParams(location.search);
  var wantB=(q.get('b')||'').trim(),STORE=(q.get('s')||'').trim(),PROD=(q.get('p')||'').trim();
  var META=null,TARGET='',REF='',FIT='',SIZE='',STEP=0,steps=[];
  var view=document.getElementById('view'),dots=document.getElementById('dots');
  var uuid;try{uuid=localStorage.getItem('vx_uuid');if(!uuid){uuid='scan-'+Math.random().toString(36).slice(2)+Date.now().toString(36);localStorage.setItem('vx_uuid',uuid)}}catch(e){uuid='scan-'+Math.random().toString(36).slice(2)}
  function track(ev,result){try{fetch('/sync_state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event:ev,product_id:PROD||'store-scan',brand:TARGET||'Unknown Brand',result:result||'N/A',uuid:uuid,price:0,name:PROD||('Store scan'+(STORE?' - '+STORE:'')),url:location.href}),keepalive:true}).catch(function(){})}catch(e){}}
  function esc(s){return String(s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
  function hue(name){var h=0;for(var i=0;i<name.length;i++)h=(h*31+name.charCodeAt(i))%360;return h}
  function setAccent(b){document.documentElement.style.setProperty('--h',b?hue(b):160)}
  function paintDots(){dots.innerHTML=steps.map(function(s,i){return '<b class="'+(i<=STEP?'on':'')+'"></b>'}).join('')}

  function loading(msg,sub){view.innerHTML='<div class="panel"><div class="load"><div class="spin"></div><p style="color:var(--fg);font-weight:600;margin:0 0 4px">'+msg+'</p><p style="margin:0;font-size:14px">'+(sub||'')+'</p></div></div>'}

  function loadMeta(attempt){
    attempt=attempt||0;
    var slow=setTimeout(function(){loading('Getting your sizes ready','The sizing engine is waking up. This can take a few seconds.')},1500);
    var ctl=new AbortController(),to=setTimeout(function(){ctl.abort()},20000);
    fetch('/meta',{signal:ctl.signal}).then(function(r){if(!r.ok)throw 0;return r.json()}).then(function(m){
      clearTimeout(slow);clearTimeout(to);
      if(!m||!Object.keys(m).length)throw 0;
      META=m;start();
    }).catch(function(){
      clearTimeout(slow);clearTimeout(to);
      if(attempt<3){loading('Getting your sizes ready','Still waking up. Trying again.');setTimeout(function(){loadMeta(attempt+1)},2500)}
      else{view.innerHTML='<div class="panel"><div class="load"><p style="color:var(--fg);font-weight:600">We could not load sizing data</p><p style="font-size:14px">Check your connection, then try again.</p></div><button class="btn" id="retry" type="button">Try again</button></div>';document.getElementById('retry').onclick=function(){loadMeta(0)}}
    });
  }

  function start(){
    var brands=Object.keys(META).sort();
    var hit=brands.filter(function(b){return b.toLowerCase()===wantB.toLowerCase()})[0];
    steps=hit?['ref','size','result']:['target','ref','size','result'];
    if(hit)TARGET=hit;
    STEP=0;REF=FIT=SIZE='';setAccent(TARGET);
    track('store_scan_opened','Scan Opened');
    render();
  }

  function head(eyebrow,title,lead){return '<p class="eyebrow">'+eyebrow+'</p><h1>'+title+'</h1><p class="lead">'+lead+'</p>'}
  function backBtn(){return STEP>0&&steps[STEP]!=='result'?'<button class="back" type="button" id="bk">← Back</button>':''}

  function render(){
    paintDots();
    var s=steps[STEP],brands=Object.keys(META).sort(),h='';
    if(s==='target'){
      h='<div class="panel">'+head('Step '+(STEP+1)+' of '+steps.length,'Which brand are you<br>shopping?','Pick the brand on this tag.')+
        '<input class="search" id="sr" type="search" placeholder="Search brands" autocomplete="off" aria-label="Search brands"><div class="chips" id="ch"></div></div>';
      view.innerHTML=h;chips(brands,'',function(b){TARGET=b;setAccent(b);STEP++;render()});
    }else if(s==='ref'){
      h='<div class="panel">'+backBtn()+head('Step '+(STEP+1)+' of '+steps.length,TARGET?'Your size in '+esc(TARGET):'Your tee','Which brand do you wear now? Pick a tee you own and like the fit of.')+
        '<input class="search" id="sr" type="search" placeholder="Search brands" autocomplete="off" aria-label="Search brands"><div class="chips" id="ch"></div></div>';
      view.innerHTML=h;chips(brands.filter(function(b){return b!==TARGET||brands.length<2}),REF,function(b){REF=b;FIT='';SIZE='';var f=Object.keys(META[b]||{});if(f.length===1)FIT=f[0];STEP++;render()});
    }else if(s==='size'){
      var fits=Object.keys(META[REF]||{});
      h='<div class="panel">'+backBtn()+head('Step '+(STEP+1)+' of '+steps.length,'Your '+esc(REF)+' size','Choose the fit and size you usually buy.')+
        (fits.length>1?'<p class="lab">Fit style</p><div class="chips" id="fits"></div>':'')+
        '<p class="lab">Size</p><div class="chips" id="szs"></div><p class="err" id="er" role="alert" hidden></p></div>';
      view.innerHTML=h;
      if(fits.length>1){var fe=document.getElementById('fits');fits.forEach(function(f){var b=btn(f,f===FIT);b.onclick=function(){FIT=f;SIZE='';render()};fe.appendChild(b)})}
      var sizes=FIT?(META[REF][FIT]||[]):[];var se=document.getElementById('szs');
      if(!FIT){se.innerHTML='<span style="color:var(--muted);font-size:14px">Choose a fit first.</span>'}
      else sizes.forEach(function(z){var b=btn(z,z===SIZE);b.classList.add('big');b.onclick=function(){SIZE=z;calc()};se.appendChild(b)});
    }
    var bk=document.getElementById('bk');if(bk)bk.onclick=function(){STEP--;render()};
    var sr=document.getElementById('sr');
    if(sr){sr.addEventListener('input',function(){var t=sr.value.trim().toLowerCase();var list=Object.keys(META).sort().filter(function(b){return b.toLowerCase().indexOf(t)>-1});chips(list,s==='ref'?REF:'',s==='target'?function(b){TARGET=b;setAccent(b);STEP++;render()}:function(b){REF=b;FIT='';SIZE='';var f=Object.keys(META[b]||{});if(f.length===1)FIT=f[0];STEP++;render()})})}
  }
  function btn(t,sel){var b=document.createElement('button');b.type='button';b.className='chip'+(sel?' sel':'');b.textContent=t;return b}
  function chips(list,sel,on){var ch=document.getElementById('ch');ch.innerHTML='';if(!list.length){ch.innerHTML='<span style="color:var(--muted);font-size:14px">No match. Try a different spelling.</span>';return}list.forEach(function(b){var e=btn(b,b===sel);e.onclick=function(){on(b)};ch.appendChild(e)})}

  function calc(){
    loading('Matching your size','');
    var url='/get_fit?ref_brand='+encodeURIComponent(REF)+'&ref_fit='+encodeURIComponent(FIT)+'&ref_size='+encodeURIComponent(SIZE)+'&target_brand='+encodeURIComponent(TARGET)+'&target_fit=Regular%20Fit';
    fetch(url).then(function(r){return r.ok?r.json():Promise.reject()}).then(function(d){
      if(d&&d.recommended_size){STEP=steps.length-1;paintDots();result(d);track('size_calculated','Rec: '+d.recommended_size+' (from '+REF+')')}
      else fail()
    }).catch(fail);
  }
  function fail(){
    view.innerHTML='<div class="panel"><div class="load"><p style="color:var(--fg);font-weight:600">We could not match that one</p><p style="font-size:14px">Try another tee you own.</p></div><button class="btn" id="again" type="button">Try another tee</button></div>';
    document.getElementById('again').onclick=function(){STEP=steps.indexOf('ref');SIZE='';render()};
  }
  var deferred=null;
  window.addEventListener('beforeinstallprompt',function(e){e.preventDefault();deferred=e;var b=document.getElementById('inst');if(b)b.hidden=false});
  function result(d){
    view.innerHTML='<div class="panel"><div class="reveal">'+
      '<p class="eyebrow">Your size in '+esc(TARGET||'this brand')+'</p>'+
      '<div class="ring"><svg viewBox="0 0 210 210"><circle class="tr" cx="105" cy="105" r="95"/><circle class="pr" cx="105" cy="105" r="95"/></svg><div class="sz">'+esc(d.recommended_size)+'</div></div>'+
      '<p class="based">Based on your '+esc(REF)+' '+esc(FIT)+' size '+esc(SIZE)+'</p>'+
      (d.warning?'<div class="note">'+esc(d.warning)+'</div>':'')+
      '</div><button class="btn" id="again" type="button">Try another tee</button>'+
      '<button class="ghost" id="inst" type="button"'+(deferred?'':' hidden')+'>Add Vault to your home screen</button></div>';
    document.getElementById('again').onclick=function(){STEP=steps.indexOf('ref');SIZE='';render()};
    document.getElementById('inst').onclick=function(){if(deferred){deferred.prompt();deferred=null;this.hidden=true}};
  }

  if('serviceWorker' in navigator){window.addEventListener('load',function(){navigator.serviceWorker.register('/sw.js').catch(function(){})})}
  loadMeta(0);
})();
</script>
</body>
</html>
"""


@router.get("/scan", response_class=HTMLResponse)
def scan_page():
    return HTMLResponse(PAGE)


@router.get("/manifest.webmanifest")
def manifest():
    return JSONResponse(MANIFEST, media_type="application/manifest+json")


@router.get("/sw.js")
def service_worker():
    return Response(
        SW,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


def _icon(name: str):
    return FileResponse(os.path.join(ASSETS, name), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@router.get("/icon-192.png")
def icon_192():
    return _icon("icon-192.png")


@router.get("/icon-512.png")
def icon_512():
    return _icon("icon-512.png")


@router.get("/icon-maskable-512.png")
def icon_maskable():
    return _icon("icon-maskable-512.png")


@router.get("/apple-touch-icon.png")
def apple_icon():
    return _icon("apple-touch-icon.png")
