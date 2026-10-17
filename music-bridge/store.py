"""Compact SQLite jobs with bounded history and preserved destination mappings."""
import hashlib
import json
import sqlite3
import time
import uuid
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
        db.execute("DELETE FROM jobs WHERE status NOT IN ('queued','running') AND created < ?", (cutoff,))
        db.execute("DELETE FROM jobs WHERE id IN (SELECT id FROM jobs WHERE status NOT IN ('queued','running') ORDER BY created DESC LIMIT -1 OFFSET 1000)")
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
                if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 20:
                    raise CapacityError('20 jobs are already pending; wait before submitting more.')
                now = time.time()
                jid = str(uuid.uuid4())
                db.execute('INSERT INTO jobs(id,idem,fingerprint,created,updated,status,total,payload) VALUES(?,?,?,?,?,?,?,?)',
                    (jid, key, fingerprint, now, now, 'queued', len(payload['tracks']), serialized))
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

    def take(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not row: return None
            db.execute("UPDATE jobs SET status='running',updated=? WHERE id=?", (time.time(), row['id']))
            return row['id'], json.loads(row['payload'])

    def update(self, jid, status, processed=0, result=None, error=None):
        with self.connect() as db:
            db.execute('UPDATE jobs SET status=?,processed=?,result=?,error=?,updated=? WHERE id=?',
                (status, processed, json.dumps(result, ensure_ascii=False) if result is not None else None, error, time.time(), jid))

    def recover(self):
        # A crash during a remote request has an uncertain outcome. Do not retry it silently.
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted',error=CASE WHEN json_extract(payload, '$.mode') = 'youtube_video_playlist' THEN 'Service restarted during YouTube job. Inspect the saved playlist and checkpoints; do not submit a replacement job blindly.' ELSE 'Service restarted during job. Submit a new job if needed.' END,updated=? WHERE status='running'", (time.time(),))

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
