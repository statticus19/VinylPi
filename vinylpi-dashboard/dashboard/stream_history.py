"""Last completed Last.fm scrobble for authenticated Stream page.
Read-only service; shared 60s success cache; never exposes API credentials.
"""
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CONFIG=Path('/home/vinyl/pi_epaper/config.json')
LOCK=threading.Lock()
CACHE={'expiry':0.0,'data':None}

def _text(v):
    if isinstance(v,dict):v=v.get('#text','')
    return ' '.join(str(v or '').split())[:240]

def lastfm_previous():
    """Return most recent *completed* scrobble, including other devices."""
    now=time.monotonic()
    with LOCK:
        if now < CACHE['expiry']:return CACHE['data']
        try:
            cfg=json.loads(CONFIG.read_text(encoding='utf-8')).get('lastfm',{})
            if not isinstance(cfg,dict):cfg={}
            key=os.getenv('LASTFM_API_KEY') or cfg.get('api_key','')
            username=cfg.get('username','')
            if not key or not username:
                CACHE['expiry']=now+30
                return CACHE['data']
            query=urlencode({'method':'user.getRecentTracks','user':username,'api_key':key,'format':'json','limit':10})
            with urlopen(Request('https://ws.audioscrobbler.com/2.0/?'+query,headers={'User-Agent':'VinylPiDashboard/1.0'}),timeout=5) as reply:
                payload=json.loads(reply.read(100000))
            tracks=payload.get('recenttracks',{}).get('track',[])
            if isinstance(tracks,dict):tracks=[tracks]
            previous=None
            for track in tracks:
                if not isinstance(track,dict):continue
                if str(track.get('@attr',{}).get('nowplaying','')).lower()=='true':continue
                # Last.fm only gives completed tracks a scrobble timestamp.
                if not isinstance(track.get('date'),dict):continue
                artist=_text(track.get('artist'))
                title=_text(track.get('name'))
                if not artist or not title:continue
                images=track.get('image',[])
                art=''
                if isinstance(images,list):
                    for item in reversed(images):
                        if isinstance(item,dict) and str(item.get('#text','')).startswith('https://lastfm-img.freetls.fastly.net/'):
                            art=str(item['#text'])[:1000]
                            break
                previous={'artist':artist,'title':title,'album':_text(track.get('album')),'art_url':art}
                break
            CACHE.update(expiry=now+60,data=previous)
        except (OSError,ValueError,KeyError,TypeError):
            CACHE['expiry']=now+30
        return CACHE['data']
