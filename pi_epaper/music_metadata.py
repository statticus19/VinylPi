"""Album metadata for vinyl submissions and a matching display artwork fallback."""
import json
import math
import logging
import os
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LOG = logging.getLogger("metadata")
PLACEHOLDER = "2a96cbd8b46e442fc41c2b86b821562f"


def clean(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


def normalized(value):
    return unicodedata.normalize("NFKC", clean(value)).casefold()


def useful_art(value):
    return isinstance(value, str) and value.startswith("https://") and PLACEHOLDER not in value.lower()


def recognition_metadata(track):
    album = ""
    sections = track.get("sections")
    for section in sections if isinstance(sections, list) else []:
        if not isinstance(section, dict) or section.get("type") not in (None, "SONG"):
            continue
        items = section.get("metadata")
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and normalized(item.get("title")) == "album":
                album = clean(item.get("text"))
                if album:
                    break
        if album:
            break
    if not album:
        value = track.get("album")
        album = clean(value.get("title") or value.get("name")) if isinstance(value, dict) else clean(value)
    images = track.get("images") or {}
    art = next((images.get(name) for name in ("coverarthq", "coverart")
                if useful_art(images.get(name))), "") if isinstance(images, dict) else ""
    return {"artist": clean(track.get("subtitle")), "title": clean(track.get("title")),
            "album": album, "art_url": art, "album_source": "Shazam" if album else ""}


def lastfm_track(api_key, artist, title):
    query = urlencode({"method": "track.getInfo", "artist": artist, "track": title,
                       "api_key": api_key, "autocorrect": 0, "format": "json"})
    request = Request("https://ws.audioscrobbler.com/2.0/?"+query,
                      headers={"User-Agent": "Pi-Epaper-Vinyl/1.0"})
    with urlopen(request, timeout=8) as response:
        value = json.loads(response.read(2_000_000))
    if "error" in value:
        raise ValueError("Last.fm track lookup failed")
    track = value.get("track") or {}
    artist_value = track.get("artist") or {}
    found_artist = artist_value.get("name") if isinstance(artist_value, dict) else artist_value
    if (normalized(found_artist), normalized(track.get("name"))) != (normalized(artist), normalized(title)):
        return {}
    album = track.get("album") or {}
    if not isinstance(album, dict):
        return {}
    images = album.get("image") or []
    art = next((item.get("#text") for item in reversed(images)
                if isinstance(item, dict) and useful_art(item.get("#text"))), "")
    return {"album": clean(album.get("title")), "art_url": art}


class AlbumResolver:
    def __init__(self, api_key, fetch=None):
        self.api_key = api_key
        self.fetch = fetch or lastfm_track
        self.cache = {}

    def resolve(self, track, now=None):
        result = recognition_metadata(track)
        if result["album"] or not self.api_key or not result["artist"] or not result["title"]:
            return result
        now = time.time() if now is None else now
        key = (normalized(result["artist"]), normalized(result["title"]))
        cached = self.cache.get(key)
        if not cached or cached[0] <= now:
            try:
                value = self.fetch(self.api_key, result["artist"], result["title"])
                seconds = 86400 if value.get("album") else 900
            except Exception as exc:
                LOG.warning("Album lookup unavailable (%s); keeping recognized artist/title", type(exc).__name__)
                value, seconds = {}, 300
            if len(self.cache) >= 256 and key not in self.cache:
                self.cache.pop(next(iter(self.cache)))
            cached = self.cache[key] = (now+seconds, value)
        value = cached[1]
        result["album"] = clean(value.get("album"))
        if result["album"]:
            result["album_source"] = "Last.fm"
        if not result["art_url"] and useful_art(value.get("art_url")):
            result["art_url"] = value["art_url"]
        return result


def publish_metadata(path, metadata, now=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+".tmp")
    payload = dict(metadata, at=time.time() if now is None else now)
    with temporary.open("w") as handle:
        os.chmod(temporary, 0o600)
        json.dump(payload, handle)
    os.replace(temporary, path)


def recognition_status(path, now=None):
    """Read a recent audio result; an absent/stopped producer cannot leave a badge."""
    try:
        value = json.loads(Path(path).read_text())
        now = time.time() if now is None else now
        age = now-float(value["at"])
        status = value["status"]
        if math.isfinite(age) and 0 <= age <= 180 and status in (
                "no_match", "matched", "silent", "error", "unknown", "stopped"):
            return status
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return "unknown"


def enrich_from_vinyl(track, path, now=None):
    """Fill missing fields only for the same recently recognized artist/title."""
    if not track:
        return track
    try:
        metadata = json.loads(Path(path).read_text())
        now = time.time() if now is None else now
        age = now-float(metadata["at"])
        if not 0 <= age <= 300:
            return track
        if any(normalized(track.get(field)) != normalized(metadata.get(field)) for field in ("artist", "title")):
            return track
        result = dict(track)
        if not clean(result.get("album")):
            result["album"] = clean(metadata.get("album"))
        album_matches = normalized(result.get("album")) == normalized(metadata.get("album"))
        if not useful_art(result.get("art_url")) and album_matches and useful_art(metadata.get("art_url")):
            result["art_url"] = metadata["art_url"]
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return track
