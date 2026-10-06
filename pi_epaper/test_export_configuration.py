import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from display_service import ROOT
from routing import DriveTimes


class OriginTests(unittest.TestCase):
    def test_enabled_routing_without_origin_never_requests_an_address(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            router = DriveTimes({"enabled": True, "api_key": "test-only"}, directory)
            with patch("routing.request_json") as api:
                self.assertEqual(router.estimate("123 Example St"),
                                 {"drive_minutes": None, "drive_status": "origin_missing"})
                api.assert_not_called()

    def test_configure_without_origin_leaves_original_file_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.json")
            original = '{"lastfm": {"api_key": "test-only"}}'
            path.write_text(original)
            with patch("display_service.ROOT", Path(directory)), \
                 patch("getpass.getpass", return_value="test-only"), \
                 patch("builtins.input", return_value=""), self.assertRaises(SystemExit):
                runpy.run_path(str(ROOT / "configure_routing.py"), run_name="__main__")
            self.assertEqual(path.read_text(), original)

    def test_explicit_origin_coordinates_need_no_origin_address(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            router = DriveTimes({"api_key": "test-only", "origin_coordinates": [-1, 51]}, directory)
            with patch.object(router, "geocode", return_value=[-1.1, 51.1]) as geo, \
                 patch("routing.request_json", return_value={"routes": [{"summary": {"duration": 900}}]}):
                self.assertEqual(router.estimate("123 Example St")["drive_minutes"], 15)
                geo.assert_called_once_with("123 Example St")

