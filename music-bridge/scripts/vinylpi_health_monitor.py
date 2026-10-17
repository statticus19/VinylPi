#!/usr/bin/env python3
"""VinylPi critical service incident monitor. One execution per systemd timer tick.

No service restarts or privileged operations. Persistent SQLite incident/outbox
state; Music Bridge's existing Discord sender handles delivery and history.
"""
import argparse
import asyncio
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import time

from music_bridge.discord_notify import notify, NotificationError

LOG = logging.getLogger('vinylpi.health_monitor')
DB = Path(os.getenv('VINYLPI_HEALTH_DB', '/var/lib/vinylpi-health/incidents.db'))
DASHBOARD_URL = os.getenv('VINYLPI_DASHBOARD_PUBLIC_URL', '').rstrip('/') + '/notifications' if os.getenv('VINYLPI_DASHBOARD_PUBLIC_URL') else ''
UNITS = (
    'vinylpi-dashboard.service', 'music-bridge.service',
    'vinyl-learning.service', 'vinyl-orchestrator.service',
    'pi-epaper.service', 'icecast2.service',
)
FAIL_AFTER = 180
RETRY_AFTER = 180


def connect(path=DB):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=15000')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS units (
        name TEXT PRIMARY KEY, state TEXT NOT NULL,
        down_since REAL, incident_id INTEGER NOT NULL DEFAULT 0,
        alert_queued INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, unit TEXT NOT NULL,
        incident_id INTEGER NOT NULL, kind TEXT NOT NULL,
        created REAL NOT NULL, delivered REAL,
        attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '',
        UNIQUE(unit, incident_id, kind)
    );
    ''')
    return db


def sample_unit(unit):
    try:
        run = subprocess.run(
            ['/usr/bin/systemctl', 'is-active', unit],
            capture_output=True, text=True, timeout=8, check=False)
        return run.returncode == 0 and run.stdout.strip() == 'active'
    except (OSError, subprocess.TimeoutExpired):
        # A failed systemctl command is not proof of an outage; skip sampling.
        LOG.warning('Unable to sample %s', unit)
        return None


def update_states(db, observations, now):
    """Persist incident transitions atomically; only a sustained outage alerts."""
    with db:
        for unit, healthy in observations.items():
            if healthy is None:
                continue
            old = db.execute('SELECT * FROM units WHERE name=?', (unit,)).fetchone()
            if old is None:
                db.execute('INSERT INTO units(name,state,down_since) VALUES(?,?,?)',
                           (unit, 'up' if healthy else 'down', None if healthy else now))
                continue
            if healthy:
                if old['state'] == 'down':
                    if old['alert_queued']:
                        db.execute('INSERT OR IGNORE INTO messages(unit,incident_id,kind,created) VALUES(?,?,?,?)',
                                   (unit, old['incident_id'], 'recovered', now))
                        LOG.info('%s recovered, recovery event queued', unit)
                    db.execute('UPDATE units SET state=?,down_since=NULL,alert_queued=0 WHERE name=?',
                               ('up', unit))
                continue
            # Currently unhealthy.
            if old['state'] != 'down':
                db.execute('UPDATE units SET state=?,down_since=?,incident_id=incident_id+1,alert_queued=0 WHERE name=?',
                           ('down', now, unit))
                LOG.info('%s down; observing for %ss', unit, FAIL_AFTER)
            elif not old['alert_queued'] and old['down_since'] is not None and now-old['down_since'] >= FAIL_AFTER:
                db.execute('INSERT OR IGNORE INTO messages(unit,incident_id,kind,created) VALUES(?,?,?,?)',
                           (unit, old['incident_id'], 'failed', now))
                db.execute('UPDATE units SET alert_queued=1 WHERE name=?', (unit,))
                LOG.warning('%s sustained outage, failure event queued', unit)


async def deliver(db, now, sender=notify):
    # Head-of-line per unit: failure alert must precede its recovery alert.
    rows = db.execute('''SELECT m.* FROM messages m
        WHERE m.delivered IS NULL AND m.next_try <= ?
          AND NOT EXISTS (SELECT 1 FROM messages earlier
            WHERE earlier.unit=m.unit AND earlier.id<m.id AND earlier.delivered IS NULL)
        ORDER BY m.id LIMIT 10''', (now,)).fetchall()
    for row in rows:
        unit = row['unit']
        if row['kind'] == 'failed':
            title = 'VinylPi service unavailable'
            body = f'{unit} has remained inactive or failed for at least 3 minutes. Please check VinylPi services.'
        else:
            title = 'VinylPi service recovered'
            body = f'{unit} is active again after a monitored outage.'
        ok = False
        err = ''
        try:
            ok = bool(await sender('health', title, body, DASHBOARD_URL))
            if not ok:
                err = 'Discord sender not configured'
        except NotificationError as exc:
            err = str(getattr(exc, 'status', 'Discord notification failed'))[:160]
        except Exception:
            # Do not crash the monitor or leak exception content/tokens.
            err = 'Unexpected Discord notification failure'
            LOG.exception('Unexpected health alert delivery exception')
        with db:
            db.execute('''UPDATE messages SET attempts=attempts+1,
                delivered=CASE WHEN ? THEN ? ELSE delivered END,
                next_try=CASE WHEN ? THEN next_try ELSE ? END,
                last_error=? WHERE id=? AND delivered IS NULL''',
                (int(ok), now, int(ok), now+RETRY_AFTER, '' if ok else err, row['id']))
        LOG.info('%s %s: %s', unit, row['kind'], 'delivered' if ok else 'queued for retry')


async def run_once(path=DB, sampler=sample_unit, sender=notify, now=None):
    now = time.time() if now is None else now
    db = connect(path)
    try:
        observations = {unit: sampler(unit) for unit in UNITS}
        update_states(db, observations, now)
        await deliver(db, now, sender)
    finally:
        db.close()


async def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / 'incidents.db'
        values = {unit: True for unit in UNITS}
        sent = []
        async def fake_sender(category, title, body, url):
            sent.append((category, title, body, url))
            return True
        def fake_sample(unit):
            return values[unit]
        await run_once(path, fake_sample, fake_sender, 1000)
        values[UNITS[0]] = False
        await run_once(path, fake_sample, fake_sender, 1060)
        await run_once(path, fake_sample, fake_sender, 1180)
        assert not sent, 'Premature alert'
        await run_once(path, fake_sample, fake_sender, 1240)
        assert len(sent) == 1 and 'unavailable' in sent[0][1]
        await run_once(path, fake_sample, fake_sender, 1300)
        assert len(sent) == 1, 'Duplicate failure alert'
        values[UNITS[0]] = True
        await run_once(path, fake_sample, fake_sender, 1360)
        assert len(sent) == 2 and 'recovered' in sent[1][1]
        await run_once(path, fake_sample, fake_sender, 1420)
        assert len(sent) == 2, 'Duplicate recovery alert'
        # Retry failure, then send recovery in correct order.
        sent.clear()
        values[UNITS[1]] = False
        await run_once(path, fake_sample, fake_sender, 1500)
        async def failing_sender(*args):
            raise NotificationError('Simulated failure')
        await run_once(path, fake_sample, failing_sender, 1690)
        values[UNITS[1]] = True
        await run_once(path, fake_sample, fake_sender, 1750)
        assert len(sent) == 0, 'Recovery must wait for failed alert retry'
        await run_once(path, fake_sample, fake_sender, 1880)
        assert len(sent) == 1 and 'unavailable' in sent[0][1]
        await run_once(path, fake_sample, fake_sender, 1940)
        assert len(sent) == 2 and 'recovered' in sent[1][1]
    print('PASS: 3-minute debounce and single failure alert')
    print('PASS: Recovery alert and no duplicates')
    print('PASS: Failed sends retry, delivery order preserved')
    print('PASS: No real systemd queries, Discord messages, or production writes')


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    p = argparse.ArgumentParser()
    p.add_argument('--self-test', action='store_true')
    args = p.parse_args()
    if args.self_test:
        asyncio.run(self_test())
    else:
        asyncio.run(run_once())


if __name__ == '__main__':
    main()
