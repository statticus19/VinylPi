#!/usr/bin/env python3
"""One-time YouTube OAuth setup. Standard library only; no playlist writes.

Run on the Pi while Windows forwards local port 8767 over SSH. Credentials
remain on the Pi. Never upload the resulting token file or commit it to git.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

SCOPE = 'https://www.googleapis.com/auth/youtube.force-ssl'
REDIRECT = 'http://127.0.0.1:8767/oauth2callback'


class SetupError(Exception):
    pass


def read_client(path):
    try:
        data = json.loads(Path(path).read_text())['installed']
        client_id = data['client_id']
        secret = data['client_secret']
        if not isinstance(client_id, str) or not client_id.endswith('.apps.googleusercontent.com'):
            raise ValueError()
        if not isinstance(secret, str) or not secret:
            raise ValueError()
        return client_id, secret
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise SetupError('Cannot read Desktop-app client JSON. Check the file path and client type.') from exc


def authorization_url(client_id, state, verifier):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode().rstrip('=')
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': client_id, 'redirect_uri': REDIRECT, 'response_type': 'code',
        'scope': SCOPE, 'state': state, 'code_challenge': challenge,
        'code_challenge_method': 'S256', 'access_type': 'offline', 'prompt': 'consent'})


def request_json(request):
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise SetupError('Google response exceeded the safe size limit.')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise SetupError('Google returned an unexpected response.')
        return data
    except HTTPError as exc:
        # Do not print request bodies, OAuth codes, token responses, or URLs.
        raise SetupError(f'Google request failed with HTTP {exc.code}. Check API enablement, consent, and account access.') from exc
    except (URLError, ValueError, OSError) as exc:
        raise SetupError('Google request failed or returned invalid JSON. Check the Pi internet connection.') from exc


def exchange_code(client_id, secret, code, verifier):
    data = request_json(Request('https://oauth2.googleapis.com/token', data=urlencode({
        'client_id': client_id, 'client_secret': secret, 'code': code,
        'code_verifier': verifier, 'redirect_uri': REDIRECT,
        'grant_type': 'authorization_code'}).encode(),
        headers={'Content-Type': 'application/x-www-form-urlencoded'}))
    if any(not isinstance(data.get(k), str) or not data[k] for k in ('access_token', 'refresh_token')):
        raise SetupError('Google did not return usable access and refresh tokens. Run authorization again with consent.')
    if data.get('token_type', '').lower() != 'bearer':
        raise SetupError('Google returned an unexpected token type.')
    if SCOPE not in data.get('scope', '').split():
        raise SetupError('YouTube permission was not granted. Select the requested permission on the consent screen.')
    return data


def get_channel(access_token):
    data = request_json(Request('https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true',
                                headers={'Authorization': 'Bearer ' + access_token}))
    items = data.get('items')
    if not isinstance(items, list) or len(items) != 1:
        raise SetupError('Authorization must identify one YouTube channel. Create/select the intended channel and authorize again.')
    item = items[0]
    if not isinstance(item.get('id'), str) or not item['id']:
        raise SetupError('Google returned invalid channel information.')
    return {'id': item['id'], 'title': item.get('snippet', {}).get('title', item['id'])}


def save_new_token(path, data):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        raise SetupError('Token directory is not private. Set its permissions to 700, then try again.')
    # Exclusive creation: never replace a user's existing authorization.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise SetupError('Token file already exists; it was not replaced.') from exc
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def wait_for_code(client_id, state, verifier):
    outcome = {}

    class Callback(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            pass  # OAuth codes must never enter logs.

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            status, message = 400, 'Invalid authorization callback.'
            returned_state = query.get('state', [''])[0]
            if parsed.path == '/oauth2callback' and secrets.compare_digest(returned_state, state):
                if 'error' in query:
                    outcome['error'] = 'Authorization was denied or failed. Nothing was saved.'
                    message = outcome['error']
                elif len(query.get('code', [])) == 1 and query['code'][0]:
                    outcome['code'] = query['code'][0]
                    status, message = 200, 'Authorization received. Return to the Pi terminal for the channel check.'
            body = message.encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        server = HTTPServer(('127.0.0.1', 8767), Callback)
    except OSError as exc:
        raise SetupError('Cannot listen on Pi local port 8767. Check whether another setup helper is running.') from exc
    with server:
        server.timeout = 1
        print('Open this URL in your WINDOWS browser (not on the iPhone):', flush=True)
        print(authorization_url(client_id, state, verifier), flush=True)
        print('\nWaiting up to 10 minutes. Keep the Windows SSH tunnel open.', flush=True)
        deadline = time.monotonic() + 600
        while not outcome and time.monotonic() < deadline:
            server.handle_request()
    if 'error' in outcome:
        raise SetupError(outcome['error'])
    if not outcome.get('code'):
        raise SetupError('Authorization timed out. Check the SSH tunnel and start the helper again.')
    return outcome['code']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', required=True)
    parser.add_argument('--token', required=True)
    args = parser.parse_args()
    try:
        if Path(args.token).exists():
            raise SetupError('Token file already exists; nothing was changed.')
        client_id, secret = read_client(args.client)
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        code = wait_for_code(client_id, state, verifier)
        tokens = exchange_code(client_id, secret, code, verifier)
        channel = get_channel(tokens['access_token'])
        # Refresh token alone is sufficient; do not persist the temporary code
        # or access token. The worker will request short-lived access as needed.
        save_new_token(args.token, {
            'schema_version': 1, 'client_id': client_id, 'client_secret': secret,
            'refresh_token': tokens['refresh_token'], 'scope': SCOPE,
            'channel_id': channel['id'], 'channel_title': channel['title']})
        print('Authorization saved with private file permissions.')
        print('Channel:', channel['title'])
        print('Channel ID:', channel['id'])
        print('No playlist was created. No existing playlist was changed.')
    except (SetupError, OSError) as exc:
        print('Setup stopped:', str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nSetup cancelled.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
