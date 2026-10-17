"""Read-only remote audit, then explicit local-only completion of a fully inserted job.

Never sends YouTube playlist writes. The finalization path is deliberately limited
 to a reviewed job with zero rejections and all planned insertions checkpointed.
"""
import argparse
import asyncio
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from .recovery_inspect import inspect


def validate(db_path, state_path, jid):
    checkpoint_bytes = Path(state_path).read_bytes()
    state = json.loads(checkpoint_bytes)
    with sqlite3.connect(db_path) as db:
        row = db.execute('SELECT status,payload,result,processed,total FROM jobs WHERE id=?', (jid,)).fetchone()
    if row is None or row[0] != 'interrupted':
        raise ValueError('Expected an interrupted job; no changes made.')
    payload, result = json.loads(row[1]), json.loads(row[2])
    planned = state.get('planned_video_ids')
    inserted = state.get('inserted')
    if (payload.get('mode') != 'youtube_video_playlist'
        or payload.get('review_before_create') is not True
        or state.get('status') != 'creating'
        or not isinstance(planned, list) or not planned or len(planned) > 1000
        or not isinstance(inserted, list) or len(inserted) != len(planned)
        or [item.get('video_id') for item in inserted] != planned
        or any(not item.get('playlist_item_id') for item in inserted)
        or state.get('video_ids') != planned or state.get('rejected') != []
        or state.get('pending') is not None
        or not state.get('playlist_id') or not state.get('channel_id')
        or result.get('playlist_id') != state['playlist_id']
        or row[3] != row[4] or row[4] != len(payload.get('tracks', []))):
        raise ValueError('Checkpoint/job not eligible for local finalization; no changes made.')
    plan = result.get('creation_plan')
    if (not isinstance(plan, list) or len(plan) != len(payload['tracks'])
        or [(i, video) for i, video in enumerate(plan) if video is not None]
           != list(zip(state.get('source_indices', []), planned))):
        raise ValueError('Frozen creation plan does not match checkpoint; no changes made.')
    return checkpoint_bytes, state, result, payload


async def finalize(jid, *, db_path, state_path, token_path, commit=False, confirm_id=None):
    original, state, result, payload = validate(db_path, state_path, jid)
    audit = await inspect(token_path, state_path)
    planned = state['planned_video_ids']
    # Ignore older inspection assessment: it expects a pending write and labels
    # fully inserted jobs MISMATCH despite exact remote equality.
    if (audit.get('playlist_owned_by_authorized_channel') is not True
        or audit.get('playlist_id') != state['playlist_id']
        or audit.get('remote_count') != len(planned)
        or audit.get('remote_video_ids') != planned
        or audit.get('confirmed_checkpoint_insertions') != len(planned)
        or audit.get('pending') is not None):
        raise ValueError('YouTube remote contents do not exactly match checkpoint; no changes made.')
    summary = {'playlist_id': state['playlist_id'], 'verified_count': len(planned),
               'remote_order_matches_checkpoint': True,
               'youtube_playlist_writes': 0}
    if not commit:
        return {'mode': 'read_only', **summary}
    if confirm_id != state['playlist_id']:
        raise ValueError('Exact playlist confirmation required; no changes made.')

    # All changes happen locally after a second checkpoint read and an exclusive
    # SQLite lock. Save checkpoint atomically before transaction commit. In rare
    # filesystem/DB partial failure, do not rerun blindly; inspect local status.
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT status,result,payload,processed,total FROM jobs WHERE id=?', (jid,)).fetchone()
        if (row is None or row[0] != 'interrupted' or json.loads(row[1]) != result
            or json.loads(row[2]) != payload or row[3] != row[4]
            or Path(state_path).read_bytes() != original):
            raise ValueError('Local job/checkpoint changed since verification; no changes made.')
        final_state = dict(state, status='completed')
        final_state['verification'] = {
            'source': 'recovery_finalize_read_only_remote_audit',
            'ordered_video_ids': list(planned), 'positions_valid': True,
            'verified_at': time.time()}
        final_result = dict(result)
        final_result.update(playlist_created=True, creation_verified=True,
            playlist_id=state['playlist_id'], playlist_name=state['name'],
            playlist_url='https://www.youtube.com/playlist?list=' + state['playlist_id'],
            inserted_count=len(planned), rejected=[], pending=None)
        tmp = Path(state_path).with_name(Path(state_path).name + '.finalize-tmp')
        try:
            with open(tmp, 'x', encoding='utf8') as stream:
                os.chmod(tmp, Path(state_path).stat().st_mode & 0o777)
                json.dump(final_state, stream, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, state_path)
            db.execute('INSERT OR IGNORE INTO mappings VALUES(?,?,?,?,?,?,?,?)',
                (jid, payload.get('source_url'), 'youtube', state['channel_id'],
                 state['playlist_id'], state['name'], 'videos', time.time()))
            db.execute("UPDATE jobs SET status='completed',result=?,error=NULL,updated=? WHERE id=? AND status='interrupted'",
                       (json.dumps(final_result, ensure_ascii=False), time.time(), jid))
            db.commit()
        finally:
            tmp.unlink(missing_ok=True)
    return {'mode': 'finalized_local_only', 'job_status': 'completed', **summary}


def main():
    p = argparse.ArgumentParser(description='Verify all remote videos then finalize locally (zero YouTube playlist writes)')
    p.add_argument('job_id')
    p.add_argument('--commit-local', action='store_true', help='Update checkpoint/database only, after fresh remote verification')
    p.add_argument('--confirm-playlist-id', help='Confirm exact existing destination playlist ID')
    p.add_argument('--db', default='/var/lib/music-bridge/jobs.db')
    p.add_argument('--state-directory', default='/var/lib/music-bridge/youtube-jobs')
    p.add_argument('--token-path', default=os.environ.get('MUSIC_BRIDGE_YOUTUBE_TOKEN', '/home/vinyl/music-bridge/secrets/youtube-token.json'))
    args = p.parse_args()
    jid = str(uuid.UUID(args.job_id))
    try:
        data = asyncio.run(finalize(jid, db_path=args.db,
            state_path=str(Path(args.state_directory) / (jid + '.json')),
            token_path=args.token_path, commit=args.commit_local, confirm_id=args.confirm_playlist_id))
    except Exception as exc:
        print('Finalization stopped:', type(exc).__name__, str(exc)[:180] if isinstance(exc, ValueError) else '(inspect logs locally)')
        raise SystemExit(1) from None
    print(json.dumps(data, indent=2))


if __name__ == '__main__':
    main()
