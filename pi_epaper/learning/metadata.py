"""Last.fm track URL resolution without fetching arbitrary user-supplied URLs."""
from urllib.parse import unquote, urlparse
from music_metadata import lastfm_track


def from_lastfm_url(url, api_key=''):
    parsed = urlparse(url.strip())
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('last.fm', 'www.last.fm'):
        raise ValueError('Use a Last.fm track link, such as https://www.last.fm/music/Artist/_/Title')
    parts = parsed.path.strip('/').split('/')
    if len(parts) != 4 or parts[0] != 'music' or parts[2] != '_':
        raise ValueError('Use a track page link, not an album or artist page')
    # Last.fm uses literal + for spaces and percent-escapes literal plus signs.
    artist, title = [unquote(p.replace('+', ' ')).strip() for p in (parts[1], parts[3])]
    if not artist or not title or len(artist) > 500 or len(title) > 500:
        raise ValueError('Invalid artist/title in link')
    result = {'artist': artist, 'title': title, 'album': '', 'art_url': '', 'lastfm_url': url.strip()}
    if api_key:
        try:
            result.update(lastfm_track(api_key, artist, title))
        except Exception:
            result['notice'] = 'Link parsed; Last.fm lookup unavailable. Confirm artist/title manually.'
    return result
