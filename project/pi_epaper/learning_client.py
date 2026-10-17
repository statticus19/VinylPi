"""Optional learning coordination; standard library only, no service dependencies."""
import fcntl
import json
import logging
import os
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen


class LearningClient:
    def __init__(self, directory, secrets=()):
        self.directory = Path(directory)
        self.monitor = None
        if Path(sys.argv[0]).name == 'orchestrator_display_ready.py':
            try:
                from orchestrator_health import HealthMonitor
                self.monitor = HealthMonitor(self.directory, secrets)
            except Exception:
                logging.warning('Orchestrator diagnostics unavailable; recognition will continue.')

    def privacy_paused(self):
        """A durable privacy pause, independent of recording leases."""
        try:
            return json.loads((self.directory / 'privacy_pause.json').read_text()).get('paused') is True
        except (OSError, ValueError, TypeError):
            # Fail closed on unreadable/corrupt privacy state, if present.
            return (self.directory / 'privacy_pause.json').exists()

    def paused(self):
        if self.privacy_paused():
            return True
        return self.learning_paused()

    def learning_paused(self):
        try:
            return float(json.loads((self.directory / 'pause.json').read_text())['expires']) > time.time()
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def enter(self):
        if not self.directory.exists():
            return True
        handle = (self.directory / 'recognition.lock').open('a')
        try:
            fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            if self.paused():
                if self.monitor:self.monitor.phase('learning_paused')
                handle.close()
                try:
                    lease = json.loads((self.directory / 'pause.json').read_text())
                    tmp = self.directory / ('ack.%d.tmp' % os.getpid())
                    tmp.write_text(json.dumps({'session_id': lease['session_id'], 'pid': os.getpid(), 'at': time.time()}))
                    os.chmod(tmp, 0o600)
                    os.replace(tmp, self.directory / 'ack.json')
                except (OSError, ValueError, KeyError):
                    pass
                return None
            return handle
        except BlockingIOError:
            if self.monitor:self.monitor.phase('learning_paused')
            handle.close()
            return None

    @staticmethod
    def leave(handle):
        if handle is not None and handle is not True:
            handle.close()

    def match(self, audio_path):
        """A missing/offline learning service leaves the usual no-match behavior intact."""
        if not (self.directory / 'client.json').exists():
            return None
        try:
            if self.monitor:self.monitor.phase('local_fallback')
            config = json.loads((self.directory / 'client.json').read_text())
            # PrivateTmp may hide the orchestrator's /tmp from the web service.
            data = Path(audio_path).read_bytes()
            if len(data) > 5_000_000:
                return None
            request = Request('http://127.0.0.1:%d/api/match' % int(config['port']),
                              data=data, headers={'Content-Type': 'audio/wav',
                              'X-Learning-Key': config['token']}, method='POST')
            with urlopen(request, timeout=15) as response:
                value = json.loads(response.read(100_000))
            track = value.get('track')
            if isinstance(track, dict) and track.get('title') and track.get('subtitle'):
                return track
        except Exception as exc:
            logging.warning('Local fingerprint fallback unavailable (%s); keeping Shazam flow.', type(exc).__name__)
        return None
