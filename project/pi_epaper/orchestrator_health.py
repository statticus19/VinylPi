"""Bounded, redacted orchestrator diagnostics. Standard library only."""
from collections import deque
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote


class Redactor:
    def __init__(self, secrets=()):
        values = list(secrets) + [value for key,value in os.environ.items()
                   if re.search(r'PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY|AUTHORIZATION',key,re.I)]
        self.secrets = sorted({variant for value in values if isinstance(value,str) and len(value)>=4
                              for variant in (value,quote(value,safe=''))},key=len,reverse=True)

    def clean(self, text):
        text = str(text)
        for value in self.secrets:
            text = text.replace(value,'[redacted]')
        text = re.sub(r'(https?://)[^/\s@]+@',r'\1[redacted]@',text)
        text = re.sub(r'(?i)\b(Basic|Bearer)\s+[A-Za-z0-9+/=._-]+',r'\1 [redacted]',text)
        text = re.sub(r'(?i)(\b(?:api[_-]?key|api[_-]?secret|password(?:_hash)?|access[_-]?(?:token|key)|token|authorization)\b["\s]*[:=]["\s]*)[^\s&"\',;]+',r'\1[redacted]',text)
        return text[:1000]


class HealthMonitor(logging.Handler):
    def __init__(self, directory, secrets=(), interval=5):
        super().__init__(logging.INFO)
        self.directory=Path(directory)
        self.path=self.directory/'orchestrator_health.json'
        self.guard=threading.RLock()
        self.redactor=Redactor(secrets)
        self.closed_event=threading.Event()
        self.interval=interval
        self.events=deque(maxlen=60)
        self.status={'pid':os.getpid(),'started':time.time(),'stage':'starting',
                     'last_activity':time.time(),'lastfm':'unknown','scrobble_status':'No submission in this run'}
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        try:
            previous=json.loads(self.path.read_text())
            self.status['last_scrobble_success']=previous.get('last_scrobble_success')
        except (OSError,ValueError):
            pass
        logging.getLogger().addHandler(self)
        self.flush_snapshot()
        self.worker=threading.Thread(target=self.run,daemon=True)
        self.worker.start()

    def phase(self, stage):
        with self.guard:
            if self.status['stage'] != stage:
                self.status['stage']=stage
                self.status['last_activity']=time.time()

    def emit(self, record):
        try:
            text=self.redactor.clean(record.getMessage())
            with self.guard:
                self.events.append({'at':record.created,'level':record.levelname,'message':text})
                self.status['last_activity']=record.created
                if 'Capturing audio sample' in text:self.status['stage']='listening'
                elif 'Querying Shazam' in text:self.status['stage']='identifying'
                elif 'Stream is silent/idle' in text:self.status['stage']='silent'
                elif 'No match found' in text:self.status['stage']='no_match'
                elif 'MATCH FOUND:' in text:
                    self.status.update(stage='matched',last_match={'at':record.created,'track':text.split('MATCH FOUND:',1)[1].strip()})
                elif 'Scrobbling to Last.fm:' in text:
                    self.status.update(stage='scrobbling',last_scrobble_attempt=record.created,scrobble_status='Submission in progress')
                elif 'Scrobble accepted by Last.fm' in text:
                    self.status.update(stage='matched',last_scrobble_success=record.created,scrobble_status='Last.fm accepted the submission',lastfm='configured')
                elif 'Scrobble result uncertain' in text:
                    self.status.update(stage='error',scrobble_status='Submission result uncertain; duplicate retry suppressed')
                elif 'Skipping scrobble' in text or 'already submitted' in text:
                    self.status['scrobble_status']=text
                if 'Connected to Last.fm' in text:self.status['lastfm']='configured'
                if 'credentials incomplete' in text:self.status.update(lastfm='disabled',scrobble_status='Last.fm credentials incomplete')
                if 'Last.fm authentication failed' in text:self.status.update(lastfm='error',scrobble_status='Last.fm authentication failed')
                if 'Last.fm client not connected' in text:self.status.update(lastfm='disabled',scrobble_status=text)
                match=re.search(r'Sample audio level: (-?[\d.]+) dB',text)
                if match:self.status['audio_db']=float(match[1])
                if record.levelno >= logging.ERROR:
                    self.status.update(last_error={'at':record.created,'message':text},stage='error')
        except Exception:
            # Diagnostics must never raise into the recognition/scrobbling flow.
            pass

    def flush_snapshot(self):
        try:
            with self.guard:
                value=dict(self.status,at=time.time(),logs=list(self.events))
            tmp=self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(value))
            os.chmod(tmp,0o600)
            os.replace(tmp,self.path)
        except OSError:
            pass

    def run(self):
        while not self.closed_event.wait(self.interval):
            self.flush_snapshot()

    def close(self):
        if not self.closed_event.is_set():
            self.closed_event.set()
            logging.getLogger().removeHandler(self)
            if hasattr(self,'worker'):self.worker.join(timeout=1)
            self.phase('stopped')
            self.flush_snapshot()
        super().close()
