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
import youtube_preview as yt


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


async def api_write(client, endpoint, headers, part, body):
    response = await client.post(yt.API + endpoint, headers=headers, params={'part': part}, json=body)
    if not 200 <= response.status_code < 300:
        raise yt.PreviewError(f'YouTube write returned HTTP {response.status_code}. A playlist may already exist; do not retry blindly.')
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
        for _ in range(10):
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
                raise yt.PreviewError('Playlist verification received a repeated page token.')
            seen.add(page)
        else:
            raise yt.PreviewError('Playlist verification exceeded its page limit.')
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


async def create_playlist(report, token_path, state_path, source_url):
    if Path(state_path).exists():
        raise yt.PreviewError('State file already exists. No writes were sent; inspect the previous run first.')
    selected = [r['proposal'] for r in report['tracks'] if r['proposal']]
    if not selected or len(selected) > 20:
        raise yt.PreviewError('Creation test needs 1–20 matched videos.')
    if any(not v.get('metadata_eligible') or not re.fullmatch(r'[A-Za-z0-9_-]{11}', v.get('video_id', '')) for v in selected):
        raise yt.PreviewError('Invalid selection; no writes were sent.')
    token_path = Path(token_path)
    if token_path.stat().st_mode & 0o077:
        raise yt.PreviewError('Token permissions must be 600.')
    credentials = json.loads(token_path.read_text())
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H%M UTC')
    name = report['playlist_title'][:90] + ' + Music Videos (' + stamp + ' ' + uuid.uuid4().hex[:6] + ')'
    state = {'status': 'planned', 'source_url': source_url, 'name': name,
             'channel_id': credentials['channel_id'], 'playlist_id': None,
             'video_ids': [v['video_id'] for v in selected], 'inserted': [], 'pending': None}
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
        save_state(state_path, state, initial=True)
        try:
            state.update(status='creating', pending='create_playlist')
            save_state(state_path, state)
            created = await api_write(client, 'playlists', headers, 'snippet,status', {
                'snippet': {'title': name, 'description': 'Music Bridge: music videos with lyric-video fallback. Metadata-selected; review availability and content.'},
                'status': {'privacyStatus': 'private'}})
            state.update(playlist_id=created['id'], pending=None)
            save_state(state_path, state)
            print('Playlist created:', 'https://www.youtube.com/playlist?list=' + created['id'], flush=True)
            if created.get('snippet', {}).get('channelId') != credentials['channel_id']:
                raise yt.PreviewError('Created playlist ownership was unexpected. No videos were added.')
            for index, video_id in enumerate(state['video_ids']):
                state['pending'] = {'insert_index': index, 'video_id': video_id}
                save_state(state_path, state)
                item = await api_write(client, 'playlistItems', headers, 'snippet', {'snippet': {
                    'playlistId': state['playlist_id'], 'position': index,
                    'resourceId': {'kind': 'youtube#video', 'videoId': video_id}}})
                state['inserted'].append({'video_id': video_id, 'playlist_item_id': item['id']})
                state['pending'] = None
                save_state(state_path, state)
            return await verify_remote(client, headers, state, state_path)
        except BaseException:
            state['status'] = 'needs_review'
            save_state(state_path, state)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--playlist')
    parser.add_argument('--token', default='secrets/youtube-token.json')
    parser.add_argument('--state', default='secrets/youtube-create-test.json')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--create', action='store_true')
    mode.add_argument('--verify', action='store_true', help='Read and verify the existing playlist without YouTube writes.')
    args = parser.parse_args()
    try:
        if args.verify:
            state = asyncio.run(verify_existing(args.token, args.state))
            print('Verified videos:', len(state['video_ids']))
            print('Run status:', state['status'])
            print('https://www.youtube.com/playlist?list=' + state['playlist_id'])
            return 0
        if not args.playlist:
            parser.error('--playlist is required unless using --verify')
        if Path(args.state).exists():
            state = json.loads(Path(args.state).read_text())
            print('Existing run status:', state.get('status'))
            if state.get('playlist_id'):
                print('https://www.youtube.com/playlist?list=' + state['playlist_id'])
            print('No new playlist was created. Send this status before retrying.')
            return 0 if state.get('status') == 'completed' else 1
        report = asyncio.run(yt.preview(args.playlist, args.token, 10))
        print('Source:', report['playlist_title'])
        print('Channel:', report['channel_title'])
        for row in report['tracks']:
            v = row['proposal']
            print(row['artists'][0] + ' — ' + row['title'] + ': ' +
                  (v['kind'] + ' | ' + v['channel'] + ' | ' + v['url'] if v else 'SKIPPED'))
        print('Uploader identity and content are inferred from metadata, except explicitly reviewed content. Review the plan above.')
        if not args.create:
            print('Preview only. No playlist was created.')
            return 0
        if input('Type CREATE to create ONE PRIVATE playlist with these selections: ').strip() != 'CREATE':
            print('Cancelled. No playlist was created.')
            return 0
        state = asyncio.run(create_playlist(report, args.token, args.state, args.playlist))
        print('Verified videos:', len(state['inserted']))
        print('https://www.youtube.com/playlist?list=' + state['playlist_id'])
    except KeyboardInterrupt:
        print('Stopped. If a write started, inspect the state file before any retry.', file=sys.stderr)
        return 1
    except Exception as exc:
        print('Creation test stopped: ' + (str(exc) if isinstance(exc, yt.PreviewError) else 'File, network, or response error.'), file=sys.stderr)
        print('Do not remove the state file or retry writes blindly. A partial playlist may exist.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
