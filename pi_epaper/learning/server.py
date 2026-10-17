"""Authenticated LAN web interface and loopback-only recognition endpoint."""
import base64
import hmac
import json
import logging
import os
import re
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from learning.metadata import from_lastfm_url
from learning.health import HealthReader
from learning.model import Learner, atomic_json

STATIC = Path(__file__).parent / 'static'


class Handler(BaseHTTPRequestHandler):
    server_version = 'VinylLearning/1.0'

    def log_message(self, fmt, *args):
        # Paths may contain track links; keep credentials/metadata out of HTTP logs.
        logging.info('Web request: %s', self.command)

    def auth(self):
        # Dashboard gateway: private shared secret, accepted only on loopback.
        # The orchestrator's /api/match token is deliberately unrelated.
        secret = self.server.dashboard_secret
        supplied = self.headers.get('X-VinylPi-Internal-Key', '')
        if (len(secret) >= 32 and self.client_address[0] in ('127.0.0.1', '::1')
                and hmac.compare_digest(supplied, secret)):
            return True
        value = self.headers.get('Authorization', '')
        expected = 'Basic ' + base64.b64encode((self.server.username+':'+self.server.password).encode()).decode()
        if hmac.compare_digest(value, expected):
            return True
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Vinyl learning", charset="UTF-8"')
        self.send_header('Content-Length', '0')
        self.end_headers()
        return False

    def reply(self, value, code=200):
        data = json.dumps(value).encode()
        self.send_response(code)
        self.headers_common('application/json', len(data))
        self.end_headers()
        self.wfile.write(data)

    def headers_common(self, content_type, size):
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(size))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self'; frame-ancestors 'none'; base-uri 'none'")

    def body(self, limit=150_000):
        size = int(self.headers.get('Content-Length', '0'))
        if size <= 0 or size > limit:
            raise ValueError('Request is empty or too large')
        return self.rfile.read(size)

    def do_GET(self):
        if not self.auth():
            return
        try:
            path = urlparse(self.path).path
            if path == '/api/health':
                return self.reply(self.server.health.read())
            if path == '/api/state':
                sessions = self.server.learner.list()
                return self.reply({'sessions': sessions, 'library': self.server.learner.engine.list(),
                                   'recording_id': self.server.learner.recording_id,
                                   'discord_warning_configured': self.server.learner.notifier.configured,
                                   'server_time': time.time(), 'notice': self.server.learner.notice()})
            match = re.fullmatch(r'/audio/([a-f0-9]{32})', path)
            if match:
                file = self.server.learner.folder(match[1]) / 'recording.mp3'
                return self.send_file(file, 'audio/mpeg', ranges=True)
            filename = {'/':'index.html','/app.js':'app.js','/style.css':'style.css'}.get(path)
            if filename:
                mime = {'index.html':'text/html; charset=utf-8','app.js':'text/javascript','style.css':'text/css'}[filename]
                return self.send_file(STATIC/filename,mime)
            self.reply({'error':'Not found'},404)
        except FileNotFoundError:
            self.reply({'error':'Audio is unavailable or has been deleted'},404)
        except Exception as exc:
            logging.exception('GET failed')
            self.reply({'error':str(exc)},400)

    def send_file(self, path, mime, ranges=False):
        with path.open('rb') as file:
            size = os.fstat(file.fileno()).st_size
            start, end, partial = 0, size-1, False
            value = self.headers.get('Range','') if ranges else ''
            if value:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
                if not match or not (match[1] or match[2]):
                    self.reply({'error':'Invalid range'},416)
                    return
                if match[1]:
                    start = int(match[1]); end = min(int(match[2]) if match[2] else size-1, size-1)
                else:
                    start = max(0,size-int(match[2]))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header('Content-Range','bytes */%d'%size)
                    self.send_header('Content-Length','0')
                    self.end_headers()
                    return
                partial = True
            self.send_response(206 if partial else 200)
            self.headers_common(mime,max(0,end-start+1))
            if ranges:
                self.send_header('Accept-Ranges','bytes')
            if partial:
                self.send_header('Content-Range','bytes %d-%d/%d'%(start,end,size))
            self.end_headers()
            file.seek(start)
            remaining = end-start+1
            while remaining > 0:
                data = file.read(min(65536,remaining))
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == '/api/match':
                if self.client_address[0] not in ('127.0.0.1','::1') or not hmac.compare_digest(
                        self.headers.get('X-Learning-Key',''), self.server.learner.token):
                    return self.reply({'error':'Forbidden'},403)
                if not self.server.match_lock.acquire(blocking=False):
                    return self.reply({'track':None,'busy':True})
                sample = self.server.learner.directory/'query.wav'
                try:
                    sample.write_bytes(self.body(5_000_000))
                    return self.reply({'track':self.server.learner.engine.match(sample)})
                finally:
                    sample.unlink(missing_ok=True)
                    self.server.match_lock.release()
            if not self.auth():
                return
            origin = self.headers.get('Origin')
            if self.headers.get('X-Vinyl-Request') != '1' or (
                    origin and urlparse(origin).netloc != self.headers.get('Host')):
                return self.reply({'error':'Cross-origin requests are not allowed'},403)
            if self.headers.get('Content-Type','').split(';')[0] != 'application/json':
                return self.reply({'error':'Use a JSON request'},415)
            value = json.loads(self.body())
            learner = self.server.learner
            if path == '/api/start':
                return self.reply(learner.start(value.get('label','')))
            if path == '/api/stop':
                learner.stop()
                return self.reply({'ok':True})
            if path == '/api/lastfm':
                return self.reply(from_lastfm_url(value['url'], os.environ.get('LASTFM_API_KEY','')))
            if path == '/api/library/delete':
                learner.engine.delete(value['id'])
                return self.reply({'ok':True})
            match = re.fullmatch(r'/api/session/([a-f0-9]{32})/(edit|process|save|cleanup)', path)
            if match:
                sid, action = match.groups()
                if action == 'edit':
                    return self.reply(learner.edit(sid,value['tracks']))
                if action == 'cleanup':
                    return self.reply(learner.cleanup(sid))
                return self.reply(learner.job(sid,action))
            self.reply({'error':'Not found'},404)
        except Exception as exc:
            logging.exception('Request failed')
            self.reply({'error':str(exc)},400)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, learner, username, password):
        super().__init__(address,Handler)
        self.learner, self.username, self.password = learner, username, password
        self.dashboard_secret = os.environ.get('VINYLPI_LEARNING_INTERNAL_KEY', '')
        self.match_lock = threading.Lock()
        self.health = HealthReader(learner.directory, os.environ.get('LEARNING_ORCHESTRATOR_UNIT','vinyl-orchestrator.service'))


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    os.umask(0o077)
    root = Path(__file__).resolve().parents[1]
    password = os.environ.get('LEARNING_PASSWORD','')
    if len(password) < 12:
        raise SystemExit('Set LEARNING_PASSWORD to a unique password of at least 12 characters')
    directory = root/'state'/'learning'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    instance = (directory/'service.lock').open('a')
    try:
        __import__('fcntl').flock(instance, __import__('fcntl').LOCK_EX|__import__('fcntl').LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Learning service already running')
    learner = Learner(directory,os.environ.get('ICECAST_URL','http://127.0.0.1:8000').rstrip('/')+
                      os.environ.get('ICECAST_MOUNT','/vinyl'))
    server = Server((os.environ.get('LEARNING_BIND','127.0.0.1'),int(os.environ.get('LEARNING_PORT','8090'))),
                    learner,os.environ.get('LEARNING_USERNAME','vinyl'),password)
    atomic_json(learner.directory/'client.json',{'port':server.server_port,'token':learner.token})
    def stop(signum,frame):
        learner.close()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        server.serve_forever()
    finally:
        learner.close()
        if learner.process:
            try:
                learner.process.wait(timeout=12)
            except Exception:
                learner.process.kill()
        (learner.directory/'client.json').unlink(missing_ok=True)
        learner.release_pause()
        server.server_close()
        instance.close()
