import asyncio
import json
import httpx
import pytest
from music_bridge.youtube_creation import api_write, DefinitiveVideoRejection
from music_bridge.youtube_matching import PreviewError

def response(status, reason):
    def handler(request):
        return httpx.Response(status, json={'error': {'errors':[{'reason': reason}]}})
    return httpx.MockTransport(handler)

@pytest.mark.parametrize('reason', ['videoNotFound','videoNotAllowed'])
def test_narrow_definitive_errors(reason):
    async def go():
        async with httpx.AsyncClient(transport=response(400,reason)) as client:
            with pytest.raises(DefinitiveVideoRejection):
                await api_write(client, 'playlistItems', {}, 'snippet', {})
    asyncio.run(go())

@pytest.mark.parametrize('status,reason',[(429,'rateLimitExceeded'),(403,'forbidden'),(500,'videoNotFound'),(400,'quotaExceeded')])
def test_uncertain_writes_never_skip(status, reason):
    async def go():
        async with httpx.AsyncClient(transport=response(status,reason)) as client:
            with pytest.raises(PreviewError) as caught:
                await api_write(client, 'playlistItems', {}, 'snippet', {})
            assert not isinstance(caught.value, DefinitiveVideoRejection)
    asyncio.run(go())

def test_non_insert_writes_never_skip():
    async def go():
        async with httpx.AsyncClient(transport=response(400,'videoNotFound')) as client:
            with pytest.raises(PreviewError) as caught:
                await api_write(client, 'playlists', {}, 'snippet', {})
            assert not isinstance(caught.value, DefinitiveVideoRejection)
    asyncio.run(go())

def test_partial_creation_skips_definite_rejection_and_verifies(monkeypatch, tmp_path):
    from music_bridge import youtube_creation as creation
    original = httpx.AsyncClient
    posted=[]
    def handler(request):
        url=str(request.url)
        if 'oauth2.googleapis.com/token' in url:
            return httpx.Response(200,json={'access_token':'fake'})
        if '/channels' in url:
            return httpx.Response(200,json={'items':[{'id':'channel123'}]})
        if request.method == 'POST' and '/playlists?' in url:
            return httpx.Response(200,json={'id':'PL_test123','snippet':{'channelId':'channel123'}})
        if request.method == 'POST' and '/playlistItems?' in url:
            body=json.loads(request.content)
            vid=body['snippet']['resourceId']['videoId']; posted.append((vid,body['snippet']['position']))
            if vid=='AAAAAAAAAAA':
                return httpx.Response(400,json={'error':{'errors':[{'reason':'videoNotFound'}]}})
            return httpx.Response(200,json={'id':'item'+vid})
        if request.method == 'GET' and '/playlists?' in url:
            return httpx.Response(200,json={'items':[{'snippet':{'channelId':'channel123'}}]})
        if request.method == 'GET' and '/playlistItems?' in url:
            return httpx.Response(200,json={'items':[{'id':'x'+v,'snippet':{'position':i,'resourceId':{'videoId':v}}} for i,(v,_) in enumerate([(v,p) for v,p in posted if v!='AAAAAAAAAAA'])]})
        raise AssertionError(str(request))
    transport=httpx.MockTransport(handler)
    monkeypatch.setattr(creation.httpx,'AsyncClient',lambda **kw: original(transport=transport,**kw))
    token=tmp_path/'token.json'; token.write_text(json.dumps({'channel_id':'channel123','client_id':'test','client_secret':'test','refresh_token':'test'}));token.chmod(0o600)
    report={'playlist_title':'Test','tracks':[{'title':'one','artists':['x'],'proposal':{'video_id':'AAAAAAAAAAA','metadata_eligible':True}}, {'title':'two','artists':['x'],'proposal':{'video_id':'BBBBBBBBBBB','metadata_eligible':True}}, {'title':'three','artists':['x'],'proposal':{'video_id':'CCCCCCCCCCC','metadata_eligible':True}}]}
    result=asyncio.run(creation.create_playlist(report,token,tmp_path/'job.json','source'))
    assert result['status']=='partial_verified'
    assert result['video_ids']==['BBBBBBBBBBB','CCCCCCCCCCC']
    assert len(result['rejected'])==1 and result['rejected'][0]['source_index']==0
    assert posted==[('AAAAAAAAAAA',0),('BBBBBBBBBBB',0),('CCCCCCCCCCC',1)]
