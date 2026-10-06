"""Offline regression checks for continuous playback, restarts and process locks."""
import asyncio
import importlib.util
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from scrobble_guard import ScrobbleGuard, single_instance


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "state.json"
        self.guard = ScrobbleGuard(self.path, now=1000)

    def first(self):
        self.assertEqual(self.guard.claim("Thrice", "Hurricane", 1000), 1000)
        self.guard.result(True)

    def test_repeated_identification_is_not_another_scrobble(self):
        self.first()
        for now in (1072, 1144, 1216):
            self.assertIsNone(self.guard.claim("Thrice", "Hurricane", now))

    def test_case_and_extra_spaces_do_not_create_a_new_track(self):
        self.first()
        self.assertIsNone(self.guard.claim(" THRICE ", " hurricane  ", 1072))

    def test_restart_preserves_duplicate_protection(self):
        self.first()
        restarted = ScrobbleGuard(self.path, now=1072)
        self.assertIsNone(restarted.claim("Thrice", "Hurricane", 1072))

    def test_quiet_passage_and_restart_do_not_reset_track(self):
        self.first()
        self.assertFalse(self.guard.silence(1050))
        self.assertFalse(self.guard.silence(1090))
        restarted = ScrobbleGuard(self.path, now=1100)
        self.assertIsNone(restarted.claim("Thrice", "Hurricane", 1100))

    def test_sustained_silence_allows_a_new_play_of_the_same_song(self):
        self.first()
        self.assertFalse(self.guard.silence(1050))
        self.assertFalse(self.guard.silence(1139))
        self.assertTrue(self.guard.silence(1140))
        self.assertEqual(self.guard.claim("Thrice", "Hurricane", 1150), 1150)

    def test_no_match_does_not_forget_the_scrobble(self):
        self.first()
        self.guard.audio(1072)
        self.assertIsNone(self.guard.claim("Thrice", "Hurricane", 1144))

    def test_one_wrong_match_then_original_track_does_not_duplicate(self):
        self.first()
        self.assertIsNone(self.guard.claim("Other artist", "False match", 1072))
        self.assertIsNone(self.guard.claim("Thrice", "Hurricane", 1144))

    def test_track_change_needs_two_matches(self):
        self.first()
        self.assertIsNone(self.guard.claim("Thrice", "The Window", 1072))
        self.assertEqual(self.guard.claim("Thrice", "The Window", 1144), 1144)
        self.assertIsNone(self.guard.claim("Thrice", "The Window", 1216))

    def test_uncertain_submission_is_not_resent_on_restart(self):
        self.assertEqual(self.guard.claim("Thrice", "Hurricane", 1000), 1000)
        self.guard.result(False)
        restarted = ScrobbleGuard(self.path, now=1072)
        self.assertIsNone(restarted.claim("Thrice", "Hurricane", 1072))

    def test_crash_between_claim_and_response_does_not_resubmit(self):
        self.assertEqual(self.guard.claim("Thrice", "Hurricane", 1000), 1000)
        restarted = ScrobbleGuard(self.path, now=1072)
        self.assertIsNone(restarted.claim("Thrice", "Hurricane", 1072))

    def test_long_unmonitored_gap_allows_new_session(self):
        self.first()
        restarted = ScrobbleGuard(self.path, now=1901)
        self.assertEqual(restarted.claim("Thrice", "Hurricane", 1901), 1901)

    def test_invalid_state_and_failed_state_write_do_not_allow_submission(self):
        self.path.write_text("broken json")
        with self.assertRaises(ValueError):
            ScrobbleGuard(self.path, now=1100)
        with patch.object(self.guard, "save", side_effect=OSError):
            with self.assertRaises(OSError):
                self.guard.claim("Thrice", "Hurricane", 1100)

    def test_state_is_private(self):
        self.first()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_second_process_lock_refused_then_released(self):
        path = self.path.with_suffix(".lock")
        with single_instance(path):
            with self.assertRaises(RuntimeError):
                with single_instance(path):
                    self.fail("Second instance acquired the same lock")
        with single_instance(path):
            pass


class LoopTests(unittest.TestCase):
    def test_now_playing_renews_without_duplicate_scrobbles_even_after_restart(self):
        spec = importlib.util.spec_from_file_location(
            "orchestrator_under_test", Path(__file__).with_name("orchestrator_display_ready.py"))
        module = importlib.util.module_from_spec(spec)
        modules = {"pylast": types.SimpleNamespace(md5=lambda value: "test-only"),
                   "shazamio": types.SimpleNamespace(Shazam=Mock())}
        with patch.dict("sys.modules", modules):
            spec.loader.exec_module(module)
        network = Mock()
        shazam = types.SimpleNamespace(recognize=AsyncMock(return_value={
            "track": {"title": "Hurricane", "subtitle": "Thrice"}}))
        class EndLoop(BaseException):
            pass
        sleeps = 0
        async def sleep(seconds):
            nonlocal sleeps
            sleeps += 1
            if sleeps % 3 == 0:
                raise EndLoop()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(module, "ROOT", Path(directory)), \
             patch.object(module, "connect_lastfm", return_value=network), \
             patch.object(module, "Shazam", return_value=shazam), \
             patch.object(module, "capture_sample", return_value=True), \
             patch.object(module, "get_wav_rms_db", return_value=-22.9), \
             patch.object(module.time, "time", return_value=1000), \
             patch.object(module.asyncio, "sleep", new=sleep):
            for _ in range(2):
                with self.assertRaises(EndLoop):
                    asyncio.run(module.main())
        self.assertEqual(network.update_now_playing.call_count, 6)
        network.scrobble.assert_called_once_with(artist="Thrice", title="Hurricane", timestamp=1000)


if __name__ == "__main__":
    unittest.main()
