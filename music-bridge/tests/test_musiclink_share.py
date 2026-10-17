import pytest
from music_bridge.core import classify
from music_bridge.musiclink_share import normalize_musiclink, resolve_share
from music_bridge.catalog import CatalogError

URL = 'https://music.apple.com/us/album/still-into-you/593146366?i=593148441'
ITEM = classify(URL)
VIDEO = 'https://music.youtube.com/watch?v=abcdefghijk'
TIDAL = 'https://tidal.com/browse/track/12345678'
SPOTIFY = 'https://open.spotify.com/track/ABCDEFGHIJKLMNOPQRSTUV'

def record(**extra):
    return [{'type': 'track', 'title': 'Still Into You', 'artist': 'Paramore',
             'platforms': {'apple_music': URL, 'tidal': TIDAL, 'youtube_music': VIDEO, **extra}}]

def test_links_and_format():
    r = normalize_musiclink(ITEM, record(), SPOTIFY)
    assert [l['service'] for l in r['links']] == ['Apple Music', 'Spotify', 'TIDAL', 'YouTube Music']
    assert r['missing_services'] == []
    assert r['share_text'].startswith('Still Into You — Paramore\n\nApple Music\n')

def test_missing_spotify_still_returns_other_links():
    r = normalize_musiclink(ITEM, record())
    assert r['missing_services'] == ['Spotify']
    assert 'No confirmed provider link for: Spotify' in r['warnings']

def test_malicious_domains_rejected():
    r = normalize_musiclink(ITEM, record(tidal='https://tidal.com.evil.test/track/1', youtube_music='https://evil.test/watch?v=abcdefghijk'))
    assert r['missing_services'] == ['Spotify', 'TIDAL', 'YouTube Music']

def test_wrong_source_refused():
    with pytest.raises(CatalogError):
        normalize_musiclink(ITEM, record(apple_music='https://music.apple.com/us/album/other/777?i=888'))

def test_wrong_type_refused():
    r = record(); r[0]['type'] = 'album'
    with pytest.raises(CatalogError):
        normalize_musiclink(ITEM, r)

@pytest.mark.asyncio
async def test_fallback_only_when_spotify_missing(monkeypatch):
    class Catalog:
        calls = 0
        async def musiclink_resolve(self, client, url):
            return record(spotify=SPOTIFY)
        async def musiclink_spotify(self, client, source):
            self.calls += 1
    cat = Catalog()
    r = await resolve_share(None, cat, ITEM)
    assert cat.calls == 0 and 'Spotify' not in r['missing_services']

@pytest.mark.asyncio
async def test_spotify_fallback_on_missing_platform(monkeypatch):
    from music_bridge import musiclink_share as share
    async def fake_apple_source(client, url):
        return {'type': 'song', 'url': url, 'title': 'Still Into You', 'artist': 'Paramore', 'duration': 207}
    monkeypatch.setattr(share, 'apple_source', fake_apple_source)
    class Catalog:
        calls = 0
        async def musiclink_resolve(self, client, url):
            return record()
        async def musiclink_spotify(self, client, source):
            self.calls += 1
            return {'url': SPOTIFY}, 'musiclink_source_url_conversion'
    cat = Catalog()
    result = await resolve_share(None, cat, ITEM)
    assert cat.calls == 1
    assert 'Spotify' not in result['missing_services']

@pytest.mark.asyncio
async def test_fallback_error_does_not_invent_spotify(monkeypatch):
    from music_bridge import musiclink_share as share
    async def fake_apple_source(client, url):
        raise RuntimeError('metadata lookup offline')
    monkeypatch.setattr(share, 'apple_source', fake_apple_source)
    class Catalog:
        async def musiclink_resolve(self, client, url):
            return record()
    result = await resolve_share(None, Catalog(), ITEM)
    assert 'Spotify' in result['missing_services']
    assert len(result['links']) == 3


def test_musiclink_countryless_song_identity():
    r = normalize_musiclink(ITEM, record(apple_music='https://music.apple.com/song/593148441'), SPOTIFY)
    assert r['links'][0]['url'] == URL
    assert [link['service'] for link in r['links']] == ['Apple Music', 'Spotify', 'TIDAL', 'YouTube Music']

def test_musiclink_countryless_album_identity():
    album_url = 'https://music.apple.com/us/album/still-into-you/593146366'
    album_item = classify(album_url)
    album_record = [{'type': 'album', 'title': 'Still Into You', 'artist': 'Paramore',
                     'platforms': {'apple_music': 'https://music.apple.com/album/593146366'}}]
    result = normalize_musiclink(album_item, album_record)
    assert result['links'][0]['url'] == album_url
    assert result['missing_services'] == ['Spotify', 'TIDAL', 'YouTube Music']

@pytest.mark.parametrize('invalid', [
    'https://music.apple.com/song/999999999',
    'http://music.apple.com/song/593148441',
    'https://music.apple.com.evil.test/song/593148441',
    'https://music.apple.com@evil.test/song/593148441',
    'https://music.apple.com/song/593148441?i=999',
    'https://music.apple.com/song/593148441/extra',
    'https://music.apple.com/album/593148441',
])
def test_invalid_or_wrong_countryless_source_rejected(invalid):
    with pytest.raises(CatalogError):
        normalize_musiclink(ITEM, record(apple_music=invalid))
