#!/usr/bin/env python3
"""Send a single Discord DM per boot using the existing Music Bridge bot.

No new credentials, incoming ports, or periodic cron jobs. The marker is
stored in the systemd RuntimeDirectory, which is cleared on reboot.
"""
import asyncio
import json
import logging
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen

from music_bridge.discord_notify import NotificationError, notify

LOG = logging.getLogger('vinylpi.boot_notification')
MARKER = Path('/run/vinylpi-boot-notify/sent')
DASHBOARD_HEALTH = 'http://127.0.0.1:8780/health'
DASHBOARD_LINK = os.getenv('VINYLPI_DASHBOARD_PUBLIC_URL', '').rstrip('/') + '/' if os.getenv('VINYLPI_DASHBOARD_PUBLIC_URL') else ''
RETRY_SECONDS = 10
WAIT_SECONDS = 300


def dashboard_ready():
    try:
        with urlopen(DASHBOARD_HEALTH, timeout=3) as res:
            return res.status == 200 and json.load(res).get('status') == 'ok'
    except (OSError, ValueError, TimeoutError):
        return False


def tailscale_ready():
    try:
        p = subprocess.run(['tailscale', 'status', '--json'],
                           capture_output=True, text=True, timeout=5, check=False)
        if p.returncode != 0:
            return False
        status = json.loads(p.stdout)
        return status.get('BackendState') == 'Running' and bool(status.get('TailscaleIPs'))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    if MARKER.exists():
        LOG.info('Boot notification already sent this boot; skipping')
        return 0
    until = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < until:
        if dashboard_ready() and tailscale_ready():
            try:
                sent = asyncio.run(notify(
                    'boot', 'VinylPi is online',
                    'The Raspberry Pi restarted. The dashboard and Tailscale are available.',
                    DASHBOARD_LINK))
            except NotificationError as exc:
                LOG.warning('Discord delivery unavailable: %s', exc.status)
            else:
                if sent:
                    MARKER.write_text('sent\n', encoding='utf-8')
                    LOG.info('Discord boot notification delivered')
                    return 0
                LOG.error('Discord bot credentials are not configured')
                return 1
        time.sleep(RETRY_SECONDS)
    LOG.error('No boot notification sent: dashboard, Tailscale or Discord unavailable before timeout')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
