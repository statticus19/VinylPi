"""URL validation and provider response normalization, independent of clients."""
import re
from urllib.parse import urlsplit, parse_qs, urlencode, urlunsplit

PLATFORMS = {'appleMusic': ('Apple Music', 'music.apple.com'),
             'spotify': ('Spotify', 'open.spotify.com'),
             'tidal': ('TIDAL', 'tidal.com'),
             'youtubeMusic': ('YouTube Music', 'music.youtube.com')}


def classify(raw):
    p = urlsplit(raw.strip())
    if (p.scheme != 'https' or p.hostname != 'music.apple.com' or
            p.username or p.password or p.port not in (None, 443)):
        raise ValueError('Use a full https://music.apple.com share URL.')
    parts = p.path.strip('/').split('/')
    if len(parts) < 3 or not re.fullmatch('[a-zA-Z]{2}', parts[0]):
        raise ValueError('Apple Music URL must include its country and item ID.')
    kind = parts[1]
    if kind not in ('album', 'song', 'artist', 'playlist'):
        raise ValueError('Supported types: song, album, artist, playlist.')
    query = parse_qs(p.query)
    item = query.get('i', [parts[-1]])[0]
    if kind != 'playlist' and not item.isdigit():
        raise ValueError('Invalid Apple Music item ID.')
    if kind == 'playlist' and not re.fullmatch(r'pl\.[A-Za-z0-9-]+', item):
        raise ValueError('Invalid Apple Music playlist ID.')
    if kind == 'album' and 'i' in query:
        kind = 'song'
    clean_query = urlencode({'i': item}) if 'i' in query else ''
    return {'type': kind, 'id': item, 'country': parts[0].upper(),
            'url': urlunsplit(('https', 'music.apple.com', p.path, clean_query, ''))}


def normalize(item, data):
    entities = data.get('entitiesByUniqueId', {})
    entity = entities.get(data.get('entityUniqueId'), {})
    links = [{'service': 'Apple Music', 'url': item['url'], 'match': 'source'}]
    for key, (label, domain) in PLATFORMS.items():
        if key == 'appleMusic':
            continue
        info = data.get('linksByPlatform', {}).get(key, {})
        url = info.get('url', '')
        p = urlsplit(url)
        if p.scheme == 'https' and p.hostname in (domain, 'www.' + domain) and not p.username:
            links.append({'service': label, 'url': url, 'match': 'provider'})
    title = entity.get('title') or 'Apple Music ' + item['type']
    artist = entity.get('artistName') or ''
    heading = title + (' — ' + artist if artist else '')
    text = heading + '\n\n' + '\n\n'.join(x['service'] + '\n' + x['url'] for x in links)
    missing = [label for _, (label, _) in PLATFORMS.items() if label not in {x['service'] for x in links}]
    return {'api_version': 1, 'type': item['type'], 'title': title,
            'artist': artist, 'links': links, 'missing_services': missing,
            'share_text': text, 'warnings': []}
