import unittest
from unittest.mock import patch
from music_bridge import youtube_creation as creation
from music_bridge import youtube_matching as yt


class FakeResponse:
    status_code = 200
    def __init__(self, data):
        self.data = data
    def json(self):
        return self.data


class FakeClient:
    def __init__(self, broken_attempts):
        self.broken_attempts = broken_attempts
        self.first_pages = 0
        self.paths = []
    async def get(self, url, headers=None, params=None):
        if url.endswith('/playlists'):
            return FakeResponse({'items': [{'snippet': {'channelId': 'channel'}}]})
        token = params.get('pageToken')
        self.paths.append(token)
        if token is None:
            self.first_pages += 1
            return FakeResponse({'items': [{'id': 'item0', 'snippet': {'position': 0, 'resourceId': {'videoId': 'a'}}}], 'nextPageToken': 'PAGE2'})
        if self.first_pages <= self.broken_attempts:
            return FakeResponse({'items': [{'id': 'item1', 'snippet': {'position': 1, 'resourceId': {'videoId': 'b'}}}], 'nextPageToken': 'PAGE2'})
        return FakeResponse({'items': [{'id': 'item1', 'snippet': {'position': 1, 'resourceId': {'videoId': 'b'}}}]})


class PaginationRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_token_then_clean_traversal_completes(self):
        client = FakeClient(1)
        state = {'playlist_id': 'playlist', 'channel_id': 'channel', 'video_ids': ['a', 'b'], 'pending': None, 'status': 'creating'}
        with patch.object(creation, 'save_state'), patch.object(creation.asyncio, 'sleep'):
            done = await creation.verify_remote(client, {}, state, '/unused', attempts=3)
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(done['verification']['attempts'], 2)
        self.assertEqual(client.first_pages, 2)
        self.assertEqual(client.paths, [None, 'PAGE2', None, 'PAGE2'])

    async def test_persistent_repeated_token_does_not_complete(self):
        client = FakeClient(99)
        state = {'playlist_id': 'playlist', 'channel_id': 'channel', 'video_ids': ['a', 'b'], 'pending': None, 'status': 'creating'}
        with patch.object(creation, 'save_state') as save, patch.object(creation.asyncio, 'sleep'):
            with self.assertRaisesRegex(yt.PreviewError, 'after 3 read-only attempts'):
                await creation.verify_remote(client, {}, state, '/unused', attempts=3)
        self.assertEqual(state['status'], 'creating')
        self.assertNotIn('verification', state)
        save.assert_not_called()
        self.assertEqual(client.first_pages, 3)

    async def test_uncertain_pending_write_still_blocks_completion(self):
        client = FakeClient(0)
        state = {'playlist_id': 'playlist', 'channel_id': 'channel', 'video_ids': ['a', 'b'], 'pending': {'insert_index': 1, 'video_id': 'b'}, 'status': 'creating'}
        with patch.object(creation, 'save_state'):
            with self.assertRaisesRegex(yt.PreviewError, 'uncertain write'):
                await creation.verify_remote(client, {}, state, '/unused', attempts=3)
        self.assertNotEqual(state['status'], 'completed')
