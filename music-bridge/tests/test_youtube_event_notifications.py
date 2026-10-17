"""Focused, offline notification event lifecycle tests."""
import tempfile
from pathlib import Path
from music_bridge.store import JobStore


def _new_job(store, key):
    payload={'mode':'youtube_video_playlist','title':'Test Playlist','tracks':[{'title':'Song','artists':['Artist']}],'country':'US'}
    job,_=store.create(payload,key)
    return job['job_id']


def test_rate_limit_completion_and_no_ordinary_completion():
    with tempfile.TemporaryDirectory() as tmp:
        store=JobStore(Path(tmp)/'jobs.db')
        normal=_new_job(store,'normal_test_key_0001')
        delayed=_new_job(store,'delayed_test_key_0002')
        success={'destination':'youtube','playlist_url':'https://www.youtube.com/playlist?list=abc','creation_verified':True}
        store.update(normal,'completed',1,success)
        store.update(delayed,'waiting_for_rate_limit',0,{'retry_at':'2027-01-01T00:00:00+00:00'})
        store.update(delayed,'completed',1,success)
        found=store.pending_youtube_events()
        assert len(found)==1 and found[0][0]==delayed and found[0][1]=='rate_limit_completed'
        store.mark_youtube_event(delayed,'rate_limit_completed',True)
        assert store.pending_youtube_events()==[]


def test_rate_limit_terminal_failure():
    with tempfile.TemporaryDirectory() as tmp:
        store=JobStore(Path(tmp)/'jobs.db')
        jid=_new_job(store,'failed_test_key_0003')
        store.update(jid,'waiting_for_rate_limit',0,{'retry_at':'2027-01-01T00:00:00+00:00'})
        store.update(jid,'failed',0,error='Rate limit retries exhausted')
        events=store.pending_youtube_events()
        assert len(events)==1 and events[0][1]=='rate_limit_failed'


def test_no_matches_and_review():
    with tempfile.TemporaryDirectory() as tmp:
        store=JobStore(Path(tmp)/'jobs.db')
        a=_new_job(store,'nomatch_test_key_0004')
        b=_new_job(store,'review_test_key_0005')
        store.update(a,'failed',1,error='No confident video matches')
        store.queue_youtube_event(a,'no_matches')
        store.update(b,'needs_review',1,{'destination':'youtube','playlist_url':'https://www.youtube.com/playlist?list=x'},error='Write HTTP 403')
        store.queue_youtube_event(b,'needs_review')
        assert {row[1] for row in store.pending_youtube_events()}=={'no_matches','needs_review'}
