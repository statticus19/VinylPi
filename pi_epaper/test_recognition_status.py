"""No-match signal and idle-only display checks; no network or audio hardware."""
import asyncio
import importlib.util
import json
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from PIL import ImageChops, ImageDraw
from dashboard import font, render_dashboard
from display_service import DisplayWorker, FrameStore, Playback, ROOT
from music_metadata import publish_metadata, recognition_status


class SignalTests(unittest.TestCase):
    def test_fresh_status_and_expiration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"status.json"
            self.assertEqual(recognition_status(path, 1000), "unknown")
            publish_metadata(path, {"status": "no_match"}, now=1000)
            self.assertEqual(recognition_status(path, 1000), "no_match")
            self.assertEqual(recognition_status(path, 1180), "no_match")
            self.assertEqual(recognition_status(path, 1181), "unknown")
            self.assertEqual(recognition_status(path, 999), "unknown")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_broken_or_unexpected_status_cannot_show_badge(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"status.json"
            for value in ("broken", "[]", '{"at":1000}',
                          '{"at":1000,"status":"other"}',
                          '{"at":"NaN","status":"no_match"}',
                          '{"at":1000,"status":[]}'):
                path.write_text(value)
                self.assertEqual(recognition_status(path, 1000), "unknown")


class RenderingTests(unittest.TestCase):
    def test_badge_changes_only_the_date_bar(self):
        now = datetime(2026, 10, 5, 21, 30, tzinfo=ZoneInfo("America/New_York"))
        normal = render_dashboard(None, [], now)
        badge = render_dashboard(None, [], now, no_match=True)
        diff = ImageChops.difference(normal.convert("L"), badge.convert("L")).getbbox()
        self.assertIsNotNone(diff)
        self.assertLessEqual(diff[3], 34)
        self.assertEqual(badge.crop((0, 34, 400, 300)).tobytes(),
                         normal.crop((0, 34, 400, 300)).tobytes())
        self.assertEqual(badge.mode, "1")

    def test_label_fits_between_date_and_time_throughout_year(self):
        draw = ImageDraw.Draw(render_dashboard(None, [], datetime(2026, 1, 1)))
        label_width = draw.textlength("NO MATCH", font=font(13, True))+34
        start = datetime(2026, 1, 1)
        for day in range(366):
            now = start+timedelta(days=day)
            left = 11+draw.textlength(now.strftime("%a %b %d"), font=font(18, True))+10
            right = 389-draw.textlength("12:55 PM", font=font(18))-10
            self.assertLessEqual(label_width, right-left)

    def test_no_match_never_overrides_playing_or_ten_minute_wait(self):
        cfg = json.loads((ROOT / "config.example.json").read_text())
        track = {"artist": "Thrice", "title": "Hurricane"}
        worker = DisplayWorker(cfg, FrameStore())
        worker.playback = Playback()
        worker.playback.observe(track, 100)
        with patch("display_service.recognition_status", return_value="no_match"), \
             patch("display_service.render_dashboard", wraps=render_dashboard) as idle, \
             patch.object(worker.providers.corner, "card", return_value=None):
            worker.render(200)
            self.assertEqual(worker.store.status["mode"], "playing")
            idle.assert_not_called()
            worker.playback.observe(None, 300)
            worker.render(899)
            self.assertEqual(worker.store.status["mode"], "recent")
            idle.assert_not_called()
            worker.render(900)
            self.assertEqual(worker.store.status["mode"], "idle")
            self.assertTrue(idle.call_args.kwargs["no_match"])
            self.assertEqual(worker.playback.data["stopped_at"], 300)

    def test_worker_clears_idle_badge_after_silence_or_stale_result(self):
        cfg = json.loads((ROOT / "config.example.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/"state/vinyl_recognition.json"
            with patch("display_service.ROOT", root):
                worker = DisplayWorker(cfg, FrameStore())
                with patch.object(worker.providers.corner, "card", return_value=None), \
                     patch("display_service.render_dashboard", wraps=render_dashboard) as idle:
                    publish_metadata(path, {"status": "no_match"}, now=1000)
                    worker.render(1001)
                    self.assertTrue(idle.call_args.kwargs["no_match"])
                    publish_metadata(path, {"status": "silent"}, now=1002)
                    worker.render(1003)
                    self.assertFalse(idle.call_args.kwargs["no_match"])
                    publish_metadata(path, {"status": "no_match"}, now=1004)
                    worker.render(1185)
                    self.assertFalse(idle.call_args.kwargs["no_match"])


class AudioTests(unittest.TestCase):
    def load_module(self):
        spec = importlib.util.spec_from_file_location(
            "audio_status_test", Path(__file__).with_name("orchestrator_display_ready.py"))
        module = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {
                "pylast": types.SimpleNamespace(md5=lambda value: "test-only"),
                "shazamio": types.SimpleNamespace(Shazam=Mock())}):
            spec.loader.exec_module(module)
        return module

    def cycle(self, response=None, exception=None, captured=True, volume=-22.9):
        module = self.load_module()
        shazam = types.SimpleNamespace(recognize=AsyncMock(return_value=response, side_effect=exception))
        class EndLoop(BaseException):
            pass
        async def sleep(seconds):
            raise EndLoop()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(module, "ROOT", Path(directory)), \
             patch.object(module, "connect_lastfm", return_value=None), \
             patch.object(module, "Shazam", return_value=shazam), \
             patch.object(module, "capture_sample", return_value=captured), \
             patch.object(module, "get_wav_rms_db", return_value=volume), \
             patch.object(module, "API_KEY", ""), \
             patch.object(module.asyncio, "sleep", new=sleep):
            with self.assertRaises(EndLoop):
                asyncio.run(module.main())
            status = recognition_status(Path(directory)/"state/vinyl_recognition.json")
        return status, shazam.recognize.await_count

    def test_actual_no_match_sets_signal(self):
        self.assertEqual(self.cycle(response={"matches": []}), ("no_match", 1))

    def test_quiet_sample_never_calls_shazam(self):
        self.assertEqual(self.cycle(volume=-78.5), ("silent", 0))

    def test_capture_failure_never_calls_shazam(self):
        self.assertEqual(self.cycle(captured=False), ("error", 0))

    def test_shazam_exception_is_not_no_match(self):
        self.assertEqual(self.cycle(exception=OSError("offline")), ("error", 1))

    def test_malformed_or_error_response_is_not_no_match(self):
        for response in (None, [], {"track": []}, {"error": "unavailable"}):
            self.assertEqual(self.cycle(response=response), ("error", 1))

    def test_incomplete_match_does_not_claim_no_match(self):
        self.assertEqual(self.cycle(response={"track": {"title": "Only title"}}), ("unknown", 1))

    def test_successful_match_clears_badge(self):
        self.assertEqual(self.cycle(response={"track": {"title": "Hurricane", "subtitle": "Thrice"}}),
                         ("matched", 1))


if __name__ == "__main__":
    unittest.main()
