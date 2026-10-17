import tempfile
import uuid
import unittest
from pathlib import Path
from pydantic import ValidationError
from music_bridge.playlists import PlaylistJob
from music_bridge.store import JobStore, KeyConflict

TRACK = {'title':'Test Track','artists':['Test Artist']}

class ReviewModeTests(unittest.TestCase):
    def test_opt_in_over_twenty(self):
        # YouTube video playlist jobs automatically enter review mode, even
        # when older Shortcuts omit the flag or explicitly send false.
        for flag in ({}, {'review_before_create': False}, {'review_before_create': True}):
            job = PlaylistJob(mode='youtube_video_playlist', title='Test',
                              tracks=[TRACK] * 21, **flag)
            self.assertTrue(job.review_before_create)
            self.assertEqual(len(job.tracks), 21)

        # Keep the independent 1,000-track safety limit.
        with self.assertRaises(ValidationError):
            PlaylistJob(mode='youtube_video_playlist', title='Test',
                        tracks=[TRACK] * 1001, review_before_create=True)

    def test_decisions_are_persistent_and_validated(self):
        with tempfile.TemporaryDirectory() as root:
            store=JobStore(Path(root)/'jobs.db')
            payload=PlaylistJob(mode='youtube_video_playlist',title='Test',tracks=[TRACK]*21,review_before_create=True).model_dump()
            jid=store.create(payload, str(uuid.uuid4()))[0]['job_id']
            video='ABCDEFGHIJK'
            review={'review_candidates':[{'source_index':i,'candidates':[{'video_id':video,'metadata_eligible':True}]} for i in range(21)],'review_decisions':{},'playlist_created':False}
            store.update(jid,'awaiting_approval',21,review)
            self.assertEqual(store.set_youtube_review_decision(jid,0,'approve',video)['reviewed_count'],1)
            self.assertEqual(store.set_youtube_review_decision(jid,1,'skip')['reviewed_count'],2)
            with self.assertRaises(ValueError):store.set_youtube_review_decision(jid,2,'approve','ZZZZZZZZZZZ')
            with self.assertRaises(ValueError):store.set_youtube_review_decision(jid,21,'skip')
            reopened=JobStore(Path(root)/'jobs.db')
            self.assertEqual(reopened.get(jid)['result']['review_decisions']['0']['video_id'],video)
            self.assertEqual(reopened.get(jid)['result']['review_decisions']['1']['action'],'skip')
            with reopened.connect() as db:
                db.execute('UPDATE jobs SET created=0 WHERE id=?',(jid,))
                reopened.maintain(db)
            self.assertIsNotNone(reopened.get(jid))
