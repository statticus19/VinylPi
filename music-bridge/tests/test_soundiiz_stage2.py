import asyncio
import time
import pytest
from music_bridge.store import JobStore, KeyConflict
from music_bridge.soundiiz import create_handoff, SoundiizResponseError
from music_bridge.playlists import PlaylistJob


def make_payload(n=450):
    return PlaylistJob(mode='soundiiz_transfer', title='Big Playlist', tracks=[
        {'title': f'Track {i}', 'artists':['Artist']} for i in range(n)
    ]).model_dump()


def test_part_state_persists_and_does_not_overwrite_other_parts(tmp_path):
    store=JobStore(tmp_path/'db.sqlite')
    job,_=store.create(make_payload(), 'phase2_persistent_part_test')
    jid=job['job_id']
    batch,_=store.claim_soundiiz_part(jid,2)
    assert batch['title']=='Big Playlist — Part 2 of 3'
    assert batch['tracks'][0]['title']=='Track 200'
    with pytest.raises(KeyConflict): store.claim_soundiiz_part(jid,2)
    store.finish_soundiiz_part(jid,2, {'handoff_url':'https://soundiiz.com/go/import-playlist/test2', 'expires_at': time.time()+8000, 'accepted_tracks':200})
    batch,exists=store.claim_soundiiz_part(jid,2)
    assert batch is None and exists['result']['parts'][1]['status']=='ready'
    store.mark_soundiiz_transferred(jid,2)
    assert store.get(jid)['result']['parts'][1]['marked_transferred']
    batch,_=store.claim_soundiiz_part(jid,1)
    store.finish_soundiiz_part(jid,1)
    outcome=store.get(jid)
    assert [p['status'] for p in outcome['result']['parts']]==['uncertain','ready','not_generated']
    assert outcome['result']['parts'][1]['marked_transferred']
    with pytest.raises(KeyConflict): store.claim_soundiiz_part(jid,1)
    store.claim_soundiiz_part(jid,1,regenerate=True)
    store.recover()
    assert store.get(jid)['result']['parts'][0]['status']=='uncertain'
    assert store.get(jid)['result']['parts'][1]['status']=='ready'


class Response:
    status_code=200
    def __init__(self,data): self.data=data
    def raise_for_status(self): pass
    def json(self): return self.data


class Client:
    def __init__(self,data): self.data=data;self.posted=None
    async def post(self,url,json): self.posted=(url,json);return Response(self.data)


def test_handoff_omits_destination_and_preserves_duplicates():
    tracks=[{'title':'Same','artists':['Artist']},{'title':'Same','artists':['Artist']}]
    data={'status':'success','shareUrl':'https://soundiiz.com/go/import-playlist/token', 'nbTracks':2,'expiresAt':time.time()+8000}
    client=Client(data)
    result=asyncio.run(create_handoff(client,'Part 1',tracks))
    assert result['accepted_tracks']==2
    assert 'destination' not in client.posted[1]
    assert client.posted[1]['tracklist']==tracks


@pytest.mark.parametrize('override', [
    {'nbTracks':1}, {'shareUrl':'https://evil.example/go/import-playlist/token'},
    {'shareUrl':'https://soundiiz.com.evil.example/go/import-playlist/token'},
    {'shareUrl':'http://soundiiz.com/go/import-playlist/token'},
    {'shareUrl':'https://soundiiz.com/go/import-playlist/token?secret=yes'},
    {'expiresAt':0}, {'status':'error'},
])
def test_rejects_invalid_handoff(override):
    data={'status':'success','shareUrl':'https://soundiiz.com/go/import-playlist/token', 'nbTracks':2,'expiresAt':time.time()+8000}
    data.update(override)
    with pytest.raises(SoundiizResponseError):
        asyncio.run(create_handoff(Client(data),'Part',[{'title':'One','artists':['A']},{'title':'Two','artists':['A']}]))
