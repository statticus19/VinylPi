#!/usr/bin/env python3
"""Restricted local-only guest audio page for Tailscale Serve. Stdlib-only."""
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

HOST = '127.0.0.1'
PORT = 8781
STATE = Path('/home/vinyl/pi_epaper/state')
CONFIG = Path('/home/vinyl/pi_epaper/config.json')
ICECAST = 'http://127.0.0.1:8000/vinyl'
CACHE_LOCK = threading.Lock()
recent_cache = {'expiry': 0., 'data': None}

HTML = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer"><title>VinylPi · Live Vinyl Radio</title><meta name="description" content="Listen to live vinyl playback, see what&#x27;s playing, and explore recently played music."><meta property="og:type" content="website"><meta property="og:title" content="VinylPi · Live Vinyl Radio"><meta property="og:description" content="Listen to live vinyl playback, see what&#x27;s playing, and explore recently played music."><meta property="og:url" content="/"><meta property="og:image" content="/social-preview.png"><meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="VinylPi · Live Vinyl Radio"><meta name="twitter:description" content="Listen to live vinyl playback, see what&#x27;s playing, and explore recently played music."><meta name="twitter:image" content="/social-preview.png"><link rel="icon" type="image/x-icon" href="/favicon.ico"><link rel="apple-touch-icon" href="/apple-touch-icon.png"><style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif;background:#10151c;color:#f2f5f9}*{box-sizing:border-box}body{margin:0}main{max-width:790px;margin:auto;padding:2rem 1.1rem 4rem}header{display:flex;justify-content:space-between;align-items:center;margin-bottom:3rem}h1{font-size:1.4rem;letter-spacing:.02em}h2{font-size:.85rem;letter-spacing:.12em;text-transform:uppercase;color:#9faec0}#status{border:1px solid #455569;border-radius:2rem;padding:.4rem .8rem;font-size:.85rem}section{background:#19222e;border:1px solid #303e4d;border-radius:1.2rem;padding:1.3rem;margin-bottom:1rem}.track{display:flex;gap:1.5rem;align-items:center;flex-wrap:wrap}.cover{width:210px;height:210px;border-radius:.7rem;object-fit:cover;background:#263442}#prev-art{width:75px;height:75px}.info{flex:1;min-width:170px}.song{font-size:1.8rem;font-weight:750;margin:.3rem 0}.artist{font-size:1.15rem}.album{color:#a9b8c9;margin-top:.5rem}button{background:#d1e6fe;color:#122033;border:0;border-radius:.6rem;padding:.8rem 1.4rem;font-weight:700;cursor:pointer}button:focus-visible,input:focus-visible{outline:3px solid #9bc7ff}audio{display:block;width:100%;margin:1.3rem 0}.muted{color:#a9b8c9}footer{color:#9faec0;text-align:center;margin-top:2rem;font-size:.85rem}@media(max-width:540px){main{padding-top:.5rem}header{margin-bottom:1.5rem}.track{justify-content:center;text-align:center}.cover{width:min(80vw,270px);height:min(80vw,270px)}.song{font-size:1.5rem}.info{min-width:100%}}
</style></head><body><main><header><h1>VinylPi <span class="muted">/ Live Vinyl</span></h1><span id="status" role="status">Checking…</span></header><section><h2>Now Playing</h2><div class="track"><img id="art" class="cover" alt="Album cover" hidden><div class="info"><div id="title" class="song">Waiting for vinyl</div><div id="artist" class="artist"></div><div id="album" class="album"></div></div></div><audio id="audio" controls preload="none" aria-label="VinylPi live audio" src="/audio"></audio><p class="muted" style="font-size:.85rem">Live stream · audio may be delayed slightly from the turntable</p></section><section><h2>Previously Played · Last.fm</h2><div class="track"><img id="prev-art" class="cover" alt="Previous album cover" hidden><div class="info"><div id="prev-title" style="font-size:1.2rem;font-weight:700">Loading listening history…</div><div id="prev-artist" class="artist"></div><div id="prev-album" class="album"></div></div></div></section><footer>Private listening link · Requires access through Tailscale</footer></main><script>
const $=id=>document.getElementById(id);
function setArt(id,url){let img=$(id);if(typeof url==='string'&&url.startsWith('https://')){img.src=url;img.hidden=false}else{img.removeAttribute('src');img.hidden=true}}
function setTrack(prefix,t){$(prefix+'title').textContent=t?.title|| (prefix?'No recent scrobbles':'Waiting for vinyl');$(prefix+'artist').textContent=t?.artist||'';$(prefix+'album').textContent=t?.album||'';setArt(prefix?'prev-art':'art',t?.art_url)}
async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('offline');const d=await r.json();$('status').textContent=d.stream_online?'Stream online':'Stream unavailable';setTrack('',d.current);setTrack('prev-',d.previous)}catch(e){$('status').textContent='Status unavailable'}}
refresh();setInterval(refresh,15000);
</script></body></html>'''

def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}

def clean(v):
    return re.sub(r'\s+', ' ', str(v or '')).strip()[:240]

def now_track():
    status=read_json(STATE/'vinyl_recognition.json')
    metadata=read_json(STATE/'vinyl_metadata.json')
    try:
        age=time.time()-float(metadata['at'])
        sa=time.time()-float(status['at'])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not (0<=age<=300 and 0<=sa<=180 and status.get('status')=='matched'):
        return None
    if not clean(metadata.get('artist')) or not clean(metadata.get('title')):
        return None
    return normalize(metadata)

def normalize(t):
    art=clean(t.get('art_url'))
    if not art.startswith('https://'):art=''
    return {'artist':clean(t.get('artist')),'title':clean(t.get('title')),'album':clean(t.get('album')),'art_url':art}

def lastfm_previous(current):
    # Cache successful results: one Last.fm call maximum per minute, across all guests.
    now=time.monotonic()
    with CACHE_LOCK:
        if recent_cache['expiry']>now:
            return recent_cache['data']
        config=read_json(CONFIG).get('lastfm',{})
        if not isinstance(config,dict):config={}
        key=os.getenv('LASTFM_API_KEY','') or config.get('api_key','')
        user=config.get('username','')
        if not key or not user:
            return recent_cache['data']
        query=urlencode({'method':'user.getRecentTracks','user':user,'api_key':key,'format':'json','limit':5})
        try:
            with urlopen(Request('https://ws.audioscrobbler.com/2.0/?'+query,headers={'User-Agent':'VinylPiGuest/1.0'}),timeout=5) as r:
                payload=json.loads(r.read(100000))
            tracks=payload.get('recenttracks',{}).get('track',[])
            if isinstance(tracks,dict):tracks=[tracks]
            previous=None
            for t in tracks:
                if str(t.get('@attr',{}).get('nowplaying','')).lower()=='true':continue
                images=t.get('image',[])
                art=next((i.get('#text','') for i in reversed(images) if isinstance(i,dict) and str(i.get('#text','')).startswith('https://')), '')
                def field(name):
                    v=t.get(name,'')
                    return v.get('#text','') if isinstance(v,dict) else v
                row=normalize({'artist':field('artist'),'title':t.get('name'),'album':field('album'),'art_url':art})
                if current and row['title'].casefold()==current['title'].casefold() and row['artist'].casefold()==current['artist'].casefold():
                    continue
                if row['title'] and row['artist']:
                    previous=row
                    break
            recent_cache.update(expiry=now+60,data=previous)
        except (OSError, ValueError, KeyError, TypeError):
            recent_cache['expiry']=now+30
        return recent_cache['data']

def icecast_online():
    try:
        with urlopen('http://127.0.0.1:8000/status-json.xsl',timeout=3) as r:
            info=json.loads(r.read(200000))
        sources=info.get('icestats',{}).get('source',[])
        if isinstance(sources,dict):sources=[sources]
        return any(urlsplit(x.get('listenurl','')).path=='/vinyl' for x in sources)
    except (OSError,ValueError,TypeError):return False

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,fmt,*args):
        # Do not log query strings, cookies, or listener identities.
        logging.info('Guest request %s',self.path.split('?')[0])
    def send_bytes(self, data, typ, code=200):
        self.send_response(code)
        self.send_header('Content-Type',typ)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src https:; connect-src 'self'; media-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/':return self.send_bytes(HTML.encode(),'text/html; charset=utf-8')
        if path in ('/favicon.ico','/apple-touch-icon.png','/favicon-32x32.png'):
            file = Path(__file__).parent / path.lstrip('/')
            try:
                return self.send_bytes(file.read_bytes(),'image/x-icon' if path.endswith('.ico') else 'image/png')
            except OSError:
                return self.send_bytes(b'Not found','text/plain',404)
        if path=='/social-preview.png':
            try:
                return self.send_bytes((Path(__file__).parent/'social-preview.png').read_bytes(),'image/png')
            except OSError:
                return self.send_bytes(b'Not found','text/plain',404)
        if path=='/health':return self.send_bytes(b'{"status":"ok"}','application/json')
        if path=='/api/status':
            current=now_track()
            result={'stream_online':icecast_online(),'current':current,'previous':lastfm_previous(current)}
            return self.send_bytes(json.dumps(result).encode(),'application/json')
        if path=='/audio':return self.audio()
        return self.send_bytes(b'Not found','text/plain; charset=utf-8',404)
    def audio(self):
        try:
            up=urlopen(Request(ICECAST,headers={'User-Agent':'VinylPiGuest/1.0'}),timeout=8)
        except (OSError, ValueError):
            return self.send_bytes(b'Stream unavailable','text/plain',502)
        try:
            typ=up.headers.get('Content-Type','').split(';')[0].strip()
            if typ not in ('audio/mpeg','audio/aac','audio/aacp','audio/ogg','application/ogg','audio/mp4'):
                return self.send_bytes(b'Unexpected stream type','text/plain',502)
            self.send_response(200)
            self.send_header('Content-Type',typ)
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers()
            while True:
                chunk=up.read(16384)
                if not chunk:break
                self.wfile.write(chunk)
        except (BrokenPipeError,ConnectionResetError,OSError,TimeoutError):
            pass
        finally:
            up.close()
            self.close_connection=True

def run():
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    server=ThreadingHTTPServer((HOST,PORT),Handler)
    server.daemon_threads=True
    logging.info('Guest player listening on %s:%d',HOST,PORT)
    server.serve_forever(poll_interval=.25)

if __name__=='__main__':run()
