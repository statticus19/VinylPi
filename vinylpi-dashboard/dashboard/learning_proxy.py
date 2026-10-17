"""Strict same-origin dashboard gateway for the existing learning HTTP service.

Do not expose /api/match. The backend is loopback-only; the dashboard uses
a distinct internal key, never the orchestrator recognition token.
"""
import hmac
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request as URLRequest, urlopen
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse, HTMLResponse, RedirectResponse
from html import escape
from .main import get_session, templates

router = APIRouter()
UPSTREAM = 'http://127.0.0.1:8090'
GET_PATHS = {'state', 'health'}
POST_PATHS = {'start', 'stop', 'lastfm', 'library/delete'}
SESSION_RE = re.compile(r'^session/[a-f0-9]{32}/(?:edit|process|save|cleanup)$')
AUDIO_RE = re.compile(r'^[a-f0-9]{32}$')

def internal_key():
    key = os.environ.get('VINYLPI_LEARNING_INTERNAL_KEY', '')
    if len(key) < 32:
        raise HTTPException(503, 'Learning internal connection is not configured')
    return key

def session_or_401(request):
    session = get_session(request)
    if not session:
        raise HTTPException(401, 'Sign in to VinylPi first')
    return session

def authorized_post(request, session):
    token = request.headers.get('X-VinylPi-CSRF', '')
    if not hmac.compare_digest(token, session['csrf']):
        raise HTTPException(403, 'Invalid dashboard request token')
    origin = request.headers.get('Origin')
    if origin:
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        if parsed.netloc != request.headers.get('host') or parsed.scheme != request.url.scheme:
            # TLS is terminated by Tailscale Serve, so Request.url.scheme can be http.
            if parsed.netloc != request.headers.get('host'):
                raise HTTPException(403, 'Cross-origin request rejected')
    if request.headers.get('content-type','').split(';')[0] != 'application/json':
        raise HTTPException(415, 'JSON required')

def upstream(path, method='GET', body=None, range_header=None):
    headers={'X-VinylPi-Internal-Key': internal_key(), 'X-Vinyl-Request': '1'}
    if body is not None:
        headers['Content-Type']='application/json'
    if range_header:
        headers['Range']=range_header
    req=URLRequest(UPSTREAM + '/' + path, data=body, headers=headers, method=method)
    try:
        return urlopen(req, timeout=30)
    except HTTPError as exc:
        # Propagate only the status and JSON body. No upstream credentials leak.
        data=exc.read(65536)
        return Response(content=data, status_code=exc.code, media_type='application/json')
    except (URLError, TimeoutError, OSError):
        raise HTTPException(502, 'Learning service is unavailable')

@router.get('/learning')
def learning_page(request: Request):
    session_or_401(request)
    return templates.TemplateResponse(request, 'learning.html', {'csrf': get_session(request)['csrf'], 'active_page':'/learning', 'stream_ready': False})

@router.get('/learning/interface')
def interface(request: Request):
    session=session_or_401(request)
    from pathlib import Path
    path=Path(__file__).parent/'templates'/'learning-interface.html'
    html=path.read_text().replace('__VINYLPI_CSRF__',escape(session['csrf'],quote=True))
    return HTMLResponse(html)

@router.api_route('/learning/api/{path:path}', methods=['GET','POST'])
async def api(request: Request, path: str):
    session=session_or_401(request)
    if request.method == 'GET':
        if path not in GET_PATHS:
            raise HTTPException(404)
        reply=upstream('api/'+path)
    else:
        if path not in POST_PATHS and not SESSION_RE.fullmatch(path):
            raise HTTPException(404)
        authorized_post(request, session)
        content=await request.body()
        if not content or len(content)>150_000:
            raise HTTPException(413, 'Request too large')
        reply=upstream('api/'+path,'POST',content)
    if isinstance(reply, Response): return reply
    with reply:
        data=reply.read(1_000_000)
        status=reply.status
    return Response(content=data,status_code=status,media_type='application/json')

@router.get('/learning/audio/{recording_id}')
def audio(request: Request, recording_id: str):
    session_or_401(request)
    if not AUDIO_RE.fullmatch(recording_id):
        raise HTTPException(404)
    range_header=request.headers.get('range','')
    if range_header and not re.fullmatch(r'bytes=\d*-\d*',range_header):
        raise HTTPException(416, 'Invalid range')
    reply=upstream('audio/'+recording_id,range_header=range_header)
    if isinstance(reply, Response):return reply
    headers={k: v for k,v in reply.headers.items() if k.lower() in ('content-range','accept-ranges','content-length')}
    def chunks():
        try:
            with reply:
                while True:
                    block=reply.read(65536)
                    if not block: break
                    yield block
        finally:
            reply.close()
    return StreamingResponse(chunks(), status_code=reply.status,headers=headers,media_type='audio/mpeg')
