"""YouTube job orchestration; remote writes are never retried automatically."""
import asyncio
import os
from pathlib import Path
from . import youtube_matching as yt
from .youtube_creation import create_playlist


async def run_youtube_job(store, jid, payload):
    token = Path(os.environ.get('MUSIC_BRIDGE_YOUTUBE_TOKEN', 'secrets/youtube-token.json'))
    directory = store.path.parent / 'youtube-jobs'
    state_path = directory / (jid + '.json')
    report = None
    latest = {}

    def result(state):
        playlist_id = state.get('playlist_id')
        matches, skipped = [], []
        if report:
            for index, row in enumerate(report['tracks']):
                if row['proposal']:
                    matches.append({'source_index': index, **row['proposal']})
                else:
                    skipped.append({'source_index': index, 'title': row['title'],
                                    'artists': row['artists'], 'reason': 'no_confident_video_match'})
        return {'destination': 'youtube', 'playlist_id': playlist_id,
                'playlist_url': 'https://www.youtube.com/playlist?list=' + playlist_id if playlist_id else None,
                'playlist_name': state.get('name'), 'playlist_created': bool(playlist_id),
                'creation_verified': state.get('status') == 'completed',
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
        report = await yt.preview(payload.get('source_url'), token, 20,
            resolved={'title': payload['title'], 'country': payload['country'],
                      'request': {'tracks': payload['tracks']}},
            progress=lambda n: store.update(jid, 'running', n))
        if not any(row['proposal'] for row in report['tracks']):
            store.update(jid, 'failed', len(payload['tracks']), result({}),
                         error='No confident video matches. No playlist was created.')
            return
        state = await create_playlist(report, token, state_path, payload.get('source_url'), on_state=checkpoint)
        final = result(state)
        store.record_youtube_creation(jid, final, state['channel_id'])
        store.update(jid, 'completed', len(payload['tracks']), final)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        progress = store.get(jid)['processed']
        uncertain = bool(latest.get('playlist_id') or latest.get('pending'))
        message = ('YouTube creation needs review. Keep this job ID; inspect its playlist and saved checkpoints before retrying.'
                   if uncertain else 'YouTube job failed before creation. Check authorization, quota, and service configuration.')
        if not uncertain and isinstance(exc, yt.PreviewError):
            message = str(exc)
        store.update(jid, 'needs_review' if uncertain else 'failed', progress, result(latest), error=message)
