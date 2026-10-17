import tempfile
import unittest
import uuid
from pathlib import Path
from music_bridge.store import JobStore


class ListingV2Tests(unittest.TestCase):
    def test_bounded_listing_prioritizes_pending_and_excludes_input(self):
        with tempfile.TemporaryDirectory() as td:
            store = JobStore(Path(td)/'jobs.db')
            job_id = store.create({'title':'Demo','mode':'youtube_video_playlist',
                                   'review_before_create': True,
                                   'tracks':[{'title':'X','artists':['Y']}]}, str(uuid.uuid4()))[0]['job_id']
            jobs = store.list_recent(50)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0]['job_id'], job_id)
            self.assertEqual(jobs[0]['title'], 'Demo')
            self.assertEqual(jobs[0]['status'], 'queued')
            self.assertNotIn('payload', jobs[0])
            self.assertNotIn('result', jobs[0])
            self.assertNotIn('tracks', jobs[0])
            store.update(job_id, 'awaiting_approval', 1, {'review_decisions': {}})
            self.assertEqual(store.list_recent(1)[0]['status'], 'awaiting_approval')
