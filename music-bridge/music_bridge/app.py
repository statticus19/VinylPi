"""Private HTTP API. Bind localhost; expose only through Tailscale Serve."""
import asyncio
import json
import os
import secrets
import re
import time
from collections import OrderedDict
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID
from .playlists import PlaylistJob, AppleCreated
from .store import JobStore, CapacityError, KeyConflict
from .soundiiz import create_handoff, SoundiizResponseError
from .jobs import JobRunner
from .discord_notify import send_pending_notifications
from .source_playlists import resolve_playlist, SourcePlaylistError
import httpx
from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Literal
from .core import classify
from .musiclink_share import resolve_share
from .catalog import Catalogs, CatalogError, NotConfigured

async def quota_scheduler(app):
    # Keep this separate from the worker so waiting jobs can wake after quota reset.
    while True:
        if app.state.store.requeue_due_quota_jobs(datetime.now(timezone.utc).isoformat()):
            app.state.runner.wake.set()
        await send_pending_notifications(app.state.store)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app):
    path = os.environ.get('MUSIC_BRIDGE_DB', str(Path.home() / '.local/share/music-bridge/jobs.db'))
    app.state.store = JobStore(path)
    app.state.runner = JobRunner(app.state.store)
    app.state.worker = asyncio.create_task(app.state.runner.loop())
    app.state.quota_scheduler = asyncio.create_task(quota_scheduler(app))
    try:
        yield
    finally:
        app.state.quota_scheduler.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.quota_scheduler
        app.state.worker.cancel()
        with suppress(asyncio.CancelledError):
            await app.state.worker

app = FastAPI(title='Music Link Bridge', version='0.2.0', lifespan=lifespan)
TOKEN = os.environ.get('MUSIC_BRIDGE_TOKEN', '')
cache = OrderedDict()
lock = asyncio.Lock()
sharing_catalog = Catalogs()

class ShareRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2048)

class PlaylistResolveRequest(ShareRequest):
    mode: Literal['apple_video_plan', 'spotify_handoff', 'youtube_video_playlist', 'soundiiz_transfer'] = 'apple_video_plan'

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
    return {'sharing': ['song', 'album', 'artist'], 'artist_links': 'musiclink',
            'playlist_jobs': ['apple_video_plan', 'spotify_handoff', 'youtube_video_playlist', 'soundiiz_transfer'],
            'youtube_playlist_limit': 20,
            'youtube_review_mode_limit': 1000,
            'youtube_review_mode': 'review_then_explicit_create',
            'direct_tidal_transfer': 'not yet implemented',
            'clients': ['ios', 'android', 'windows', 'browser'],
            'resolver': 'MusicLink; Spotify convert fallback when required'}

@app.post('/v1/share', dependencies=[Depends(authorize)])
async def share(body: ShareRequest):
    try:
        item = classify(body.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if item['type'] == 'playlist':
        raise HTTPException(501, {'type': item['type'], 'message':
            'Playlist link sharing is not supported. Use the playlist workflow.'})
    async with lock:
        cached = cache.get(item['url'])
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                result = await resolve_share(client, sharing_catalog, item)
        except FileNotFoundError:
            raise HTTPException(503, 'MusicLink credentials are not configured.') from None
        except (CatalogError, httpx.HTTPError, ValueError, OSError) as exc:
            # Never return upstream payloads, credentials, or response bodies.
            raise HTTPException(502, 'MusicLink resolver unavailable or response invalid. No links were fabricated.') from None
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

@app.get('/v1/playlist/jobs', dependencies=[Depends(authorize)])
def list_playlist_jobs():
    return {'jobs': app.state.store.list_recent(50)}

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


class YouTubeReviewDecision(BaseModel):
    source_index: int = Field(ge=0, le=999)
    action: Literal['approve', 'skip']
    video_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{11}$')


@app.post('/v1/playlist/jobs/{job_id}/review', dependencies=[Depends(authorize)])
def save_youtube_review(job_id: UUID, body: YouTubeReviewDecision):
    try:
        return app.state.store.set_youtube_review_decision(
            str(job_id), body.source_index, body.action, body.video_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except KeyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/v1/playlist/jobs/{job_id}/create-reviewed', status_code=202,
          dependencies=[Depends(authorize)])
def create_reviewed_playlist(job_id: UUID):
    if app.state.worker.done():
        raise HTTPException(503, 'Playlist worker unavailable')
    try:
        outcome = app.state.store.queue_review_creation(str(job_id))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except KeyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    app.state.runner.wake.set()
    return outcome


class SoundiizGenerateRequest(BaseModel):
    regenerate: bool = False


@app.post('/v1/playlist/jobs/{job_id}/soundiiz-parts/{part_number}/handoff',
          dependencies=[Depends(authorize)])
async def generate_soundiiz_handoff(job_id: UUID, part_number: int,
                                  body: SoundiizGenerateRequest):
    try:
        batch, existing = app.state.store.claim_soundiiz_part(
            str(job_id), part_number, body.regenerate)
    except KeyError:
        raise HTTPException(404, 'Playlist job not found.') from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except KeyConflict as exc:
        raise HTTPException(409, str(exc)) from None
    if batch is None:
        return existing
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            handoff = await create_handoff(client, batch['title'], batch['tracks'])
    except (httpx.HTTPError, SoundiizResponseError, ValueError):
        app.state.store.finish_soundiiz_part(str(job_id), part_number)
        raise HTTPException(502, 'Soundiiz handoff outcome uncertain; inspect job before explicitly regenerating.') from None
    return app.state.store.finish_soundiiz_part(str(job_id), part_number, handoff)


class SoundiizTransferConfirmation(BaseModel):
    destination: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_]{1,39}$')


@app.post('/v1/playlist/jobs/{job_id}/soundiiz-parts/{part_number}/transferred',
          dependencies=[Depends(authorize)])
def acknowledge_soundiiz_transfer(job_id: UUID, part_number: int,
                                  body: SoundiizTransferConfirmation | None = None):
    try:
        return app.state.store.mark_soundiiz_transferred(
            str(job_id), part_number, body.destination if body else None)
    except KeyError:
        raise HTTPException(404, 'Playlist job not found.') from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except KeyConflict as exc:
        raise HTTPException(409, str(exc)) from None
