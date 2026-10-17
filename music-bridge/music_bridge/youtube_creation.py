#!/usr/bin/env python3
"""Interactive private-playlist test. No changes to the bridge service.

Writes require --create AND typing CREATE after reviewing the selected plan.
The state file blocks blind retries after an uncertain remote write.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid
import httpx
from . import youtube_matching as yt


def save_state(path, state, initial=False):
    path = Path(path)
    if not path.parent.is_dir() or path.parent.stat().st_mode & 0o077:
        raise yt.PreviewError('State directory must already exist with permissions 700.')
    if initial:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(state, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        fd, temp = tempfile.mkstemp(prefix='.youtube-state-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(state, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class DefinitiveVideoRejection(yt.PreviewError):
    """Narrow, explicitly rejected playlistItems insertion (not a transport failure)."""
    def __init__(self, reason, status):
        self.reason, self.status = reason, status
        super().__init__(f'YouTube rejected video insertion HTTP {status}: {reason}.')


# Do not generalize 403/429/5xx or timeout responses as definitive failures.
# Only a 400 with these explicit resource errors is safe to skip.
DEFINITIVE_VIDEO_REASONS = frozenset({'videoNotFound', 'videoNotAllowed'})


async def api_write(client, endpoint, headers, part, body):
    response = await client.post(yt.API + endpoint, headers=headers, params={'part': part}, json=body)
    if not 200 <= response.status_code < 300:
        # Preserve *sanitized* Google reason, never the response body, request,
        # token, or personal data. An attempted remote write is never safe to
        # repeat automatically just because the response was an error.
        reason = 'request_failed'
        try:
            error = response.json().get('error', {})
            if isinstance(error, dict):
                errors = error.get('errors') or []
                value = errors[0].get('reason', '') if isinstance(errors, list) and errors and isinstance(errors[0], dict) else ''
                if re.fullmatch(r'[A-Za-z0-9_]{1,80}', str(value)):
                    reason = value
        except (ValueError, TypeError, AttributeError):
            pass
        if (endpoint == 'playlistItems' and response.status_code == 400
                and reason in DEFINITIVE_VIDEO_REASONS):
            raise DefinitiveVideoRejection(reason, response.status_code)
        raise yt.PreviewError(
            f'YouTube {endpoint} write HTTP {response.status_code}: {reason}. '            'Remote outcome may be uncertain; inspect checkpoint and verify playlist before any retry.')
    try:
        data = response.json()
        if not isinstance(data, dict) or not isinstance(data.get('id'), str) or not data['id']:
            raise ValueError()
        return data
    except ValueError as exc:
        raise yt.PreviewError('YouTube write response was invalid. Remote outcome may be uncertain.') from exc


async def verify_remote(client, headers, state, state_path, attempts=3):
    """Read only from YouTube; preserve duplicates and compare explicit positions."""
    owner = await yt.response_json(await client.get(yt.API + 'playlists', headers=headers,
        params={'part': 'snippet', 'id': state['playlist_id']}))
    items = owner.get('items', [])
    if len(items) != 1 or items[0].get('snippet', {}).get('channelId') != state['channel_id']:
        raise yt.PreviewError('Playlist ownership could not be verified.')
    for attempt in range(attempts):
        observed = []
        page = None
        seen = set()
        pagination_problem = None
        for _ in range(25):
            params = {'part': 'snippet,contentDetails', 'playlistId': state['playlist_id'], 'maxResults': 50}
            if page:
                params['pageToken'] = page
            data = await yt.response_json(await client.get(yt.API + 'playlistItems', headers=headers, params=params))
            for item in data.get('items', []):
                snippet = item.get('snippet', {})
                observed.append({'video_id': snippet.get('resourceId', {}).get('videoId'),
                                 'position': snippet.get('position'), 'playlist_item_id': item.get('id')})
            page = data.get('nextPageToken')
            if not page:
                break
            if page in seen:
                pagination_problem = 'repeated page token'
                break
            seen.add(page)
        else:
            pagination_problem = 'page limit exceeded'
        if pagination_problem is not None:
            # A partial or inconsistent page walk is not evidence of success or
            # failure. Discard it and retry the *read-only* traversal from page 1.
            # In particular, never mark the job completed based on partial data.
            if attempt + 1 < attempts:
                await asyncio.sleep(2)
                continue
            raise yt.PreviewError(
                f'Playlist verification failed after {attempts} read-only attempts '
                f'({pagination_problem}). No additional playlist writes were sent.')
        positions = [item['position'] for item in observed]
        valid_positions = all(type(p) is int for p in positions) and sorted(positions) == list(range(len(observed)))
        ordered = sorted(observed, key=lambda item: item['position']) if valid_positions else observed
        actual = [item['video_id'] for item in ordered]
        state['verification'] = {'observed': observed, 'ordered_video_ids': actual,
                                 'positions_valid': valid_positions, 'attempts': attempt + 1}
        save_state(state_path, state)
        if valid_positions and actual == state['video_ids']:
            if state.get('pending') is not None:
                raise yt.PreviewError('Remote contents match, but an uncertain write remains in the state file. Review is required.')
            state['status'] = 'completed'
            save_state(state_path, state)
            return state
        if attempt + 1 < attempts:
            await asyncio.sleep(2)
    state['status'] = 'needs_review'
    save_state(state_path, state)
    print('Expected video IDs:', json.dumps(state['video_ids']))
    print('Observed items:', json.dumps(observed))
    raise yt.PreviewError('Existing playlist order/count did not verify. No corrective writes were sent.')


async def verify_existing(token_path, state_path):
    state = json.loads(Path(state_path).read_text())
    if not state.get('playlist_id') or not isinstance(state.get('video_ids'), list):
        raise yt.PreviewError('State file does not identify a playlist and expected videos.')
    token_path = Path(token_path)
    if token_path.stat().st_mode & 0o077:
        raise yt.PreviewError('Token permissions must be 600.')
    credentials = json.loads(token_path.read_text())
    if credentials['channel_id'] != state['channel_id']:
        raise yt.PreviewError('Saved channel identity mismatch.')
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        refresh = await yt.response_json(await client.post('https://oauth2.googleapis.com/token', data={
            'client_id': credentials['client_id'], 'client_secret': credentials['client_secret'],
            'refresh_token': credentials['refresh_token'], 'grant_type': 'refresh_token'}))
        access = refresh.get('access_token')
        if not isinstance(access, str) or not access:
            raise yt.PreviewError('Invalid access token.')
        headers = {'Authorization': 'Bearer ' + access}
        identity = await yt.response_json(await client.get(yt.API + 'channels', headers=headers,
            params={'part': 'snippet', 'mine': 'true'}))
        if len(identity.get('items', [])) != 1 or identity['items'][0].get('id') != state['channel_id']:
            raise yt.PreviewError('Authorized channel identity mismatch.')
        return await verify_remote(client, headers, state, state_path)


async def create_playlist(report, token_path, state_path, source_url, on_state=None, reviewed=False):
    def checkpoint(state, initial=False):
        save_state(state_path, state, initial=initial)
        if on_state:
            on_state(state)

    if Path(state_path).exists():
        raise yt.PreviewError('State file already exists. No writes were sent; inspect the previous run first.')
    selected = [r['proposal'] for r in report['tracks'] if r['proposal']]
    if not selected or len(selected) > (1000 if reviewed else 20):
        raise yt.PreviewError('Invalid number of selected videos; no writes were sent.')
    if any((not reviewed and not v.get('metadata_eligible')) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', v.get('video_id', '')) for v in selected):
        raise yt.PreviewError('Invalid selection; no writes were sent.')
    token_path = Path(token_path)
    if token_path.stat().st_mode & 0o077:
        raise yt.PreviewError('Token permissions must be 600.')
    credentials = json.loads(token_path.read_text())
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H%M UTC')
    name = report['playlist_title'][:90] + ' + Music Videos (' + stamp + ' ' + uuid.uuid4().hex[:6] + ')'
    state = {'status': 'planned', 'source_url': source_url, 'name': name,
             'channel_id': credentials['channel_id'], 'playlist_id': None,
             'video_ids': [v['video_id'] for v in selected],
             'planned_video_ids': [v['video_id'] for v in selected],
             'source_indices': [i for i, row in enumerate(report['tracks']) if row['proposal']],
             'rejected': [], 'inserted': [], 'pending': None}
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        refresh = await yt.response_json(await client.post('https://oauth2.googleapis.com/token', data={
            'client_id': credentials['client_id'], 'client_secret': credentials['client_secret'],
            'refresh_token': credentials['refresh_token'], 'grant_type': 'refresh_token'}))
        if not isinstance(refresh.get('access_token'), str) or not refresh['access_token']:
            raise yt.PreviewError('Invalid access token; no writes were sent.')
        headers = {'Authorization': 'Bearer ' + refresh['access_token']}
        identity = await yt.response_json(await client.get(yt.API + 'channels', headers=headers, params={'part': 'snippet', 'mine': 'true'}))
        if len(identity.get('items', [])) != 1 or identity['items'][0]['id'] != credentials['channel_id']:
            raise yt.PreviewError('Channel identity mismatch; no writes were sent.')
        checkpoint(state, initial=True)
        try:
            state.update(status='creating', pending='create_playlist')
            checkpoint(state)
            created = await api_write(client, 'playlists', headers, 'snippet,status', {
                'snippet': {'title': name, 'description': 'Music Bridge: music videos with lyric-video fallback. Metadata-selected; review availability and content.'},
                'status': {'privacyStatus': 'private'}})
            state.update(playlist_id=created['id'], pending=None)
            checkpoint(state)
            print('Playlist created:', 'https://www.youtube.com/playlist?list=' + created['id'], flush=True)
            if created.get('snippet', {}).get('channelId') != credentials['channel_id']:
                raise yt.PreviewError('Created playlist ownership was unexpected. No videos were added.')
            for original_index, video_id in enumerate(state['planned_video_ids']):
                # The destination index excludes definitively rejected entries.
                position = len(state['inserted'])
                state['pending'] = {'insert_index': position, 'video_id': video_id}
                checkpoint(state)
                try:
                    item = await api_write(client, 'playlistItems', headers, 'snippet', {'snippet': {
                        'playlistId': state['playlist_id'], 'position': position,
                        'resourceId': {'kind': 'youtube#video', 'videoId': video_id}}})
                except DefinitiveVideoRejection as exc:
                    state['rejected'].append({'source_index': state['source_indices'][original_index],
                        'video_id': video_id, 'reason': exc.reason})
                    # This specific 400 is unambiguously invalid input: clear
                    # pending and exclude from expected remote content.
                    # Rebuild by original positions, preserving repeated video IDs.
                    rejected_positions = {x['source_index'] for x in state['rejected']}
                    state['video_ids'] = [v for i, v in zip(state['source_indices'], state['planned_video_ids'])
                                          if i not in rejected_positions]
                    state['pending'] = None
                    checkpoint(state)
                    continue
                state['inserted'].append({'video_id': video_id, 'playlist_item_id': item['id']})
                state['pending'] = None
                checkpoint(state)
            verified = await verify_remote(client, headers, state, state_path)
            if verified['rejected']:
                verified['status'] = 'partial_verified'
                checkpoint(verified)
            return verified
        except BaseException:
            state['status'] = 'needs_review'
            checkpoint(state)
            raise
