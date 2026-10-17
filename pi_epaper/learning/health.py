"""Read-only service status plus the orchestrator's bounded diagnostic snapshot."""
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path


class HealthReader:
    def __init__(self, directory, unit='vinyl-orchestrator.service', runner=None):
        if not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service',unit):
            raise ValueError('Invalid orchestrator service name')
        self.directory=Path(directory)
        self.unit=unit
        self.runner=runner or subprocess.run
        self.lock=threading.Lock()
        self.cached_at=0
        self.cached_service={}

    def service(self):
        with self.lock:
            if time.monotonic()-self.cached_at < 10:
                return self.cached_service
            try:
                result=self.runner(['systemctl','show',self.unit,'--no-pager',
                    '--property=ActiveState,SubState,MainPID,Result,ExecMainStatus,NRestarts'],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=2)
                if result.returncode:
                    raise OSError('Service status unavailable')
                value=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
                if not value.get('ActiveState'):
                    raise OSError('Service status unavailable')
                self.cached_service={'available':True,'unit':self.unit,**value}
            except (OSError,subprocess.TimeoutExpired):
                self.cached_service={'available':False,'unit':self.unit}
            self.cached_at=time.monotonic()
            return self.cached_service

    def read(self):
        now=time.time()
        service=self.service()
        try:
            snapshot=json.loads((self.directory/'orchestrator_health.json').read_text())
            if not isinstance(snapshot,dict):raise ValueError('Invalid snapshot')
        except (OSError,ValueError):
            snapshot={}
        age=now-float(snapshot.get('at',0))
        fresh=bool(snapshot) and 0 <= age <= 20
        try:
            pid=int(snapshot.get('pid',0))
            if pid > 0:os.kill(pid,0)
            else:fresh=False
        except (OSError,ValueError,TypeError):
            fresh=False
        if service.get('available') and service.get('MainPID','0') not in ('0',str(snapshot.get('pid'))):
            fresh=False
        state='healthy'
        summary='Recognition is running.'
        if service.get('available') and service.get('ActiveState') in ('inactive','failed','deactivating'):
            state='error';summary='Orchestrator service is '+service['ActiveState']+'.'
        elif not fresh:
            state='unknown';summary='Live diagnostics are unavailable or stale. Apply the update and restart the orchestrator.'
        elif snapshot.get('stage')=='learning_paused':
            state='paused';summary='Recognition and scrobbling are paused for learning.'
        elif snapshot.get('lastfm') in ('disabled','error'):
            state='warning';summary='Recognition is running, but Last.fm scrobbling is unavailable.'
        elif snapshot.get('stage')=='error':
            state='warning';summary='The orchestrator reported an error. Check the recent log lines below.'
        elif now-float(snapshot.get('last_activity',now)) > 180:
            state='warning';summary='The process is alive, but no recognition progress has been reported for over three minutes.'
        elif snapshot.get('stage')=='silent':
            summary='Listening; the audio sample is below the silence threshold.'
        elif snapshot.get('stage')=='no_match':
            summary='Listening; the last audible sample had no match.'
        return {'state':state,'summary':summary,'service':service,'snapshot':snapshot,
                'snapshot_fresh':fresh,'server_time':now,'updated':snapshot.get('at')}
