"""Offline tests; no external YouTube requests."""
import json
import uuid
import pytest
from music_bridge.store import JobStore, KeyConflict
from music_bridge.youtube_creation import create_playlist
from music_bridge import youtube_matching as yt


def build(tmp_path, n=26):
    store = JobStore(tmp_path / 'jobs.db')
    payload = dict(mode='youtube_video_playlist', title='Test', country='US',
                   review_before_create=True, source_url=None,
                   tracks=[{'title':f'Track {i}', 'artists':['Artist']} for i in range(n)])
    jid = store.create(payload, str(uuid.uuid4()))[0]['job_id']
    rows = []
    for i in range(n):
        rows.append({'source_index':i, 'title':f'Track {i}', 'artists':['Artist'],
                     'suggested_video_id':'AAAAAAAAAAA',
                     'candidates':[{'video_id':'AAAAAAAAAAA','metadata_eligible':True,'kind':'music_video'},
                                   {'video_id':'BBBBBBBBBBB','metadata_eligible':False,'kind':'unlabeled_needs_review'}]})
    store.update(jid, 'awaiting_approval', n, {'review_required':True,'review_candidates':rows,
                                                'review_decisions':{}, 'playlist_created':False})
    return store, jid


def test_confirm_freezes_override_skip_and_suggestions(tmp_path):
    store, jid = build(tmp_path)
    store.set_youtube_review_decision(jid, 0, 'approve', 'BBBBBBBBBBB')
    store.set_youtube_review_decision(jid, 1, 'skip')
    outcome = store.queue_review_creation(jid)
    assert outcome['selected_count'] == 25
    plan = store.get(jid)['result']['creation_plan']
    assert plan[:3] == ['BBBBBBBBBBB', None, 'AAAAAAAAAAA']
    with pytest.raises(KeyConflict):
        store.queue_review_creation(jid)
    with pytest.raises(KeyConflict):
        store.set_youtube_review_decision(jid, 0, 'skip')
    reopened = JobStore(store.path)
    task_id, payload = reopened.take()
    assert task_id == jid and payload['_review_creation'] is True
    assert reopened.get(jid)['result']['creation_plan'] == plan
    reopened.recover()
    assert reopened.get(jid)['status'] == 'interrupted'
    assert reopened.take() is None


def test_unusable_selection_never_queues(tmp_path):
    store, jid = build(tmp_path, 2)
    with store.connect() as db:
        result=store.get(jid)['result']
        result['review_candidates'][0]['suggested_video_id']='XXXXXXXXXXX'
        db.execute('UPDATE jobs SET result=? WHERE id=?', (json.dumps(result),jid))
    with pytest.raises(ValueError):
        store.queue_review_creation(jid)
    assert store.get(jid)['status'] == 'awaiting_approval'


def test_zero_choices_never_queues(tmp_path):
    store, jid = build(tmp_path, 2)
    store.set_youtube_review_decision(jid, 0, 'skip')
    store.set_youtube_review_decision(jid, 1, 'skip')
    with pytest.raises(ValueError):
        store.queue_review_creation(jid)
    assert store.get(jid)['status'] == 'awaiting_approval'


@pytest.mark.asyncio
async def test_creation_gates_before_remote_writes(tmp_path):
    report = {'playlist_title':'Test', 'tracks':[
        {'proposal':{'video_id':'AAAAAAAAAAA','metadata_eligible':False}}]*26}
    with pytest.raises(yt.PreviewError):
        await create_playlist(report, tmp_path/'missing-token', tmp_path/'x.json', None)
    # reviewed=True passes size/eligibility guards and stops on missing token.
    with pytest.raises(FileNotFoundError):
        await create_playlist(report, tmp_path/'missing-token', tmp_path/'x.json', None, reviewed=True)

@pytest.mark.asyncio
async def test_review_worker_uses_frozen_plan_once(tmp_path, monkeypatch):
    from music_bridge import youtube_jobs
    store, jid = build(tmp_path)
    store.set_youtube_review_decision(jid, 0, 'approve', 'BBBBBBBBBBB')
    store.queue_review_creation(jid)
    jid2, payload = store.take()
    assert jid == jid2
    observed = {}
    async def fake_create(report, token, path, source_url, on_state=None, reviewed=False):
        assert reviewed is True
        assert len(report['tracks']) == 26
        assert report['tracks'][0]['proposal']['video_id'] == 'BBBBBBBBBBB'
        assert report['tracks'][1]['proposal']['video_id'] == 'AAAAAAAAAAA'
        observed['calls'] = observed.get('calls', 0) + 1
        on_state({'playlist_id': 'PL_TEST', 'name': 'Test', 'pending': None,
                  'inserted': [], 'rejected': []})
        return {'playlist_id':'PL_TEST', 'name':'Test', 'status':'completed',
                'channel_id':'UC12345', 'inserted':[], 'rejected':[]}
    monkeypatch.setattr(youtube_jobs, 'create_playlist', fake_create)
    await youtube_jobs.run_review_creation(store, jid, payload)
    result = store.get(jid)
    assert result['status'] == 'completed'
    assert result['result']['creation_verified'] is True
    assert observed['calls'] == 1
    assert store.take() is None
