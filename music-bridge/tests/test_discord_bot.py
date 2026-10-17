import asyncio
import os
from unittest.mock import patch
import httpx
from music_bridge.discord_notify import notify, NotificationError


def test_dm_only_two_posts_and_no_mentions():
    calls=[]
    def handler(request):
        calls.append(request)
        if request.url.path == '/api/v10/users/@me/channels':
            return httpx.Response(200, json={'id':'123456789012345678'})
        if request.url.path == '/api/v10/channels/123456789012345678/messages':
            assert request.read().decode().find('"parse":[]') >= 0
            return httpx.Response(200, json={'id':'456'})
        return httpx.Response(404)
    with patch.dict(os.environ, {'MUSIC_BRIDGE_DISCORD_BOT_TOKEN':'fake-test-token', 'MUSIC_BRIDGE_DISCORD_USER_ID':'123456789012345679'}):
        assert asyncio.run(notify('test','Title','message',transport=httpx.MockTransport(handler))) is True
    assert len(calls)==2
    assert all(c.headers['Authorization']=='Bot fake-test-token' for c in calls)


def test_unconfigured_does_nothing():
    with patch.dict(os.environ, {}, clear=True):
        assert asyncio.run(notify('test','Title','message')) is False


def test_403_is_failure_not_success():
    with patch.dict(os.environ, {'MUSIC_BRIDGE_DISCORD_BOT_TOKEN':'fake-test-token', 'MUSIC_BRIDGE_DISCORD_USER_ID':'123456789012345679'}):
        try:
            asyncio.run(notify('test','Title','message',transport=httpx.MockTransport(lambda request:httpx.Response(403))))
        except NotificationError as exc:
            assert '403' in exc.status
        else:
            raise AssertionError('403 must fail')
