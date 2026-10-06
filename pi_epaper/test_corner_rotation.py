"""Offline checks of time windows, source parsing, rotation, caching and layout."""
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw
from corner_rotation import CornerRotation, next_uk_game, parse_uk_schedule, sun_card, network_from_logo
from corner_render import render_corner, CORNER_POSITION, CORNER_SIZE
from dashboard import render_dashboard
from display_service import ROOT

ZONE = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 7, 14, 30, tzinfo=ZONE)


def game(start, **values):
    return {"start": start.isoformat(), "opponent": "Kansas", "venue": "United Center",
            "city": "Chicago", "site": "Neutral", **values}


class ScheduleTests(unittest.TestCase):
    def test_nearest_game_and_precise_seven_day_cutoff(self):
        rows = [game(NOW-timedelta(minutes=1)), game(NOW+timedelta(days=7)),
                game(NOW+timedelta(days=6)), game(NOW+timedelta(days=2))]
        self.assertEqual(next_uk_game(rows, NOW)["start"], rows[-1]["start"])
        self.assertIsNone(next_uk_game(rows[:2], NOW))
        self.assertIsNone(next_uk_game([], NOW))

    def test_tbd_today_not_dropped_at_midnight_and_timezone_conversion(self):
        self.assertTrue(next_uk_game([game(NOW.replace(hour=0, minute=0), time_tbd=True)], NOW))
        self.assertIsNone(next_uk_game([game(NOW-timedelta(days=1), time_tbd=True)], NOW))
        future = game(NOW.astimezone(ZoneInfo("UTC"))+timedelta(hours=1))
        self.assertEqual(next_uk_game([future], NOW), future)

    def test_official_structured_schedule_parsing_tbd_exhibitions_and_cancelled(self):
        events = [
            {"@type": "Event", "name": "vs. Big Blue Madness (EXH)", "startDate": "2026-10-02T19:00"},
            {"@type": "Event", "name": "vs. Little Rock (EXH)", "startDate": "2026-10-16",
             "location": {"name": "Rupp Arena at Central Bank Center | Lexington, Ky."}},
            {"@type": "Event", "name": "at Virginia", "startDate": "2026-12-02T19:15",
             "location": {"name": "John Paul Jones Arena | Charlottesville, Va."}},
            {"@type": "Event", "name": "vs. Cancelled game", "startDate": "2026-11-01",
             "eventStatus": "https://schema.org/EventCancelled"}]
        html = '<script type="application/ld+json">'+json.dumps({"@graph": events})+'</script>'
        rows = parse_uk_schedule(html)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["opponent"], "Little Rock (EXH)")
        self.assertTrue(rows[0]["time_tbd"])
        self.assertEqual(rows[0]["site"], "Home")
        self.assertEqual(rows[1]["site"], "Away")
        self.assertEqual(rows[1]["start"], "2026-12-02T19:15:00-05:00")
        with self.assertRaises(ValueError):
            parse_uk_schedule("<html>Temporarily unavailable</html>")

    def test_tv_logos_are_matched_to_correct_game_without_guessing_station(self):
        import base64
        def card(title, day, src):
            return '<div class="schedule__item schedule-item neutral"><div><time>Tue. Nov '+day+'</time>' \
                   '<h3>'+title+'</h3><img src="'+src+'"></div></div>'
        source = 'https://storage.googleapis.com/ukathletics-com/2026/05/example-espn_wordmark.svg_.png'
        encoded = base64.urlsafe_b64encode(source.encode()).decode().rstrip('=')
        proxy = 'https://ukathletics.com/imgproxy/hash/fit/100/100/ce/0/'+encoded+'.png'
        events = [{"@type": "Event", "name": "vs. Kansas", "startDate": "2026-11-10T21:30"},
                  {"@type": "Event", "name": "vs. Other school", "startDate": "2026-11-13"}]
        html = '<script type="application/ld+json">'+json.dumps(events)+'</script>'
        html += card('Kansas', '10', proxy)+card('Other school', '13', 'https://example.test/cbs-sports-classic.png')
        rows = parse_uk_schedule(html)
        self.assertEqual(rows[0]['tv'], 'ESPN')
        self.assertEqual(rows[1]['tv'], '')
        self.assertEqual(network_from_logo('https://example.test/espn2_wordmark.png'), 'ESPN2')
        self.assertEqual(network_from_logo('https://example.test/espn_plus.png'), 'ESPN+')
        self.assertEqual(network_from_logo('https://example.test/school_logo.png'), '')


class RotationTests(unittest.TestCase):
    def rotation(self, root=ROOT, **options):
        return CornerRotation({"timezone": "America/New_York", "lastfm": {
            "username": "test-only", "api_key": "test-only"}, "corner": options}, root, Mock())

    def test_rotation_visits_every_eligible_card_and_keeps_pokemon_stable(self):
        rotation = self.rotation()
        epoch = NOW.timestamp()
        rotation.cache = {"uk": {"games": [game(NOW+timedelta(days=1))], "fetched": epoch},
                          "stats": {"day": NOW.date().isoformat(), "fetched": epoch,
                                    "tracks_today": 37, "artist": "Thrice", "artist_plays": 42}}
        weather = {"sunrise": NOW.replace(hour=7).isoformat(), "sunset": NOW.replace(hour=18).isoformat()}
        cards = [rotation.card(epoch+i*900, weather) for i in range(4)]
        self.assertEqual({c["kind"] for c in cards}, {"uk", "stats", "sun", "pokemon"})
        pokemon_time = next(epoch+i*900 for i, c in enumerate(cards) if c["kind"] == "pokemon")
        first = rotation.card(pokemon_time, weather)
        second = rotation.card(pokemon_time+15, weather)
        self.assertEqual(first["number"], second["number"])
        next_visit = rotation.card(pokemon_time+3600, weather)
        self.assertNotEqual(first["number"], next_visit["number"])

    def test_no_next_week_game_and_stale_stats_are_omitted(self):
        rotation = self.rotation(sun=False, pokemon=False)
        epoch = NOW.timestamp()
        rotation.cache = {"uk": {"games": [game(NOW+timedelta(days=8))], "fetched": epoch},
                          "stats": {"day": (NOW-timedelta(days=1)).date().isoformat(), "fetched": epoch}}
        self.assertIsNone(rotation.card(epoch, None))
        rotation.cache["uk"] = {"games": [game(NOW+timedelta(days=1))], "fetched": epoch-9*86400-1}
        self.assertIsNone(rotation.card(epoch, None))
        self.assertIsNone(self.rotation(enabled=False).card(epoch, None))

    def test_sunset_switches_to_tomorrows_sunrise(self):
        weather = {"sunrise": NOW.replace(hour=7).isoformat(), "sunset": NOW.replace(hour=17).isoformat(),
                   "tomorrow": {"sunrise": (NOW+timedelta(days=1)).replace(hour=7).isoformat(),
                                "sunset": (NOW+timedelta(days=1)).replace(hour=17).isoformat()}}
        self.assertEqual(sun_card(weather, NOW)["next"], "Sunset")
        after = sun_card(weather, NOW.replace(hour=20))
        self.assertEqual(after["next"], "Sunrise")
        self.assertEqual(after["day"], "Tomorrow")
        self.assertIsNone(sun_card(None, NOW))

    def test_lastfm_stats_local_midnight_across_dst_and_seven_day_artist(self):
        rotation = self.rotation()
        rotation.get_json.side_effect = [
            {"recenttracks": {"@attr": {"total": "37"}, "track": [{"@attr": {"nowplaying": "true"}}]}},
            {"topartists": {"artist": [{"name": "Thrice", "playcount": "42"}]}}]
        now = datetime(2026, 11, 1, 16, 30, tzinfo=ZONE)
        with patch.dict("os.environ", {}, clear=True):
            stats = rotation.fetch_stats(now.timestamp())
        params = rotation.get_json.call_args_list[0].args[1]
        self.assertEqual(params["from"], int(datetime(2026, 11, 1, 0, 0, tzinfo=ZONE).timestamp()))
        self.assertEqual(rotation.get_json.call_args_list[1].args[1]["period"], "7day")
        self.assertEqual(stats["tracks_today"], 37)
        self.assertEqual(stats["artist"], "Thrice")

    def test_failure_backoff_does_not_repeat_each_tick_after_midnight(self):
        with tempfile.TemporaryDirectory() as root:
            rotation = self.rotation(root, uk=False, sun=False, pokemon=False)
            epoch = NOW.timestamp()
            rotation.cache["stats"] = {"day": "2026-11-06"}
            rotation.stats_day = "2026-11-06"
            with patch("corner_rotation.threading.Thread") as thread:
                rotation.refresh(epoch)
                thread.assert_called_once()
                rotation.results.put(("stats", None))
                rotation.results.put(("done", None))
                rotation.refresh(epoch+15)
                rotation.refresh(epoch+30)
                self.assertEqual(thread.call_count, 1)
                rotation.refresh(epoch+316)
                self.assertEqual(thread.call_count, 2)

    def test_private_cache_survives_restart(self):
        with tempfile.TemporaryDirectory() as root:
            rotation = self.rotation(root, uk=False, stats=False)
            rotation.results.put(("uk", {"games": [], "fetched": NOW.timestamp()}))
            rotation.results.put(("done", None))
            rotation.refresh(NOW.timestamp())
            self.assertEqual(rotation.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.rotation(root).cache, rotation.cache)

    def test_weekly_schedule_cache_survives_restart_without_extra_fetch(self):
        with tempfile.TemporaryDirectory() as root:
            now = NOW.timestamp()
            path = Path(root, "state/corner.json")
            path.parent.mkdir()
            path.write_text(json.dumps({"uk": {"games": [game(NOW+timedelta(days=1))], "fetched": now, "tv_checked": True}}))
            rotation = self.rotation(root, stats=False, sun=False, pokemon=False)
            with patch("corner_rotation.threading.Thread") as thread:
                rotation.refresh(now+86400)
                thread.assert_not_called()
                self.assertEqual(rotation.card(now+86400, None)["kind"], "uk")
                rotation.refresh(now+7*86400)
                thread.assert_called_once()


class CornerLayoutTests(unittest.TestCase):
    def test_all_four_cards_preserve_one_bit_bmp_and_reserved_panel(self):
        cards = [
            {"kind": "uk", **game(NOW+timedelta(days=3)), "logo": str(ROOT / "assets/uk_logo.png")},
            {"kind": "stats", "tracks_today": 37, "artist": "Thrice", "artist_plays": 42},
            {"kind": "sun", "day": "Today", "sunrise": NOW.replace(hour=7), "sunset": NOW.replace(hour=17), "next": "Sunset"},
            {"kind": "pokemon", "number": 25, "name": "Pikachu", "sprite": str(ROOT / "assets/pokemon/025.png")}]
        weather = {"temperature": 72, "unit": "F", "code": 2, "feels": 71, "high": 75, "low": 56}
        event = {"start": (NOW+timedelta(hours=1)).isoformat(), "summary": "Long event title "*20,
                 "calendar": "Work", "meeting": "Teams", "drive_minutes": 18}
        for card in cards:
            image = render_dashboard(weather, [event]*3, NOW, corner=card)
            output = io.BytesIO(); image.save(output, "BMP")
            self.assertEqual(len(output.getvalue()), 15662)
            tile = render_corner(card, NOW).convert("1", dither=Image.Dither.NONE)
            x, y = CORNER_POSITION
            width, height = CORNER_SIZE
            self.assertEqual(image.crop((x, y, x+width, y+height)).tobytes(), tile.tobytes())

    def test_crowded_calendar_keeps_date_meeting_and_drive_time_readable(self):
        now = datetime(2026, 12, 30, 14, 30, tzinfo=ZONE)
        event = {"start": "2026-12-31T23:59:00-05:00", "summary": "Long appointment title "*12,
                 "calendar": "Work", "meeting": "Zoom/Teams", "drive_minutes": 128}
        text_calls = []
        original = ImageDraw.ImageDraw.text
        def record(draw, xy, text, *args, **kwargs):
            if xy[0] < CORNER_POSITION[0] and xy[1] >= 171:
                text_calls.append((xy, text, kwargs['font'].size))
            return original(draw, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', new=record):
            render_dashboard(None, [event]*3, now, corner={"kind": "stats", "tracks_today": 37,
                             "artist": "Thrice", "artist_plays": 42})
        texts = [call[1] for call in text_calls]
        self.assertIn('Thu 12/31 11:59 PM', texts)
        self.assertIn('Work · Zoom/Teams', texts)
        self.assertIn('~128 min', texts)
        self.assertTrue(any(text.endswith('…') for text in texts))
        self.assertTrue(all(size == 11 for xy, text, size in text_calls if size != 13))
        self.assertTrue(all(xy[1] <= 284 for xy, text, size in text_calls))


if __name__ == "__main__":
    unittest.main()
