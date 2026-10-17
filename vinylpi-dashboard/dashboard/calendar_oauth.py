"""Server-side, one-use Google Calendar web OAuth. Never log codes or tokens."""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import AuthorizedSession

ROOT = Path('/home/vinyl/pi_epaper')
CLIENT = ROOT / 'dashboard_oauth_credentials.json'
TOKEN_DIR = ROOT / 'state' / 'dashboard-oauth'
TOKEN = TOKEN_DIR / 'token.json'
CONFIG = ROOT / 'config.json'
REDIRECT = os.getenv('VINYLPI_GOOGLE_CALENDAR_REDIRECT_URI', '')
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
_PENDING = {}
_LOCK = threading.RLock()


def new_flow(state=None):
    flow = Flow.from_client_secrets_file(str(CLIENT), scopes=SCOPES, state=state)
    if not REDIRECT.startswith('https://'):
        raise ValueError('Set VINYLPI_GOOGLE_CALENDAR_REDIRECT_URI to a registered HTTPS OAuth callback URL')
    flow.redirect_uri = REDIRECT
    return flow


def begin(session_cookie):
    if not CLIENT.is_file():
        raise ValueError('Dashboard Google OAuth credentials unavailable')
    nonce = secrets.token_urlsafe(32)
    digest = hashlib.sha256(session_cookie.encode()).hexdigest()
    with _LOCK:
        now = time.time()
        for key, row in list(_PENDING.items()):
            if now - row['created'] > 600:
                _PENDING.pop(key, None)
        _PENDING[nonce] = {'created': now, 'session_digest': digest}
    flow = new_flow(nonce)
    url, _ = flow.authorization_url(access_type='offline', prompt='consent', include_granted_scopes='false')
    with _LOCK:
        _PENDING[nonce]['code_verifier'] = flow.code_verifier
    return url


def complete(code, state):
    if not code or not state or len(code) > 4096 or len(state) > 256:
        raise ValueError('Invalid Google authorization response')
    with _LOCK:
        row = _PENDING.pop(state, None)
    if not row or time.time() - row['created'] > 600:
        raise ValueError('Authorization expired or already used')
    flow = new_flow(state)
    flow.code_verifier = row['code_verifier']
    flow.fetch_token(code=code)
    creds = flow.credentials
    if not creds.valid or not creds.refresh_token:
        raise ValueError('Google did not provide a reusable refresh token')
    session = AuthorizedSession(creds)
    response = session.get('https://www.googleapis.com/calendar/v3/users/me/calendarList', params={'maxResults': 1}, timeout=12)
    response.raise_for_status()
    if not isinstance(response.json().get('items', []), list):
        raise ValueError('Google Calendar verification failed')
    # Save in a dedicated, private directory; do not overwrite legacy token.
    TOKEN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = TOKEN.with_suffix('.new')
    with tmp.open('xb') as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(creds.to_json().encode()); f.flush(); os.fsync(f.fileno())
    try:
        os.replace(tmp, TOKEN)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def activate():
    """Switch renderer configuration only after its new token was validated."""
    from .display_settings import LOCK, BACKUPS
    with LOCK:
        old = CONFIG.read_bytes()
        cfg = json.loads(old)
        if cfg['google'].get('token_file') == 'state/dashboard-oauth/token.json':
            return None
        BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = time.strftime('%Y%m%d-%H%M%S') + '-' + hashlib.sha256(old).hexdigest()[:8] + '.json'
        backup = BACKUPS / name
        with backup.open('xb') as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(old); f.flush(); os.fsync(f.fileno())
        cfg['google']['token_file'] = 'state/dashboard-oauth/token.json'
        updated = (json.dumps(cfg, indent=2, ensure_ascii=False) + '\n').encode()
        try:
            with CONFIG.open('r+b') as f:
                f.write(updated); f.truncate(); f.flush(); os.fsync(f.fileno())
        except Exception:
            with CONFIG.open('r+b') as f:
                f.write(old); f.truncate(); f.flush(); os.fsync(f.fileno())
            raise
        return name


def restore_backup(name):
    if name:
        from .display_settings import restore
        restore(name)
