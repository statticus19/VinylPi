"""Private, bounded dashboard audit trail. No credentials or passwords stored."""
import ipaddress
import logging
import os
import sqlite3
import time
from pathlib import Path

log=logging.getLogger('vinylpi.notification_history')
DB=Path(os.environ.get('VINYLPI_NOTIFICATION_DB',str(Path.home()/'.local/share/vinylpi-dashboard/notifications.db')))

def connect():
    DB.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    conn=sqlite3.connect(DB,timeout=5)
    conn.execute('''CREATE TABLE IF NOT EXISTS events
       (id INTEGER PRIMARY KEY AUTOINCREMENT, occurred REAL NOT NULL, kind TEXT NOT NULL,
        status TEXT NOT NULL, actor TEXT NOT NULL, client_ip TEXT NOT NULL,
        content TEXT NOT NULL, source TEXT NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS events_by_date ON events(occurred DESC)')
    try: os.chmod(DB,0o600)
    except OSError: pass
    return conn

def record_event(kind,status,actor='vinyl',ip='',content='',source='dashboard'):
    if kind not in ('login','discord','system') or len(str(content))>1500:
        raise ValueError('Invalid audit event')
    # request.client.host is resolved by Uvicorn/Tailscale; don't consume browser headers.
    try: client=str(ipaddress.ip_address(ip)) if ip else ''
    except ValueError: client='unknown'
    with connect() as db:
        db.execute('DELETE FROM events WHERE occurred < ?', (time.time()-30*86400,))
        db.execute('INSERT INTO events(occurred,kind,status,actor,client_ip,content,source) VALUES(?,?,?,?,?,?,?)',
                   (time.time(),kind,str(status)[:32],str(actor)[:32],client,str(content)[:1500],str(source)[:50]))

def record_event_safe(*args,**kwargs):
    try: record_event(*args,**kwargs)
    except (OSError,sqlite3.Error,ValueError) as exc:
        # Logging failures must not alter whether login succeeds or fails.
        log.warning('Audit event could not be recorded: %s',type(exc).__name__)

def recent(kind=None,limit=100):
    with connect() as db:
        db.row_factory=sqlite3.Row
        db.execute('DELETE FROM events WHERE occurred < ?', (time.time()-30*86400,))
        if kind:
            rows=db.execute('SELECT * FROM events WHERE kind=? ORDER BY occurred DESC LIMIT ?', (kind,min(limit,200))).fetchall()
        else:
            rows=db.execute('SELECT * FROM events ORDER BY occurred DESC LIMIT ?', (min(limit,200),)).fetchall()
    from datetime import datetime
    return [dict(r,when=datetime.fromtimestamp(r['occurred']).astimezone().strftime('%Y-%m-%d %I:%M:%S %p %Z')) for r in rows]
