import unittest
from music_bridge.core import classify, normalize

class CoreTests(unittest.TestCase):
    def test_song_in_album_preserves_id(self):
        x = classify('https://music.apple.com/us/album/title/123?i=456&ls=1')
        self.assertEqual((x['type'], x['id']), ('song', '456'))
        self.assertEqual(x['url'], 'https://music.apple.com/us/album/title/123?i=456')
    def test_reject_arbitrary_hosts(self):
        for url in ['https://music.apple.com.evil.com/us/album/a/123', 'http://music.apple.com/us/album/a/123', 'https://u@music.apple.com/us/album/a/123', 'https://music.apple.com:444/us/album/a/123']:
            with self.assertRaises(ValueError): classify(url)
    def test_missing_and_untrusted_links(self):
        x = normalize(classify('https://music.apple.com/us/album/a/123'),
            {'linksByPlatform': {'spotify': {'url': 'https://evil.com/track/a'}}})
        self.assertEqual(len(x['links']), 1)
        self.assertIn('Spotify', x['missing_services'])
    def test_provider_metadata(self):
        x = normalize(classify('https://music.apple.com/us/song/a/123'),
            {'entityUniqueId': 'x', 'entitiesByUniqueId': {'x': {'title':'Song','artistName':'Artist'}},
             'linksByPlatform': {'spotify': {'url':'https://open.spotify.com/track/abc'}}})
        self.assertIn('Song — Artist', x['share_text'])
        self.assertEqual(x['links'][1]['match'], 'provider')

if __name__ == '__main__': unittest.main()
