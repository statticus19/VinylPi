"""Offline transfer lifecycle tests; never contact Soundiiz."""
import uuid
import pytest
from music_bridge.store import JobStore, KeyConflict
from music_bridge.soundiiz import refresh_notes


def payload(count=226):
    return {'mode':'soundiiz_transfer','title':'Favorite Songs',
            'tracks':[{'title':f'Track {i}', 'artists':['Artist']} for i in range(count)],
            'country':'US','source_url':None,'review_before_create':False}


def ready(store, job_id, number):
    batch, _ = store.claim_soundiiz_part(job_id, number)
    assert len(batch['tracks']) in (26, 200)
    return store.finish_soundiiz_part(job_id,number,{'handoff_url':f'https://soundiiz.com/go/import-playlist/{uuid.uuid4().hex}',
                'expires_at':9999999999,'accepted_tracks':len(batch['tracks'])})


def test_two_parts_complete_and_persist(tmp_path):
    s = JobStore(tmp_path/'jobs.sqlite')
    job,_=s.create(payload(),uuid.uuid4().hex)
    jid=job['job_id']; assert job['status']=='handoff_pending'
    ready(s,jid,1); ready(s,jid,2)
    first=s.mark_soundiiz_transferred(jid,2,'tidal')
    assert first['status']=='handoff_pending'
    assert first['processed']==26
    last=s.mark_soundiiz_transferred(jid,1,'tidal')
    assert last['status']=='completed'
    assert last['processed']==226
    assert last['result']['transfers_confirmed']==2
    assert 'user' in last['result']['notes'][0]
    assert last['result']['completed_by_user_at']
    assert last['result']['parts'][1]['transferred_to']=='tidal'
    assert s.get(jid)==last
    s.recover(); assert s.get(jid)==last


def test_upgrade_legacy_transfer_metadata_without_resubmission(tmp_path):
    s=JobStore(tmp_path/'jobs.sqlite')
    jid=s.create(payload(26),uuid.uuid4().hex)[0]['job_id']
    batch,_=s.claim_soundiiz_part(jid,1)
    s.finish_soundiiz_part(jid,1,{'handoff_url':'https://soundiiz.com/go/import-playlist/abc', 'expires_at':9999999999,'accepted_tracks':26})
    existing=s.mark_soundiiz_transferred(jid,1)
    assert existing['status']=='completed'
    upgraded=s.mark_soundiiz_transferred(jid,1,'youtube_music')
    assert upgraded['result']['parts'][0]['transferred_to']=='youtube_music'
    assert upgraded['result']['parts'][0]['transferred_at']==existing['result']['parts'][0]['transferred_at']
    with pytest.raises(KeyConflict): s.mark_soundiiz_transferred(jid,1,'tidal')
    with pytest.raises(KeyConflict): s.claim_soundiiz_part(jid,1,True)


def test_confirmed_part_not_regenerated_even_if_remaining_pending(tmp_path):
    s=JobStore(tmp_path/'jobs.sqlite'); jid=s.create(payload(),uuid.uuid4().hex)[0]['job_id']
    ready(s,jid,2); s.mark_soundiiz_transferred(jid,2,'tidal')
    with pytest.raises(KeyConflict): s.claim_soundiiz_part(jid,2,True)
    batch,_=s.claim_soundiiz_part(jid,1)
    assert len(batch['tracks'])==200


def test_uncertain_stays_uncertain_without_silent_retry(tmp_path):
    s=JobStore(tmp_path/'jobs.sqlite'); jid=s.create(payload(26),uuid.uuid4().hex)[0]['job_id']
    s.claim_soundiiz_part(jid,1)
    s.recover()
    assert s.get(jid)['result']['parts'][0]['status']=='uncertain'
    with pytest.raises(KeyConflict): s.claim_soundiiz_part(jid,1)
    batch,_=s.claim_soundiiz_part(jid,1,True)
    assert len(batch['tracks'])==26


def test_manifest_and_notes_have_cautious_language():
    from music_bridge.soundiiz import build_manifest
    m=build_manifest(payload())
    assert len(m['consolidation_instructions'])>=3
    assert 'No handoffs' in m['notes'][0]
    m['parts'][0]['status']='ready'
    refresh_notes(m)
    assert '0 of 2' in m['notes'][0]
    assert 'No handoffs generated' not in '\n'.join(m['notes'])



def test_api_transfer_body_remains_optional():
    from music_bridge.app import app
    op = app.openapi()['paths']['/v1/playlist/jobs/{job_id}/soundiiz-parts/{part_number}/transferred']['post']
    assert op.get('requestBody', {}).get('required', False) is False
