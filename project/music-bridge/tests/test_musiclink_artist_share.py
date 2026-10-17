import pytest
from music_bridge.core import classify
from music_bridge.catalog import CatalogError
from music_bridge.musiclink_share import normalize_musiclink, resolve_share

URL = 'https://music.apple.com/us/artist/paramore/75950796'
SPOTIFY = 'https://open.spotify.com/artist/74XFHRwlV6OrjEM0A2NCMF'
TIDAL = 'https://listen.tidal.com/artist/19981'
YOUTUBE = 'https://music.youtube.com/channel/UCc7_woMAIVIW2mAr1rPCsFQ'

def record(**override):
    return [{'type':'artist', 'title':None, 'artist':None,
             'platforms': {'apple_music':'https://music.apple.com/artist/75950796',
                           'spotify':SPOTIFY, 'tidal':TIDAL, 'youtube_music':YOUTUBE, **override}}]

def test_artist_live_shape():
    result = normalize_musiclink(classify(URL), record(), artist_name='Paramore')
    assert result['type'] == 'artist'
    assert result['title'] == 'Paramore' and result['artist'] == ''
    assert result['share_text'].startswith('Paramore\n\nApple Music\n'+URL)
    assert [r['service'] for r in result['links']] == ['Apple Music','Spotify','TIDAL','YouTube Music']
    assert not result['missing_services']

def test_artist_wrong_id_rejected():
    with pytest.raises(CatalogError, match='different Apple Music item'):
        normalize_musiclink(classify(URL), record(apple_music='https://music.apple.com/artist/12345'), artist_name='Paramore')

def test_artist_bad_domain_rejected():
    with pytest.raises(CatalogError, match='invalid source identity'):
        normalize_musiclink(classify(URL), record(apple_music='https://music.apple.com.evil.test/artist/75950796'), artist_name='Paramore')

def test_artist_requires_confirmed_name():
    with pytest.raises(CatalogError, match='confirmed display name'):
        normalize_musiclink(classify(URL), record())

def test_artist_missing_platform_and_bad_url():
    result = normalize_musiclink(classify(URL), record(spotify='',tidal='https://evil.test/artist/19981'), artist_name='Paramore')
    assert result['missing_services'] == ['Spotify', 'TIDAL']
    assert [r['service'] for r in result['links']] == ['Apple Music','YouTube Music']

@pytest.mark.asyncio
async def test_artist_one_musiclink_call_when_all_platforms_present(monkeypatch):
    from music_bridge import musiclink_share as share
    calls=[]
    async def fake_apple(client, url):
        calls.append('apple')
        return {'type':'artist', 'url':url, 'title':'Paramore', 'artist':'Paramore'}
    monkeypatch.setattr(share, 'apple_source', fake_apple)
    class Catalog:
        async def musiclink_resolve(self, client,url):
            calls.append('resolve'); return record()
        async def musiclink_spotify(self,client,source):
            raise AssertionError('unnecessary Spotify conversion')
    result = await resolve_share(None,Catalog(),classify(URL))
    assert result['title'] == 'Paramore' and calls == ['resolve','apple']

@pytest.mark.asyncio
async def test_artist_spotify_fallback_uses_existing_apple_source(monkeypatch):
    from music_bridge import musiclink_share as share
    calls=[]
    async def fake_apple(client, url):
        calls.append('apple')
        return {'type':'artist', 'url':url, 'title':'Paramore', 'artist':'Paramore'}
    monkeypatch.setattr(share, 'apple_source', fake_apple)
    class Catalog:
        async def musiclink_resolve(self, client,url): return record(spotify='')
        async def musiclink_spotify(self,client,source):
            calls.append('convert'); return {'url':SPOTIFY},'musiclink_source_url_conversion'
    result = await resolve_share(None,Catalog(),classify(URL))
    assert not result['missing_services'] and calls == ['apple','convert']

def test_artist_share_api_accepts_artist_and_capabilities(monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock
    api = importlib.import_module('music_bridge.app')
    monkeypatch.setattr(api, 'TOKEN', 'test-token')
    api.cache.clear()
    expected = normalize_musiclink(classify(URL), record(), artist_name='Paramore')
    mock = AsyncMock(return_value=expected)
    monkeypatch.setattr(api, 'resolve_share', mock)
    client = TestClient(api.app)
    auth = {'Authorization': 'Bearer test-token'}
    response = client.post('/v1/share', headers=auth, json={'url': URL})
    assert response.status_code == 200 and response.json()['type'] == 'artist'
    assert response.json()['title'] == 'Paramore'
    assert mock.await_count == 1
    assert 'artist' in client.get('/v1/capabilities',headers=auth).json()['sharing']
