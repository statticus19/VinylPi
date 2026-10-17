"""Manual privacy pause independent of fingerprint recording leases.

The marker is set BEFORE waiting for the exclusive recognition lock. Only report
paused once all existing recognition (including any in-flight Last.fm write)
has drained. Changes are persisted across service/device restarts.
"""
import fcntl
import json
import os
import time
from pathlib import Path

STATE_DIR = Path('/home/vinyl/pi_epaper/state/learning')
MARKER = 'privacy_pause.json'
LOCK = 'recognition.lock'


def state():
    path = STATE_DIR / MARKER
    try:
        return {'paused': json.loads(path.read_text()).get('paused') is True, 'error': None}
    except FileNotFoundError:
        return {'paused': False, 'error': None}
    except (OSError, ValueError, TypeError):
        return {'paused': True, 'error': 'Cannot read privacy pause state'}


def atomic_marker():
    tmp = STATE_DIR / ('.privacy_pause.%d.tmp' % os.getpid())
    try:
        with tmp.open('x') as handle:
            json.dump({'paused': True, 'requested_at': time.time()}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, STATE_DIR / MARKER)
        dirfd = os.open(STATE_DIR, os.O_DIRECTORY)
        try: os.fsync(dirfd)
        finally: os.close(dirfd)
    finally:
        tmp.unlink(missing_ok=True)


def pause(timeout=120):
    if not STATE_DIR.is_dir():
        raise RuntimeError('Learning state directory is missing')
    atomic_marker()
    # If orchestrator is currently processing, wait until it releases its
    # shared flock; unlike a simple toggle this is an actual privacy barrier.
    deadline = time.monotonic() + timeout
    with (STATE_DIR / LOCK).open('a') as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Pause requested, but an existing recognition cycle has not finished; check status before resuming')
                time.sleep(0.2)


def resume():
    # Removing only OUR marker preserves all active learning-session leases.
    (STATE_DIR / MARKER).unlink(missing_ok=True)
