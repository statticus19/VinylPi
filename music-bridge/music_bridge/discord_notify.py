"""Reusable, outbound-only Discord bot DM notifications for VinylPi.

No Gateway connection or listening port is required. Credentials are read from
MUSIC_BRIDGE_DISCORD_BOT_TOKEN and MUSIC_BRIDGE_DISCORD_USER_ID.
"""
import argparse
import asyncio
import os
import re
from urllib.parse import urlsplit
import httpx

API = 'https://discord.com/api/v10'

class NotificationError(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


def settings():
    token = os.environ.get('MUSIC_BRIDGE_DISCORD_BOT_TOKEN', '').strip()
    recipient = os.environ.get('MUSIC_BRIDGE_DISCORD_USER_ID', '').strip()
    if not token or not recipient:
        return None
    if not re.fullmatch(r'[0-9]{15,22}', recipient):
        raise NotificationError('Invalid Discord user ID configuration')
    if '\n' in token or '\r' in token:
        raise NotificationError('Invalid Discord bot token configuration')
    return token, recipient


async def _notify_without_history(category, title, message, url=None, *, transport=None, raw=False):
    """Send a private notification. Callable by other Pi Python projects.

    Returns False if unconfigured. Raises a sanitized error otherwise.
    """
    config = settings()
    if not config:
        return False
    token, recipient = config
    headers = {'Authorization': 'Bot ' + token,
               'User-Agent': 'DiscordBot (https://localhost/, 1.0)'}
    # Avoid unexpected mentions and limit arbitrary content length.
    content = str(message)[:1200] if raw else f'**{str(title)[:160]}**\n{str(message)[:1200]}'
    if url:
        parsed = urlsplit(str(url))
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise NotificationError('Notification URL must be HTTPS')
        content += '\n' + str(url)[:300]
    options = {'timeout': 15, 'follow_redirects': False}
    if transport is not None:
        options['transport'] = transport
    try:
        async with httpx.AsyncClient(**options) as client:
            dm = await client.post(API + '/users/@me/channels', headers=headers,
                                   json={'recipient_id': recipient})
            if dm.status_code != 200:
                raise NotificationError(f'Discord DM channel HTTP {dm.status_code}')
            channel_id = dm.json().get('id', '')
            if not re.fullmatch(r'[0-9]{15,22}', str(channel_id)):
                raise NotificationError('Discord returned invalid DM channel')
            sent = await client.post(API + f'/channels/{channel_id}/messages', headers=headers,
                                     json={'content': content, 'allowed_mentions': {'parse': []}})
            if sent.status_code not in (200, 201):
                raise NotificationError(f'Discord message HTTP {sent.status_code}')
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise NotificationError('Discord request failed') from exc
    return True


def _history_record(status, category, title, message, url, raw=False):
    """Best-effort shared audit. Database failure must NEVER affect a Discord send."""
    import logging
    import sqlite3
    import time
    from pathlib import Path
    path = os.environ.get('VINYLPI_NOTIFICATION_DB', '').strip()
    if not path:
        return
    try:
        db_path = Path(path)
        # Do not create an arbitrary parent directory from the bot service.
        if not db_path.parent.is_dir():
            return
        body = str(message)[:1200] if raw else ('**'+str(title)[:160]+'**\n'+str(message)[:1200])
        if url:
            body += '\n'+str(url)[:300]
        with sqlite3.connect(db_path, timeout=2) as db:
            db.execute('INSERT INTO events(occurred,kind,status,actor,client_ip,content,source) '
                       'VALUES(?,?,?,?,?,?,?)',
                       (time.time(), 'discord', status, 'bot', '', body[:1500], 'music-bridge'))
    except (OSError, sqlite3.Error, ValueError, TypeError):
        logging.getLogger('music_bridge.discord_notify').warning(
            'Discord history write unavailable; delivery unaffected')


async def notify(category, title, message, url=None, *, transport=None, raw=False):
    """Preserve outbound delivery semantics; audit the result independently."""
    try:
        ok = await _notify_without_history(category, title, message, url,
                                           transport=transport, raw=raw)
    except NotificationError:
        _history_record('failed', category, title, message, url, raw)
        raise
    if ok:
        _history_record('sent', category, title, message, url, raw)
    return ok


async def send_pending_notifications(store):
    if not settings():
        return
    # Apple plans are finished on the Pi but still require iOS playlist creation.
    # Each DM is its own durable event, so a failure of the second doesn't resend the first.
    for jid, part, name in store.pending_apple_video_notifications():
        url = os.getenv('VINYLPI_DASHBOARD_PUBLIC_URL', '').rstrip('/') + '/playlists?job_id=' + jid if os.getenv('VINYLPI_DASHBOARD_PUBLIC_URL') else ''
        try:
            if part == 'details':
                ok = await notify('apple', 'Apple Music video playlist ready',
                                  str(name)[:160] + '\nProcessing complete. Run Resume Apple Music Video Playlist on your iPhone.',
                                  url)
            else:
                ok = await notify('apple', '', jid, raw=True)
            if ok:
                store.mark_apple_video_notification(jid, part, True)
        except NotificationError as exc:
            store.mark_apple_video_notification(jid, part, False, exc.status)

    for jid, kind, payload, result, error in store.pending_youtube_events():
        name = str(payload.get('title') or 'Music video playlist')[:160]
        titles = {
            'no_matches': 'YouTube playlist: no confident matches',
            'needs_review': 'YouTube playlist requires review',
            'rate_limit_completed': 'YouTube playlist completed after HTTP 429 delay',
            'rate_limit_failed': 'YouTube playlist failed after HTTP 429 retries',
        }
        detail = (str(error or '')[:230] if kind in ('needs_review','rate_limit_failed','no_matches') else '')
        message = name + ('\n' + detail if detail else '')
        url = result.get('playlist_url') if kind in ('needs_review','rate_limit_completed') else None
        try:
            ok = await notify('youtube', titles[kind], message, url)
            if ok:
                store.mark_youtube_event(jid, kind, True)
        except NotificationError as exc:
            store.mark_youtube_event(jid, kind, False, exc.status)
    for jid, result in store.pending_discord_notifications():
        title = 'YouTube playlist completed after quota recovery'
        message = str(result.get('playlist_name') or 'Music video playlist')
        try:
            ok = await notify('youtube', title, message, result['playlist_url'])
            if ok:
                store.mark_discord_attempt(jid, True)
        except NotificationError as exc:
            # Never log tokens or full Discord responses.
            store.mark_discord_attempt(jid, False, exc.status)


def main():
    parser = argparse.ArgumentParser(description='Send a private VinylPi test DM')
    parser.add_argument('--test', action='store_true', required=True)
    parser.parse_args()
    try:
        delivered = asyncio.run(notify('test', 'VinylPi Message Bot — test',
                                       'The Raspberry Pi can send you private notifications.'))
    except NotificationError as exc:
        print('FAILED:', exc.status)
        return 1
    if not delivered:
        print('FAILED: Discord bot environment variables are not configured.')
        return 1
    print('Discord test DM sent successfully.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
