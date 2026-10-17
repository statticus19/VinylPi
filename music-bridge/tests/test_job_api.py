import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
os.environ['MUSIC_BRIDGE_TOKEN']='test-token'
m=importlib.import_module('music_bridge.app')

class JobApiTests(unittest.TestCase):
    def test_enqueue_retry_conflict_and_authenticated_read(self):
        async def idle(self):
            import asyncio
            await asyncio.Event().wait()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'MUSIC_BRIDGE_DB':str(Path(tmp)/'jobs.db')}), patch.object(m.JobRunner,'loop',idle):
            with TestClient(m.app) as c:
                h={'Authorization':'Bearer test-token','Idempotency-Key':'20261007020100-123456'}
                p={'mode':'apple_video_plan','title':'Test','tracks':[{'title':'Song','artists':['Artist']}]}
                a=c.post('/v1/playlist/jobs',headers=h,json=p)
                self.assertEqual(a.status_code,202)
                jid=a.json()['job_id']
                b=c.post('/v1/playlist/jobs',headers=h,json=p)
                self.assertEqual(b.json()['job_id'],jid)
                p['title']='Changed'
                self.assertEqual(c.post('/v1/playlist/jobs',headers=h,json=p).status_code,409)
                self.assertEqual(c.get('/v1/playlist/jobs/'+jid).status_code,401)
                self.assertEqual(c.get('/v1/playlist/jobs/'+jid,headers=h).status_code,200)
    def test_missing_key_and_oversized_soundiiz(self):
        async def idle(self):
            import asyncio
            await asyncio.Event().wait()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'MUSIC_BRIDGE_DB':str(Path(tmp)/'jobs.db')}), patch.object(m.JobRunner,'loop',idle):
            with TestClient(m.app) as c:
                h={'Authorization':'Bearer test-token'}
                p={'mode':'apple_video_plan','title':'Test','tracks':[{'title':'Song','artists':['Artist']}]}
                self.assertEqual(c.post('/v1/playlist/jobs',headers=h,json=p).status_code,422)
                p.update(mode='spotify_handoff',tracks=p['tracks']*201)
                h['Idempotency-Key']='20261007020100-123456'
                self.assertEqual(c.post('/v1/playlist/jobs',headers=h,json=p).status_code,422)
