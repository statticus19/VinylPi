"""Audfprint landmark extraction with a compact, transactional SQLite index."""
from collections import defaultdict
import json
import sqlite3
import sys
import subprocess
import threading
from pathlib import Path

VENDOR = Path(__file__).parent / 'vendor' / 'audfprint'


class FingerprintEngine:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.analysis_lock = threading.Lock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS songs (
                    id TEXT PRIMARY KEY, metadata TEXT NOT NULL, hashes INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS landmarks (
                    song TEXT NOT NULL REFERENCES songs(id), t INTEGER NOT NULL, h INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS landmark_hash ON landmarks(h);
                CREATE INDEX IF NOT EXISTS landmark_song ON landmarks(song);
                PRAGMA user_version=1;
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def hashes(self, path):
        with self.analysis_lock:
            if str(VENDOR) not in sys.path:
                sys.path.insert(0, str(VENDOR))
            import numpy as np
            from audfprint_analyze import Analyzer, landmarks2hashes
            decoded = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
                '-ar', '11025', '-ac', '1', '-f', 'f32le', '-'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=True)
            data = np.frombuffer(decoded.stdout, dtype='<f4').copy()
            analyzer = Analyzer()
            peaks = analyzer.find_peaks(data, 11025)
            hashes = landmarks2hashes(analyzer.peaks2landmarks(peaks))
            return sorted({(int(t), int(h)) for t, h in hashes})

    def save(self, tracks):
        """Compute everything before opening the all-or-nothing write transaction."""
        entries = []
        for track_id, path, metadata in tracks:
            hashes = self.hashes(path)
            if len(hashes) < 20:
                raise ValueError('Not enough usable audio landmarks for ' + metadata['title'])
            if not metadata.get('artist') or not metadata.get('title'):
                raise ValueError('Every track needs an artist and title')
            entries.append((track_id, metadata, hashes))
        with self.connect() as db:
            for track_id, metadata, hashes in entries:
                db.execute('DELETE FROM landmarks WHERE song=?', (track_id,))
                db.execute('INSERT OR REPLACE INTO songs VALUES (?,?,?)',
                           (track_id, json.dumps(metadata), len(hashes)))
                db.executemany('INSERT INTO landmarks VALUES (?,?,?)',
                               [(track_id, t, h) for t, h in hashes])
            # Verify saved counts inside the transaction before committing.
            for track_id, _, hashes in entries:
                count = db.execute('SELECT count(*) FROM landmarks WHERE song=?', (track_id,)).fetchone()[0]
                if count != len(hashes):
                    raise RuntimeError('Fingerprint storage verification failed')
        return len(entries)

    def list(self):
        with self.connect() as db:
            return [dict(json.loads(meta), id=id_, hashes=n)
                    for id_, meta, n in db.execute('SELECT id,metadata,hashes FROM songs ORDER BY rowid DESC')]

    def delete(self, track_id):
        with self.connect() as db:
            db.execute('DELETE FROM landmarks WHERE song=?', (track_id,))
            db.execute('DELETE FROM songs WHERE id=?', (track_id,))

    def match(self, path):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM songs LIMIT 1').fetchone():
                return None
        hashes = self.hashes(path)
        if len(hashes) < 12:
            return None
        query = defaultdict(list)
        for t, h in hashes:
            query[h].append(t)
        votes = defaultdict(set)
        keys = list(query)
        with self.connect() as db:
            for first in range(0, len(keys), 400):
                batch = keys[first:first+400]
                sql = 'SELECT song,t,h FROM landmarks WHERE h IN (%s)' % ','.join('?' * len(batch))
                for song, ref_t, h in db.execute(sql, batch):
                    for query_t in query[h]:
                        # One-frame tolerance on either side of each offset.
                        for offset in range(ref_t-query_t-1, ref_t-query_t+2):
                            votes[(song, offset)].add((query_t, h))
            best_by_song = {}
            for (song, offset), hits in votes.items():
                current = best_by_song.get(song)
                if current is None or len(hits) > len(current[1]):
                    best_by_song[song] = (offset, hits)
            ranked = sorted(best_by_song.items(), key=lambda row: len(row[1][1]), reverse=True)
            if not ranked:
                return None
            song, (offset, hits) = ranked[0]
            matched_times = [t for t, _ in hits]
            row = db.execute('SELECT metadata FROM songs WHERE id=?', (song,)).fetchone()
            if not row:
                return None
            meta = json.loads(row[0])
            identity = (meta['artist'].casefold(), meta['title'].casefold())
            second = 0
            for candidate, (_, candidate_hits) in ranked[1:]:
                other_row = db.execute('SELECT metadata FROM songs WHERE id=?', (candidate,)).fetchone()
                other_meta = json.loads(other_row[0])
                if (other_meta['artist'].casefold(), other_meta['title'].casefold()) != identity:
                    second = len(candidate_hits)
                    break
            if (len(hits) < 12 or len(hits)/len(hashes) < .04 or
                    max(matched_times)-min(matched_times) < 65 or len(hits) < 1.5*second):
                return None
            row = db.execute('SELECT metadata FROM songs WHERE id=?', (song,)).fetchone()
            if not row:
                return None
        meta = json.loads(row[0])
        return {'title': meta['title'], 'subtitle': meta['artist'], 'album': meta.get('album', ''),
                'images': {'coverart': meta.get('art_url', '')},
                'recognition_source': 'Local fingerprint', 'local_id': song}
