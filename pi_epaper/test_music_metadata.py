"""Album submission and cover fallback tests; no real Last.fm writes."""
import asyncio
import importlib.util
import io
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from music_metadata import (AlbumResolver, recognition_metadata, lastfm_track,
                            publish_metadata, enrich_from_vinyl, useful_art)
from display_service import parse_now_playing, Providers, ROOT

TRACK = {"title": "Fata Morgana", "subtitle": "Spiritbox",
         "sections": [{"type": "SONG", "metadata": [
             {"title": "Label", "text": "Label name"},
             {"title": "Album", "text": "Tsunami Sea"}]}],
         "images": {"coverarthq": "https://example.org/album.jpg",
                    "background": "https://example.org/artist.jpg"}}


class MetadataTests(unittest.TestCase):
    def test_reads_album_and_cover_from_song_metadata(self):
        value = recognition_metadata(TRACK)
        self.assertEqual(value["album"], "Tsunami Sea")
        self.assertEqual(value["art_url"], "https://example.org/album.jpg")
        self.assertEqual(value["album_source"], "Shazam")

    def test_lyrics_and_artist_images_are_not_album_metadata(self):
        value = recognition_metadata({"sections": [{"type": "LYRICS", "metadata": [
            {"title": "Album", "text": "Not an album"}]}],
            "images": {"background": "https://example.org/artist.jpg"}})
        self.assertEqual(value["album"], "")
        self.assertEqual(value["art_url"], "")

    def test_recognition_with_missing_or_malformed_optional_metadata(self):
        for value in ({}, {"sections": [None], "images": []},
                      {"sections": 1}, {"sections": [{"metadata": 1}]},
                      {"sections": [{"metadata": [None]}], "album": {}}):
            self.assertEqual(recognition_metadata(value)["album"], "")

    def test_shazam_album_does_not_trigger_a_lastfm_lookup(self):
        fetch = Mock()
        value = AlbumResolver("test-only", fetch).resolve(TRACK, now=1000)
        self.assertEqual(value["album"], "Tsunami Sea")
        fetch.assert_not_called()

    def test_lookup_uses_existing_key_and_caches_by_artist_title(self):
        fetch = Mock(return_value={"album": "Tsunami Sea", "art_url": "https://example.org/fallback.jpg"})
        resolver = AlbumResolver("test-only", fetch)
        track = {"title": "Fata Morgana", "subtitle": "Spiritbox"}
        value = resolver.resolve(track, now=1000)
        resolver.resolve({"title": "  FATA  MORGANA ", "subtitle": "SPIRITBOX"}, now=1001)
        fetch.assert_called_once_with("test-only", "Spiritbox", "Fata Morgana")
        self.assertEqual(value["album_source"], "Last.fm")
        self.assertEqual(value["art_url"], "https://example.org/fallback.jpg")
        resolver.resolve(track, now=87401)
        self.assertEqual(fetch.call_count, 2)

    def test_lookup_failure_is_cached_and_does_not_block_recognition(self):
        fetch = Mock(side_effect=OSError)
        resolver = AlbumResolver("test-only", fetch)
        track = {"title": "Fata Morgana", "subtitle": "Spiritbox"}
        with self.assertLogs("metadata", level="WARNING"):
            value = resolver.resolve(track, now=1000)
            resolver.resolve(track, now=1299)
        self.assertEqual(value["title"], "Fata Morgana")
        self.assertEqual(value["album"], "")
        fetch.assert_called_once()

    def test_lookup_refuses_metadata_for_a_different_track(self):
        payload = {"track": {"name": "Another song", "artist": {"name": "Spiritbox"},
                             "album": {"title": "Wrong album"}}}
        with patch("music_metadata.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
            self.assertEqual(lastfm_track("test-only", "Spiritbox", "Fata Morgana"), {})

    def test_lookup_reads_exact_matching_lastfm_album(self):
        payload = {"track": {"name": "Fata Morgana", "artist": {"name": "Spiritbox"},
                             "album": {"title": "Tsunami Sea", "image": [
                                 {"#text": "https://example.org/cover.jpg"}]}}}
        with patch("music_metadata.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())) as fetch:
            value = lastfm_track("test-only", "Spiritbox", "Fata Morgana")
        self.assertEqual(value["album"], "Tsunami Sea")
        self.assertIn("autocorrect=0", fetch.call_args.args[0].full_url)

    def test_lastfm_placeholder_image_is_ignored(self):
        url = "https://lastfm.freetls.fastly.net/i/u/300x300/2a96cbd8b46e442fc41c2b86b821562f.png"
        self.assertFalse(useful_art(url))
        payload = {"recenttracks": {"track": [{"@attr": {"nowplaying": "true"},
                   "name": "Fata Morgana", "artist": {"#text": "Spiritbox"}, "image": [{"#text": url}]}]}}
        self.assertEqual(parse_now_playing(payload)["art_url"], "")

    def test_fresh_matching_metadata_fills_missing_display_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"metadata.json"
            publish_metadata(path, recognition_metadata(TRACK), now=1000)
            track = {"title": "Fata Morgana", "artist": "Spiritbox", "album": "", "art_url": ""}
            value = enrich_from_vinyl(track, path, now=1100)
            self.assertEqual(value["album"], "Tsunami Sea")
            self.assertEqual(value["art_url"], "https://example.org/album.jpg")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(track["album"], "")

    def test_unrelated_stale_or_broken_metadata_does_not_modify_track(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"metadata.json"
            publish_metadata(path, recognition_metadata(TRACK), now=1000)
            for track, now in (({"title": "Another song", "artist": "Spiritbox"}, 1100),
                               ({"title": "Fata Morgana", "artist": "Another artist"}, 1100),
                               ({"title": "Fata Morgana", "artist": "Spiritbox"}, 1301)):
                self.assertEqual(enrich_from_vinyl(track, path, now=now), track)
            path.write_text("broken json")
            self.assertEqual(enrich_from_vinyl(track, path, now=1100), track)

    def test_existing_lastfm_album_and_cover_have_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"metadata.json"
            publish_metadata(path, recognition_metadata(TRACK), now=1000)
            track = {"title": "Fata Morgana", "artist": "Spiritbox", "album": "Another release", "art_url": ""}
            self.assertEqual(enrich_from_vinyl(track, path, now=1100), track)
            track.update(album="Tsunami Sea", art_url="https://example.org/lastfm.jpg")
            self.assertEqual(enrich_from_vinyl(track, path, now=1100), track)

    def test_provider_combines_account_now_playing_with_matching_vinyl_metadata(self):
        cfg = json.loads((ROOT / "config.example.json").read_text())
        cfg["lastfm"]["api_key"] = "test-only"
        payload = {"recenttracks": {"track": [{"@attr": {"nowplaying": "true"},
                   "name": "Fata Morgana", "artist": {"#text": "Spiritbox"}, "album": {"#text": ""}}]}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publish_metadata(root/"state/vinyl_metadata.json", recognition_metadata(TRACK), now=1000)
            with patch("display_service.ROOT", root), patch("display_service.get_json", return_value=payload), \
                 patch("music_metadata.time.time", return_value=1001):
                track = Providers(cfg).lastfm()
            self.assertEqual(track["album"], "Tsunami Sea")
            self.assertEqual(track["art_url"], "https://example.org/album.jpg")


class SubmissionTests(unittest.TestCase):
    def test_album_sent_in_both_operations_without_duplicate_scrobble(self):
        spec = importlib.util.spec_from_file_location(
            "orchestrator_metadata_test", Path(__file__).with_name("orchestrator_display_ready.py"))
        module = importlib.util.module_from_spec(spec)
        modules = {"pylast": types.SimpleNamespace(md5=lambda value: "test-only"),
                   "shazamio": types.SimpleNamespace(Shazam=Mock())}
        with patch.dict("sys.modules", modules):
            spec.loader.exec_module(module)
        network = Mock()
        shazam = types.SimpleNamespace(recognize=AsyncMock(return_value={"track": TRACK}))
        class EndLoop(BaseException):
            pass
        count = 0
        async def sleep(seconds):
            nonlocal count
            count += 1
            if count == 3:
                raise EndLoop()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(module, "ROOT", Path(directory)), \
             patch.object(module, "connect_lastfm", return_value=network), \
             patch.object(module, "Shazam", return_value=shazam), \
             patch.object(module, "capture_sample", return_value=True), \
             patch.object(module, "get_wav_rms_db", return_value=-22.9), \
             patch.object(module.time, "time", return_value=1000), \
             patch.object(module.asyncio, "sleep", new=sleep):
            with self.assertRaises(EndLoop):
                asyncio.run(module.main())
            saved = json.loads((Path(directory)/"state/vinyl_metadata.json").read_text())
        self.assertEqual(saved["album"], "Tsunami Sea")
        self.assertEqual(network.update_now_playing.call_count, 3)
        network.update_now_playing.assert_called_with(artist="Spiritbox", title="Fata Morgana", album="Tsunami Sea")
        network.scrobble.assert_called_once_with(artist="Spiritbox", title="Fata Morgana", timestamp=1000, album="Tsunami Sea")


if __name__ == "__main__":
    unittest.main()
