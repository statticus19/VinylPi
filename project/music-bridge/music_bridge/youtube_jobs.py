"""YouTube job orchestration; remote writes are never retried automatically."""
import asyncio
import os
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from . import youtube_matching as yt
from .youtube_creation import create_playlist




async def run_review_creation(store, jid, payload):
    """Single confirmed creation attempt. Never search, recreate or auto-retry writes."""
    job = store.get(jid)
    saved = job.get('result') or {}
    plan = saved.get('creation_plan')
    entries = saved.get('review_candidates') or []
    token = Path(os.environ.get('MUSIC_BRIDGE_YOUTUBE_TOKEN', 'secrets/youtube-token.json'))
    path = store.path.parent / 'youtube-jobs' / (jid + '.json')
    if (not isinstance(plan, list) or len(plan) != len(payload['tracks']) or
            len(entries) != len(plan) or not any(plan)):
        store.update(jid, 'interrupted', job['processed'], saved,
                     'Frozen creation plan invalid. No writes attempted.')
        return
    rows = []
    for i, vid in enumerate(plan):
        entry = entries[i]
        if entry.get('source_index') != i or (vid is not None and not any(
                c.get('video_id') == vid for c in entry.get('candidates', []))):
            store.update(jid, 'interrupted', job['processed'], saved,
                         'Frozen selection mismatch. No writes attempted.')
            return
        rows.append({'title': payload['tracks'][i]['title'],
                     'artists': payload['tracks'][i]['artists'],
                     'proposal': {'video_id': vid, 'metadata_eligible': True} if vid else None})
    report = {'playlist_title': payload['title'], 'tracks': rows}
    latest = {}

    def checkpoint(state):
        latest.clear()
        latest.update(state)
        merged = dict(saved)
        merged.update(playlist_id=state.get('playlist_id'),
                      playlist_url='https://www.youtube.com/playlist?list=' + state['playlist_id'] if state.get('playlist_id') else None,
                      playlist_name=state.get('name'), playlist_created=bool(state.get('playlist_id')),
                      pending=state.get('pending'), inserted_count=len(state.get('inserted', [])),
                      rejected=state.get('rejected', []))
        store.update(jid, 'creating_review', job['processed'], merged)

    try:
        state = await create_playlist(report, token, path, payload.get('source_url'),
                                      on_state=checkpoint, reviewed=True)
        final = dict(saved)
        final.update(playlist_id=state['playlist_id'],
                     playlist_url='https://www.youtube.com/playlist?list=' + state['playlist_id'],
                     playlist_name=state['name'], playlist_created=True,
                     creation_verified=True, rejected=state.get('rejected', []),
                     inserted_count=len(state.get('inserted', [])), pending=None)
        store.record_youtube_creation(jid, final, state['channel_id'])
        status = 'needs_review' if state.get('status') == 'partial_verified' else 'completed'
        store.update(jid, status, job['processed'], final,
                     'Some selected videos were definitively rejected. Review the playlist.' if status == 'needs_review' else None)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Preserve sanitized provider diagnostics, never raw HTTP bodies or tokens.
        # Any attempted remote write remains uncertain and cannot be retried blindly.
        current = store.get(jid)['result'] or saved
        detail = (str(exc)[:320] if isinstance(exc, yt.PreviewError)
                  else 'Unexpected creation error (' + type(exc).__name__ + ').')
        store.update(jid, 'interrupted', job['processed'], current,
                     detail + ' Inspect checkpoint and remote playlist; no automatic retry.')

async def run_youtube_job(store, jid, payload):
    token = Path(os.environ.get('MUSIC_BRIDGE_YOUTUBE_TOKEN', 'secrets/youtube-token.json'))
    directory = store.path.parent / 'youtube-jobs'
    state_path = directory / (jid + '.json')
    search_path = directory / (jid + '.search.json')
    report = None
    latest = {}

    def result(state):
        playlist_id = state.get('playlist_id')
        matches, skipped = [], []
        if report:
            for index, row in enumerate(report['tracks']):
                if row['proposal'] and not payload.get('review_before_create'):
                    matches.append({'source_index': index, **row['proposal']})
                else:
                    skipped.append({'source_index': index, 'title': row['title'],
                                    'artists': row['artists'], 'reason': 'no_confident_video_match'})
        return {'destination': 'youtube', 'playlist_id': playlist_id,
                'playlist_url': 'https://www.youtube.com/playlist?list=' + playlist_id if playlist_id else None,
                'playlist_name': state.get('name'), 'playlist_created': bool(playlist_id),
                'creation_verified': state.get('status') in ('completed', 'partial_verified'),
                'rejected': state.get('rejected', []),
                'review_candidates': [{'source_index': i, 'title': row['title'],
                    'artists': row['artists'], 'candidates': row.get('candidates', [])}
                    for i, row in enumerate(report['tracks'])] if report else [],
                'matched_count': len(matches), 'skipped_count': len(skipped),
                'inserted_count': len(state.get('inserted', [])),
                'matches': matches, 'skipped': skipped, 'pending': state.get('pending'),
                'notes': report['notes'] if report else []}

    def checkpoint(state):
        latest.clear()
        latest.update(state)
        store.update(jid, 'running', len(payload['tracks']), result(state))

    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        report = await yt.preview(payload.get('source_url'), token,
            len(payload['tracks']) if payload.get('review_before_create') else 20,
            resolved={'title': payload['title'], 'country': payload['country'],
                      'request': {'tracks': payload['tracks']}},
            progress=lambda n: store.update(jid, 'running', n), checkpoint_path=search_path,
            review_only=bool(payload.get('review_before_create')))
        if payload.get('review_before_create'):
            # No YouTube remote writes in review mode. Job result is durable in SQLite;
            # the full read-only search checkpoint remains in the private jobs directory.
            final = result({})
            # Suggestions are provisional; no user approval or remote write.
            for item in final['review_candidates']:
                preferred = next((c for c in item['candidates']
                                  if c.get('metadata_eligible') and
                                  c.get('kind') in ('music_video', 'lyric_video')), None)
                if preferred:
                    item['suggested_video_id'] = preferred['video_id']
            final['review_decisions'] = {}
            final['review_required'] = True
            final['playlist_created'] = False
            store.update(jid, 'awaiting_approval', len(payload['tracks']), final)
            return
        if not any(row['proposal'] for row in report['tracks']):
            store.update(jid, 'failed', len(payload['tracks']), result({}),
                         error='No confident video matches. No playlist was created.')
            store.queue_youtube_event(jid, 'no_matches')
            return
        state = await create_playlist(report, token, state_path, payload.get('source_url'), on_state=checkpoint)
        final = result(state)
        store.record_youtube_creation(jid, final, state['channel_id'])
        if state.get('status') == 'partial_verified':
            store.update(jid, 'needs_review', len(payload['tracks']), final,
                         error='Playlist verified with definitively rejected videos; review alternatives.')
            store.queue_youtube_event(jid, 'needs_review')
        else:
            store.update(jid, 'completed', len(payload['tracks']), final)
    except asyncio.CancelledError:
        raise
    except yt.TemporaryRateLimit as exc:
        if state_path.exists():
            store.update(jid, 'needs_review', store.get(jid)['processed'], result(latest),
                error='Rate limit during playlist creation or verification. Inspect remote state before retrying.')
            store.queue_youtube_event(jid, 'needs_review')
            return
        # Bound cross-run automatic retries. Store a counter in a separate private
        # file because normal per-track progress updates replace the public result.
        retry_state = directory / (jid + '.rate-limit.json')
        tries = 0
        if retry_state.exists():
            try:
                tries = int(json.loads(retry_state.read_text()).get('attempts', 0))
            except (ValueError, KeyError, TypeError):
                store.update(jid, 'failed', store.get(jid)['processed'], result(latest),
                    error='Rate-limit retry metadata is invalid; manual review required.')
                return
        tries += 1
        if tries > 4:
            store.update(jid, 'failed', store.get(jid)['processed'], result(latest),
                error='Temporary YouTube rate limit persisted after four retry windows; manual review required.')
            return
        from .youtube_creation import save_state
        save_state(retry_state, {'attempts': tries})
        wait_seconds = max(300 * (2 ** (tries - 1)), exc.retry_after or 0)
        resume_at = datetime.now(timezone.utc) + timedelta(seconds=wait_seconds)
        waiting = result(latest)
        waiting['retry_at'] = resume_at.isoformat()
        waiting['rate_limit_attempt'] = tries
        store.update(jid, 'waiting_for_rate_limit', store.get(jid)['processed'], waiting,
                     error='Temporary YouTube request throttling; automatic retry is scheduled.')
    except yt.DailyQuotaExceeded:
        # The creation phase has not begun; no remote playlist write can have occurred.
        if state_path.exists():
            store.update(jid, 'needs_review', store.get(jid)['processed'], result(latest),
                error='Quota error occurred after playlist creation started. Inspect checkpoints; do not retry automatically.')
            store.queue_youtube_event(jid, 'needs_review')
            return
        now = datetime.now(timezone.utc)
        pacific = now.astimezone(ZoneInfo('America/Los_Angeles'))
        next_day = (pacific + timedelta(days=1)).date()
        resume_at = datetime(next_day.year, next_day.month, next_day.day, 0, 5,
                             tzinfo=ZoneInfo('America/Los_Angeles')).astimezone(timezone.utc)
        progress = store.get(jid)['processed']
        waiting_result = result(latest)
        waiting_result['retry_at'] = resume_at.isoformat()
        store.update(jid, 'waiting_for_quota', progress, waiting_result,
                     error='YouTube daily quota exhausted; this job will retry after the Pacific Time quota reset.')
    except Exception as exc:
        progress = store.get(jid)['processed']
        uncertain = bool(latest.get('playlist_id') or latest.get('pending'))
        message = ('YouTube creation needs review. Keep this job ID; inspect its playlist and saved checkpoints before retrying.'
                   if uncertain else 'YouTube job failed before creation. Check authorization, quota, and service configuration.')
        if isinstance(exc, yt.PreviewError):
            # PreviewError contains only sanitized provider reasons. Keep these
            # diagnostics even when creation already started, while retaining
            # the needs_review status and no-retry safeguards.
            message = str(exc)[:350]
        store.update(jid, 'needs_review' if uncertain else 'failed', progress, result(latest), error=message)
        if uncertain:
            store.queue_youtube_event(jid, 'needs_review')
