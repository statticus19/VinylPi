"""Serial worker: long searches survive mobile HTTP timeouts."""
import asyncio
from .youtube_jobs import run_youtube_job, run_review_creation
import time
from urllib.parse import urlsplit
import httpx
from .playlists import Track, select_apple_video

class ProviderError(Exception): pass

class JobRunner:
    def __init__(self, store):
        self.store = store
        self.wake = asyncio.Event()
        self.last_apple_call = 0.0

    async def search(self, client, track, country):
        wait = 3.1 - (time.monotonic() - self.last_apple_call)
        if wait > 0: await asyncio.sleep(wait)
        self.last_apple_call = time.monotonic()
        r = await client.get('https://itunes.apple.com/search', params={
            'term': track.artists[0] + ' ' + track.title, 'entity': 'musicVideo',
            'media': 'musicVideo', 'country': country, 'limit': 50})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict) or not isinstance(data.get('results'), list):
            raise ProviderError('Apple video search returned an unexpected response.')
        return data['results']

    async def run_one(self, jid, payload):
        if payload.get('_review_creation'):
            await run_review_creation(self.store, jid, payload)
            return
        if payload['mode'] == 'youtube_video_playlist':
            await run_youtube_job(self.store, jid, payload)
            return
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            if payload['mode'] == 'spotify_handoff':
                request = {'title': payload['title'], 'sourceName': 'Music Link Bridge',
                    'destination': 'spotify', 'tracklist': [
                        {k:v for k,v in t.items() if v not in ('', None)} for t in payload['tracks']]}
                r = await client.post('https://soundiiz.com/go/import-playlist', json=request)
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict): raise ProviderError('Soundiiz returned an unexpected response.')
                p = urlsplit(data.get('shareUrl', ''))
                if (data.get('status') != 'success' or p.scheme != 'https' or
                    p.hostname != 'soundiiz.com' or p.username or p.port not in (None,443) or
                    not p.path.startswith('/go/import-playlist/') or
                    data.get('nbTracks') != len(payload['tracks']) or
                    not isinstance(data.get('expiresAt'), (int, float)) or data['expiresAt'] <= time.time()):
                    raise ProviderError('Soundiiz returned an invalid, expired or incomplete import handoff.')
                result = {'destination': 'spotify', 'handoff_url': data['shareUrl'],
                    'expires_at': data['expiresAt'], 'accepted_tracks': data['nbTracks'],
                    'playlist_created': False, 'requires_browser_confirmation': True}
                self.store.update(jid, 'completed', len(payload['tracks']), result)
                return
            matched, skipped = [], []
            # Per-job memoization avoids repeating searches but preserves repeated songs.
            seen = {}
            for index, raw in enumerate(payload['tracks']):
                track = Track.model_validate(raw)
                key = (track.title, tuple(track.artists), payload['country'])
                if key not in seen:
                    candidates = await self.search(client, track, payload['country'])
                    seen[key] = select_apple_video(track, candidates)
                video, reason = seen[key]
                if video:
                    matched.append({'source_index': index, **video})
                else:
                    skipped.append({'source_index': index, 'title': track.title,
                        'artists': track.artists, 'reason': reason})
                self.store.update(jid, 'running', index+1)
            # Always include time + job suffix; iOS cannot reliably check existing playlist names.
            suffix = time.strftime('%Y-%m-%d %H%M', time.gmtime()) + ' UTC ' + jid[:6]
            name = payload['title'] + ' + Music Videos (' + suffix + ')'
            result = {'destination': 'apple_music', 'playlist_name': name,
                'playlist_created': False, 'requires_ios_creation': True,
                'matched_count': len(matched), 'skipped_count': len(skipped),
                'matches': matched, 'skipped': skipped,
                'notes': ['Catalog metadata match; iOS must verify Store ID and playable video availability before creation.']}
            self.store.update(jid, 'completed', len(payload['tracks']), result)

    async def loop(self):
        self.store.recover()
        while True:
            self.wake.clear()
            item = self.store.take()
            if not item:
                await self.wake.wait()
                continue
            jid, payload = item
            try:
                await self.run_one(jid, payload)
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, ValueError, ProviderError) as exc:
                progress = self.store.get(jid)['processed']
                self.store.update(jid, 'failed', progress,
                    error='Provider request failed. No destination playlist was created by this job. Retry with a new idempotency key.')
