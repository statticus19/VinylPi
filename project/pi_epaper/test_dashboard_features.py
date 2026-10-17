import io
import json
import tempfile
import runpy
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw
from dashboard import render_dashboard, condition_icon, precipitation_text, event_metadata, event_when, uv_text, font
from event_details import meeting_platforms, physical_address, excluded_event
from routing import DriveTimes
from display_service import Providers, ROOT


class EventTests(unittest.TestCase):
    def test_actual_links_conference_metadata_and_safe_links(self):
        self.assertEqual(meeting_platforms({"description": 'Discuss Zoom and Teams'}), "")
        self.assertEqual(meeting_platforms({"location": "https://zoom.us.evil.example/j/123"}), "")
        self.assertEqual(meeting_platforms({"conferenceData": {"entryPoints": [
            {"uri": "https://us02web.zoom.us/j/123"}]}}), "Zoom")
        url = "https://nam01.safelinks.protection.outlook.com/?url=" + quote(
            "https://teams.microsoft.com/l/meetup-join/example", safe="") + "&amp;data=example"
        self.assertEqual(meeting_platforms({"description": '<a href="' + url + '">Join</a>'}), "Teams")
        self.assertEqual(meeting_platforms({"location": "https://zoom.us/j/123",
            "description": "https://teams.cloud.microsoft/meet/example"}), "Zoom/Teams")

    def test_physical_address_location_description_and_hybrid(self):
        address = "202 McCrae Ln, Covington, KY 41011"
        self.assertEqual(physical_address({"location": address}), address)
        self.assertEqual(physical_address({"description": "<p>Location: " + address + "</p>"}), address)
        self.assertEqual(physical_address({"location": "202 McCrae Ln\nCovington, KY 41011"}), address)
        self.assertEqual(physical_address({"location": "Conference room 123"}), "")
        self.assertEqual(physical_address({"location": "https://zoom.us/j/123456789"}), "")
        event = {"location": address, "description": "https://teams.microsoft.com/l/meetup-join/example"}
        self.assertEqual(meeting_platforms(event), "Teams")
        self.assertTrue(physical_address(event))

    def test_exclusions_match_exact_normalized_titles_only(self):
        for title in ("Out of Office", "OUT OF OFFICE", "  out   of office  ",
                      "GROW TRAINIG", "grow trainig", "  GROW   TRAINIG  "):
            self.assertTrue(excluded_event({"summary": title}))
        self.assertFalse(excluded_event({"summary": "Out of Office planning meeting"}))
        self.assertFalse(excluded_event({"summary": "GROW TRAINIG planning meeting"}))
        self.assertFalse(excluded_event({"summary": "Vacation", "eventType": "outOfOffice"}))


class RoutingTests(unittest.TestCase):
    def test_configuration_helper_preserves_user_configuration(self):
        script = ROOT / "configure_routing.py"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.json")
            original = {"port": 5001, "lastfm": {"api_key": "dummy-lastfm-key"},
                        "google": {"calendars": [{"id": "dummy-work-calendar-id"}]}}
            path.write_text(json.dumps(original))
            with patch("display_service.ROOT", Path(directory)), \
                 patch("getpass.getpass", return_value="dummy-routing-key"), patch("builtins.print"):
                runpy.run_path(str(script), run_name="__main__")
            updated = json.loads(path.read_text())
            self.assertEqual(updated["lastfm"], original["lastfm"])
            self.assertEqual(updated["google"], original["google"])
            self.assertEqual(updated["port"], 5001)
            self.assertEqual(updated["routing"]["api_key"], "dummy-routing-key")
            self.assertEqual(json.loads(Path(directory, "config.before-routing.json").read_text()), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def router(self, directory):
        return DriveTimes({"api_key": "test-only", "origin_address": "202 McCrae Ln, Covington, KY 41011",
                           "cache_file": "routes.json"}, directory)

    def test_duration_rounding_cache_reuse_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            router = self.router(directory)
            with patch.object(router, "geocode", side_effect=[[-84.53, 39.06], [-84.51, 39.1]]), \
                 patch("routing.request_json", return_value={"routes": [{"summary": {"duration": 1021}}]}) as api:
                self.assertEqual(router.estimate("123 Main St, Covington, KY 41011")["drive_minutes"], 18)
                self.assertEqual(router.estimate("123 Main St, Covington, KY 41011")["drive_minutes"], 18)
                self.assertEqual(api.call_count, 1)
                self.assertIn("api.heigit.org/openrouteservice", api.call_args.args[0])
                self.assertEqual(api.call_args.kwargs["body"]["coordinates"][0], [-84.53, 39.06])
            restarted = self.router(directory)
            with patch("routing.request_json", side_effect=AssertionError("Should use cache")):
                self.assertEqual(restarted.estimate("123 Main St, Covington, KY 41011")["drive_minutes"], 18)
            self.assertEqual(Path(directory, "routes.json").stat().st_mode & 0o777, 0o600)

    def test_no_api_key_no_request_or_fabricated_estimate(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            router = DriveTimes({}, directory)
            with patch("routing.request_json") as api:
                result = router.estimate("123 Main St")
            self.assertIsNone(result["drive_minutes"])
            self.assertEqual(result["drive_status"], "key_missing")
            api.assert_not_called()

    def test_route_failure_backoff_and_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            router = self.router(directory)
            with patch("routing.time.time", return_value=10000), \
                 patch.object(router, "geocode", side_effect=ValueError("Ambiguous")) as geo:
                self.assertIsNone(router.estimate("123 Main St")["drive_minutes"])
                self.assertIsNone(router.estimate("123 Main St")["drive_minutes"])
                self.assertEqual(geo.call_count, 1)
            with patch("routing.time.time", return_value=13601), \
                 patch.object(router, "geocode", side_effect=ValueError("Ambiguous")) as geo:
                router.estimate("123 Main St")
                self.assertEqual(geo.call_count, 1)

    def test_geocoder_rejects_wrong_house_and_postcode(self):
        with tempfile.TemporaryDirectory() as directory:
            router = self.router(directory)
            features = [{"properties": {"housenumber": "203", "street": "McCrae Lane", "postalcode": "41011",
                                        "confidence": 1}, "geometry": {"coordinates": [-84.53, 39.06]}}]
            with patch("routing.request_json", return_value={"features": features}):
                with self.assertRaises(ValueError):
                    router.geocode(router.config["origin_address"])
                features[0]["properties"].update(housenumber="202", postalcode="45202")
                with self.assertRaises(ValueError):
                    router.geocode(router.config["origin_address"])
                features[0]["properties"]["postalcode"] = "41011"
                self.assertEqual(router.geocode(router.config["origin_address"]), [-84.53, 39.06])

    def test_explicit_coordinates_bypass_geocoder(self):
        with tempfile.TemporaryDirectory() as directory:
            router = self.router(directory)
            router.config["address_overrides"] = {"123 Main St": [-84.5, 39.1]}
            with patch("routing.request_json", side_effect=AssertionError("No request")):
                self.assertEqual(router.geocode("123 Main St"), [-84.5, 39.1])


class LayoutTests(unittest.TestCase):
    def test_event_dates_follow_local_day_and_update_cached_events_at_midnight(self):
        zone = ZoneInfo("America/New_York")
        now = datetime(2026, 10, 5, 23, 30, tzinfo=zone)
        event = {"start": "2026-10-06T03:45:00Z"}
        self.assertEqual(event_when(event, now), "Today 11:45 PM")
        self.assertEqual(event_when(event, datetime(2026, 10, 6, 0, 5, tzinfo=zone)), "Mon 10/5 11:45 PM")
        self.assertEqual(event_when({"start": "2026-10-06T07:00:00-04:00"}, now), "Tue 10/6 7:00 AM")

    def test_all_day_event_date_is_not_shifted_by_timezone(self):
        now = datetime(2026, 10, 5, 20, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(event_when({"start": "2026-10-05", "all_day": True}, now), "Today · all day")
        self.assertEqual(event_when({"start": "2026-10-06", "all_day": True}, now), "Tue 10/6 · all day")

    def test_uv_boundaries_and_unavailable_values(self):
        for value, label in ((0, "Low"), (2.9, "Low"), (3, "Medium"), (5.9, "Medium"), (6, "High"), (11, "High")):
            self.assertIn(label, uv_text(value))
        for value in (None, -1, float("nan"), float("inf")):
            self.assertEqual(uv_text(value), "UV —")

    def test_precip_expected_possible_dry_and_missing(self):
        self.assertIn("expected", precipitation_text({"precip_probability": 65, "precip_inches": 0.12})[0])
        self.assertIn("chance", precipitation_text({"precip_probability": 20, "precip_inches": 0})[0])
        self.assertIn("Dry", precipitation_text({"precip_probability": 0, "precip_inches": 0})[0])
        self.assertNotIn("0%", precipitation_text({})[0])

    def test_meeting_badge_survives_long_calendar_label(self):
        d = ImageDraw.Draw(Image.new("L", (400, 300), 255))
        text = event_metadata(d, {"when": "Mon 3:00 PM", "calendar": "A very long calendar label", "meeting": "Teams"}, 260)
        self.assertIn("Teams", text)
        self.assertLessEqual(d.textlength(text, font=font(10)), 260)

    def test_all_weather_icons_and_dense_dashboard_are_monochrome_bmp(self):
        weather = {"temperature": 105, "unit": "F", "code": 95, "feels": 109,
                   "high": 106, "low": 82, "precip_probability": None, "precip_inches": None,
                   "tomorrow": {"code": 73, "high": 96, "low": 74, "precip_probability": 65,
                                "precip_inches": 1.25}}
        rows = [{"when": "Wed Nov 20 • all day", "calendar": "Long calendar title "*4,
                 "meeting": "Zoom/Teams", "summary": "Long event title "*10,
                 "physical_address": "123 Main St", "drive_minutes": 128}] * 3
        image = render_dashboard(weather, rows, datetime.now(timezone.utc))
        output = io.BytesIO()
        image.save(output, "BMP")
        self.assertEqual(len(output.getvalue()), 15662)
        self.assertEqual(image.mode, "1")
        for code in (0, 1, 2, 3, 45, 48, 51, 61, 66, 71, 77, 80, 85, 95, 96, None):
            self.assertEqual(condition_icon(code).size, (48, 48))

    def test_async_route_results_only_apply_to_matching_events(self):
        cfg = json.loads((ROOT / "config.example.json").read_text())
        provider = Providers(cfg)
        provider.events = [{"route_key": "current", "physical_address": "123 Main St", "calendar": "Work"}]
        provider.route_running = True
        provider.route_results.put([{"route_key": "old", "drive_minutes": 15, "drive_status": "ok"}])
        provider.refresh_routes(100)
        self.assertIsNone(provider.events[0]["drive_minutes"])
        self.assertFalse(provider.route_running)


if __name__ == "__main__":
    unittest.main()
