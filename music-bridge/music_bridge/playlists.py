"""Client-supplied playlist metadata. Never fetch a user-provided URL."""
import re
import unicodedata
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from .core import classify

class Track(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=300)
    artists: list[str] = Field(min_length=1, max_length=10)
    album: str = Field(default='', max_length=300)
    isrc: str | None = Field(default=None, pattern=r'^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$')

    @field_validator('artists', mode='before')
    @classmethod
    def valid_artists(cls, value):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            raise ValueError('Artists must be text or a list.')
        if any(not isinstance(x, str) for x in value):
            raise ValueError('Artist names must be text.')
        if any(not x.strip() or len(x) > 300 for x in value):
            raise ValueError('Each artist must contain 1–300 characters.')
        return [x.strip() for x in value]

class PlaylistJob(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    mode: Literal['apple_video_plan', 'spotify_handoff', 'youtube_video_playlist', 'youtube_music_playlist', 'soundiiz_transfer']
    title: str = Field(min_length=1, max_length=200)
    tracks: list[Track] = Field(min_length=1, max_length=1000)
    country: str = Field(default='US', pattern=r'^[A-Z]{2}$')
    source_url: str | None = Field(default=None, max_length=2048)
    review_before_create: bool = False

    @model_validator(mode='after')
    def validate_source_and_size(self):
        # The iOS shortcut predates the review flag. YouTube video jobs always
        # enter the explicit-approval workflow, even if callers send false.
        # This also lets /v1/playlist/resolve validate playlists >20 tracks
        # without truncation or an iOS shortcut change.
        if self.mode == 'youtube_video_playlist':
            self.review_before_create = True
        if self.mode == 'spotify_handoff' and len(self.tracks) > 200:
            raise ValueError('Soundiiz import accepts up to 200 tracks. No songs were silently truncated.')
        if self.mode in ('youtube_video_playlist', 'youtube_music_playlist') and len(self.tracks) > 20 and not self.review_before_create:
            raise ValueError('For more than 20 YouTube tracks, enable review_before_create. No songs were truncated.')
        if self.source_url:
            source = classify(self.source_url)
            if source['type'] != 'playlist':
                raise ValueError('source_url must identify an Apple Music playlist.')
            self.source_url = source['url']
        return self

def plain(value):
    value = unicodedata.normalize('NFKD', value.casefold())
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+', value, re.UNICODE))

LABEL = re.compile(r'\s*[\[(](?:official\s+)?(?:music\s+)?video[\])]\s*$', re.I)
LYRIC_LABEL = re.compile(r'\s*(?:[\[(](?:official\s+)?(?:lyric\s+video|lyrics?)[\])]|[-–—]\s*(?:official\s+)?(?:lyric\s+video|lyrics?))\s*$', re.I)
EXCLUDED = re.compile(r'\b(?:lyric video|lyrics|official audio|audio only|visuali[sz]er|fan video|cover version|live at|live from|live in|live performance)\b|[\[(](?:live|acoustic|remix|instrumental)\b|\s[-–—]\s(?:live|acoustic|remix|instrumental)\b', re.I)

def select_apple_video(track, candidates):
    """Strict metadata match; choose one deterministically among exact matches."""
    expected_title = plain(track.title)
    expected_artists = {plain(x) for x in track.artists}
    eligible = {}
    lyric_eligible = {}
    for c in candidates:
        if not isinstance(c, dict) or c.get('kind') != 'music-video':
            continue
        title = c.get('trackName', '')
        if not isinstance(title, str):
            continue
        lyric_title = LYRIC_LABEL.sub('', title)
        is_lyric = lyric_title != title
        clean_title = lyric_title if is_lyric else title
        if EXCLUDED.search(clean_title):
            continue
        if plain(LABEL.sub('', clean_title)) != expected_title:
            continue
        if not isinstance(c.get('artistName'), str) or plain(c['artistName']) not in expected_artists:
            continue
        item_id = str(c.get('trackId', ''))
        if not item_id.isdigit():
            continue
        group = lyric_eligible if is_lyric else eligible
        group[item_id] = {'store_id': item_id, 'title': title,
            'artist': c['artistName'], 'query': c['artistName'] + ' ' + title,
            'match': 'exact_normalized_metadata'}
    # Lyric videos are a fallback only when no standard video is eligible.
    choices = eligible or lyric_eligible
    if choices:
        # All eligible entries already passed the exact artist/title and type
        # checks above. User preference: choose one rather than omit the song.
        # Numeric ID ordering makes the choice independent of search ordering.
        selected = dict(choices[min(choices, key=int)])
        if not eligible:
            selected['match'] = 'exact_normalized_metadata_lyric_fallback'
        elif len(choices) > 1:
            selected['match'] = 'exact_metadata_multiple_entries_selected_one'
        return selected, None
    return None, 'no_confident_video_match'

class AppleCreated(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    account_alias: str = Field(min_length=1, max_length=100)
    store_ids: list[str] = Field(min_length=1, max_length=1000)

    @field_validator('store_ids')
    @classmethod
    def numeric_ids(cls, value):
        if any(not re.fullmatch(r'[0-9]{1,30}', x) for x in value):
            raise ValueError('Store IDs must be numeric strings.')
        return value
