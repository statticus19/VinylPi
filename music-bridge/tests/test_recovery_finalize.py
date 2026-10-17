import json
import sqlite3
import uuid
import pytest
from music_bridge.recovery_finalize import finalize
from music_bridge.playlists import PlaylistJob
from music_bridge.store import JobStore


def make(tmp_path):
    store = JobStore(tmp_path / 'jobs.db')
    payload = PlaylistJob(mode='youtube_video_playlist', title='Test', tracks=[{'title':'A','artists':['B']}], review_before_create=True).model_dump()
    jid = store.create(payload, str(uuid.uuid4()))[0]['job_id']
    result = {'creation_plan':['ABCDEFGHIJK'], 'playlist_id':'PLexisting'}
    store.update(jid,'interrupted',1,result,'verification failed')
    path = tmp_path / (jid+'.json')
    state = {'status':'creating','playlist_id':'PLexisting','name':'Test', 'channel_id':'owner',
             'planned_video_ids':['ABCDEFGHIJK'],'video_ids':['ABCDEFGHIJK'],
             'source_indices':[0],'pending':None,'rejected':[],
             'inserted':[{'video_id':'ABCDEFGHIJK','playlist_item_id':'insertid'}]}
    path.write_text(json.dumps(state))
    return store,jid,path,state


def mock_audit(monkeypatch, remote=None):
    from music_bridge import recovery_finalize
    async def audit(*args):
        return {'playlist_owned_by_authorized_channel':True,'playlist_id':'PLexisting',
            'remote_count':1, 'remote_video_ids':['ABCDEFGHIJK'] if remote is None else remote,
            'confirmed_checkpoint_insertions':1, 'pending':None,'assessment':'MISMATCH'}
    monkeypatch.setattr(recovery_finalize,'inspect',audit)


@pytest.mark.asyncio
async def test_dry_run_does_not_modify_local(tmp_path,monkeypatch):
    store,jid,path,state = make(tmp_path); mock_audit(monkeypatch)
    before = path.read_bytes()
    out = await finalize(jid,db_path=store.path,state_path=path,token_path='unused')
    assert out['mode']=='read_only' and out['youtube_playlist_writes']==0
    assert before==path.read_bytes() and store.get(jid)['status']=='interrupted'


@pytest.mark.asyncio
async def test_local_finalize_only(tmp_path,monkeypatch):
    store,jid,path,state = make(tmp_path); mock_audit(monkeypatch)
    out=await finalize(jid,db_path=store.path,state_path=path,token_path='unused',commit=True,confirm_id='PLexisting')
    assert out['mode']=='finalized_local_only'
    assert store.get(jid)['status']=='completed'
    assert store.get(jid)['result']['creation_verified'] is True
    assert json.loads(path.read_text())['status']=='completed'
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM mappings WHERE id=?',(jid,)).fetchone()[0]==1


@pytest.mark.asyncio
async def test_reject_remote_mismatch(tmp_path,monkeypatch):
    store,jid,path,state=make(tmp_path); mock_audit(monkeypatch,remote=['ZZZZZZZZZZZ'])
    with pytest.raises(ValueError,match='do not exactly match'):
        await finalize(jid,db_path=store.path,state_path=path,token_path='unused',commit=True,confirm_id='PLexisting')
    assert store.get(jid)['status']=='interrupted'


@pytest.mark.asyncio
async def test_wrong_playlist_confirmation(tmp_path,monkeypatch):
    store,jid,path,state=make(tmp_path); mock_audit(monkeypatch)
    with pytest.raises(ValueError,match='Exact playlist'):
        await finalize(jid,db_path=store.path,state_path=path,token_path='unused',commit=True,confirm_id='wrong')
    assert store.get(jid)['status']=='interrupted'
