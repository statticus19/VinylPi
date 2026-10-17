"""Offline diagnostics tests; no YouTube API calls or playlist writes."""
import asyncio
import httpx
import pytest
from music_bridge.youtube_creation import api_write
from music_bridge.youtube_matching import PreviewError

@pytest.mark.parametrize('status,body,want', [
    (403, {'error': {'errors': [{'reason':'playlistItemsNotAccessible'}]}}, 'playlistItemsNotAccessible'),
    (429, {'error': {'errors': [{'reason':'rateLimitExceeded'}]}}, 'rateLimitExceeded'),
    (403, {'error': {'errors': [{'reason':'INVALID credentials: secret'}]}}, 'request_failed'),
])
def test_safe_write_errors(status,body,want):
    def handler(request):
        return httpx.Response(status, json=body)
    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PreviewError) as failure:
                await api_write(client, 'playlistItems', {}, 'snippet', {'snippet':{}})
        assert f'HTTP {status}: {want}' in str(failure.value)
        assert 'secret' not in str(failure.value)
        assert 'verify playlist' in str(failure.value)
    asyncio.run(check())
