"""Read public Apple playlist pages; fail closed if the track list is incomplete.

This uses Apple's public web-page data, not a supported MusicKit API. Private
playlists and page format changes produce explicit errors, never partial jobs.
"""
import json
from html.parser import HTMLParser
from urllib.parse import urljoin
import httpx
from .core import classify
from .playlists import PlaylistJob

MAX_PAGE_BYTES = 8 * 1024 * 1024


class SourcePlaylistError(ValueError):
    pass


class ServerDataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('id') == 'serialized-server-data':
            self.active = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def parse_playlist_page(page, source):
    parser = ServerDataParser()
    parser.feed(page)
    try:
        document = json.loads(''.join(parser.parts))
        pages = document['data']
        matching = [p['data'] for p in pages
                    if p['intent']['contentDescriptor']['kind'] == 'playlist'
                    and p['intent']['contentDescriptor']['identifiers']['storeAdamID'] == source['id']]
        if len(matching) != 1:
            raise ValueError('Playlist identity mismatch')
        sections = matching[0]['sections']
        headers = [item for section in sections
                   if section.get('itemKind') == 'containerDetailHeaderLockup'
                   for item in section['items']
                   if item.get('contentDescriptor', {}).get('identifiers', {}).get('storeAdamID') == source['id']]
        if len(headers) != 1:
            raise ValueError('Missing playlist header')
        header = headers[0]
        count = header['trackCount']
        if type(count) is not int or not 1 <= count <= 1000:
            raise ValueError('Playlist must contain 1–1000 tracks')
        track_sections = [s for s in sections if s.get('itemKind') == 'trackLockup'
                          and s.get('containerContentDescriptor', {}).get('identifiers', {}).get('storeAdamID') == source['id']]
        items = [item for section in track_sections for item in section['items']]
        if len(items) != count:
            raise SourcePlaylistError(
                f'Apple advertises {count} tracks but the public page supplied {len(items)}. '
                'No partial playlist was submitted. Use the library-selection shortcut instead.')
        tracks = []
        for item in items:
            if item['contentDescriptor']['kind'] != 'song':
                raise ValueError('Source includes non-song items')
            tracks.append({'title': item['title'], 'artists': [item['artistName']]})
        return PlaylistJob(mode='apple_video_plan', title=header['title'],
                           tracks=tracks, country=source['country'], source_url=source['url'])
    except SourcePlaylistError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise SourcePlaylistError(
            'Cannot read a complete public Apple Music playlist. It may be private, '
            'unavailable, empty, unsupported, or Apple may have changed its page format.') from exc


async def resolve_playlist(raw_url):
    source = classify(raw_url)
    if source['type'] != 'playlist':
        raise SourcePlaylistError('Share an Apple Music playlist, not a song or album.')
    # Start with a fixed path. Apple redirects to the canonical title slug;
    # follow only validated same-playlist redirects on the fixed Apple host.
    url = f"https://music.apple.com/{source['country'].lower()}/playlist/{source['id']}"
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            for attempt in range(4):
                async with client.stream('GET', url, headers={'Accept-Language': 'en-US'}) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        if attempt == 3 or not response.headers.get('location'):
                            raise SourcePlaylistError('Apple returned too many or invalid redirects.')
                        try:
                            destination = classify(urljoin(url, response.headers['location']))
                        except ValueError as exc:
                            raise SourcePlaylistError('Apple returned an unsafe redirect.') from exc
                        if any(destination[k] != source[k] for k in ('type', 'id', 'country')):
                            raise SourcePlaylistError('Apple redirected to a different playlist.')
                        url = destination['url']
                        continue
                    response.raise_for_status()
                    if 'text/html' not in response.headers.get('content-type', ''):
                        raise SourcePlaylistError('Apple returned an unexpected page type.')
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_PAGE_BYTES:
                            raise SourcePlaylistError('Apple playlist page exceeds the safe size limit.')
                    break
    except httpx.HTTPError as exc:
        raise SourcePlaylistError('Apple playlist page is unavailable. Confirm that the playlist is public and try again.') from exc
    job = parse_playlist_page(data.decode('utf-8'), source)
    payload = job.model_dump()
    return {'title': job.title, 'track_count': len(job.tracks), 'country': job.country,
            'source_url': job.source_url, 'request': payload,
            'request_json': json.dumps(payload, ensure_ascii=False)}
