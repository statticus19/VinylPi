import json
import sqlite3
import uuid
from pathlib import Path
import pytest
from music_bridge.recovery_resume import validate_local, resume
from music_bridge.playlists import PlaylistJob
from music_bridge.store import JobStore


def setup_job(tmp_path):
    store=JobStore(tmp_path/'jobs.db')
    track={'title':'Test','artists':['Artist']}
    payload=PlaylistJob(mode='youtube_video_playlist',title='Test',tracks=[track]*2,review_before_create=True).model_dump()
    jid=store.create(payload,str(uuid.uuid4()))[0]['job_id']
    result={'creation_plan':[None,'ABCDEFGHIJK'], 'review_candidates':[], 'review_decisions':{}}
    store.update(jid,'interrupted',2,result, 'Previously interrupted')
    path=tmp_path/'youtube-jobs'/(jid+'.json')
    path.parent.mkdir()
    path.write_text(json.dumps({'playlist_id':'PLexisting','status':'needs_review','name':'Test',
       'channel_id':'owner','planned_video_ids':['ABCDEFGHIJK'],'source_indices':[1],
       'video_ids':['ABCDEFGHIJK'],'inserted':[],'rejected':[],
       'pending':{'insert_index':0,'video_id':'ABCDEFGHIJK'}}))
    return jid,store,path


def test_frozen_plan_validates(tmp_path):
    jid,store,path=setup_job(tmp_path)
    _,_,state,_=validate_local(jid,store.path,path)
    assert state['playlist_id']=='PLexisting'


def test_frozen_plan_mismatch_blocks(tmp_path):
    jid,store,path=setup_job(tmp_path)
    state=json.loads(path.read_text());state['planned_video_ids']=['ZZZZZZZZZZZ'];path.write_text(json.dumps(state))
    with pytest.raises(ValueError,match='no writes permitted'):
        validate_local(jid,store.path,path)


@pytest.mark.asyncio
async def test_inspection_never_writes(tmp_path,monkeypatch):
    jid,store,path=setup_job(tmp_path)
    from music_bridge import recovery_resume as recovery
    async def audit(*a):
        return {'playlist_id':'PLexisting','remote_count':0,'assessment':'PENDING_VIDEO_ABSENT: test'}
    monkeypatch.setattr(recovery,'inspect',audit)
    result=await resume(jid,db_path=store.path,state_path=path,token_path=tmp_path/'token',commit=False)
    assert result['mode']=='read_only'
    assert store.get(jid)['status']=='interrupted'
    assert json.loads(path.read_text())['pending']['video_id']=='ABCDEFGHIJK'


@pytest.mark.asyncio
async def test_wrong_confirmation_does_not_claim_job(tmp_path,monkeypatch):
    jid,store,path=setup_job(tmp_path)
    from music_bridge import recovery_resume as recovery
    async def audit(*a):
        return {'playlist_id':'PLexisting','remote_count':0,'assessment':'PENDING_VIDEO_ABSENT: test'}
    monkeypatch.setattr(recovery,'inspect',audit)
    with pytest.raises(ValueError,match='Exact playlist ID'):
        await resume(jid,db_path=store.path,state_path=path,token_path=tmp_path/'token',commit=True,confirm_id='WRONG')
    assert store.get(jid)['status']=='interrupted'
