"""Compact SQLite jobs with bounded history and preserved destination mappings."""
import hashlib
import json
import sqlite3
import time
import uuid
import re
from pathlib import Path
from contextlib import contextmanager

class StoreError(Exception): pass
class KeyConflict(StoreError): pass
class CapacityError(StoreError): pass

class JobStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, idem TEXT UNIQUE NOT NULL,
                fingerprint TEXT NOT NULL, created REAL NOT NULL,
                updated REAL NOT NULL, status TEXT NOT NULL,
                processed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL,
                payload TEXT NOT NULL, result TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS mappings (
                id TEXT PRIMARY KEY, source_url TEXT, destination TEXT NOT NULL,
                account_id TEXT NOT NULL, destination_playlist_id TEXT,
                destination_name TEXT NOT NULL, mode TEXT NOT NULL, created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS job_status ON jobs(status,created);
            CREATE TABLE IF NOT EXISTS youtube_notification_events (
                job_id TEXT NOT NULL, kind TEXT NOT NULL, sent_at REAL,
                attempts INTEGER NOT NULL DEFAULT 0, last_attempt REAL, last_error TEXT,
                PRIMARY KEY(job_id,kind));
            CREATE TABLE IF NOT EXISTS quota_discord_notifications (
                job_id TEXT PRIMARY KEY, sent_at REAL, attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt REAL, last_error TEXT);
            CREATE TABLE IF NOT EXISTS apple_video_notification_events (
                job_id TEXT NOT NULL, part TEXT NOT NULL,
                sent_at REAL, attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt REAL, last_error TEXT,
                PRIMARY KEY(job_id,part));
            
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        # 32 MiB main DB. DELETE journal has space within the 75 MiB total budget.
        page_size = db.execute('PRAGMA page_size').fetchone()[0]
        db.execute(f'PRAGMA max_page_count={32*1024*1024//page_size}')
        try:
            with db:
                yield db
        finally:
            db.close()

    def maintain(self, db):
        cutoff = time.time() - 90*86400
        db.execute("DELETE FROM jobs WHERE status NOT IN ('queued','running','waiting_for_quota','waiting_for_rate_limit','awaiting_approval','creation_queued','creating_review','handoff_pending') AND created < ?", (cutoff,))
        db.execute("DELETE FROM jobs WHERE id IN (SELECT id FROM jobs WHERE status NOT IN ('queued','running','waiting_for_quota','waiting_for_rate_limit','awaiting_approval','creation_queued','creating_review','handoff_pending') ORDER BY created DESC LIMIT -1 OFFSET 1000)")
        # Never remove mappings. SQLite reuses freed pages without repeated VACUUM.

    def create(self, payload, key):
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                existing = db.execute('SELECT * FROM jobs WHERE idem=?', (key,)).fetchone()
                if existing:
                    if existing['fingerprint'] != fingerprint:
                        raise KeyConflict('Idempotency key was already used with different input.')
                    return self.public(existing), False
                self.maintain(db)
                if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','waiting_for_quota','waiting_for_rate_limit','creation_queued','creating_review')").fetchone()[0] >= 20:
                    raise CapacityError('20 jobs are already pending; wait before submitting more.')
                now = time.time()
                jid = str(uuid.uuid4())
                # Soundiiz parents are persistent plans, not worker jobs. A separate,
                # explicitly requested operation will create individual handoffs later.
                soundiiz = payload['mode'] == 'soundiiz_transfer'
                if soundiiz:
                    # Import only for this mode: legacy YouTube tests load store.py standalone.
                    from music_bridge.soundiiz import build_manifest
                    initial_result = json.dumps(build_manifest(payload), ensure_ascii=False)
                else:
                    initial_result = None
                initial_status = 'handoff_pending' if soundiiz else 'queued'
                db.execute('INSERT INTO jobs(id,idem,fingerprint,created,updated,status,total,payload,result) VALUES(?,?,?,?,?,?,?,?,?)',
                    (jid, key, fingerprint, now, now, initial_status, len(payload['tracks']), serialized, initial_result))
                return self.public(db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()), True
        except sqlite3.DatabaseError as exc:
            raise CapacityError('Playlist storage unavailable or full; no new job accepted.') from exc

    @staticmethod
    def public(row):
        return {'job_id': row['id'], 'status': row['status'], 'processed': row['processed'],
            'total': row['total'], 'result': json.loads(row['result']) if row['result'] else None,
            'error': row['error']}

    def get(self, jid):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
            return self.public(row) if row else None

    def list_recent(self, limit=50):
        """Bounded job summaries only. Never expose payloads, tokens or candidate data."""
        limit = max(1, min(int(limit), 100))
        with self.connect() as db:
            rows = db.execute("""SELECT id,status,processed,total,created,updated,payload,result
                FROM jobs ORDER BY
                CASE WHEN status IN ('queued','running','creation_queued','creating_review','awaiting_approval','waiting_for_quota','waiting_for_rate_limit','needs_review','interrupted') THEN 0 ELSE 1 END,
                updated DESC LIMIT ?""", (limit,)).fetchall()
        jobs = []
        for row in rows:
            payload = json.loads(row['payload'])
            jobs.append({'job_id': row['id'], 'title': payload.get('title') or 'Untitled',
                         'mode': payload.get('mode'), 'status': row['status'],
                         'processed': row['processed'], 'total': row['total'],
                         'created': row['created'], 'updated': row['updated'],
                         'review_before_create': bool(payload.get('review_before_create'))})
        return jobs

    def take(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE status IN ('queued','creation_queued') ORDER BY created LIMIT 1").fetchone()
            if not row: return None
            creation = row['status'] == 'creation_queued'
            db.execute('UPDATE jobs SET status=?,updated=? WHERE id=?',
                       ('creating_review' if creation else 'running', time.time(), row['id']))
            payload = json.loads(row['payload'])
            if creation:
                payload['_review_creation'] = True  # Internal worker-only flag, not persisted.
            return row['id'], payload

    def update(self, jid, status, processed=0, result=None, error=None):
        with self.connect() as db:
            # Read old state in the same transaction. Historical completions are NOT backfilled.
            row = db.execute('SELECT status,payload FROM jobs WHERE id=?', (jid,)).fetchone()
            if (row and row['status'] != 'completed' and status == 'completed'
                    and (result or {}).get('destination') == 'apple_music'
                    and json.loads(row['payload']).get('mode') == 'apple_video_plan'):
                for part in ('details', 'id'):
                    db.execute('INSERT OR IGNORE INTO apple_video_notification_events(job_id,part) VALUES(?,?)',
                               (jid, part))
            db.execute('UPDATE jobs SET status=?,processed=?,result=?,error=?,updated=? WHERE id=?',
                (status, processed, json.dumps(result, ensure_ascii=False) if result is not None else None, error, time.time(), jid))
            if status == 'waiting_for_rate_limit':
                db.execute("INSERT OR IGNORE INTO youtube_notification_events(job_id,kind) VALUES(?, 'rate_limit_seen')", (jid,))
            if status == 'waiting_for_quota':
                # Remember permanently for this job, even after it runs again.
                db.execute('INSERT OR IGNORE INTO quota_discord_notifications(job_id) VALUES(?)', (jid,))

    def recover(self):
        # A crash during a remote request has an uncertain outcome. Do not retry it silently.
        with self.connect() as db:
            rows = db.execute("SELECT id,result FROM jobs WHERE status='handoff_pending'").fetchall()
            for row in rows:
                result = json.loads(row['result'] or '{}')
                changed = False
                for part in result.get('parts', []):
                    if part.get('status') == 'generating':
                        part['status'] = 'uncertain'
                        changed = True
                if changed:
                    db.execute('UPDATE jobs SET result=?,updated=? WHERE id=?',
                        (json.dumps(result, ensure_ascii=False), time.time(), row['id']))

        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE jobs SET status='interrupted',error=?,updated=? WHERE status='creating_review'",
                ('Review creation interrupted. Inspect saved YouTube checkpoint and remote playlist; no automatic write retries.', time.time()))
            rows = db.execute("SELECT id,payload FROM jobs WHERE status='running'").fetchall()
            for row in rows:
                payload = json.loads(row['payload'])
                if payload.get('mode') == 'youtube_video_playlist':
                    creation_state = self.path.parent / 'youtube-jobs' / (row['id'] + '.json')
                    # Only read-only search jobs are safe to replay. Creation state
                    # appears before any remote write and must be reviewed manually.
                    if not creation_state.exists():
                        db.execute("UPDATE jobs SET status='queued',error=NULL,updated=? WHERE id=?", (time.time(), row['id']))
                        continue
                    reason = 'Service restarted after YouTube playlist creation began. Review saved creation state; no automatic write retries.'
                else:
                    reason = 'Service restarted during job. Submit a new job if needed.'
                db.execute("UPDATE jobs SET status='interrupted',error=?,updated=? WHERE id=?", (reason, time.time(), row['id']))

    def claim_soundiiz_part(self, jid, number, regenerate=False):
        """Claim a single handoff before network I/O; another caller cannot double-submit."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload,result,status FROM jobs WHERE id=?', (jid,)).fetchone()
            if row is None:
                raise KeyError('Job not found.')
            payload = json.loads(row['payload'])
            if payload.get('mode') != 'soundiiz_transfer' or row['status'] != 'handoff_pending':
                raise KeyConflict('Not a Soundiiz parent job.')
            result = json.loads(row['result'] or '{}')
            if type(number) is not int or not 1 <= number <= len(result.get('parts', [])):
                raise ValueError('Invalid part number.')
            part = result['parts'][number - 1]
            state = part['status']
            if part.get('marked_transferred'):
                raise KeyConflict('Part already confirmed transferred; do not regenerate or re-submit it.')
            if state == 'ready' and part.get('expires_at', 0) > time.time():
                return None, self.public(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
            if state == 'generating':
                raise KeyConflict('This part is already being generated.')
            if state in ('ready', 'uncertain') and not regenerate:
                raise KeyConflict('This part has expired or its previous outcome is uncertain. Explicit regeneration required.')
            raw = payload['tracks'][part['start_index']:part['end_index_exclusive']]
            if len(raw) != part['track_count'] or not 1 <= len(raw) <= 200:
                raise ValueError('Stored part positions are invalid.')
            part.update(status='generating', handoff_url=None, expires_at=None)
            db.execute('UPDATE jobs SET result=?,updated=? WHERE id=?',
                (json.dumps(result, ensure_ascii=False), time.time(), jid))
            return {'title':part['title'], 'tracks':raw}, None

    def finish_soundiiz_part(self, jid, number, handoff=None):
        """Persist exactly one part without overwriting results from other parts."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload,result FROM jobs WHERE id=?', (jid,)).fetchone()
            if row is None or json.loads(row['payload']).get('mode') != 'soundiiz_transfer':
                raise KeyError('Soundiiz job not found.')
            result = json.loads(row['result'])
            part = result['parts'][number - 1]
            if part['status'] != 'generating':
                raise KeyConflict('Part is not being generated.')
            if handoff is None:
                part.update(status='uncertain', handoff_url=None, expires_at=None)
            else:
                part.update(status='ready', **handoff)
            from music_bridge.soundiiz import refresh_notes
            refresh_notes(result)
            db.execute('UPDATE jobs SET result=?,updated=? WHERE id=?',
                (json.dumps(result, ensure_ascii=False), time.time(), jid))
            return self.public(db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone())

    def mark_soundiiz_transferred(self, jid, number, destination=None):
        """User acknowledgement only; preserve successful handoffs and prior confirmations."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload,result,status FROM jobs WHERE id=?',(jid,)).fetchone()
            if row is None or json.loads(row['payload']).get('mode') != 'soundiiz_transfer':
                raise KeyError('Soundiiz job not found.')
            if row['status'] not in ('handoff_pending', 'completed'):
                raise KeyConflict('Soundiiz job cannot be confirmed in its current state.')
            result = json.loads(row['result'])
            if type(number) is not int or not 1 <= number <= len(result['parts']):
                raise ValueError('Invalid part number.')
            part = result['parts'][number - 1]
            if part['status'] != 'ready':
                raise KeyConflict('Part has no confirmed handoff.')
            existing_destination = part.get('transferred_to')
            if destination is not None and existing_destination is not None and destination != existing_destination:
                raise KeyConflict('Destination already recorded differently for this part.')
            if destination is not None and existing_destination is None:
                part['transferred_to'] = destination
            if not part.get('marked_transferred'):
                part['marked_transferred'] = True
                part['transferred_at'] = time.time()
            from music_bridge.soundiiz import refresh_notes
            refresh_notes(result)
            completed = all(p.get('marked_transferred') for p in result['parts'])
            if completed:
                result['completed_by_user_at'] = result.get('completed_by_user_at') or time.time()
            status = 'completed' if completed else 'handoff_pending'
            count = sum(p['track_count'] for p in result['parts'] if p.get('marked_transferred'))
            db.execute('UPDATE jobs SET status=?,processed=?,result=?,updated=? WHERE id=?',
                (status, count, json.dumps(result, ensure_ascii=False), time.time(), jid))
            return self.public(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())

    def set_youtube_review_decision(self, jid, index, action, video_id=None):
        """Save a review decision atomically; never perform a remote write."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status,payload,result FROM jobs WHERE id=?', (jid,)).fetchone()
            if row is None:
                raise KeyError('Job not found')
            if row['status'] != 'awaiting_approval':
                raise KeyConflict('Job is not awaiting approval')
            payload = json.loads(row['payload'])
            result = json.loads(row['result'])
            if payload.get('mode') != 'youtube_video_playlist' or not payload.get('review_before_create'):
                raise KeyConflict('Not a review-mode YouTube job')
            if type(index) is not int or not (0 <= index < len(payload['tracks'])):
                raise ValueError('Invalid track index')
            choices = result['review_candidates'][index]['candidates']
            if action == 'skip':
                decision = {'action': 'skip'}
            elif action == 'approve':
                if (
                    not isinstance(video_id, str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id)
                    or not any(c.get('video_id') == video_id for c in choices)
                ):
                    raise ValueError('Choose a candidate from this track\'s saved search results')
                decision = {'action': 'approve', 'video_id': video_id}
            else:
                raise ValueError('Invalid review action')
            result.setdefault('review_decisions', {})[str(index)] = decision
            db.execute('UPDATE jobs SET result=?,updated=? WHERE id=?',
                       (json.dumps(result, ensure_ascii=False), time.time(), jid))
            return {'job_id': jid, 'status': row['status'], 'reviewed_count': len(result['review_decisions']),
                    'total': len(payload['tracks']), 'decision': decision}


    def queue_review_creation(self, jid):
        """Freeze reviewed choices once; repeated confirmation cannot enqueue twice.

        No remote YouTube writes. The worker performs those later after this
        transaction commits. Does not assume an unverified uploader is official.
        """
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
            if row is None:
                raise KeyError('Job not found')
            if row['status'] != 'awaiting_approval':
                raise KeyConflict('This job is not available for creation. Do not submit it twice.')
            payload = json.loads(row['payload'])
            if payload.get('mode') != 'youtube_video_playlist' or not payload.get('review_before_create'):
                raise KeyConflict('Only reviewed YouTube video jobs can be created')
            result = json.loads(row['result'] or '{}')
            rows = result.get('review_candidates')
            if not isinstance(rows, list) or len(rows) != len(payload['tracks']):
                raise ValueError('Search results incomplete; no playlist queued')
            decisions = result.get('review_decisions') or {}
            plan = []
            for index, entry in enumerate(rows):
                if not isinstance(entry, dict) or entry.get('source_index') != index:
                    raise ValueError('Candidate order mismatch; no playlist queued')
                choices = entry.get('candidates') or []
                decision = decisions.get(str(index))
                if decision is not None and decision.get('action') == 'skip':
                    plan.append(None)
                    continue
                if decision is not None and decision.get('action') == 'approve':
                    chosen = decision.get('video_id')
                else:
                    chosen = entry.get('suggested_video_id')
                    if chosen is None:
                        chosen = next((c.get('video_id') for c in choices if c.get('metadata_eligible')
                                       and c.get('kind') in ('music_video','lyric_video')), None)
                if chosen is None:
                    plan.append(None)
                    continue
                if not isinstance(chosen, str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}', chosen):
                    raise ValueError('Invalid selected video; no playlist queued')
                if not any(c.get('video_id') == chosen for c in choices):
                    raise ValueError('Selected video missing from saved candidates; no playlist queued')
                plan.append(chosen)
            if not any(plan):
                raise ValueError('No videos selected; no playlist queued')
            state_path = self.path.parent / 'youtube-jobs' / (jid + '.json')
            if state_path.exists():
                raise KeyConflict('Creation checkpoint already exists; inspect it before any new write')
            result['creation_plan'] = plan
            result['creation_confirmed_at'] = time.time()
            db.execute('UPDATE jobs SET status=?,result=?,updated=? WHERE id=?',
                       ('creation_queued', json.dumps(result, ensure_ascii=False), time.time(), jid))
            return {'job_id': jid, 'status': 'creation_queued',
                    'selected_count': sum(v is not None for v in plan),
                    'skipped_count': len(plan) - sum(v is not None for v in plan)}

    def requeue_due_quota_jobs(self, now_iso):
        """Requeue only explicitly paused read-only search jobs after their retry time."""
        from datetime import datetime, timezone
        now = datetime.fromisoformat(now_iso)
        count = 0
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT id,result FROM jobs WHERE status IN ('waiting_for_quota','waiting_for_rate_limit')").fetchall()
            for row in rows:
                result = json.loads(row['result']) if row['result'] else {}
                retry = result.get('retry_at')
                try:
                    due = retry is not None and datetime.fromisoformat(retry) <= now
                except (ValueError, TypeError):
                    due = False
                if due:
                    db.execute("UPDATE jobs SET status='queued',error=NULL,updated=? WHERE id=? AND status IN ('waiting_for_quota','waiting_for_rate_limit')",
                               (time.time(), row['id']))
                    count += 1
        return count


    def queue_youtube_event(self, jid, kind):
        """Queue a notification for an explicit YouTube job outcome."""
        if kind not in ('no_matches', 'needs_review'):
            raise ValueError('Invalid notification kind')
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO youtube_notification_events(job_id,kind) VALUES(?,?)', (jid,kind))

    def pending_youtube_events(self):
        """Derive terminal HTTP 429 outcomes from a durable marker; exclude ordinary jobs."""
        now = time.time()
        with self.connect() as db:
            rate_rows = db.execute("""SELECT e.job_id,j.status,j.result FROM youtube_notification_events e
                JOIN jobs j ON j.id=e.job_id WHERE e.kind='rate_limit_seen'
                AND j.status IN ('completed','failed')""").fetchall()
            for row in rate_rows:
                kind = 'rate_limit_completed' if row['status']=='completed' else 'rate_limit_failed'
                db.execute('INSERT OR IGNORE INTO youtube_notification_events(job_id,kind) VALUES(?,?)', (row['job_id'],kind))
            rows = db.execute("""SELECT e.job_id,e.kind,j.status,j.result,j.error,j.payload
                FROM youtube_notification_events e JOIN jobs j ON j.id=e.job_id
                WHERE e.kind IN ('no_matches','needs_review','rate_limit_completed','rate_limit_failed')
                  AND e.sent_at IS NULL AND (e.last_attempt IS NULL OR e.last_attempt < ?)
                ORDER BY j.updated LIMIT 10""", (now-120,)).fetchall()
        out=[]
        for row in rows:
            if row['kind']=='no_matches' and row['status']!='failed': continue
            if row['kind']=='needs_review' and row['status']!='needs_review': continue
            if row['kind']=='rate_limit_completed' and row['status']!='completed': continue
            if row['kind']=='rate_limit_failed' and row['status']!='failed': continue
            try:
                payload=json.loads(row['payload']); result=json.loads(row['result'] or '{}')
            except (ValueError, TypeError): continue
            if payload.get('mode')!='youtube_video_playlist': continue
            if row['kind']=='rate_limit_completed' and not (result.get('creation_verified') and result.get('playlist_url')): continue
            out.append((row['job_id'], row['kind'], payload, result, row['error']))
        return out

    def mark_youtube_event(self, jid, kind, success, error=None):
        with self.connect() as db:
            db.execute("""UPDATE youtube_notification_events
                SET attempts=attempts+1,last_attempt=?,sent_at=CASE WHEN ? THEN ? ELSE sent_at END,last_error=?
                WHERE job_id=? AND kind=? AND sent_at IS NULL""",
                (time.time(),bool(success),time.time(),None if success else str(error or 'send_failed')[:200],jid,kind))

    def pending_apple_video_notifications(self):
        """Only events created after this feature was installed, never historical jobs."""
        now = time.time()
        with self.connect() as db:
            rows = db.execute("""SELECT e.job_id,e.part,j.payload
                FROM apple_video_notification_events e JOIN jobs j ON j.id=e.job_id
                WHERE e.sent_at IS NULL AND j.status='completed'
                  AND (e.last_attempt IS NULL OR e.last_attempt < ?)
                  AND (e.part='details' OR NOT EXISTS (
                      SELECT 1 FROM apple_video_notification_events d
                      WHERE d.job_id=e.job_id AND d.part='details' AND d.sent_at IS NULL))
                ORDER BY j.updated, CASE e.part WHEN 'details' THEN 0 ELSE 1 END
                LIMIT 10""", (now-120,)).fetchall()
        return [(r['job_id'], r['part'], json.loads(r['payload']).get('title') or 'Music video playlist')
                for r in rows]

    def mark_apple_video_notification(self, jid, part, success, error=None):
        if part not in ('details', 'id'):
            raise ValueError('Invalid Apple notification part')
        with self.connect() as db:
            db.execute("""UPDATE apple_video_notification_events
                SET attempts=attempts+1,last_attempt=?,sent_at=CASE WHEN ? THEN ? ELSE sent_at END,
                    last_error=? WHERE job_id=? AND part=? AND sent_at IS NULL""",
                (time.time(), int(bool(success)), time.time(),
                 None if success else str(error or 'send_failed')[:200], jid, part))

    def pending_discord_notifications(self):
        """Only verified completed YouTube jobs that previously hit daily quota."""
        now = time.time()
        with self.connect() as db:
            rows = db.execute("""SELECT n.job_id, j.result FROM quota_discord_notifications n
                JOIN jobs j ON j.id=n.job_id
                WHERE n.sent_at IS NULL AND j.status='completed'
                  AND (n.last_attempt IS NULL OR n.last_attempt < ?)
                ORDER BY j.updated LIMIT 10""", (now - 120,)).fetchall()
        out = []
        for row in rows:
            try:
                result = json.loads(row['result'] or '{}')
            except (ValueError, TypeError):
                continue
            if result.get('destination') == 'youtube' and result.get('creation_verified') is True and result.get('playlist_url'):
                out.append((row['job_id'], result))
        return out

    def mark_discord_attempt(self, jid, success, error=None):
        with self.connect() as db:
            db.execute("""UPDATE quota_discord_notifications
                SET attempts=attempts+1, last_attempt=?,
                    sent_at=CASE WHEN ? THEN ? ELSE sent_at END, last_error=?
                WHERE job_id=? AND sent_at IS NULL""",
                (time.time(), bool(success), time.time(), None if success else str(error or 'send_failed')[:200], jid))

    def record_apple_creation(self, jid, account_alias, store_ids):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not row: raise KeyError('Job not found.')
            result = json.loads(row['result']) if row['result'] else None
            if row['status'] != 'completed' or not result or result.get('destination') != 'apple_music':
                raise KeyConflict('Only completed Apple video plans can be acknowledged.')
            # Accept an ordered subsequence: iOS may be unable to rehydrate every candidate.
            expected = [x['store_id'] for x in result['matches']]
            cursor = 0
            for item in store_ids:
                while cursor < len(expected) and expected[cursor] != item: cursor += 1
                if cursor == len(expected): raise KeyConflict('Created video IDs are not an ordered subset of this plan.')
                cursor += 1
            existing = db.execute('SELECT * FROM mappings WHERE id=?',(jid,)).fetchone()
            if existing:
                if existing['account_id'] != account_alias or result.get('created_store_ids') != store_ids:
                    raise KeyConflict('Creation was already recorded with different input.')
                return self.public(row)
            payload = json.loads(row['payload'])
            db.execute('INSERT INTO mappings VALUES(?,?,?,?,?,?,?,?)',
                (jid,payload.get('source_url'),'apple_music',account_alias,None,result['playlist_name'],'videos',time.time()))
            result.update(playlist_created=True,requires_ios_creation=False,
                created_store_ids=store_ids,created_count=len(store_ids),
                ios_skipped_count=len(expected)-len(store_ids),creation_reported_by_client=True)
            db.execute('UPDATE jobs SET result=?,updated=? WHERE id=?',(json.dumps(result),time.time(),jid))
            return self.public(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())

    def record_youtube_creation(self, jid, result, account_id):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM jobs WHERE id=?', (jid,)).fetchone()
            payload = json.loads(row['payload'])
            db.execute('INSERT OR IGNORE INTO mappings VALUES(?,?,?,?,?,?,?,?)',
                (jid, payload.get('source_url'), 'youtube', account_id, result['playlist_id'],
                 result['playlist_name'], 'videos', time.time()))
