"""Offline tests: mode transitions, API interpretation, BMP format, HTTP delivery."""
import io
import json
import threading
import sys
import types
import unittest
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch, Mock
from zoneinfo import ZoneInfo

from PIL import Image
from display_service import (Playback, parse_now_playing, render_music, render_dashboard,
                             FrameStore, handler_for, DisplayWorker, Providers, ROOT)

TRACK = {"title": "Beyond the Pines", "artist": "Thrice", "album": "Palms"}


class PlaybackTests(unittest.TestCase):
    def test_exact_ten_minute_transition_and_resume(self):
        p = Playback()
        self.assertEqual(p.mode(0), "idle")
        p.observe(TRACK, 100)
        p.observe(TRACK, 200)
        self.assertEqual(p.mode(1000), "playing")
        p.observe(None, 1000)
        p.observe(None, 1200)
        self.assertEqual(p.mode(1599), "recent")
        self.assertEqual(p.mode(1600), "idle")
        p.observe(TRACK, 1601)
        self.assertEqual(p.mode(1601), "playing")

    def test_restart_preserves_stop_time(self):
        p = Playback()
        p.observe(TRACK, 100)
        p.observe(None, 120)
        restarted = Playback(json.loads(json.dumps(p.data)))
        self.assertEqual(restarted.mode(719), "recent")
        self.assertEqual(restarted.mode(720), "idle")

    def test_only_explicit_nowplaying_is_active(self):
        past = {"recenttracks": {"track": [{"name": "Old song", "date": {"uts": "123"}}]}}
        self.assertIsNone(parse_now_playing(past))
        active = {"recenttracks": {"track": {"name": "New song", "artist": {"#text": "Artist"},
                    "@attr": {"nowplaying": "true"}, "image": [{"#text": "https://example.org/art"}]}}}
        self.assertEqual(parse_now_playing(active)["artist"], "Artist")
        self.assertIsNone(parse_now_playing({"recenttracks": {"track": []}}))
        with self.assertRaises(ValueError):
            parse_now_playing({"error": 10})

    def test_api_outage_does_not_start_idle_timer(self):
        cfg = json.loads((ROOT / "config.example.json").read_text())
        worker = DisplayWorker(cfg, FrameStore())
        worker.playback = Playback()
        worker.playback.observe(TRACK, 100)
        with patch.object(worker.providers, "lastfm", side_effect=OSError("offline")), \
             patch.object(worker.providers, "refresh_weather"), \
             patch.object(worker, "render"):
            worker.tick()
        self.assertTrue(worker.music_error)
        self.assertTrue(worker.playback.data["playing"])
        self.assertIsNone(worker.playback.data["stopped_at"])

    def test_weather_refresh_runs_while_music_is_active(self):
        cfg = json.loads((ROOT / "config.example.json").read_text())
        worker = DisplayWorker(cfg, FrameStore())
        with patch.object(worker.providers, "lastfm", return_value=TRACK), \
             patch.object(worker.providers, "refresh_weather") as weather, \
             patch.object(worker.providers, "refresh_idle") as idle, \
             patch("display_service.atomic_write"), patch.object(worker, "render"):
            worker.tick()
        weather.assert_called_once()
        idle.assert_not_called()


class DeliveryTests(unittest.TestCase):
    def test_music_local_date_header_and_actual_or_fallback_artwork(self):
        now = datetime(2026, 12, 31, 23, 55, tzinfo=ZoneInfo("America/New_York"))
        fallback = render_music(TRACK, now=now)
        idle = render_dashboard(None, [], now)
        self.assertEqual(fallback.crop((0, 0, 400, 34)).tobytes(),
                         idle.crop((0, 0, 400, 34)).tobytes())
        cover = Image.new("L", (180, 180), 0)
        actual = render_music(TRACK, art=cover, now=now)
        self.assertEqual(actual.crop((14, 58, 194, 238)).tobytes(),
                         cover.convert("1").tobytes())
        self.assertNotEqual(fallback.crop((14, 58, 194, 238)).tobytes(),
                            actual.crop((14, 58, 194, 238)).tobytes())

    def test_bmp_is_400_by_300_uncompressed_one_bit(self):
        image = render_music(TRACK)
        store = FrameStore()
        store.publish(image, {"mode": "playing"})
        bmp = store.bmp
        self.assertEqual(len(bmp), 15662)
        self.assertEqual(int.from_bytes(bmp[28:30], "little"), 1)
        self.assertEqual(int.from_bytes(bmp[30:34], "little"), 0)
        decoded = Image.open(io.BytesIO(bmp))
        self.assertEqual(decoded.size, (400, 300))
        self.assertEqual(decoded.mode, "1")
        self.assertEqual(decoded.tobytes(), image.tobytes())

    def test_http_etag_and_fixed_routes(self):
        store = FrameStore()
        store.publish(render_music(TRACK), {"mode": "playing"})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:%s" % server.server_port
        try:
            with urlopen(base + "/display.bmp", timeout=2) as response:
                old = response.read()
                tag = response.headers["ETag"]
                self.assertEqual(int(response.headers["Content-Length"]), len(old))
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(base + "/display.bmp", headers={"If-None-Match": tag}), timeout=2)
            self.assertEqual(caught.exception.code, 304)
            store.publish(render_music({**TRACK, "title": "New Song"}), {"mode": "playing"})
            with urlopen(Request(base + "/display.bmp", headers={"If-None-Match": tag}), timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertNotEqual(response.headers["ETag"], tag)
            with urlopen(base + "/preview.png", timeout=2) as response:
                self.assertEqual(Image.open(io.BytesIO(response.read())).size, (400, 300))
            with self.assertRaises(HTTPError) as caught:
                urlopen(base + "/token.json", timeout=2)
            self.assertEqual(caught.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_missing_data_and_long_text_render(self):
        image = render_dashboard(None, [], datetime.now(timezone.utc), "Not configured", "Not authorized")
        self.assertEqual(image.size, (400, 300))
        self.assertEqual(render_music({"title": "Long title " * 50, "artist": "Artist " * 20}).mode, "1")


class ProviderTests(unittest.TestCase):
    def test_weather_refresh_interval_and_retry_preserve_cached_conditions(self):
        cfg = json.loads((ROOT / "config.example.json").read_text())
        p = Providers(cfg)
        start = datetime(2026, 10, 5, 14, 30, tzinfo=p.zone).timestamp()
        def success():
            p.weather = {"date": "2026-10-05", "temperature": 72}
        with patch.object(p, "fetch_weather", side_effect=success) as fetch:
            p.refresh_weather(start)
            p.refresh_weather(start+899)
        fetch.assert_called_once()
        with patch.object(p, "fetch_weather", side_effect=OSError) as fetch:
            p.refresh_weather(start+900)
            p.refresh_weather(start+959)
            self.assertEqual(fetch.call_count, 1)
            p.refresh_weather(start+960)
            self.assertEqual(fetch.call_count, 2)
        self.assertEqual(p.weather["temperature"], 72)
        self.assertTrue(p.weather_note)

    def test_excluded_events_do_not_consume_slots_or_hide_later_pages(self):
        cfg = json.loads((ROOT / "config.example.json").read_text())
        cfg["google"]["calendars"] = [{"id": "primary", "label": "Personal"}]
        p = Providers(cfg)
        p.credentials = types.SimpleNamespace(valid=True, token="test-only")
        modules = {name: types.ModuleType(name) for name in (
            "google", "google.oauth2", "google.oauth2.credentials", "google.auth",
            "google.auth.transport", "google.auth.transport.requests")}
        modules["google.oauth2.credentials"].Credentials = Mock()
        modules["google.auth.transport.requests"].Request = Mock()
        def event(title):
            return {"summary": title, "start": {"dateTime": "2026-10-06T19:00:00Z"}}
        called = []
        def fetch(url, params, headers):
            token = params.get("pageToken")
            called.append(token)
            if token is None:
                return {"items": [event("Out of Office")]*10, "nextPageToken": "page2"}
            if token == "page2":
                return {"items": [event("out of office"), event("Appointment"), event("Meeting")], "nextPageToken": "page3"}
            return {"items": [event("Lunch")]}
        with patch.dict(sys.modules, modules), patch("display_service.get_json", side_effect=fetch):
            p.fetch_calendar()
        self.assertEqual([e["summary"] for e in p.events], ["Appointment", "Meeting", "Lunch"])
        self.assertEqual(called, [None, "page2", "page3"])

    def test_weather_fahrenheit(self):
        cfg = json.loads((ROOT / "config.example.json").read_text())
        cfg["weather"].update(latitude=40, longitude=-74)
        p = Providers(cfg)
        payload = {"current": {"temperature_2m": 72, "apparent_temperature": 71, "weather_code": 2,
                               "is_day": 1, "uv_index": 4.2},
                   "daily": {"time": ["2026-10-05", "2026-10-06"], "weather_code": [2, 61],
                             "temperature_2m_max": [75, 68], "temperature_2m_min": [56, 52],
                             "precipitation_probability_max": [65, 80], "precipitation_sum": [0.12, 0.24]}}
        with patch("display_service.get_json", return_value=payload) as request:
            p.fetch_weather()
        self.assertEqual(request.call_args.args[1]["temperature_unit"], "fahrenheit")
        self.assertEqual(p.weather["unit"], "F")
        self.assertEqual(p.weather["high"], 75)
        self.assertEqual(p.weather["tomorrow"]["high"], 68)
        self.assertEqual(p.weather["precip_probability"], 65)
        self.assertEqual(request.call_args.args[1]["forecast_days"], 2)
        self.assertIn("uv_index", request.call_args.args[1]["current"].split(","))
        self.assertEqual(p.weather["uv_index"], 4.2)
        payload["current"].pop("uv_index")
        with patch("display_service.get_json", return_value=payload):
            p.fetch_weather()
        self.assertIsNone(p.weather["uv_index"])

    def test_daily_snow_indicator_even_when_current_conditions_are_clear(self):
        from dashboard import snowflake_icon, drop_icon
        cfg = json.loads((ROOT / "config.example.json").read_text())
        provider = Providers(cfg)
        payload = {"current": {"temperature_2m": 29, "apparent_temperature": 23,
                               "weather_code": 0, "is_day": 1, "uv_index": 1.2},
                   "daily": {"time": ["2026-10-05", "2026-10-06"], "weather_code": [73, 71],
                             "temperature_2m_max": [32, 30], "temperature_2m_min": [19, 17],
                             "precipitation_probability_max": [85, 70], "precipitation_sum": [0.18, 0.10]}}
        with patch("display_service.get_json", return_value=payload):
            provider.fetch_weather()
        now = datetime(2026, 10, 5, 14, 30, tzinfo=ZoneInfo("America/New_York"))
        snow = render_dashboard(provider.weather, [], now)
        snow_pixels = snow.crop((12, 105, 27, 120)).tobytes()
        self.assertEqual(provider.weather["code"], 0)
        self.assertEqual(snow_pixels, snowflake_icon(15).convert("1", dither=Image.Dither.NONE).tobytes())
        for daily_code in (61, 66, 0, None):
            rain = render_dashboard({**provider.weather, "precip_code": daily_code}, [], now)
            self.assertEqual(rain.crop((12, 105, 27, 120)).tobytes(),
                             drop_icon(15).convert("1", dither=Image.Dither.NONE).tobytes())
            self.assertEqual(snow.crop((0, 34, 400, 85)).tobytes(), rain.crop((0, 34, 400, 85)).tobytes())

    def test_calendar_merge_all_day_timezones_and_refresh_timeout(self):
        cfg = json.loads((ROOT / "config.example.json").read_text())
        cfg["google"]["calendars"] = [{"id": "primary", "label": "Personal"},
                                      {"id": "shared", "label": "Shared"}]
        p = Providers(cfg)
        transport = Mock()
        creds = types.SimpleNamespace(valid=False, token="test-only", to_json=lambda: "{}")
        def refresh(request):
            request("https://example.invalid", timeout=120)
            creds.valid = True
        creds.refresh = refresh
        p.credentials = creds
        modules = {name: types.ModuleType(name) for name in (
            "google", "google.oauth2", "google.oauth2.credentials", "google.auth",
            "google.auth.transport", "google.auth.transport.requests")}
        modules["google.oauth2.credentials"].Credentials = Mock()
        modules["google.auth.transport.requests"].Request = Mock(return_value=transport)
        def fetch(url, params, headers):
            self.assertEqual(params["singleEvents"], "true")
            if "primary" in url:
                return {"items": [{"summary": "Late", "location": "202 McCrae Ln, Covington, KY 41011",
                                   "start": {"dateTime": "2026-10-06T19:00:00Z"}},
                                  {"summary": "Out of Office", "start": {"date": "2026-10-04"}},
                                  {"summary": "Cancelled", "status": "cancelled", "start": {"date": "2026-10-04"}}]}
            return {"items": [{"summary": "All day", "start": {"date": "2026-10-05"}},
                              {"summary": "Early", "description": '<a href="https://teams.microsoft.com/l/meetup-join/example">Join</a>',
                               "start": {"dateTime": "2026-10-05T13:00:00Z"}}]}
        with patch.dict(sys.modules, modules), patch("display_service.get_json", side_effect=fetch), \
             patch("display_service.atomic_write"):
            p.fetch_calendar()
        self.assertEqual([e["summary"] for e in p.events], ["All day", "Early", "Late"])
        self.assertIn("9:00 AM", p.events[1]["when"])
        self.assertEqual(p.events[1]["calendar"], "Shared")
        self.assertEqual(p.events[1]["meeting"], "Teams")
        self.assertTrue(p.events[2]["physical_address"])
        self.assertEqual(transport.call_args.kwargs["timeout"], 12)

    def test_failed_dashboard_refresh_preserves_cached_data(self):
        cfg = json.loads((ROOT / "config.example.json").read_text())
        p = Providers(cfg)
        p.weather = {"temperature": 72}
        p.events = [{"summary": "Cached event"}]
        with patch.object(p, "fetch_weather", side_effect=OSError), \
             patch.object(p, "fetch_calendar", side_effect=OSError), patch.object(p.corner, "refresh"):
            p.refresh_idle(100)
        self.assertEqual(p.weather["temperature"], 72)
        self.assertEqual(p.events[0]["summary"], "Cached event")
        self.assertTrue(p.weather_note)
        self.assertTrue(p.calendar_note)
        self.assertEqual(p.next_calendar, 160)


if __name__ == "__main__":
    unittest.main()
