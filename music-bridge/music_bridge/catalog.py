"""Read-only catalog matching. Fixed provider hosts; no user URL is fetched."""
import asyncio
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote, urlsplit, parse_qs
import httpx
from .core import classify
from .playlists import plain

SERVICES = {'spotify': 'Spotify', 'youtube_music': 'YouTube Music', 'tidal': 'Tidal'}

class CatalogError(Exception):
    pass

class NotConfigured(CatalogError):
    pass


def safe_url(service, kind, url):
    if not isinstance(url, str):
        return False
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
            return False
        if service == 'spotify':
            return p.hostname == 'open.spotify.com' and bool(re.fullmatch('/' + {'song':'track','album':'album','artist':'artist'}[kind] + r'/[A-Za-z0-9]{22}', p.path))
        if service == 'tidal':
            return p.hostname in ('tidal.com', 'www.tidal.com', 'listen.tidal.com') and bool(re.fullmatch(r'/(?:browse/)?' + {'song':'track','album':'album','artist':'artist'}[kind] + r'/[0-9]+', p.path))
        if service == 'youtube_music' and p.hostname == 'music.youtube.com':
            if kind == 'song':
                return p.path == '/watch' and bool(re.fullmatch(r'[A-Za-z0-9_-]{11}', parse_qs(p.query).get('v', [''])[0]))
            if kind == 'album':
                return p.path == '/playlist' and bool(re.fullmatch(r'OLAK5uy_[A-Za-z0-9_-]+', parse_qs(p.query).get('list', [''])[0]))
            return bool(re.fullmatch(r'/channel/UC[A-Za-z0-9_-]{22}', p.path))
    except (ValueError, KeyError):
        pass
    return False


def seconds(value):
    if isinstance(value, (int, float)):
        return value
    m = re.fullmatch(r'PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?', value or '')
    return sum(float(v or 0)*f for v, f in zip(m.groups(), (3600,60,1))) if m else None


def choose(source, candidates, service):
    eligible = []
    for c in candidates:
        if c.get('kind') != source['type'] or not safe_url(service, source['type'], c.get('url')):
            continue
        if plain(c.get('title', '')) != plain(source['title']):
            continue
        if source['type'] != 'artist' and plain(source['artist']) not in {plain(a.get('name','')) for a in c.get('artists', [])}:
            continue
        if c.get('available') is False:
            continue
        duration = source.get('duration')
        if source['type'] == 'song' and duration is not None:
            if c.get('duration') is None or abs(duration-c['duration']) > 5:
                continue
        if source['type'] == 'song' and source.get('explicit') is not None and c.get('explicit') is not None and c['explicit'] != source['explicit']:
            continue
        if source['type'] == 'album' and source.get('track_count') is not None and c.get('track_count') is not None and c['track_count'] != source['track_count']:
            continue
        eligible.append(c)
    # Prefer the source album when catalog duplicates exist; keep version words intact.
    eligible.sort(key=lambda c: (plain(c.get('album','')) != plain(source.get('album','')), c['url']))
    return eligible[0] if eligible else None


async def json_response(response):
    if response.status_code != 200:
        raise CatalogError(f'Catalog request returned HTTP {response.status_code}.')
    data = response.json()
    if not isinstance(data, dict):
        raise CatalogError('Catalog response format changed.')
    return data


async def apple_source(client, raw):
    item = classify(raw)
    if item['type'] == 'playlist':
        raise CatalogError('Playlists require destination creation or transfer; they do not have an equivalent catalog item.')
    params = {'id': item['id'], 'country': item['country']}
    if item['type'] == 'artist':
        params.update(entity='song', limit=10)
    elif item['type'] == 'album':
        params.update(entity='song', limit=1)
    data = await json_response(await client.get('https://itunes.apple.com/lookup', params=params))
    rows = data.get('results', [])
    key = {'song':'trackId','album':'collectionId','artist':'artistId'}[item['type']]
    expected = {'song':'track','album':'collection','artist':'artist'}[item['type']]
    matches = [r for r in rows if str(r.get(key)) == item['id'] and r.get('wrapperType') == expected]
    if item['type'] == 'song':
        matches = [r for r in matches if r.get('kind') == 'song']
    if len(matches) != 1:
        raise CatalogError('Apple did not return the requested catalog item in this country.')
    row = matches[0]
    result = {**item, 'title': row.get({'song':'trackName','album':'collectionName','artist':'artistName'}[item['type']], ''),
              'artist': row.get('artistName', ''), 'album': row.get('collectionName', '')}
    if not result['title'] or not result['artist']:
        raise CatalogError('Apple metadata was incomplete.')
    if item['type'] == 'song':
        result['duration'] = row.get('trackTimeMillis', 0) / 1000 or None
        result['explicit'] = {'explicit':True,'notExplicit':False,'cleaned':False}.get(row.get('trackExplicitness'))
    if item['type'] == 'album':
        result['track_count'] = row.get('trackCount')
        seed = next((t for t in rows if t.get('kind') == 'song' and str(t.get('collectionId')) == item['id']), None)
        if seed:
            result['album_seed'] = seed['trackName']
    if item['type'] == 'artist':
        result['seeds'] = []
        titles = set()
        for track in rows:
            if track.get('kind') != 'song' or str(track.get('artistId')) != item['id'] or track.get('trackName') in titles:
                continue
            titles.add(track['trackName'])
            result['seeds'].append({'type':'song', 'title':track['trackName'], 'artist':row['artistName'],
                'album':track.get('collectionName',''), 'duration':track.get('trackTimeMillis',0)/1000 or None,
                'country':item['country']})
            if len(result['seeds']) == 2:
                break
    return result


class Catalogs:
    def __init__(self):
        self.tokens = {}
        self.musiclink_lock = asyncio.Lock()
        self.musiclink_next = 0

    def musiclink_path(self):
        return Path(os.environ.get('MUSIC_BRIDGE_MUSICLINK_CLIENT', 'secrets/musiclink-client.json'))

    async def musiclink_request(self, client, endpoint, params):
        """Shared MusicLink credential and rate limiter for resolve and convert."""
        path = self.musiclink_path()
        if path.stat().st_mode & 0o077:
            raise CatalogError('Credential file permissions must be 600.')
        try:
            key = json.loads(path.read_text()).get('api_key')
            if not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key):
                raise ValueError()
        except (ValueError, AttributeError) as exc:
            raise CatalogError('MusicLink credential file format is invalid.') from exc
        async with self.musiclink_lock:
            await asyncio.sleep(max(0, self.musiclink_next - time.monotonic()))
            self.musiclink_next = time.monotonic() + 6.1
            return await client.get('https://api.musiclink.one/v2/' + endpoint,
                                    params=params, headers={'Authorization': 'Bearer ' + key}, timeout=30)

    async def musiclink_resolve(self, client, source_url):
        response = await self.musiclink_request(client, 'resolve', {'q': source_url})
        payload = await json_response(response)
        if payload.get('success') is not True or not isinstance(payload.get('data'), list):
            raise CatalogError('MusicLink resolve response format changed.')
        return payload['data']

    async def musiclink_spotify(self, client, source):
        response = await self.musiclink_request(client, 'convert',
                                                {'q': source['url'], 'to': 'spotify'})
        if response.status_code == 404:
            return None, 'no_confident_match'
        payload = await json_response(response)
        data = payload.get('data')
        if payload.get('success') is not True or not isinstance(data, dict):
            raise CatalogError('MusicLink response format changed.')
        origin, target = data.get('from'), data.get('to')
        kind = {'song':'track','album':'album','artist':'artist'}[source['type']]
        if (data.get('type') != kind or not isinstance(origin, dict)
            or origin.get('platform') not in ('apple_music','apple')
            or str(origin.get('id')) != str(source['id'])
            or not isinstance(target, dict) or target.get('platform') != 'spotify'
            or not safe_url('spotify', source['type'], target.get('value'))):
            raise CatalogError('MusicLink returned an unexpected source or destination.')
        metadata = data.get('item')
        if not isinstance(metadata, dict):
            return None, 'incomplete_conversion_metadata'
        candidate = {'kind':source['type'], 'title':metadata.get('title',''),
            'url':target['value'], 'artists':[{'name':metadata.get('artist','')}],
            'duration':metadata.get('duration_ms',0)/1000 or None}
        candidate = choose(source, [candidate], 'spotify')
        return candidate, 'musiclink_source_url_conversion' if candidate else 'no_confident_match'

    async def token(self, client, service):
        path = Path(os.environ.get('MUSIC_BRIDGE_' + service.upper() + '_CLIENT', 'secrets/' + service + '-client.json'))
        if not path.exists():
            raise NotConfigured('Developer app credentials are not configured.')
        if path.stat().st_mode & 0o077:
            raise CatalogError('Credential file permissions must be 600.')
        try:
            config = json.loads(path.read_text())
            if any(not isinstance(config.get(k), str) or not config[k] for k in ('client_id','client_secret')):
                raise ValueError()
        except (ValueError, AttributeError) as exc:
            raise CatalogError('Credential file format is invalid.') from exc
        cached = self.tokens.get(service)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        url = {'spotify':'https://accounts.spotify.com/api/token','tidal':'https://auth.tidal.com/v1/oauth2/token'}[service]
        data = await json_response(await client.post(url, auth=httpx.BasicAuth(config['client_id'], config['client_secret']), data={'grant_type':'client_credentials'}))
        if not isinstance(data.get('access_token'), str) or not data['access_token'] or data.get('token_type','').lower() != 'bearer':
            raise CatalogError('Invalid catalog access token.')
        token = data['access_token']
        self.tokens[service] = (time.monotonic()+max(0,min(float(data.get('expires_in',0))-60,3600)),token)
        return token

    async def spotify(self, client, source):
        token = await self.token(client, 'spotify')
        kind = {'song':'track','album':'album','artist':'artist'}[source['type']]
        query = source['title'] + (' ' + source['artist'] if source['type'] != 'artist' else '')
        data = await json_response(await client.get('https://api.spotify.com/v1/search', headers={'Authorization':'Bearer '+token},
            params={'q':query,'type':kind,'market':source['country'],'limit':10}))
        rows = data.get(kind+'s', {}).get('items', [])
        return [{'kind':source['type'],'title':r.get('name',''),'url':r.get('external_urls',{}).get('spotify',''),
                 'artists':[{'name':a.get('name',''),'url':a.get('external_urls',{}).get('spotify','')} for a in r.get('artists',[])],
                 'duration':r.get('duration_ms',0)/1000 or None,'explicit':r.get('explicit'),
                 'album':r.get('album',{}).get('name',''), 'available':r.get('is_playable'),
                 'track_count':r.get('total_tracks')} for r in rows]

    async def tidal(self, client, source):
        token = await self.token(client, 'tidal')
        kind = {'song':'tracks','album':'albums','artist':'artists'}[source['type']]
        query = (source['title'] + (' ' + source['artist'] if source['type'] != 'artist' else ''))[:256]
        headers = {'Authorization':'Bearer '+token, 'Accept':'application/vnd.api+json'}
        # The current API accepts search text in filter[query]. Relationship
        # URLs require an opaque search-results ID, not the query text.
        search = await json_response(await client.get('https://openapi.tidal.com/v2/searchResults',
            headers=headers, params={'filter[query]':query, 'countryCode':source['country'], 'include':kind}))
        roots = search.get('data')
        if not isinstance(roots, list) or len(roots) != 1:
            raise CatalogError('Tidal search response format changed.')
        refs = roots[0].get('relationships',{}).get(kind,{}).get('data')
        if not isinstance(refs, list):
            raise CatalogError('Tidal search omitted the requested relationship.')
        ids = list(dict.fromkeys(r.get('id') for r in refs if r.get('type') == kind
                                and isinstance(r.get('id'),str) and r['id'].isdigit()))[:20]
        if not ids:
            return []
        # Fetch type-specific catalog records with direct artist relationships.
        # No undocumented nested include on the relationship-search endpoint.
        data = await json_response(await client.get('https://openapi.tidal.com/v2/'+kind,
            headers=headers, params={'filter[id]':','.join(ids), 'countryCode':source['country'],
                                     **({'include':'artists'} if kind != 'artists' else {})}))
        primary = data.get('data')
        if not isinstance(primary,list):
            raise CatalogError('Tidal catalog response format changed.')
        records = {(r['type'],r['id']):r for r in data.get('included', []) + primary}
        def link(r, entity_kind):
            return next((v.get('href','') for v in r.get('attributes',{}).get('externalLinks',[])
                         if safe_url('tidal',entity_kind,v.get('href'))), '')
        rows = []
        for ref in refs[:20]:
            r = records.get((ref.get('type'),ref.get('id')), {})
            attrs = r.get('attributes',{})
            artists = []
            for a in r.get('relationships',{}).get('artists',{}).get('data',[]):
                artist = records.get((a.get('type'),a.get('id')), {})
                artists.append({'name':artist.get('attributes',{}).get('name',''),'url':link(artist,'artist')})
            title = attrs.get('name' if source['type']=='artist' else 'title','')
            if attrs.get('version') and plain(attrs['version']) not in plain(title):
                title += ' (' + attrs['version'] + ')'
            rows.append({'kind':source['type'],'title':title,'artists':artists,'url':link(r,source['type']),
                'duration':seconds(attrs.get('duration')), 'explicit':attrs.get('explicit'),
                'track_count':attrs.get('numberOfItems'),'available':attrs.get('accessType') != 'PRIVATE'})
        return rows

    async def youtube_music(self, client, source):
        def search():
            from ytmusicapi import YTMusic
            import requests
            session = requests.Session()
            # Bound every request, including library initialization.
            original = session.request
            def request(*args, **kwargs):
                kwargs['timeout'] = 20
                return original(*args, **kwargs)
            session.request = request
            try:
                yt = YTMusic(requests_session=session, location=source['country'])
                category = {'song':'songs','album':'albums','artist':'artists'}[source['type']]
                query = source['title'] + (' ' + source['artist'] if source['type'] != 'artist' else '')
                found = yt.search(query,filter=category,limit=10,ignore_spelling=True)
                if source['type'] == 'album' and source.get('album_seed') and not any(plain(r.get('title','')) == plain(source['title']) for r in found):
                    songs = yt.search(source['album_seed']+' '+source['artist'],filter='songs',limit=10,ignore_spelling=True)
                    album_ids = []
                    for song in songs:
                        album = song.get('album') or {}
                        if (plain(song.get('title','')) == plain(source['album_seed'])
                            and plain(album.get('name','')) == plain(source['title'])
                            and plain(source['artist']) in {plain(a.get('name','')) for a in song.get('artists',[])}
                            and re.fullmatch(r'MPRE[A-Za-z0-9_-]+',album.get('id',''))):
                            album_ids.append(album['id'])
                    for album_id in list(dict.fromkeys(album_ids))[:2]:
                        album = yt.get_album(album_id)
                        album.update(resultType='album', playlistId=album.get('audioPlaylistId',''))
                        found.append(album)
                if source['type'] == 'album' and not any(plain(r.get('title','')) == plain(source['title']) for r in found):
                    related = [r for r in found if plain(source['artist']) in {plain(a.get('name','')) for a in r.get('artists',[])}
                               and plain(r.get('title','')).startswith(plain(source['title'])+' ')]
                    for r in related[:2]:
                        if not re.fullmatch(r'MPRE[A-Za-z0-9_-]+', r.get('browseId','')):
                            continue
                        details = yt.get_album(r['browseId'])
                        for version in details.get('other_versions',[])[:10]:
                            if plain(version.get('title','')) != plain(source['title']) or not re.fullmatch(r'MPRE[A-Za-z0-9_-]+', version.get('browseId','')):
                                continue
                            album = yt.get_album(version['browseId'])
                            album.update(resultType='album', playlistId=album.get('audioPlaylistId',''))
                            found.append(album)
                            break
                rows = []
                for r in found:

                    if r.get('resultType') != source['type']:
                        continue
                    url = ''
                    if source['type']=='song' and re.fullmatch(r'[A-Za-z0-9_-]{11}',r.get('videoId','')):
                        url = 'https://music.youtube.com/watch?v='+r['videoId']
                    elif source['type']=='album' and re.fullmatch(r'OLAK5uy_[A-Za-z0-9_-]+',r.get('playlistId','')):
                        url = 'https://music.youtube.com/playlist?list='+r['playlistId']
                    elif source['type']=='artist' and re.fullmatch(r'UC[A-Za-z0-9_-]{22}',r.get('browseId','')):
                        url = 'https://music.youtube.com/channel/'+r['browseId']
                    artists = [{'name':a.get('name',''),'url':'https://music.youtube.com/channel/'+a['id'] if re.fullmatch(r'UC[A-Za-z0-9_-]{22}',a.get('id') or '') else ''} for a in r.get('artists',[])]
                    if not artists and isinstance(r.get('artist'),str):
                        artists = [{'name':r['artist'],'url':''}]
                    rows.append({'kind':source['type'],'title':r.get('artist','') if source['type']=='artist' else r.get('title',''),
                        'url':url,'artists':artists,'album':r.get('album',{}).get('name','') if isinstance(r.get('album'),dict) else '',
                        'duration':r.get('duration_seconds'), 'available':r.get('isAvailable'), 'explicit':r.get('isExplicit'), 'track_count':r.get('trackCount')})
                return rows
            finally:
                session.close()
        return await asyncio.to_thread(search)

    async def resolve_service(self, client, source, service):
        if service == 'spotify' and self.musiclink_path().exists():
            return await self.musiclink_spotify(client, source)
        search = getattr(self, service)
        if source['type'] != 'artist':
            candidate = choose(source, await search(client, source), service)
            return candidate, 'exact_metadata' if candidate else 'no_confident_match'
        # Artist identity is anchored to TWO different source songs, not a name alone.
        if len(source.get('seeds',[])) < 2:
            return None, 'insufficient_artist_catalog_evidence'
        anchors = []
        for seed in source['seeds']:
            candidate = choose(seed, await search(client, seed), service)
            if not candidate:
                return None, 'artist_catalog_not_matched'
            anchors.append({a['url'] for a in candidate.get('artists',[]) if plain(a.get('name','')) == plain(source['artist']) and safe_url(service,'artist',a.get('url'))})
        common = anchors[0] & anchors[1]
        if len(common) != 1:
            return None, 'ambiguous_artist_identity'
        url = common.pop()
        return {'kind':'artist','title':source['title'],'url':url}, 'two_source_songs_same_artist'

    async def resolve(self, raw_url, services=None):
        services = services or list(SERVICES)
        async with httpx.AsyncClient(timeout=25,follow_redirects=False) as client:
            source = await apple_source(client,raw_url)
            rows = []
            for service in services:
                try:
                    candidate, reason = await self.resolve_service(client,source,service)
                    rows.append({'service':SERVICES[service], 'service_key':service,
                                 'status':'matched' if candidate else 'missing', 'reason':reason,
                                 'url':candidate['url'] if candidate else None})
                except NotConfigured as exc:
                    rows.append({'service':SERVICES[service],'service_key':service,'status':'not_configured','reason':str(exc),'url':None})
                except Exception:
                    rows.append({'service':SERVICES[service],'service_key':service,'status':'unavailable','reason':'Catalog request failed; check credentials, quota, or provider availability.','url':None})
        heading = source['title'] + (' — '+source['artist'] if source['type']!='artist' else '')
        links = [{'service':'Apple Music','url':source['url'],'match':'source'}]+[
            {'service':r['service'],'url':r['url'],'match':r['reason']} for r in rows if r['status']=='matched']
        missing = [r['service'] for r in rows if r['status']!='matched']
        text = heading+'\n\n'+'\n\n'.join(l['service']+'\n'+l['url'] for l in links)
        if missing:
            text += '\n\nNo confirmed link: '+', '.join(missing)
        attribution = 'Spotify link matching powered by MusicLink — https://musiclink.one' if any(
            r['reason'] == 'musiclink_source_url_conversion' for r in rows) else ''
        return {'api_version':1,'type':source['type'],'title':source['title'],'artist':source['artist'],
                'links':links,'services':rows,'missing_services':missing,'share_text':text,
                'attribution':attribution,
                'warnings':['Metadata matching is not a guarantee of an identical recording or edition.',
                            'YouTube Music catalog search uses the unofficial ytmusicapi library.']}
