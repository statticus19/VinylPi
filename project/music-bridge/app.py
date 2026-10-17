"""Private HTTP API. Bind localhost; expose only through Tailscale Serve."""
import asyncio
import json
import os
import secrets
import re
import time
from collections import OrderedDict
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import UUID
from .playlists import PlaylistJob, AppleCreated
from .store import JobStore, CapacityError, KeyConflict
from .jobs import JobRunner
from .source_playlists import resolve_playlist, SourcePlaylistError
import httpx
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Literal
from .core import classify, normalize

@asynccontextmanager
async def lifespan(app):
    path = os.environ.get('MUSIC_BRIDGE_DB', str(Path.home() / '.local/share/music-bridge/jobs.db'))
    app.state.store = JobStore(path)
    app.state.runner = JobRunner(app.state.store)
    app.state.worker = asyncio.create_task(app.state.runner.loop())
    try:
        yield
    finally:
        app.state.worker.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.worker

app = FastAPI(title='Music Link Bridge', version='0.2.0', lifespan=lifespan)
TOKEN = os.environ.get('MUSIC_BRIDGE_TOKEN', '')
cache = OrderedDict()
lock = asyncio.Lock()
last_call = 0.0

class ShareRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2048)

class PlaylistResolveRequest(ShareRequest):
    mode: Literal['apple_video_plan', 'spotify_handoff', 'youtube_video_playlist'] = 'apple_video_plan'

async def authorize(authorization: str = Header(default='')):
    if not TOKEN:
        raise HTTPException(503, 'Set MUSIC_BRIDGE_TOKEN before using this service.')
    if not secrets.compare_digest(authorization, 'Bearer ' + TOKEN):
        raise HTTPException(401, 'Invalid access token.')

@app.get('/health')
def health():
    worker = getattr(app.state, 'worker', None)
    ready = worker is not None and not worker.done()
    return {'status': 'ok' if ready else 'degraded', 'api_version': 1,
            'configured': bool(TOKEN), 'playlist_worker_ready': ready}

@app.get('/v1/capabilities', dependencies=[Depends(authorize)])
def capabilities():
    return {'sharing': ['song', 'album'], 'artist_links': 'not yet implemented',
            'playlist_jobs': ['apple_video_plan', 'spotify_handoff', 'youtube_video_playlist'],
            'youtube_playlist_limit': 20,
            'direct_tidal_transfer': 'not yet implemented',
            'clients': ['ios', 'android', 'windows', 'browser'],
            'resolver': 'songlink legacy API; live availability must be verified'}

@app.post('/v1/share', dependencies=[Depends(authorize)])
async def share(body: ShareRequest):
    global last_call
    try:
        item = classify(body.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if item['type'] in ('playlist', 'artist'):
        raise HTTPException(501, {'type': item['type'], 'message':
            'This first milestone supports songs and albums. Playlist transfer and artist matching require additional integrations.'})
    async with lock:
        cached = cache.get(item['url'])
        if cached and cached[0] > time.monotonic():
            return cached[1]
        # Conservatively pace legacy Songlink requests (10/minute).
        delay = max(0, 6.1 - (time.monotonic() - last_call))
        if delay:
            await asyncio.sleep(delay)
        last_call = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
                r = await client.get('https://api.song.link/v1-alpha.1/links',
                    params={'url': item['url'], 'userCountry': item['country'],
                            **({'key': os.environ['SONGLINK_API_KEY']} if os.environ.get('SONGLINK_API_KEY') else {})})
                if r.status_code == 429:
                    raise HTTPException(503, 'Resolver rate limit reached. Try again later.', headers={'Retry-After': '60'})
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict) or 'linksByPlatform' not in data:
                    raise ValueError('Unexpected resolver response')
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, 'The link resolver is unavailable or returned no usable match. No links were fabricated.') from exc
        result = normalize(item, data)
        if result['missing_services']:
            result['warnings'].append('No confirmed provider link for: ' + ', '.join(result['missing_services']))
        cache[item['url']] = (time.monotonic() + 86400, result)
        cache.move_to_end(item['url'])
        while len(cache) > 500:
            cache.popitem(last=False)
        return result


@app.post('/v1/playlist/resolve', dependencies=[Depends(authorize)])
async def resolve_source_playlist(body: PlaylistResolveRequest):
    try:
        resolved = await resolve_playlist(body.url)
        if body.mode != 'apple_video_plan':
            payload = PlaylistJob.model_validate({**resolved['request'], 'mode': body.mode}).model_dump()
            resolved.update(request=payload, request_json=json.dumps(payload, ensure_ascii=False))
        return resolved
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/v1/playlist/jobs', status_code=202, dependencies=[Depends(authorize)])
async def create_playlist_job(body: PlaylistJob, idempotency_key: str = Header(default='')):
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', idempotency_key):
        raise HTTPException(422, 'Idempotency-Key must contain 16–128 letters, numbers, underscores or hyphens.')
    key = idempotency_key
    if app.state.worker.done():
        raise HTTPException(503, 'Playlist worker unavailable. Check service logs before retrying.')
    try:
        job, created = app.state.store.create(body.model_dump(), key)
    except KeyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except CapacityError as exc:
        raise HTTPException(503, str(exc)) from exc
    if created:
        app.state.runner.wake.set()
    return job

@app.get('/v1/playlist/jobs/{job_id}', dependencies=[Depends(authorize)])
def get_playlist_job(job_id: UUID):
    job = app.state.store.get(str(job_id))
    if not job:
        raise HTTPException(404, 'Playlist job not found or history has expired.')
    return job


@app.post('/v1/playlist/jobs/{job_id}/apple-created', dependencies=[Depends(authorize)])
def apple_created(job_id: UUID, body: AppleCreated):
    try:
        return app.state.store.record_apple_creation(str(job_id), body.account_alias, body.store_ids)
    except KeyError as exc:
        raise HTTPException(404, 'Job not found.') from exc
    except KeyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        import sqlite3
        if isinstance(exc, sqlite3.DatabaseError):
            raise HTTPException(503, 'Storage unavailable. Keep the job ID and retry this acknowledgement; do not create another playlist.') from exc
        raise
