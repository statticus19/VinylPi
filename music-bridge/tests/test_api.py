import importlib
import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from music_bridge.catalog import CatalogError
os.environ['MUSIC_BRIDGE_TOKEN'] = 'test-token'
m = importlib.import_module('music_bridge.app')
client = TestClient(m.app)
headers = {'Authorization': 'Bearer test-token'}
url = 'https://music.apple.com/us/album/title/123?i=456'

class ApiTests(unittest.TestCase):
    def setUp(self):
        m.cache.clear()
    def test_authentication(self):
        self.assertEqual(client.post('/v1/share', json={'url': url}).status_code, 401)
    def test_validation(self):
        self.assertEqual(client.post('/v1/share', headers=headers, json={'url': 'https://evil.com/a'}).status_code, 422)
    def test_unimplemented_playlist(self):
        self.assertEqual(client.post('/v1/share', headers=headers, json={'url': 'https://music.apple.com/us/playlist/a/pl.abc'}).status_code, 501)
    def test_success_and_cache(self):
        output = {'api_version': 1, 'type': 'song', 'title': 'Example', 'artist': 'Artist',
                  'share_text': 'Example\n\nSpotify\nhttps://open.spotify.com/track/ABCDEFGHIJKLMNOPQRSTUV',
                  'links': [], 'missing_services': [], 'warnings': []}
        with patch.object(m, 'resolve_share', return_value=output) as mock:
            for _ in range(2):
                r = client.post('/v1/share', headers=headers, json={'url': url})
                self.assertEqual(r.status_code, 200)
                self.assertIn('Spotify', r.json()['share_text'])
            self.assertEqual(mock.call_count, 1)
    def test_provider_failure(self):
        with patch.object(m, 'resolve_share', side_effect=CatalogError('provider failed')):
            r = client.post('/v1/share', headers=headers, json={'url': url})
            self.assertEqual(r.status_code, 502)
            self.assertNotIn('provider failed', r.text)
if __name__ == '__main__': unittest.main()
