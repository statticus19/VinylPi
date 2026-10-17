"""Read-only YouTube playlist reconciliation for a reviewed creation checkpoint.

Only OAuth token refresh and YouTube GET requests; never mutates remote playlists,
job database, or local checkpoint. Output contains no credentials.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import httpx
from . import youtube_matching as yt


async def inspect(token_path, state_path):
    token_path = Path(token_path)
    state_path = Path(state_path)
    state = json.loads(state_path.read_text())
    credentials = json.loads(token_path.read_text())
    if token_path.stat().st_mode & 0o077:
        raise yt.PreviewError('Token file permissions must be 600.')
    if not state.get('playlist_id') or not isinstance(state.get('planned_video_ids'), list):
        raise yt.PreviewError('Missing playlist ID or saved plan.')
    if credentials.get('channel_id') != state.get('channel_id'):
        raise yt.PreviewError('Saved account identity differs from the authorized account.')
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        login = await yt.response_json(await client.post('https://oauth2.googleapis.com/token', data={
            'client_id': credentials['client_id'], 'client_secret': credentials['client_secret'],
            'refresh_token': credentials['refresh_token'], 'grant_type': 'refresh_token'}))
        if not isinstance(login.get('access_token'), str) or not login['access_token']:
            raise yt.PreviewError('Could not authorize read-only verification.')
        headers = {'Authorization': 'Bearer ' + login['access_token']}
        mine = await yt.response_json(await client.get(yt.API + 'channels', headers=headers,
            params={'part': 'snippet', 'mine': 'true'}))
        if len(mine.get('items', [])) != 1 or mine['items'][0].get('id') != state['channel_id']:
            raise yt.PreviewError('Current YouTube account does not match checkpoint.')
        playlist = await yt.response_json(await client.get(yt.API + 'playlists', headers=headers,
            params={'part': 'snippet', 'id': state['playlist_id']}))
        if len(playlist.get('items', [])) != 1 or playlist['items'][0].get('snippet', {}).get('channelId') != state['channel_id']:
            raise yt.PreviewError('Destination playlist ownership cannot be verified.')
        observed = []
        page = None
        seen = set()
        for _ in range(25):
            params = {'part': 'snippet,contentDetails', 'playlistId': state['playlist_id'], 'maxResults': 50}
            if page:
                params['pageToken'] = page
            items = await yt.response_json(await client.get(yt.API + 'playlistItems', headers=headers, params=params))
            for item in items.get('items', []):
                snippet = item.get('snippet', {})
                observed.append({'position': snippet.get('position'),
                                 'video_id': snippet.get('resourceId', {}).get('videoId'),
                                 'playlist_item_id': item.get('id')})
            page = items.get('nextPageToken')
            if not page:
                break
            if page in seen:
                raise yt.PreviewError('Repeated pagination token; unsafe to reconcile.')
            seen.add(page)
        else:
            raise yt.PreviewError('Playlist exceeds the supported verification window.')
    positions = [x['position'] for x in observed]
    valid = all(type(i) is int for i in positions) and sorted(positions) == list(range(len(observed)))
    actual = [x['video_id'] for x in sorted(observed, key=lambda r: r['position'])] if valid else []
    inserted = state.get('inserted') or []
    recorded = [x.get('video_id') for x in inserted]
    pending = state.get('pending')
    expected = state['planned_video_ids']
    prefix_ok = valid and actual[:len(recorded)] == recorded and len(actual) >= len(recorded)
    pending_is_next = (isinstance(pending, dict) and pending.get('insert_index') == len(recorded)
        and len(expected) > len(recorded) and pending.get('video_id') == expected[len(recorded)])
    # Report classification, not permission to write. Operator reviews before next action.
    if not prefix_ok or not pending_is_next or len(actual) > len(recorded) + 1:
        assessment = 'MISMATCH: no writes are safe without further investigation'
    elif len(actual) == len(recorded) + 1 and actual[-1] == pending['video_id']:
        assessment = 'PENDING_VIDEO_PRESENT: reconcile successful remote insertion before any further write'
    elif len(actual) == len(recorded):
        assessment = 'PENDING_VIDEO_ABSENT: consistent with a failed first insertion; cause still unknown'
    else:
        assessment = 'MISMATCH: unexpected remote contents; do not retry'
    return {'playlist_id': state['playlist_id'], 'playlist_owned_by_authorized_channel': True,
            'checkpoint_status': state.get('status'), 'planned_count': len(expected),
            'confirmed_checkpoint_insertions': len(recorded), 'remote_count': len(observed),
            'remote_video_ids': actual, 'pending': pending, 'assessment': assessment,
            'remote_write_performed': False}


def main():
    p = argparse.ArgumentParser(description='Read-only Music Bridge YouTube creation checkpoint audit')
    p.add_argument('job_id', help='Existing UUID of interrupted reviewed job')
    p.add_argument('--state-directory', default='/var/lib/music-bridge/youtube-jobs')
    p.add_argument('--token-path', default=os.environ.get('MUSIC_BRIDGE_YOUTUBE_TOKEN', '/home/vinyl/music-bridge/secrets/youtube-token.json'))
    args = p.parse_args()
    import uuid
    jid = str(uuid.UUID(args.job_id))
    path = Path(args.state_directory) / (jid + '.json')
    try:
        report = asyncio.run(inspect(args.token_path, path))
    except (yt.PreviewError, OSError, ValueError, KeyError, httpx.HTTPError) as exc:
        # No raw upstream messages or OAuth fields printed.
        print('Verification failed:', type(exc).__name__)
        raise SystemExit(1) from None
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
