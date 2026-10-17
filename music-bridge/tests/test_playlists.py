import asyncio
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
import httpx
from pydantic import ValidationError
from music_bridge.playlists import Track, PlaylistJob, select_apple_video
from music_bridge.store import JobStore, KeyConflict, CapacityError
from music_bridge.jobs import JobRunner

track = {'title':'Black Honey', 'artists':['Thrice']}
def candidate(id=1, title='Black Honey', artist='Thrice', kind='music-video'):
    return {'trackId':id,'trackName':title,'artistName':artist,'kind':kind}
def payload(mode='apple_video_plan', tracks=None):
    return PlaylistJob(mode=mode,title='Test',tracks=tracks or [track]).model_dump()

class MatchingTests(unittest.TestCase):
    def test_lyric_fallback_and_standard_preference(self):
        song = Track(**track)
        for title in ['Black Honey (Lyric Video)', 'Black Honey (Official Lyric Video)',
                      'Black Honey (Lyrics)', 'Black Honey - Official Lyrics']:
            lyric = candidate(1, title)
            result, reason = select_apple_video(song, [lyric])
            self.assertIsNone(reason)
            self.assertEqual(result['match'], 'exact_normalized_metadata_lyric_fallback')
            for items in [[lyric, candidate(99)], [candidate(99), lyric]]:
                self.assertEqual(select_apple_video(song, items)[0]['store_id'], '99')
        self.assertEqual(select_apple_video(song, [candidate(2, 'Black Honey (Lyrics)'),
                         candidate(1, 'Black Honey (Lyric Video)')])[0]['store_id'], '1')
        for c in [candidate(1, 'Black Honey (Live) (Lyric Video)'),
                  candidate(1, 'Other Song (Lyric Video)'),
                  candidate(1, 'Black Honey (Lyric Video)', 'Other'),
                  candidate(1, 'Black Honey (Lyric Video)', kind='song')]:
            self.assertIsNone(select_apple_video(song, [c])[0])
    def test_shortcuts_artist_text(self):
        self.assertEqual(Track(title='Digital Sea', artists='Thrice').artists, ['Thrice'])
        with self.assertRaises(ValidationError): Track(title='Digital Sea', artists=[1])
    def test_digital_sea_multiple_exact_matches(self):
        song = Track(title='Digital Sea', artists=['Thrice'])
        pair = [candidate(1813957535, 'Digital Sea'), candidate(1713672995, 'Digital Sea')]
        for items in [pair, list(reversed(pair))]:
            result, reason = select_apple_video(song, items)
            self.assertIsNone(reason)
            self.assertEqual(result['store_id'], '1713672995')
            self.assertEqual(result['match'], 'exact_metadata_multiple_entries_selected_one')
        self.assertEqual(select_apple_video(song, pair + [candidate(999, 'Digital Sea')])[0]['store_id'], '999')
        other = Track(title='Digital Sea', artists=['Other'])
        self.assertEqual(select_apple_video(other, [candidate(1713672995, 'Digital Sea', 'Other'),
                         candidate(1813957535, 'Digital Sea', 'Other')])[0]['store_id'], '1713672995')
    def test_reject_audio_other_artist_and_alternates(self):
        for c in [candidate(kind='song'), candidate(artist='Other'),
                  candidate(title='Black Honey (Live)'), candidate(title='Black Honey (Official Audio)'),
                  candidate(title='Black Honey (Remix)')]:
            self.assertIsNone(select_apple_video(Track(**track),[c])[0])
    def test_unique_and_multiple_exact_matches(self):
        self.assertEqual(select_apple_video(Track(**track),[candidate(title='Black Honey (Official Video)')])[0]['store_id'],'1')
        for items in [[candidate(1),candidate(2)], [candidate(2),candidate(1)]]:
            self.assertEqual(select_apple_video(Track(**track),items)[0]['store_id'], '1')
    def test_legitimate_live_word(self):
        self.assertIsNotNone(select_apple_video(Track(title='Live and Let Die',artists=['Artist']),
            [candidate(title='Live and Let Die',artist='Artist')])[0])
    def test_soundiiz_limit_and_whitespace(self):
        with self.assertRaises(ValidationError): PlaylistJob(mode='spotify_handoff',title='Test',tracks=[track]*201)
        with self.assertRaises(ValidationError): Track(title=' ',artists=['Artist'])

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=JobStore(Path(self.temp.name)/'jobs.db')
    def tearDown(self): self.temp.cleanup()
    def test_idempotency_and_conflict(self):
        key=str(uuid.uuid4()); p=payload()
        a,created=self.store.create(p,key)
        b,created2=self.store.create(p,key)
        self.assertTrue(created);self.assertFalse(created2);self.assertEqual(a,b)
        with self.assertRaises(KeyConflict): self.store.create(payload(tracks=[{'title':'Other','artists':['Thrice']}]),key)
    def test_restart_preserves_queued_and_marks_interrupted(self):
        a,_=self.store.create(payload(),str(uuid.uuid4()))
        b,_=self.store.create(payload(),str(uuid.uuid4()))
        self.store.take();self.store.recover()
        self.assertEqual(self.store.get(a['job_id'])['status'],'interrupted')
        self.assertEqual(self.store.get(b['job_id'])['status'],'queued')
    def test_prune_preserves_mapping(self):
        a,_=self.store.create(payload(),str(uuid.uuid4()))
        self.store.update(a['job_id'],'completed')
        with self.store.connect() as db:
            db.execute('UPDATE jobs SET created=?',(time.time()-91*86400,))
            db.execute('INSERT INTO mappings VALUES(?,?,?,?,?,?,?,?)',('id','source','apple_music','account',None,'name','videos',0))
            self.store.maintain(db)
            self.assertEqual(db.execute('SELECT count(*) FROM mappings').fetchone()[0],1)
        self.assertIsNone(self.store.get(a['job_id']))
    def test_ack_creation_is_idempotent_and_records_mapping(self):
        a,_=self.store.create(payload(),str(uuid.uuid4()));jid=a['job_id']
        self.store.update(jid,'completed',1,{'destination':'apple_music','playlist_name':'Test + Music Videos',
            'matches':[{'store_id':'1'},{'store_id':'2'}],'playlist_created':False})
        result=self.store.record_apple_creation(jid,'my-account',['2'])['result']
        self.assertTrue(result['playlist_created']);self.assertEqual(result['ios_skipped_count'],1)
        self.store.record_apple_creation(jid,'my-account',['2'])
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM mappings').fetchone()[0],1)
        with self.assertRaises(KeyConflict):self.store.record_apple_creation(jid,'other-account',['2'])
    def test_ack_rejects_out_of_order_ids(self):
        a,_=self.store.create(payload(),str(uuid.uuid4()));jid=a['job_id']
        self.store.update(jid,'completed',1,{'destination':'apple_music','playlist_name':'Test',
            'matches':[{'store_id':'1'},{'store_id':'2'}]})
        with self.assertRaises(KeyConflict):self.store.record_apple_creation(jid,'my-account',['2','1'])
    def test_pending_cap(self):
        for _ in range(20): self.store.create(payload(),str(uuid.uuid4()))
        with self.assertRaises(CapacityError):self.store.create(payload(),str(uuid.uuid4()))

class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=JobStore(Path(self.temp.name)/'jobs.db')
        self.runner=JobRunner(self.store)
    async def asyncTearDown(self):self.temp.cleanup()
    async def test_order_duplicates_and_skips(self):
        p=payload(tracks=[track,{'title':'Missing','artists':['Thrice']},track])
        a,_=self.store.create(p,str(uuid.uuid4()))
        async def fake(client,t,country):return [candidate()] if t.title=='Black Honey' else []
        with patch.object(self.runner,'search',side_effect=fake) as mock, patch('music_bridge.jobs.httpx.AsyncClient', return_value=httpx.AsyncClient(trust_env=False)):
            await self.runner.run_one(a['job_id'],p)
            self.assertEqual(mock.call_count,2)
        result=self.store.get(a['job_id'])['result']
        self.assertEqual([x['source_index'] for x in result['matches']],[0,2])
        self.assertEqual(result['skipped_count'],1)
        self.assertFalse(result['playlist_created'])
    async def test_soundiiz_handoff_not_playlist_completion(self):
        p=payload('spotify_handoff');a,_=self.store.create(p,str(uuid.uuid4()))
        response={'status':'success','nbTracks':1,'shareUrl':'https://soundiiz.com/go/import-playlist/abc','expiresAt':time.time()+3600}
        class Fake:
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def post(self,url,json):
                self.request=json
                return httpx.Response(200,json=response,request=httpx.Request('POST',url))
        fake=Fake()
        with patch('music_bridge.jobs.httpx.AsyncClient',return_value=fake):
            await self.runner.run_one(a['job_id'],p)
        self.assertEqual(fake.request['destination'],'spotify')
        result=self.store.get(a['job_id'])['result']
        self.assertTrue(result['requires_browser_confirmation'])
        self.assertFalse(result['playlist_created'])
