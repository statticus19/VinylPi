"""Persistent at-most-once submissions for the Pi's identified listening session."""
import fcntl
import json
import os
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

SILENCE_RESET_SECONDS = 90
RESUME_SECONDS = 15 * 60


def identity(artist, title):
    return [" ".join(unicodedata.normalize("NFKC", value).split()).casefold()
            for value in (artist, title)]


@contextmanager
def single_instance(path):
    """Keep this advisory lock for the entire process, before audio or login."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another updated audio orchestrator is already running") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class ScrobbleGuard:
    def __init__(self, path, now=None):
        self.path = Path(path)
        self.candidate = None
        self.candidate_count = 0
        try:
            self.data = json.loads(self.path.read_text())
        except FileNotFoundError:
            self.data = {}
        if not isinstance(self.data, dict):
            raise ValueError("Invalid orchestrator state file")
        if self.data and (not isinstance(self.data.get("last_audio"), (int, float)) or
                          not isinstance(self.data.get("track_key"), (list, type(None)))):
            raise ValueError("Invalid orchestrator state file")
        self.expire(time.time() if now is None else now)
        self.reason = ""

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w") as handle:
            os.chmod(temporary, 0o600)
            json.dump(self.data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def expire(self, now):
        if self.data and now-self.data.get("last_audio", now) > RESUME_SECONDS:
            self.data = {}
            self.candidate = None
            self.candidate_count = 0

    def audio(self, now):
        self.expire(now)
        self.data["last_audio"] = now
        self.data["silent_since"] = None
        self.save()

    def silence(self, now):
        self.candidate = None
        self.candidate_count = 0
        since = self.data.get("silent_since")
        if since is None:
            since = now
            self.data["silent_since"] = since
        self.data.setdefault("last_audio", now)
        ended = now-since >= SILENCE_RESET_SECONDS
        if ended:
            self.data["track_key"] = None
        self.save()
        return ended

    def claim(self, artist, title, now):
        """Save before POST: ambiguous failures must not generate a new timestamp."""
        if not all(isinstance(value, str) and value.strip() for value in (artist, title)):
            raise ValueError("Recognition needs an artist and title")
        self.audio(now)
        key = identity(artist, title)
        previous = self.data.get("track_key")
        if key == previous:
            self.candidate = None
            self.candidate_count = 0
            self.reason = "Track already submitted in this listening session"
            return None
        if previous:
            if self.candidate == key:
                self.candidate_count += 1
            else:
                self.candidate, self.candidate_count = key, 1
            if self.candidate_count < 2:
                self.reason = "Waiting for a second match to confirm the track change"
                return None
        timestamp = int(now)
        self.data.update(track_key=key, artist=artist, title=title,
                         timestamp=timestamp, submission="attempted")
        self.save()
        self.candidate = None
        self.candidate_count = 0
        self.reason = "New listening session"
        return timestamp

    def result(self, successful):
        self.data["submission"] = "submitted" if successful else "uncertain"
        self.save()
