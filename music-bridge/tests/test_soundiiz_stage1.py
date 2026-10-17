"""Soundiiz planning tests: strictly offline, never contact Soundiiz."""
import json
from music_bridge.playlists import PlaylistJob
from music_bridge.soundiiz import build_manifest
from music_bridge.store import JobStore, KeyConflict
import pytest


def make_job(n=450, mode='soundiiz_transfer'):
    return PlaylistJob(mode=mode, title='Sample Playlist', tracks=[
        {'title': 'Song ' + str(i % 7), 'artists': ['Artist']}
        for i in range(n)
    ]).model_dump()


@pytest.mark.parametrize('n,expected', [(1,[1]),(199,[199]),(200,[200]),(201,[200,1]),(400,[200,200]),(401,[200,200,1]),(1000,[200]*5)])
def test_parts_cover_each_position(n, expected):
    manifest = build_manifest(make_job(n))
    parts = manifest['parts']
    assert [p['track_count'] for p in parts] == expected
    assert [i for p in parts for i in range(p['start_index'], p['end_index_exclusive'])] == list(range(n))
    assert [p['part_number'] for p in parts] == list(range(1,len(parts)+1))
    assert all(p['status'] == 'not_generated' and p['handoff_url'] is None for p in parts)


def test_new_mode_allows_large_playlist_legacy_handoff_still_rejects():
    make_job(1000)
    with pytest.raises(ValueError):
        make_job(201, 'spotify_handoff')


def test_soundiiz_parent_persists_without_entering_worker(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    payload = make_job(450)
    created, new = store.create(payload, 'test_Soundiiz_parent_123')
    assert new and created['status'] == 'handoff_pending'
    assert created['processed'] == 0
    assert created['result']['part_count'] == 3
    assert store.take() is None
    again, new = store.create(payload, 'test_Soundiiz_parent_123')
    assert not new and again == created
    with pytest.raises(KeyConflict):
        store.create(make_job(451), 'test_Soundiiz_parent_123')
    store.recover()
    assert store.get(created['job_id'])['status'] == 'handoff_pending'
    assert store.list_recent()[0]['mode'] == 'soundiiz_transfer'


def test_existing_youtube_job_still_queued(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    payload = make_job(2, 'apple_video_plan')
    created, _ = store.create(payload, 'existing_workflow_test123')
    assert created['status'] == 'queued'
    jid, taken = store.take()
    assert jid == created['job_id'] and taken == payload
