"""Recording sessions and review jobs. Audio capture never opens ALSA."""
import asyncio
import fcntl
import json
import logging
import math
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from learning import audio
from learning.engine import FingerprintEngine
from learning.discord_warning import DiscordWarning
from music_metadata import recognition_metadata


def atomic_json(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class Learner:
    def __init__(self, directory, stream, recognizer=None, notifier=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sessions = self.directory / 'sessions'
        self.sessions.mkdir(exist_ok=True)
        self.stream = stream
        self.lock = threading.RLock()
        self.process = None
        self.recording_id = None
        self.pause_handle = None
        self.stop_event = threading.Event()
        self.closed = threading.Event()
        self.recognizer = recognizer or self.shazam
        self.notifier = notifier if notifier is not None else DiscordWarning.from_env()
        self.recording_started_mono = None
        self.engine = FingerprintEngine(self.directory / 'fingerprints.sqlite3')
        self.max_seconds = min(7200, max(1, int(os.environ.get('LEARNING_MAX_SECONDS', '7200'))))
        self.recording_deadline = None
        self.stop_reason = None
        self.min_free = int(os.environ.get('LEARNING_MIN_FREE_MB', '512')) * 1024**2
        self.token = uuid.uuid4().hex + uuid.uuid4().hex
        self.recover()
        threading.Thread(target=self.watchdog, daemon=True).start()

    def recover(self):
        (self.directory / 'pause.json').unlink(missing_ok=True)
        for path in self.sessions.glob('*/session.json'):
            value = json.loads(path.read_text())
            if value['status'] in ('waiting', 'recording', 'processing', 'saving'):
                if value['status'] in ('waiting', 'recording') and time.time() >= value.get('expires_at', value['created']+self.max_seconds):
                    shutil.rmtree(path.parent)
                    self.expiry_notice()
                    continue
                value['status'] = 'interrupted'
                value['message'] = 'Service restarted. Retry processing if the recording is present.'
                atomic_json(path, value)

    def folder(self, sid):
        if not isinstance(sid, str) or len(sid) != 32 or any(c not in '0123456789abcdef' for c in sid):
            raise ValueError('Invalid session ID')
        return self.sessions / sid

    def read(self, sid):
        return json.loads((self.folder(sid) / 'session.json').read_text())

    def write(self, session):
        atomic_json(self.folder(session['id']) / 'session.json', session)

    def list(self):
        with self.lock:
            values = [json.loads(p.read_text()) for p in self.sessions.glob('*/session.json')]
            for value in values:
                try:
                    value['recording_warning'] = json.loads((self.folder(value['id'])/'warning.json').read_text())
                except (OSError, ValueError):
                    pass
            return sorted(values, key=lambda x:x['created'], reverse=True)

    def start(self, label):
        with self.lock:
            if self.recording_id:
                raise ValueError('A recording is already running')
            if shutil.disk_usage(self.directory).free < self.min_free:
                raise ValueError('Not enough free disk space to begin recording')
            sid = uuid.uuid4().hex
            folder = self.folder(sid)
            folder.mkdir()
            session = dict(id=sid, label=str(label)[:200] or 'Untitled record', created=time.time(),
                           status='waiting', message='Waiting for recognition to finish its current cycle…',
                           tracks=[], duration=0, audio_deleted=False, expires_at=time.time()+self.max_seconds)
            self.write(session)
            self.recording_id = sid
            self.recording_deadline = time.monotonic()+self.max_seconds
            self.recording_started_mono = None
            self.stop_reason = None
            (self.directory/'notice.json').unlink(missing_ok=True)
            self.stop_event = threading.Event()
            self.renew_pause()
            threading.Thread(target=self.record, args=(sid,), daemon=True).start()
            return session

    def renew_pause(self):
        atomic_json(self.directory / 'pause.json', {'expires': time.time()+15, 'session_id': self.recording_id})

    def release_pause(self):
        if self.pause_handle:
            self.pause_handle.close()
            self.pause_handle = None
        (self.directory / 'pause.json').unlink(missing_ok=True)
        (self.directory / 'ack.json').unlink(missing_ok=True)

    def record(self, sid):
        handle = (self.directory / 'recognition.lock').open('a')
        try:
            deadline = time.monotonic()+120
            while not self.stop_event.is_set():
                with self.lock:
                    if self.recording_deadline is not None and time.monotonic() >= self.recording_deadline:
                        self.stop('timeout')
                        break
                try:
                    acknowledged = json.loads((self.directory / 'ack.json').read_text()).get('session_id') == sid
                except (OSError, ValueError):
                    acknowledged = False
                if not acknowledged:
                    if time.monotonic() > deadline:
                        raise ValueError('Updated recognizer did not acknowledge learning mode. Restart vinyl-orchestrator.service after installing the patch.')
                    self.stop_event.wait(.2)
                    continue
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise ValueError('Recognition did not pause within two minutes; recording was canceled')
                    self.stop_event.wait(.2)
            with self.lock:
                if self.stop_event.is_set():
                    if self.stop_reason == 'timeout':
                        shutil.rmtree(self.folder(sid))
                        self.expiry_notice()
                        return
                    session = self.read(sid)
                    session.update(status='canceled', message='Recording canceled before capture began.')
                    self.write(session)
                    return
                self.pause_handle = handle
                session = self.read(sid)
                path = self.folder(sid) / 'recording.mp3'
                log = (self.folder(sid) / 'capture.log').open('wb')
                try:
                    self.process = subprocess.Popen(['ffmpeg', '-nostdin', '-y', '-hide_banner',
                        '-loglevel', 'error', '-rw_timeout', '15000000', '-i', self.stream,
                        '-vn', '-c:a', 'copy', str(path)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
                    session.update(status='recording', started=time.time(), message='Recording. Start playing the record now.')
                    self.recording_started_mono = time.monotonic()
                    self.write(session)
                except Exception:
                    log.close()
                    raise
            process = self.process
            while process.poll() is None and not self.stop_event.wait(.25):
                with self.lock:
                    if self.recording_deadline is not None and time.monotonic() >= self.recording_deadline:
                        self.stop('timeout')
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            log.close()
            with self.lock:
                self.process = None
                self.recording_id = None
                self.recording_deadline = None
                self.recording_started_mono = None
                self.release_pause()
                if self.stop_reason == 'timeout':
                    shutil.rmtree(self.folder(sid))
                    self.expiry_notice()
                    return
                session = self.read(sid)
                if self.stop_reason != 'manual':
                    session.update(status='interrupted', message='Recording ended without Finish recording. Audio was retained; retry processing when ready.')
                    self.write(session)
                    return
                session.update(status='processing', message='Finding track boundaries…')
                self.write(session)
            self.process_session(sid)
        except Exception as exc:
            with self.lock:
                if self.folder(sid).exists():
                    session = self.read(sid)
                    session.update(status='error', message=str(exc))
                    self.write(session)
        finally:
            with self.lock:
                handle.close()
                if self.recording_id == sid:
                    self.recording_id = None
                    self.recording_deadline = None
                    self.recording_started_mono = None
                    self.release_pause()

    def stop(self, reason='manual'):
        with self.lock:
            if self.recording_id and self.stop_reason is None:
                if reason == 'manual' and self.recording_deadline is not None and time.monotonic() >= self.recording_deadline:
                    reason = 'timeout'
                self.stop_reason = reason
                self.stop_event.set()

    def expiry_notice(self):
        atomic_json(self.directory/'notice.json', {'at': time.time(),
            'message': 'Learning recording reached its time limit and was discarded. Normal recognition has resumed.'})

    def notice(self):
        try:
            return json.loads((self.directory/'notice.json').read_text())
        except (OSError, ValueError):
            return None

    def watchdog(self):
        while not self.closed.wait(2):
            with self.lock:
                if self.recording_id:
                    self.renew_pause()
                    if self.recording_deadline is not None and time.monotonic() >= self.recording_deadline:
                        self.stop('timeout')
                    elif shutil.disk_usage(self.directory).free < self.min_free:
                        self.stop('low_disk')
                    else:
                        try:
                            self.maybe_warn()
                        except Exception:
                            logging.warning('Could not prepare the Discord reminder; recording time limit is still enforced.')

    def maybe_warn(self):
        """Claim once on disk; network delivery runs outside the capture/watchdog lock."""
        with self.lock:
            sid = self.recording_id
            if not sid or self.stop_event.is_set() or self.closed.is_set() or self.recording_started_mono is None:
                return
            if time.monotonic()-self.recording_started_mono < 1800:
                return
            if self.recording_deadline is not None and time.monotonic() >= self.recording_deadline:
                return
            session = self.read(sid)
            marker = self.folder(sid)/'warning.json'
            if session['status'] != 'recording' or marker.exists():
                return
            if not self.notifier.configured:
                atomic_json(marker, {'state': 'unconfigured', 'message': '30-minute Discord reminder is not configured.'})
                return
            atomic_json(marker, {'state': 'queued', 'at': time.time(), 'message': 'Sending the 30-minute Discord reminder…'})
            threading.Thread(target=self.deliver_warning, args=(sid, session, self.stop_event), daemon=True).start()

    def deliver_warning(self, sid, session, cancel_event):
        def alive():
            with self.lock:
                return self.recording_id == sid and not self.stop_event.is_set() and not self.closed.is_set() and (
                    self.recording_deadline is None or time.monotonic() < self.recording_deadline)
        try:
            result = self.notifier.send(session, alive, cancel_event)
        except Exception:
            result = {'state': 'failed', 'message': 'Discord reminder failed. Recording and the time limit are unaffected.'}
        with self.lock:
            if self.folder(sid).exists():
                try:
                    atomic_json(self.folder(sid)/'warning.json', result)
                except OSError:
                    logging.warning('Could not save Discord reminder status.')

    @staticmethod
    def shazam(path):
        async def identify():
            from shazamio import Shazam
            return await asyncio.wait_for(Shazam().recognize(str(path)), timeout=40)
        return asyncio.run(identify())

    def process_session(self, sid, retry=False):
        try:
            with self.lock:
                session = self.read(sid)
                if session['audio_deleted']:
                    raise ValueError('Audio was deleted; record again to change track boundaries')
                session.update(status='processing', message='Finding tracks and trying Shazam…')
                self.write(session)
            source = self.folder(sid) / 'recording.mp3'
            length = audio.duration(source)
            cuts = audio.silence_cuts(source, length,
                        float(os.environ.get('LEARNING_SILENCE_DB', '-42')),
                        float(os.environ.get('LEARNING_SILENCE_SECONDS', '1.8')))
            tracks = [dict(id=uuid.uuid4().hex, start=a, end=b, artist='', title='', album='',
                           art_url='', confirmed=False, identified_by='Unidentified')
                      for a,b in zip(cuts,cuts[1:])]
            with self.lock:
                session.update(duration=length, tracks=tracks)
                self.write(session)
            self.identify_tracks(session)
            with self.lock:
                session.update(status='review', message='Review every track, including automatic matches, before saving.')
                self.write(session)
        except Exception as exc:
            with self.lock:
                session = self.read(sid)
                session.update(status='error', message=str(exc))
                self.write(session)

    def identify_tracks(self, session):
        source = self.folder(session['id']) / 'recording.mp3'
        sample = self.folder(session['id']) / 'sample.wav'
        for index, track in enumerate(session['tracks']):
            for fraction in (.2, .5, .75):
                span = track['end']-track['start']
                start = track['start']+max(0, (span-12)*fraction)
                try:
                    audio.extract(source, sample, start, min(start+12, track['end']), sample=True)
                    info = self.recognizer(sample).get('track') or {}
                    meta = recognition_metadata(info)
                    if meta['artist'] and meta['title']:
                        track.update(meta, identified_by='Shazam')
                        break
                except Exception:
                    continue
            with self.lock:
                session['message'] = 'Identification: track %d of %d' % (index+1, len(session['tracks']))
                self.write(session)
        sample.unlink(missing_ok=True)

    def edit(self, sid, tracks):
        with self.lock:
            session = self.read(sid)
            if session['status'] not in ('review', 'saved', 'error') or session['audio_deleted']:
                raise ValueError('This session cannot be edited now')
            if not isinstance(tracks, list) or not tracks or len(tracks) > 100:
                raise ValueError('Provide between 1 and 100 tracks')
            previous = 0.0
            cleaned = []
            for track in tracks:
                start, end = float(track['start']), float(track['end'])
                if not math.isfinite(start+end) or start < previous-.02 or end <= start or end > session['duration']+.02:
                    raise ValueError('Tracks must have ordered, nonoverlapping boundaries within the recording')
                previous = end
                entry = {key:str(track.get(key,''))[:1000] for key in
                         ('artist','title','album','art_url','lastfm_url','identified_by')}
                entry.update(id=str(track.get('id') or uuid.uuid4().hex), start=start, end=end,
                             confirmed=bool(track.get('confirmed')))
                if len(entry['id']) != 32 or any(c not in '0123456789abcdef' for c in entry['id']):
                    raise ValueError('Invalid track ID')
                cleaned.append(entry)
            if len(set(t['id'] for t in cleaned)) != len(cleaned):
                raise ValueError('Duplicate track IDs')
            session.update(tracks=cleaned, status='review', message='Changes saved. Confirm all tracks before fingerprinting.')
            self.write(session)
            return session

    def job(self, sid, action):
        with self.lock:
            session = self.read(sid)
            if session['status'] not in ('review','saved','error','interrupted'):
                raise ValueError('Session is busy')
            if action == 'save':
                if not session['tracks'] or any(not t.get('confirmed') or not t.get('artist','').strip() or
                                               not t.get('title','').strip() for t in session['tracks']):
                    raise ValueError('Confirm an artist and title for every track before saving')
                if session['audio_deleted']:
                    raise ValueError('This session has already been saved and cleaned up')
                session.update(status='saving', message='Building full-track fingerprints…')
                target = self.save
            else:
                if session['audio_deleted']:
                    raise ValueError('Audio has been deleted')
                session.update(status='processing', message='Processing recording…')
                target = self.process_session
            self.write(session)
            threading.Thread(target=target,args=(sid,),daemon=True).start()
            return session

    def save(self, sid):
        files = []
        try:
            with self.lock:
                session = self.read(sid)
            records = []
            for track in session['tracks']:
                path = self.folder(sid) / (track['id']+'.wav')
                files.append(path)
                audio.extract(self.folder(sid)/'recording.mp3', path, track['start'], track['end'], sample=True)
                meta = {k:track.get(k,'') for k in ('artist','title','album','art_url','lastfm_url')}
                meta.update(duration=track['end']-track['start'], session_id=sid)
                records.append((track['id'],path,meta))
            self.engine.save(records)
            # Remove obsolete entries if the same session's track cuts were revised.
            current = {t['id'] for t in session['tracks']}
            for entry in self.engine.list():
                if entry.get('session_id') == sid and entry['id'] not in current:
                    self.engine.delete(entry['id'])
            with self.lock:
                session.update(status='saved', message='Fingerprints and metadata saved. Audio can now be deleted.')
                self.write(session)
        except Exception as exc:
            with self.lock:
                session = self.read(sid)
                session.update(status='error',message=str(exc))
                self.write(session)
        finally:
            for path in files:
                path.unlink(missing_ok=True)

    def cleanup(self, sid):
        with self.lock:
            session = self.read(sid)
            if session['status'] != 'saved':
                raise ValueError('Save fingerprints successfully before deleting audio')
            entries = {x['id'] for x in self.engine.list()}
            if any(t['id'] not in entries for t in session['tracks']):
                raise ValueError('Some fingerprints are missing; audio was kept')
            for path in self.folder(sid).iterdir():
                if path.suffix in ('.mp3','.wav','.log'):
                    path.unlink()
            session.update(audio_deleted=True, message='Audio deleted. Fingerprints and metadata retained.')
            self.write(session)
            return session

    def close(self):
        self.closed.set()
        self.stop('shutdown')
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.send_signal(signal.SIGINT)
        # The recording worker finalizes the file before releasing its lock.
