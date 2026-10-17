#!/usr/bin/env python3
"""Read-only YouTube candidate preview for a public Apple playlist.

No YouTube playlist mutations, no service changes, no credential printing.
Metadata proposals are NOT proof of official uploader identity or video content.
"""
import argparse
import asyncio
import html
import json
import hashlib
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import unicodedata
import httpx

API = 'https://www.googleapis.com/youtube/v3/'
SCOPE = 'https://www.googleapis.com/auth/youtube.force-ssl'
# Content review supplied by the user; NOT an official-channel certification.
# Generic review records allow a known real video without a title-type label.
REVIEWED_VIDEOS = {'jTfCvMEG8e4': {'title': 'Enough', 'artist': 'Emma Ruth Rundle',
    'channel_id': 'UCMVknn-rYSi3yMugP-OY0Fg', 'kind': 'music_video'}}


class PreviewError(Exception):
    pass


class DailyQuotaExceeded(PreviewError):
    """YouTube daily quota has been exhausted during a read-only request."""


class TemporaryRateLimit(PreviewError):
    """Retryable request-rate restriction on a read-only Google API operation."""
    def __init__(self, retry_after=None):
        super().__init__('Google temporarily rate-limited YouTube read requests.')
        self.retry_after = retry_after


def retry_after_seconds(value):
    if not value:
        return None
    try:
        return max(0, min(3600, int(value)))
    except (ValueError, TypeError):
        try:
            dt = parsedate_to_datetime(value)
            return max(0, min(3600, int((dt - datetime.now(timezone.utc)).total_seconds())))
        except (ValueError, TypeError, OverflowError):
            return None


async def read_json_with_backoff(client, endpoint, *, headers, params, sleep=asyncio.sleep):
    """Bounded retries for GET operations only; never use for YouTube writes."""
    for attempt in range(3):
        try:
            return await response_json(await client.get(endpoint, headers=headers, params=params))
        except TemporaryRateLimit as exc:
            if attempt == 2:
                raise
            delay = max(2 * (2 ** attempt), exc.retry_after or 0)
            await sleep(delay)


def plain(value):
    value = unicodedata.normalize('NFKD', html.unescape(value).casefold())
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+', value, re.UNICODE))


LABEL = re.compile(r'\s*[\[(](?:official\s+)?(?:music\s+video|video|lyric\s+video|lyrics?|audio)(?:\s+(?:hd|4k))?[\])]\s*|\s*[\[(](?:hd|4k|hq)[\])]\s*', re.I)
TRAILER = re.compile(r'\s*[-–—:]?\s*(?:official\s+)?(?:music\s+video|lyric\s+video|lyrics?|official\s+video|official\s+audio)\s*$', re.I)


def assess(track, video, country):
    snippet = video.get('snippet', {})
    title = html.unescape(snippet.get('title', ''))
    channel = html.unescape(snippet.get('channelTitle', ''))
    descriptor = plain(title)
    source_title = plain(track['title'])
    artists = [plain(a) for a in track['artists']]
    cleaned = plain(TRAILER.sub('', LABEL.sub(' ', title)))
    exact = any(cleaned in (source_title, a + ' ' + source_title, source_title + ' ' + a) for a in artists)
    artist_evidence = any(cleaned in (a + ' ' + source_title, source_title + ' ' + a)
                          or plain(channel) in (a, a + 'vevo', a + ' vevo') for a in artists)
    restrictions = video.get('contentDetails', {}).get('regionRestriction', {})
    blocked = country in restrictions.get('blocked', []) or (
        'allowed' in restrictions and country not in restrictions['allowed'])
    audio = bool(re.search(r'\b(?:official audio|audio only|visualizer|visualiser)\b', descriptor)) or snippet.get('description', '').startswith('Provided to YouTube by ')
    lyric = bool(re.search(r'\b(?:lyric video|lyrics?)\b', descriptor))
    music_video = bool(re.search(r'\b(?:official (?:music )?video|music video)\b', descriptor))
    kind = 'lyric_video' if lyric else 'music_video' if music_video else 'unlabeled_needs_review'
    classification_evidence = 'title_label' if kind != 'unlabeled_needs_review' else 'none'
    reviewed = REVIEWED_VIDEOS.get(video.get('id'))
    if (reviewed and plain(reviewed['title']) == source_title
            and plain(reviewed['artist']) in artists
            and reviewed['channel_id'] == snippet.get('channelId')):
        kind, classification_evidence = reviewed['kind'], 'user_content_review'
    elif kind == 'unlabeled_needs_review':
        # A general heuristic for artist uploads with filmmaking credits.
        # Plain artist uploads without this evidence remain unclassified.
        artist_channel = any(plain(channel) in (a, a + 'vevo', a + ' vevo') for a in artists)
        credits = re.search(r'\b(?:directed by|director\s*:|cinematography\s*:|filmed by|music video directed)', snippet.get('description', ''), re.I)
        if artist_channel and credits:
            kind, classification_evidence = 'music_video', 'artist_channel_and_production_credits'
    added_alternate = any(re.search(r'\b' + word + r'\b', descriptor) and not re.search(r'\b' + word + r'\b', source_title)
                          for word in ('live', 'cover', 'remix', 'acoustic', 'instrumental', 'karaoke', 'reaction'))
    reason = None
    if not exact or not artist_evidence:
        reason = 'title_or_artist_not_exact'
    elif added_alternate:
        reason = 'alternate_performance'
    elif audio:
        reason = 'audio_or_visualizer'
    elif blocked:
        reason = 'region_blocked'
    elif video.get('status', {}).get('privacyStatus') != 'public' or video.get('status', {}).get('uploadStatus') != 'processed':
        reason = 'not_public_processed_video'
    elif snippet.get('liveBroadcastContent') in ('live', 'upcoming'):
        reason = 'live_or_upcoming'
    video_id = video.get('id', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        reason = 'invalid_video_id'
    # YouTube's public video snippet does not authenticate the uploader as the
    # artist or label. Treat name matches and title labels as hints, not proof.
    channel_looks_related = any(
        plain(channel) in (a, a + 'vevo', a + ' vevo')
        or plain(channel).replace(' ', '') == a.replace(' ', '') + 'vevo'
        for a in artists)
    official_status = 'unverified'
    publisher_evidence = 'channel_name_match_unverified' if channel_looks_related else 'none'
    review_priority = ('other_results' if reason is not None or audio
                       else 'possible_video' if kind in ('music_video', 'lyric_video')
                       else 'unclassified')
    return {'video_id': video_id, 'title': title, 'channel': channel,
            'channel_id': snippet.get('channelId', ''),
            'url': 'https://www.youtube.com/watch?v=' + video_id,
            'kind': kind, 'duration': video.get('contentDetails', {}).get('duration', ''),
            'metadata_eligible': reason is None, 'rejection_reason': reason,
            'uploader_verified': False, 'official_status': official_status,
            'publisher_evidence': publisher_evidence, 'review_priority': review_priority,
            'classification_evidence': classification_evidence,
            'age_restricted': video.get('contentDetails', {}).get('contentRating', {}).get('ytRating') == 'ytAgeRestricted'}


async def response_json(response):
    if response.status_code >= 400:
        if response.status_code == 401:
            raise PreviewError('YouTube authorization expired or was revoked. Reauthorization is required.')
        reason = ''
        try:
            error = response.json().get('error', {})
            if isinstance(error, dict):
                reason = error.get('errors', [{}])[0].get('reason', '')
            elif isinstance(error, str):
                reason = error
        except (ValueError, TypeError, IndexError, AttributeError):
            pass
        if response.status_code == 403 and reason in ('quotaExceeded', 'dailyLimitExceeded'):
            raise DailyQuotaExceeded('YouTube daily API quota exceeded.')
        if response.status_code == 429 or reason in ('rateLimitExceeded', 'userRateLimitExceeded'):
            raise TemporaryRateLimit(retry_after_seconds(response.headers.get('Retry-After')))
        safe_reason = reason if re.fullmatch(r'[A-Za-z0-9_]+', reason) else 'request_failed'
        raise PreviewError(f'Google HTTP {response.status_code}: {safe_reason}.')
    try:
        data = response.json()
    except ValueError as exc:
        raise PreviewError('Google returned invalid JSON.') from exc
    if not isinstance(data, dict):
        raise PreviewError('Google returned an unexpected response.')
    return data


async def preview(playlist_url, token_path, limit, resolved=None, progress=None, checkpoint_path=None, review_only=False):
    from .source_playlists import resolve_playlist
    token_path = Path(token_path)
    if token_path.stat().st_mode & 0o077:
        raise PreviewError('Token file must have permissions 600.')
    try:
        credentials = json.loads(token_path.read_text())
        if credentials.get('schema_version') != 1 or credentials.get('scope') != SCOPE:
            raise ValueError()
        if any(not isinstance(credentials.get(k), str) or not credentials[k]
               for k in ('client_id', 'client_secret', 'refresh_token', 'channel_id')):
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise PreviewError('Token file is not in the expected setup-helper format.') from exc
    if resolved is None:
        resolved = await resolve_playlist(playlist_url)
    tracks = resolved['request']['tracks']
    keys = {(t['title'], tuple(t['artists'])) for t in tracks}
    if len(keys) > limit:
        raise PreviewError(f'This playlist contains {len(keys)} unique songs, above the preview limit of {limit}. Use Test; no YouTube searches were sent.')
    if len(tracks) > 20 and limit <= 20:
        raise PreviewError('This preview supports at most 20 source entries. Enable the review workflow for larger playlists.')
    # Checkpoints are only used before any remote playlist writes occur.
    checkpoint_path = Path(checkpoint_path) if checkpoint_path is not None else None
    fingerprint = hashlib.sha256(json.dumps(
        {'title': resolved['title'], 'country': resolved['country'], 'tracks': tracks},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    saved_rows = []
    if checkpoint_path is not None and checkpoint_path.exists():
        try:
            checkpoint = json.loads(checkpoint_path.read_text())
            saved_rows = checkpoint['rows']
            if (checkpoint.get('schema') != 1 or checkpoint.get('fingerprint') != fingerprint
                    or not isinstance(saved_rows, list) or len(saved_rows) > len(tracks)):
                raise ValueError('Mismatched search checkpoint')
            for index, row in enumerate(saved_rows):
                if not isinstance(row, dict) or row.get('title') != tracks[index]['title'] or row.get('artists') != tracks[index]['artists']:
                    raise ValueError('Invalid checkpoint row')
        except (ValueError, KeyError, TypeError) as exc:
            raise PreviewError('Search checkpoint is invalid. Review it before retrying.') from exc
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        result = await response_json(await client.post('https://oauth2.googleapis.com/token', data={
            'client_id': credentials['client_id'], 'client_secret': credentials['client_secret'],
            'refresh_token': credentials['refresh_token'], 'grant_type': 'refresh_token'}))
        if not isinstance(result.get('access_token'), str) or not result['access_token'] or result.get('token_type', '').lower() != 'bearer':
            raise PreviewError('Google returned an invalid access token.')
        headers = {'Authorization': 'Bearer ' + result['access_token']}
        channels = await read_json_with_backoff(client, API + 'channels', headers=headers, params={'part': 'snippet', 'mine': 'true'})
        identities = channels.get('items', [])
        if len(identities) != 1 or identities[0].get('id') != credentials['channel_id']:
            raise PreviewError('Authorized channel differs from the saved channel. Nothing was changed.')
        memo, rows = {}, []
        search_calls = 0
        for index, track in enumerate(tracks):
            key = (track['title'], tuple(track['artists']))
            if index < len(saved_rows):
                row = saved_rows[index]
                rows.append(row)
                memo[key] = row
                if progress:
                    progress(len(rows))
                continue
            if key not in memo:
                searches = await read_json_with_backoff(client, API + 'search', headers=headers, params={
                    'part': 'snippet', 'type': 'video', 'q': track['artists'][0] + ' ' + track['title'],
                    'regionCode': resolved['country'], 'maxResults': 20, 'order': 'relevance', 'safeSearch': 'none'})
                search_calls += 1
                ids = [item.get('id', {}).get('videoId') for item in searches.get('items', [])]
                ids = list(dict.fromkeys(i for i in ids if isinstance(i, str) and re.fullmatch(r'[A-Za-z0-9_-]{11}', i)))
                videos = []
                if ids:
                    detail = await read_json_with_backoff(client, API + 'videos', headers=headers, params={
                        'part': 'snippet,contentDetails,status', 'id': ','.join(ids)})
                    videos = detail.get('items', [])
                candidates = [assess(track, v, resolved['country']) for v in videos]
                rank = {'music_video': 0, 'lyric_video': 1, 'unlabeled_needs_review': 2}
                candidates.sort(key=lambda c: (not c['metadata_eligible'], rank[c['kind']], ids.index(c['video_id']) if c['video_id'] in ids else 999))
                eligible = [c for c in candidates if c['metadata_eligible'] and c['kind'] != 'unlabeled_needs_review']
                # Review-mode never recommends an unverified upload as official.
                # Keep legacy automatic proposals unchanged until their own
                # verified-publisher policy and creation path are migrated.
                memo[key] = {'title': track['title'], 'artists': track['artists'],
                             'proposal': eligible[0] if eligible and not review_only else None, 'candidates': candidates,
                             'search_may_have_more_results': bool(searches.get('nextPageToken'))}
            rows.append(memo[key])
            if checkpoint_path is not None:
                # Durable, atomic save after each completed track. No secrets are stored.
                from .youtube_creation import save_state
                save_state(checkpoint_path, {'schema': 1, 'fingerprint': fingerprint, 'rows': rows})
            if progress:
                progress(len(rows))
    return {'playlist_title': resolved['title'], 'source_track_count': len(tracks),
            'search_calls': search_calls, 'channel_title': identities[0].get('snippet', {}).get('title', ''),
            'playlist_created': False, 'review_required': True, 'tracks': rows,
            'notes': ['Selections use metadata heuristics and supplied content reviews. Official uploader identities are not verified.',
                      'One page of search results per unique song; no match is not proof that a video does not exist.',
                      'Review-mode candidate labels never establish official publication; inspect videos before approving.']}
