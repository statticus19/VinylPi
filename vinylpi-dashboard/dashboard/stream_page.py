"""Authenticated VinylPi stream page and lightweight now-playing API."""
import json
import os
import time
from pathlib import Path
from urllib.request import Request as URLRequest, urlopen
from urllib.parse import urlsplit
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse

router = APIRouter()
METADATA = Path('/home/vinyl/pi_epaper/state/vinyl_metadata.json')
RECOGNITION = Path('/home/vinyl/pi_epaper/state/vinyl_recognition.json')
LAN_URL = os.environ.get('VINYLPI_STREAM_LAN_URL', 'http://127.0.0.1:8000/vinyl')

def _read_json(path):
    try:
        if path.stat().st_size > 65536:
            return {}
        result = json.loads(path.read_text(encoding='utf-8'))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}

def _status():
    try:
        req = URLRequest('http://127.0.0.1:8000/status-json.xsl', headers={'User-Agent':'VinylPi-Dashboard/Stream'})
        with urlopen(req, timeout=2) as reply:
            if reply.status != 200: return {}
            raw = reply.read(65537)
            if len(raw) > 65536: return {}
        source = json.loads(raw).get('icestats', {}).get('source', {})
        if isinstance(source, list):
            source = next((s for s in source if isinstance(s,dict) and urlsplit(str(s.get('listenurl',''))).path == '/vinyl'), {})
        return source if isinstance(source, dict) else {}
    except (OSError, TimeoutError, ValueError, TypeError):
        return {}

def current_data(now=None, status_source=None, metadata=None, recognition=None):
    now = time.time() if now is None else now
    source = _status() if status_source is None else status_source
    meta = _read_json(METADATA) if metadata is None else metadata
    recog = _read_json(RECOGNITION) if recognition is None else recognition
    raw_title = str(source.get('title') or '').strip()[:240]
    streaming = bool(source)
    idle = not raw_title or raw_title.lower() in ('vinyl - idle','idle')
    age = now - float(meta.get('at', -1)) if isinstance(meta.get('at'), (int,float)) else 1e10
    rec_age = now - float(recog.get('at', -1)) if isinstance(recog.get('at'), (int,float)) else 1e10
    rec_state = recog.get('status') if 0 <= rec_age <= 180 else 'unknown'
    match = 0 <= age <= 300 and rec_state not in ('silent', 'stopped', 'no_match') and not idle
    artist = title = album = art = ''
    if match:
        artist = str(meta.get('artist') or '').strip()[:180]
        title = str(meta.get('title') or '').strip()[:180]
        # Avoid applying stale album art to a different Icecast track.
        label = raw_title.casefold()
        if not artist or not title or artist.casefold() not in label or title.casefold() not in label:
            match = False
        else:
            album = str(meta.get('album') or '').strip()[:180]
            url = str(meta.get('art_url') or '')[:1000]
            parsed = urlsplit(url)
            if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.fragment:
                art = url
    if not match and not idle:
        if ' - ' in raw_title:
            artist, title = (s.strip()[:180] for s in raw_title.split(' - ', 1))
        else:
            title = raw_title
    try: listeners = max(0, int(source.get('listeners', 0))) if streaming else 0
    except (ValueError,TypeError): listeners = 0
    try: bitrate = max(0, int(source.get('bitrate', 0))) if streaming else 0
    except (ValueError,TypeError): bitrate = 0
    return {'online':streaming,'idle':idle or not streaming,'artist':artist,'title':title if not idle else '',
            'album':album,'art_url':art,'listeners':listeners,'bitrate':bitrate,
            'recognition':rec_state if isinstance(rec_state,str) else 'unknown'}

@router.get('/stream')
def stream_page(request: Request):
    from .main import get_session, context, page
    if not get_session(request): return RedirectResponse('/login',303)
    return page(request, 'stream.html', **context(request, lan_url=LAN_URL))

@router.get('/stream/now-playing')
def now_playing(request: Request):
    from .main import get_session
    if not get_session(request):
        return JSONResponse({'detail':'Sign in required'},status_code=401)
    return JSONResponse(current_data(),headers={'Cache-Control':'no-store'})


# Completed Last.fm history for the signed-in Stream page. Shared cache avoids
# excessive API calls and never returns API credentials or config contents.
from .stream_history import lastfm_previous as _lastfm_previous

@router.get('/stream/previous')
def stream_previous(request: Request):
    from .main import get_session
    if not get_session(request):
        return JSONResponse({'detail': 'Sign in required'}, status_code=401)
    return JSONResponse({'previous': _lastfm_previous()}, headers={'Cache-Control':'no-store'})
