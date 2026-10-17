"""Explicit one-shot recovery of an interrupted *empty* reviewed YouTube playlist.

Default CLI is inspection-only. --commit plus exact --confirm-playlist-id are
required for writes. Never creates a playlist or automatically repeats a write.
"""
import argparse
import asyncio
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

import httpx
from . import youtube_matching as yt
from .recovery_inspect import inspect
from .youtube_creation import api_write, save_state, verify_remote, DefinitiveVideoRejection
from .store import JobStore


def validate_local(jid, db_path, state_path):
    with sqlite3.connect(db_path) as db:
        row = db.execute('SELECT status,payload,result,processed FROM jobs WHERE id=?', (jid,)).fetchone()
    if row is None or row[0] != 'interrupted':
        raise ValueError('Job must exist in interrupted status; no writes permitted.')
    payload, result = json.loads(row[1]), json.loads(row[2])
    state = json.loads(Path(state_path).read_text())
    plan = result.get('creation_plan')
    if (payload.get('mode') != 'youtube_video_playlist' or not payload.get('review_before_create')
            or not isinstance(plan, list) or len(plan) != len(payload.get('tracks', []))
            or not isinstance(state.get('planned_video_ids'), list)
            or not 1 <= len(state['planned_video_ids']) <= 1000
            or state.get('status') != 'needs_review' or not state.get('playlist_id')
            or not re.fullmatch(r'[A-Za-z0-9_-]{11}', str(state['planned_video_ids'][0]))
            or state.get('inserted') != [] or state.get('rejected') != []
            or state.get('pending') != {'insert_index': 0, 'video_id': state['planned_video_ids'][0]}):
        raise ValueError('Not the supported first-insertion recovery state; no writes permitted.')
    selected = [(i, v) for i, v in enumerate(plan) if v is not None]
    if selected != list(zip(state.get('source_indices', []), state['planned_video_ids'])):
        raise ValueError('Frozen database plan differs from checkpoint; no writes permitted.')
    return payload, result, state, row[3]


async def resume(jid, *, db_path, state_path, token_path, commit=False, confirm_id=None):
    payload, result, state, processed = validate_local(jid, db_path, state_path)
    audit = await inspect(token_path, state_path)
    if (audit['assessment'].split(':')[0] != 'PENDING_VIDEO_ABSENT'
            or audit['remote_count'] != 0 or audit['playlist_id'] != state['playlist_id']):
        raise ValueError('Remote playlist is not verified empty; no writes permitted.')
    if not commit:
        return {'mode':'read_only', 'playlist_id':state['playlist_id'], 'planned_count':len(state['planned_video_ids']), 'assessment':audit['assessment']}
    if confirm_id != state['playlist_id']:
        raise ValueError('Exact playlist ID confirmation is required; no writes permitted.')

    # Atomic claim: prevent two instances, or another worker, from owning recovery.
    with sqlite3.connect(db_path, timeout=5) as db:
        db.execute('BEGIN IMMEDIATE')
        changed = db.execute("UPDATE jobs SET status='creating_review',updated=? WHERE id=? AND status='interrupted'",
                             (time.time(), jid)).rowcount
        if changed != 1:
            raise ValueError('Recovery already claimed or job status changed; no writes permitted.')

    store = JobStore(db_path)
    credentials = json.loads(Path(token_path).read_text())
    headers = {}
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            auth = await yt.response_json(await client.post('https://oauth2.googleapis.com/token', data={
                'client_id':credentials['client_id'],'client_secret':credentials['client_secret'],
                'refresh_token':credentials['refresh_token'],'grant_type':'refresh_token'}))
            access = auth.get('access_token')
            if not isinstance(access, str) or not access:
                raise yt.PreviewError('OAuth access token missing.')
            headers = {'Authorization':'Bearer ' + access}
            identity = await yt.response_json(await client.get(yt.API+'channels', headers=headers,
                params={'part':'snippet','mine':'true'}))
            if len(identity.get('items', [])) != 1 or identity['items'][0].get('id') != state['channel_id']:
                raise yt.PreviewError('YouTube channel identity mismatch.')
            # Fresh second read immediately before first insertion, no old audit trust.
            owner = await yt.response_json(await client.get(yt.API+'playlists', headers=headers,
                params={'part':'snippet','id':state['playlist_id']}))
            if len(owner.get('items', [])) != 1 or owner['items'][0].get('snippet', {}).get('channelId') != state['channel_id']:
                raise yt.PreviewError('Playlist ownership changed.')
            empty = await yt.response_json(await client.get(yt.API+'playlistItems', headers=headers,
                params={'part':'snippet','playlistId':state['playlist_id'],'maxResults':1}))
            if empty.get('items') or empty.get('nextPageToken'):
                raise yt.PreviewError('Playlist is no longer empty; no insertions sent.')

            for original, video_id in enumerate(state['planned_video_ids']):
                position = len(state['inserted'])
                state['status'] = 'creating'
                state['pending'] = {'insert_index':position,'video_id':video_id}
                save_state(state_path, state)  # durable intent BEFORE write
                try:
                    item = await api_write(client, 'playlistItems', headers, 'snippet', {'snippet': {
                        'playlistId':state['playlist_id'], 'position':position,
                        'resourceId':{'kind':'youtube#video','videoId':video_id}}})
                except DefinitiveVideoRejection as exc:
                    state['rejected'].append({'source_index':state['source_indices'][original],
                                              'video_id':video_id,'reason':exc.reason})
                    rejected = {x['source_index'] for x in state['rejected']}
                    state['video_ids'] = [v for i,v in zip(state['source_indices'],state['planned_video_ids']) if i not in rejected]
                    state['pending'] = None
                    save_state(state_path, state)
                    continue
                state['inserted'].append({'video_id':video_id,'playlist_item_id':item['id']})
                state['pending'] = None
                save_state(state_path, state)
            state = await verify_remote(client, headers, state, state_path)
            if state['rejected']:
                state['status'] = 'partial_verified'
                save_state(state_path, state)
        final = dict(result)
        final.update(playlist_id=state['playlist_id'], playlist_name=state['name'],
                     playlist_url='https://www.youtube.com/playlist?list='+state['playlist_id'],
                     playlist_created=True,creation_verified=True,
                     rejected=state['rejected'],inserted_count=len(state['inserted']),pending=None)
        store.record_youtube_creation(jid, final, state['channel_id'])
        status = 'needs_review' if state['rejected'] else 'completed'
        store.update(jid, status, processed, final,
                     'Some videos were rejected; inspect playlist.' if state['rejected'] else None)
        return {'mode':'completed', 'playlist_id':state['playlist_id'], 'inserted_count':len(state['inserted']),
                'rejected_count':len(state['rejected']), 'status':status}
    except BaseException as exc:
        # Don't clear pending or retry. Preserve safe diagnostics only.
        current = store.get(jid) or {}
        safe = str(exc)[:300] if isinstance(exc, yt.PreviewError) else 'Recovery stopped ('+type(exc).__name__+').'
        store.update(jid, 'interrupted', processed, current.get('result') or result,
                     safe + ' Inspect checkpoint and remote playlist; no automatic retry.')
        raise


def main():
    p = argparse.ArgumentParser(description='Explicit recovery of an empty reviewed YouTube playlist')
    p.add_argument('job_id')
    p.add_argument('--commit', action='store_true', help='Perform actual YouTube playlistItems writes')
    p.add_argument('--confirm-playlist-id', help='Exact existing destination playlist ID')
    p.add_argument('--db', default='/var/lib/music-bridge/jobs.db')
    p.add_argument('--state-directory', default='/var/lib/music-bridge/youtube-jobs')
    p.add_argument('--token-path', default=os.environ.get('MUSIC_BRIDGE_YOUTUBE_TOKEN','/home/vinyl/music-bridge/secrets/youtube-token.json'))
    args=p.parse_args()
    jid=str(uuid.UUID(args.job_id))
    try:
        result=asyncio.run(resume(jid,db_path=args.db,
            state_path=str(Path(args.state_directory)/(jid+'.json')),
            token_path=args.token_path,commit=args.commit,confirm_id=args.confirm_playlist_id))
    except (ValueError, OSError, yt.PreviewError, httpx.HTTPError, sqlite3.Error) as exc:
        if isinstance(exc, yt.PreviewError):
            print('Recovery stopped:', str(exc)[:300])
        else:
            print('Recovery stopped:',type(exc).__name__)
        raise SystemExit(1) from None
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    main()
