"""Regression tests for legacy iOS shortcut resolving large YouTube playlists."""
import asyncio
import json

import pytest
from pydantic import ValidationError

from music_bridge.playlists import PlaylistJob
from music_bridge import app as api


def payload(size=44, mode='youtube_video_playlist', **extra):
    return {'title': 'Test 3', 'mode': mode,
            'tracks': [{'title': f'Song {i}', 'artists': ['Artist']} for i in range(size)],
            'country': 'US', **extra}


def test_video_playlist_defaults_to_review_even_above_20():
    job = PlaylistJob.model_validate(payload())
    assert len(job.tracks) == 44
    assert job.review_before_create is True
    assert len(job.model_dump()['tracks']) == 44


def test_explicit_false_from_legacy_client_still_requires_review():
    job = PlaylistJob.model_validate(payload(review_before_create=False))
    assert job.review_before_create is True


def test_small_video_playlist_also_requires_review():
    job = PlaylistJob.model_validate(payload(size=3))
    assert job.review_before_create is True


def test_non_video_modes_unchanged():
    assert PlaylistJob.model_validate(payload(size=3, mode='apple_video_plan')).review_before_create is False
    assert PlaylistJob.model_validate(payload(size=3, mode='spotify_handoff')).review_before_create is False
    with pytest.raises(ValidationError):
        PlaylistJob.model_validate(payload(size=201, mode='spotify_handoff'))
    with pytest.raises(ValidationError):
        PlaylistJob.model_validate(payload(size=21, mode='youtube_music_playlist'))


def test_resolve_route_returns_review_json_for_44_tracks(monkeypatch):
    async def fake_resolve(url):
        return {'title': 'Test 3', 'request': payload(mode='apple_video_plan'),
                'request_json': '{}', 'track_count': 44}

    monkeypatch.setattr(api, 'resolve_playlist', fake_resolve)
    result = asyncio.run(api.resolve_source_playlist(
        api.PlaylistResolveRequest(url='https://music.apple.com/us/playlist/test-3/pl.u-DdANGAqTaX19vr',
                                   mode='youtube_video_playlist')))
    data = json.loads(result['request_json'])
    assert data['review_before_create'] is True
    assert data['mode'] == 'youtube_video_playlist'
    assert len(data['tracks']) == 44
    assert result['request'] == data
