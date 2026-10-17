import json
from pathlib import Path
from unittest.mock import patch
import pytest
from music_bridge.recovery_inspect import inspect

class FakeResponse:
    def __init__(self, data): self.data = data; self.status_code = 200; self.headers = {}
    def json(self): return self.data

class FakeClient:
    def __init__(self, *a, **kw): self.calls = []
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def post(self, url, **kw):
        assert url == 'https://oauth2.googleapis.com/token'
        return FakeResponse({'access_token':'not-printed'})
    async def get(self, url, **kw):
        if url.endswith('/channels'): return FakeResponse({'items':[{'id':'channel'}]})
        if url.endswith('/playlists'): return FakeResponse({'items':[{'snippet':{'channelId':'channel'}}]})
        assert url.endswith('/playlistItems')
        return FakeResponse({'items':[]})

@pytest.mark.asyncio
async def test_readonly_recovery_inspection_empty_playlist(tmp_path):
    token = tmp_path/'token.json'
    token.write_text(json.dumps({'client_id':'x','client_secret':'y','refresh_token':'z','channel_id':'channel'}))
    token.chmod(0o600)
    state = tmp_path/'checkpoint.json'
    state.write_text(json.dumps({'playlist_id':'PLtest','channel_id':'channel','status':'needs_review',
        'planned_video_ids':['ABCDEFGHIJK'], 'inserted':[],
        'pending':{'insert_index':0,'video_id':'ABCDEFGHIJK'}}))
    old = state.read_bytes()
    with patch('music_bridge.recovery_inspect.httpx.AsyncClient', FakeClient):
        result = await inspect(token, state)
    assert result['remote_count'] == 0
    assert result['remote_write_performed'] is False
    assert result['assessment'].startswith('PENDING_VIDEO_ABSENT')
    assert state.read_bytes() == old
