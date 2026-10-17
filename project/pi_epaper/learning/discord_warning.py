"""One learning reminder through the existing VinylPi Discord bot's REST API."""
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


class DiscordFailure(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DiscordWarning:
    def __init__(self, token='', user_id='', enabled=True, web_url='', opener=None):
        self.token = token.strip()
        self.user_id = user_id.strip()
        self.enabled = enabled
        self.configured = bool(enabled and self.token and re.fullmatch(r'\d{15,22}', self.user_id))
        parsed = urlparse(web_url)
        self.web_url = web_url if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password else ''
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    @classmethod
    def from_env(cls):
        return cls(os.environ.get('MUSIC_BRIDGE_DISCORD_BOT_TOKEN', ''),
                   os.environ.get('MUSIC_BRIDGE_DISCORD_USER_ID', ''),
                   os.environ.get('LEARNING_DISCORD_WARNING_ENABLED', '1') == '1',
                   os.environ.get('LEARNING_WEB_URL', ''))

    def post(self, path, body):
        request = urllib.request.Request('https://discord.com/api/v10'+path,
            data=json.dumps(body).encode(), method='POST', headers={
                'Authorization': 'Bot '+self.token, 'Content-Type': 'application/json',
                'User-Agent': 'VinylPiLearning/1.0'})
        try:
            with self.opener.open(request, timeout=10) as response:
                return json.loads(response.read(65536))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                try:
                    delay = float(json.loads(exc.read(65536)).get('retry_after', 5))
                except (ValueError, TypeError):
                    delay = 5
                if not math.isfinite(delay) or delay < 0:
                    delay = 5
                raise DiscordFailure('Discord rate limited the reminder.', delay) from None
            if exc.code in (401, 403):
                raise DiscordFailure('Discord rejected the bot credentials or direct message permission.') from None
            raise DiscordFailure('Discord returned HTTP %d.' % exc.code) from None
        except (urllib.error.URLError, OSError, ValueError):
            # Never retain exception text, response bodies, tokens, or recipient IDs.
            raise DiscordFailure('Discord could not be reached; reminder delivery is uncertain.') from None

    def send(self, session, alive, cancel_event):
        if not self.configured:
            return {'state': 'unconfigured', 'message': '30-minute Discord reminder is not configured.'}
        nonce = hashlib.sha256(('learning-warning:'+session['id']).encode()).hexdigest()[:24]
        label = ' '.join(str(session.get('label', 'Untitled record')).split())[:200]
        label = re.sub(r'([\\`*_~|<>])', r'\\\1', label)
        cap = max(1, round((session['expires_at']-session['created'])/60))
        content = ('VinylPi learning reminder\n'+label+
            '\nThis learning recording has been running for 30 minutes and has not been stopped. '
            'If the record side is finished, click Finish recording to identify its tracks. '
            'An unfinished session is automatically discarded %d minutes after Start learning.' % cap)
        if self.web_url:
            content += '\nOpen learning: '+self.web_url
        deadline = time.monotonic()+120
        for attempt in range(3):
            if cancel_event.is_set() or not alive():
                return {'state': 'canceled', 'message': 'Recording ended before the reminder was delivered.'}
            try:
                channel = self.post('/users/@me/channels', {'recipient_id': self.user_id})
                channel_id = str(channel.get('id', ''))
                if not re.fullmatch(r'\d{15,22}', channel_id):
                    raise DiscordFailure('Discord did not return a valid direct message channel.')
                if cancel_event.is_set() or not alive():
                    return {'state': 'canceled', 'message': 'Recording ended before the reminder was delivered.'}
                message = self.post('/channels/'+channel_id+'/messages', {
                    'content': content, 'allowed_mentions': {'parse': []},
                    'nonce': nonce, 'enforce_nonce': True})
                if not message.get('id'):
                    raise DiscordFailure('Discord did not confirm reminder delivery.')
                return {'state': 'sent', 'message': '30-minute reminder sent through the VinylPi Discord bot.', 'at': time.time()}
            except DiscordFailure as exc:
                # Retry only an explicit rate limit. A network timeout may have delivered.
                delay = exc.retry_after
                if delay is None or attempt == 2 or time.monotonic()+delay >= deadline:
                    return {'state': 'failed', 'message': str(exc), 'at': time.time()}
                if cancel_event.wait(delay):
                    return {'state': 'canceled', 'message': 'Recording ended before the reminder was delivered.'}
        return {'state': 'failed', 'message': 'Discord reminder could not be delivered.'}

# VINYLPI_LEARNING_NOTIFICATION_HISTORY_V1
# Independent, best-effort audit; never affects reminder delivery or cancellation.
def _learning_history_record(result, session):
    import logging
    import sqlite3
    from pathlib import Path
    path = os.environ.get('VINYLPI_NOTIFICATION_DB', '').strip()
    if not path or result.get('state') not in ('sent', 'failed'):
        return
    try:
        db_path = Path(path)
        if not db_path.parent.is_dir():
            return
        label = ' '.join(str(session.get('label', 'Untitled record')).split())[:200]
        state = result['state']
        # Avoid saving recording IDs, bot tokens, Discord recipient IDs, or API responses.
        content = ('Fingerprint Learning: 30-minute recording reminder\n' + label +
                   '\n' + ('Sent to Discord.' if state == 'sent'
                            else 'Delivery failed: ' + str(result.get('message', 'Unknown error'))[:200]))
        with sqlite3.connect(db_path, timeout=2) as db:
            db.execute('INSERT INTO events(occurred,kind,status,actor,client_ip,content,source) '
                       'VALUES(?,?,?,?,?,?,?)',
                       (time.time(), 'discord', state, 'bot', '', content[:1500], 'vinyl-learning'))
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        logging.getLogger('vinylpi.learning.discord').warning(
            'Learning notification history unavailable; reminder delivery unaffected')


_original_learning_send = DiscordWarning.send

def _learning_send_with_history(self, session, alive, cancel_event):
    # Retain the original sender's retry, cancellation and exception behavior.
    try:
        result = _original_learning_send(self, session, alive, cancel_event)
    except Exception:
        # Preserve original exception propagation; do not misreport an uncertain send.
        raise
    _learning_history_record(result, session)
    return result

DiscordWarning.send = _learning_send_with_history
