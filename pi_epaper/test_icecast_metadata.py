"""Icecast transport and orchestration checks without live service writes."""
import asyncio
import base64
import importlib.util
import io
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, Mock, patch

from icecast_metadata import IcecastMetadata

SUCCESS = b'<iceresponse><message>Metadata update successful</message><return>1</return></iceresponse>'


class TransportTests(unittest.TestCase):
    def client(self, body=SUCCESS, **kwargs):
        opener = Mock(return_value=io.BytesIO(body))
        return IcecastMetadata("http://127.0.0.1:8000", "/vinyl", password="test-only", opener=opener,
                               **kwargs), opener

    def test_artist_title_and_special_characters_are_encoded(self):
        client, opener = self.client()
        self.assertTrue(client.update("Björk & Friends", "It's + 100% / Music?"))
        request = opener.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query, {"mount": ["/vinyl"], "mode": ["updinfo"], "charset": ["UTF-8"],
                                "song": ["Björk & Friends - It's + 100% / Music?"]})
        self.assertNotIn("test-only", request.full_url)
        expected = "Basic "+base64.b64encode(b"admin:test-only").decode()
        self.assertEqual(request.get_header("Authorization"), expected)
        self.assertEqual(opener.call_args.kwargs["timeout"], 5)

    def test_album_is_optional_in_the_display_string(self):
        for include, expected in ((False, "Thrice - Hurricane"),
                                  (True, "Thrice - Hurricane [To Be Everywhere Is to Be Nowhere]")):
            client, opener = self.client(include_album=include)
            client.update("Thrice", "Hurricane", "To Be Everywhere Is to Be Nowhere")
            query = parse_qs(urlsplit(opener.call_args.args[0].full_url).query)
            self.assertEqual(query["song"], [expected])

    def test_label_replaces_a_stale_song_title(self):
        client, opener = self.client(include_album=True)
        client.update(label="Vinyl - No match")
        query = parse_qs(urlsplit(opener.call_args.args[0].full_url).query)
        self.assertEqual(query["song"], ["Vinyl - No match"])

    def test_disabled_and_empty_updates_do_not_make_requests(self):
        opener = Mock()
        disabled = IcecastMetadata("http://127.0.0.1:8000", "/vinyl", opener=opener)
        self.assertFalse(disabled.update("Thrice", "Hurricane"))
        enabled = IcecastMetadata("http://127.0.0.1:8000", "/vinyl", password="test-only", opener=opener)
        self.assertFalse(enabled.update())
        opener.assert_not_called()

    def test_200_with_rejected_or_invalid_body_is_not_success(self):
        for body in (b'<iceresponse><return>0</return></iceresponse>', b'<html>Login</html>', b'bad'):
            client, _ = self.client(body)
            with self.assertLogs("icecast_metadata", level="WARNING"):
                self.assertFalse(client.update("Thrice", "Hurricane"))

    def test_http_and_connection_failures_are_isolated_and_retried(self):
        for error in (HTTPError("http://127.0.0.1/admin/metadata", 401, "Unauthorized", {}, None),
                      TimeoutError("test-only")):
            opener = Mock(side_effect=[error, io.BytesIO(SUCCESS)])
            client = IcecastMetadata("http://127.0.0.1:8000", "/vinyl", password="test-only", opener=opener)
            with self.assertLogs("icecast_metadata", level="WARNING") as logs:
                self.assertFalse(client.update("Thrice", "Hurricane"))
            self.assertNotIn("test-only", " ".join(logs.output))
            self.assertTrue(client.update("Thrice", "Hurricane"))

    def test_environment_configuration_uses_existing_server(self):
        with patch.dict("os.environ", {"ICECAST_METADATA_PASSWORD": "test-only",
                                       "ICECAST_METADATA_USERNAME": "source",
                                       "ICECAST_METADATA_INCLUDE_ALBUM": "1"}, clear=True):
            client = IcecastMetadata.from_environment("http://127.0.0.1:8000", "/vinyl")
        self.assertEqual(client.url, "http://127.0.0.1:8000")
        self.assertEqual(client.username, "source")
        self.assertTrue(client.include_album)

    def test_embedded_credentials_and_invalid_mount_rejected(self):
        for url, mount in (("file:///tmp/file", "/vinyl"), ("http://admin:secret@localhost:8000", "/vinyl"),
                           ("http://localhost:8000?secret=value", "/vinyl"), ("http://localhost:8000", "vinyl")):
            with self.assertRaises(ValueError):
                IcecastMetadata(url, mount)

    def test_real_http_auth_and_redirect_does_not_forward_credentials(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                if "/admin/metadata" in self.path:
                    self.send_response(302)
                    self.send_header("Location", "/redirect-target")
                else:
                    self.send_response(200)
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = IcecastMetadata("http://127.0.0.1:%s" % server.server_port, "/vinyl", password="test-only")
            with self.assertLogs("icecast_metadata", level="WARNING"):
                self.assertFalse(client.update("Thrice", "Hurricane"))
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0][1], "Basic "+base64.b64encode(b"admin:test-only").decode())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class OrchestratorTests(unittest.TestCase):
    def run_loop(self, results, volumes=None, captures=None, network_enabled=True, update_result=True, guard=None):
        spec = importlib.util.spec_from_file_location(
            "orchestrator_icecast_test", Path(__file__).with_name("orchestrator_display_ready.py"))
        module = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", {"pylast": types.SimpleNamespace(md5=lambda value: "test-only"),
                                       "shazamio": types.SimpleNamespace(Shazam=Mock())}):
            spec.loader.exec_module(module)
        network = Mock() if network_enabled else None
        icecast = Mock()
        icecast.update.return_value = update_result
        shazam = types.SimpleNamespace(recognize=AsyncMock(side_effect=results))
        class EndLoop(BaseException):
            pass
        count = 0
        async def sleep(seconds):
            nonlocal count
            count += 1
            if count >= len(volumes or captures or results):
                raise EndLoop()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(module, "ROOT", Path(directory)), \
             patch.object(module, "connect_lastfm", return_value=network), \
             patch.object(module.IcecastMetadata, "from_environment", return_value=icecast), \
             patch.object(module, "Shazam", return_value=shazam), \
             patch.object(module, "capture_sample", side_effect=captures or [True]*len(volumes or results)), \
             patch.object(module, "get_wav_rms_db", side_effect=volumes or [-22.9]*len(results)), \
             patch.object(module, "API_KEY", ""), \
             patch.object(module.asyncio, "sleep", new=sleep):
            if guard is not None:
                with patch.object(module, "ScrobbleGuard", return_value=guard):
                    with self.assertRaises(EndLoop):
                        asyncio.run(module.main())
            else:
                with self.assertRaises(EndLoop):
                    asyncio.run(module.main())
        return icecast, network

    def track(self):
        return {"track": {"title": "Hurricane", "subtitle": "Thrice",
                          "sections": [{"type": "SONG", "metadata": [{"title": "Album", "text": "TBEITBN"}]}]}}

    def test_repeated_matches_renew_stream_metadata_without_duplicate_scrobble(self):
        client, network = self.run_loop([self.track()]*3)
        self.assertEqual(client.update.call_count, 3)
        client.update.assert_called_with(artist="Thrice", title="Hurricane", album="TBEITBN")
        self.assertEqual(network.scrobble.call_count, 1)

    def test_icecast_failure_does_not_block_lastfm(self):
        client, network = self.run_loop([self.track()], update_result=False)
        client.update.assert_called_once()
        network.update_now_playing.assert_called_once()
        network.scrobble.assert_called_once()

    def test_lastfm_disabled_does_not_block_icecast(self):
        client, network = self.run_loop([self.track()], network_enabled=False)
        self.assertIsNone(network)
        client.update.assert_called_once_with(artist="Thrice", title="Hurricane", album="TBEITBN")

    def test_no_match_replaces_song_but_errors_and_incomplete_matches_do_not(self):
        client, _ = self.run_loop([{}, OSError("offline"), {"track": {"title": "Only title"}}])
        client.update.assert_called_once_with(label="Vinyl - No match")

    def test_short_quiet_passage_keeps_title_confirmed_silence_sets_idle(self):
        guard = Mock()
        guard.silence.side_effect = [False, True]
        client, _ = self.run_loop([], volumes=[-78.5, -78.5], guard=guard)
        client.update.assert_called_once_with(label="Vinyl - Idle")

    def test_capture_failure_keeps_current_title(self):
        client, _ = self.run_loop([], captures=[False])
        client.update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
