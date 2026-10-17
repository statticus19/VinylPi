"""Offline manifest for destination-independent Soundiiz handoffs.

Network handoffs are always explicit per-part actions, never automatic retries.
"""

BATCH_SIZE = 200


def build_manifest(payload):
    """Keep contiguous source positions (including duplicate songs) in one parent job."""
    tracks = payload['tracks']
    if not 1 <= len(tracks) <= 1000:
        raise ValueError('Expected a validated playlist of 1–1000 tracks.')
    count = (len(tracks) + BATCH_SIZE - 1) // BATCH_SIZE
    parts = []
    for i, start in enumerate(range(0, len(tracks), BATCH_SIZE), 1):
        end = min(start + BATCH_SIZE, len(tracks))
        label = payload['title'] if count == 1 else f"{payload['title']} — Part {i} of {count}"
        parts.append({
            'part_number': i, 'part_count': count,
            'title': label,
            'start_index': start, 'end_index_exclusive': end,
            'first_track': start + 1, 'last_track': end,
            'track_count': end - start,
            'status': 'not_generated', 'handoff_url': None,
            'expires_at': None, 'marked_transferred': False,
            'transferred_to': None, 'transferred_at': None,
        })
    return {
        'workflow': 'soundiiz_transfer',
        'destination': 'choose_in_soundiiz',
        'playlist_created': False,
        'handoffs_generated': 0,
        'part_count': count,
        'parts': parts,
        'consolidation_instructions': [
            'Transfer parts in ascending numerical order to the same destination service.',
            'For a single combined playlist, create or choose one destination playlist and append Part 1, Part 2, and subsequent parts in order.',
            'Keep repeated tracks when combining; do not use a remove-duplicates option if original duplicates must be preserved.',
            'Check track counts and any unmatched tracks in the destination; Soundiiz handoff acceptance alone is not a match guarantee.',
            'Adding songs to an existing playlist may require a paid Soundiiz plan or manual copying in the destination service.',
            'Once verified, remove temporary Part playlists only if desired.',
        ],
        'notes': [
            'No handoffs generated yet.',
            'A handoff does not verify destination playlist creation.',
        ],
    }


import time
from urllib.parse import urlsplit

import httpx


class SoundiizResponseError(ValueError):
    pass


async def create_handoff(client, title, tracks):
    """Submit one validated <=200-track batch; fail closed on malformed responses."""
    if not 1 <= len(tracks) <= BATCH_SIZE:
        raise ValueError('Invalid Soundiiz batch size.')
    request = {
        'title': title,
        'sourceName': 'Music Link Bridge',
        # Intentionally no destination: the user chooses on Soundiiz.
        'tracklist': [
            {k: v for k, v in track.items() if v not in ('', None)}
            for track in tracks
        ],
    }
    response = await client.post('https://soundiiz.com/go/import-playlist', json=request)
    response.raise_for_status()
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError()
        url = data['shareUrl']
        p = urlsplit(url)
        expires = data['expiresAt']
        if (data.get('status') != 'success' or p.scheme != 'https'
            or p.hostname != 'soundiiz.com' or p.username or p.password
            or p.port not in (None, 443) or p.query or p.fragment
            or not p.path.startswith('/go/import-playlist/')
            or not p.path[len('/go/import-playlist/'):] or p.path.endswith('/')
            or type(data.get('nbTracks')) is not int or data['nbTracks'] != len(tracks)
            or type(expires) not in (int, float) or expires <= time.time()):
            raise ValueError()
    except (ValueError, TypeError, KeyError) as exc:
        raise SoundiizResponseError('Invalid or incomplete Soundiiz response; handoff outcome uncertain.') from exc
    return {'handoff_url': url, 'expires_at': expires, 'accepted_tracks': data['nbTracks']}


def refresh_notes(result):
    """Derived messages never claim that a generated handoff is a finished transfer."""
    parts = result['parts']
    ready = sum(p.get('status') == 'ready' for p in parts)
    acknowledged = sum(bool(p.get('marked_transferred')) for p in parts)
    uncertain = sum(p.get('status') == 'uncertain' for p in parts)
    result['handoffs_generated'] = ready
    result['transfers_confirmed'] = acknowledged
    result['confirmation_source'] = 'user_reported'
    if acknowledged == len(parts):
        summary = 'All parts were marked transferred by the user; destination contents were not independently verified.'
    else:
        summary = f'{acknowledged} of {len(parts)} parts marked transferred by the user; {ready} handoff links ready.'
    result['notes'] = [summary,
        'Soundiiz handoff acceptance does not guarantee that every track matched at the destination.']
    if uncertain:
        result['notes'].append(
            f'{uncertain} part(s) have uncertain handoff outcomes; inspect before explicitly regenerating.')
    result.setdefault('consolidation_instructions', [
        'Transfer numbered parts to the same destination service in order.',
        'Append Part 1, then Part 2, and so on to a single destination playlist; retain duplicates and check the final track count.',
        'You may need to copy tracks manually or use a paid service feature to append to an existing playlist.',
        'Confirm destination matches before deleting temporary part playlists.',
    ])
    return result
