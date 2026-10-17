"""MusicLink-only Apple song/album/artist sharing, without invented destination links."""
from .core import classify, PLATFORMS
from .catalog import safe_url, apple_source, CatalogError
from urllib.parse import urlsplit
import re


def musiclink_apple_identity(url):
    """Accept canonical countryless item URLs only in the provider response."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname != 'music.apple.com'
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.fragment):
            raise ValueError('Invalid Apple Music identity URL')
        match = re.fullmatch(r'/(song|album|artist)/([0-9]+)', parsed.path)
        if match and not parsed.query:
            return {'type': match.group(1), 'id': match.group(2)}
        return classify(url)
    except (ValueError, TypeError, AttributeError) as exc:
        raise CatalogError('MusicLink returned an invalid source identity.') from exc


def normalize_musiclink(item, records, spotify=None, artist_name=None):
    if len(records) != 1 or not isinstance(records[0], dict):
        raise CatalogError('MusicLink did not return a unique source match.')
    record = records[0]
    expected_type = {'song': 'track', 'album': 'album', 'artist': 'artist'}[item['type']]
    if record.get('type') not in (expected_type, item['type']):
        raise CatalogError('MusicLink returned the wrong item type.')
    platforms = record.get('platforms')
    if not isinstance(platforms, dict):
        raise CatalogError('MusicLink platform list is invalid.')
    # Verify the returned Apple Music identity before trusting any external links.
    apple = platforms.get('apple_music')
    if not isinstance(apple, str):
        raise CatalogError('MusicLink did not confirm the source identity.')
    identified = musiclink_apple_identity(apple)
    if identified['type'] != item['type'] or identified['id'] != item['id']:
        raise CatalogError('MusicLink matched a different Apple Music item.')

    urls = {}
    for platform, key in [('spotify', 'spotify'), ('tidal', 'tidal'),
                          ('youtubeMusic', 'youtube_music')]:
        candidate = platforms.get(key)
        if isinstance(candidate, str) and safe_url(key, item['type'], candidate):
            urls[platform] = candidate
    if 'spotify' not in urls and isinstance(spotify, str) and safe_url('spotify', item['type'], spotify):
        urls['spotify'] = spotify
    links = [{'service': 'Apple Music', 'url': item['url'], 'match': 'source'}]
    for k, (name, _) in PLATFORMS.items():
        if k != 'appleMusic' and k in urls:
            links.append({'service': name, 'url': urls[k], 'match': 'provider'})
    title = record.get('title') if isinstance(record.get('title'), str) else ''
    artist = record.get('artist') if isinstance(record.get('artist'), str) else ''
    title = title.strip() or 'Apple Music ' + item['type']
    artist = artist.strip()
    if item['type'] == 'artist':
        # Artist records have no title or artist in MusicLink's live API.
        if not isinstance(artist_name, str) or not artist_name.strip():
            raise CatalogError('Apple artist identity has no confirmed display name.')
        title, artist = artist_name.strip(), ''
    heading = title + (' — ' + artist if artist else '')
    missing = [name for k, (name, _) in PLATFORMS.items() if k != 'appleMusic' and k not in urls]
    result = {'api_version': 1, 'type': item['type'], 'title': title,
              'artist': artist, 'links': links, 'missing_services': missing,
              'share_text': heading + '\n\n' + '\n\n'.join(x['service'] + '\n' + x['url'] for x in links),
              'warnings': []}
    if missing:
        result['warnings'].append('No confirmed provider link for: ' + ', '.join(missing))
    return result


async def resolve_share(client, catalogs, item):
    records = await catalogs.musiclink_resolve(client, item['url'])
    # Verify Apple artist metadata for a reliable display name. MusicLink's
    # artist records may omit both title and artist fields.
    source = await apple_source(client, item['url']) if item['type'] == 'artist' else None
    artist_name = source['artist'] if source is not None else None
    # Validate source identity before considering Spotify fallback.
    initial = normalize_musiclink(item, records, artist_name=artist_name)
    spotify = None
    if 'Spotify' in initial['missing_services']:
        try:
            if source is None:
                source = await apple_source(client, item['url'])
            candidate, _ = await catalogs.musiclink_spotify(client, source)
            if candidate:
                spotify = candidate['url']
        except Exception:
            # Fail closed on fallback: other verified provider links remain usable.
            pass
    return normalize_musiclink(item, records, spotify, artist_name=artist_name)
