"""VinylPi dashboard foundation: authenticated, read-only service overview."""
import hashlib
import hmac
import os
import secrets
import subprocess
import time
import shutil
import re
import logging
import socket
import json
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.request import Request as URLRequest, urlopen
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from . import privacy
from .notification_history import record_event_safe
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / 'templates'))
app = FastAPI(title='VinylPi Dashboard', docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/static', StaticFiles(directory=str(BASE / 'static')), name='static')
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('vinylpi.dashboard')
USERNAME = os.getenv('VINYLPI_DASHBOARD_USER', '')
PASSWORD_HASH = os.getenv('VINYLPI_DASHBOARD_PASSWORD_SCRYPT', '')
SECRET = os.getenv('VINYLPI_DASHBOARD_SESSION_SECRET', '')
COOKIE_SECURE = os.getenv('VINYLPI_DASHBOARD_COOKIE_SECURE', 'true').lower() == 'true'
if not USERNAME or not PASSWORD_HASH or len(SECRET) < 32:
    raise RuntimeError('Dashboard credentials/session secret not configured')
# Stream mount is explicitly configured by administrator. Never proxy arbitrary URLs.
STREAM_URL = os.getenv('VINYLPI_STREAM_URL', 'http://127.0.0.1:8000/vinyl').strip()
def stream_is_configured():
    parsed = urlparse(STREAM_URL)
    return (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1','localhost')
            and parsed.port == 8000 and bool(parsed.path.strip('/')))

signer = URLSafeTimedSerializer(SECRET, salt='vinylpi-dashboard-v1')
# Only approved application units can be restarted. Privilege is supplied by a
# separately installed root-owned helper, never by broad systemctl permissions.
RESTARTABLE = frozenset({
    'alsaloop.service', 'darkice.service', 'icecast2.service',
    'music-bridge.service', 'pi-epaper.service', 'plex-vinyl-pipe.service',
    'plexmediaserver.service', 'threadfin.service', 'vinyl-hls.service',
    'vinyl-learning.service', 'vinyl-orchestrator.service',
})
# No arbitrary unit names or shell input accepted for privileged operations.
UNIT_NAME = re.compile(r'^[a-zA-Z0-9_.@-]+\.service$')
_attempts = {}

def valid_password(password):
    try:
        salt_hex, expected_hex = PASSWORD_HASH.split(':', 1)
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=16384, r=8, p=1)
        return hmac.compare_digest(digest, bytes.fromhex(expected_hex))
    except (ValueError, TypeError):
        return False

def get_session(request):
    token = request.cookies.get('vinylpi_session')
    if not token:
        return None
    try:
        data = signer.loads(token, max_age=12*3600)
        return data if data.get('u') == USERNAME and isinstance(data.get('csrf'), str) else None
    except (BadSignature, SignatureExpired):
        return None

def safe_playlist_next(value):
    """Only allow local playlist paths, optionally with one valid job UUID."""
    from urllib.parse import urlsplit, parse_qs
    import re
    if not isinstance(value, str) or not value or len(value) > 200:
        return '/'
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or '\\' in value or '%' in value:
        return '/'
    u = urlsplit(value)
    if u.scheme or u.netloc or u.fragment or u.path != '/playlists':
        return '/'
    if not u.query:
        return '/playlists'
    params = parse_qs(u.query, keep_blank_values=True)
    ids = params.get('job_id', [])
    if len(params) != 1 or len(ids) != 1 or not re.fullmatch(
        r'[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}', ids[0]
    ):
        return '/'
    return '/playlists?job_id=' + ids[0]


def page(request, name, **kwargs):
    return templates.TemplateResponse(request, name, kwargs)

@app.middleware('http')
async def headers(request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN' if request.url.path == '/learning/interface' else 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' https://*.mzstatic.com https://lastfm-img.freetls.fastly.net; style-src 'self' 'unsafe-inline'; script-src 'self'; form-action 'self' https://accounts.google.com; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
    if request.url.path == '/stream':
        response.headers['Content-Security-Policy'] += "; img-src 'self' https:"
    if request.url.path == '/playlists':
        response.headers['Content-Security-Policy'] += '; frame-src https://www.youtube-nocookie.com'
    if request.url.path == '/learning/interface':
        response.headers['Content-Security-Policy'] = response.headers['Content-Security-Policy'].replace("frame-ancestors 'none'", "frame-ancestors 'self'")
    return response

@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request, next: str = ''):
    target = safe_playlist_next(next)
    if get_session(request):
        return RedirectResponse(target, 303)
    return page(request, 'login.html', error=False, return_to=target)

@app.post('/login')
def login(request: Request, username: str = Form(...), password: str = Form(...),
          next: str = Form('')):
    target = safe_playlist_next(next)
    # Simple in-memory rate limiting; private Tailnet binding remains mandatory.
    key = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    record = [t for t in _attempts.get(key, []) if now-t < 300]
    _attempts[key] = record
    if len(record) >= 8:
        record_event_safe('login', 'rate_limited', 'guest', key)
        return page(request, 'login.html', error=True, return_to=target)
    if not hmac.compare_digest(username, USERNAME) or not valid_password(password):
        record.append(now)
        record_event_safe('login', 'failed', 'guest', key)
        return page(request, 'login.html', error=True, return_to=target)
    _attempts.pop(key, None)
    record_event_safe('login', 'success', 'vinyl', key)
    token = signer.dumps({'u': USERNAME, 'csrf': secrets.token_urlsafe(24)})
    response = RedirectResponse(target, 303)
    response.set_cookie('vinylpi_session', token, httponly=True, secure=COOKIE_SECURE,
                        samesite='strict', max_age=12*3600, path='/')
    return response

@app.post('/logout')
def logout(request: Request, csrf: str = Form(...)):
    session = get_session(request)
    if not session or not hmac.compare_digest(csrf, session['csrf']):
        return RedirectResponse('/login', 303)
    record_event_safe('login', 'logout', 'vinyl', request.client.host if request.client else 'unknown')
    response = RedirectResponse('/login', 303)
    response.delete_cookie('vinylpi_session', path='/')
    return response

# Human-readable explanations; new enabled services remain visible automatically.
SERVICE_DESCRIPTIONS = {
 'alsaloop.service':'Routes turntable audio from the USB capture interface to its output.',
 'apparmor.service':'Loads operating-system application confinement profiles.',
 'avahi-daemon.service':'Advertises and discovers local-network services (mDNS).',
 'bluetooth.service':'Manages Bluetooth connectivity.',
 'cloud-config.service':'Cloud-init system configuration stage.',
 'cloud-final.service':'Final cloud-init provisioning stage.',
 'cloud-init-local.service':'Early local cloud-init initialization.',
 'cloud-init-main.service':'Cloud-init initialization and setup.',
 'cloud-init-network.service':'Cloud-init network configuration stage.',
 'console-setup.service':'Configures the console display and keyboard.',
 'cron.service':'Runs scheduled system tasks.',
 'darkice.service':'Encodes live turntable audio for the Icecast server.',
 'e2scrub_reap.service':'Cleans up filesystem consistency-check snapshots.',
 'getty@.service':'Provides login prompts on local terminal consoles.',
 'keyboard-setup.service':'Configures system keyboard settings.',
 'music-bridge.service':'Creates and manages Apple Music to YouTube playlist jobs.',
 'NetworkManager-dispatcher.service':'Runs network event scripts when connections change.',
 'NetworkManager-wait-online.service':'Waits for network connectivity during startup.',
 'NetworkManager.service':'Manages wired and wireless network connections.',
 'nginx.service':'Serves the Pi web endpoints and reverse proxies.',
 'pi-epaper.service':'Creates and serves images for the e-paper display.',
 'plex-vinyl-pipe.service':'Feeds turntable audio into the Plex tuner integration.',
 'plexmediaserver.service':'Plex media server and playback library.',
 'regenerate_ssh_host_keys.service':'Ensures device SSH host keys are generated.',
 'rpi-eeprom-update.service':'Checks Raspberry Pi bootloader EEPROM updates.',
 'ssh.service':'Accepts secure shell connections to the Pi.',
 'sshd-keygen.service':'Generates SSH host keys where required.',
 'sshswitch.service':'Raspberry Pi SSH configuration helper.',
 'systemd-pstore.service':'Collects persistent crash records from the kernel.',
 'systemd-timesyncd.service':'Keeps the system clock synchronized.',
 'tailscaled.service':'Provides private access over the Tailscale network.',
 'threadfin.service':'Provides IPTV tuner emulation for Plex.',
 'udisks2.service':'Manages removable storage and disks.',
 'vinyl-hls.service':'Creates the HLS audio feed for Plex/Threadfin.',
 'vinyl-learning.service':'Runs fingerprint learning, recording and its local web interface.',
 'vinyl-orchestrator.service':'Recognizes vinyl playback, scrobbles and updates metadata.',
 'vinylpi-dashboard.service':'Hosts this authenticated system monitoring dashboard.',
 'wpa_supplicant.service':'Handles Wi-Fi authentication and network association.',
 'icecast2.service':'Hosts live turntable audio streaming mounts.',
 'vinylpi-boot-notify.service':'Sends a Discord notification when VinylPi boots.',
 'vinylpi-control.service':'Restricted local controller for dashboard restarts and reboot.',
}
def service_description(unit):
    return SERVICE_DESCRIPTIONS.get(unit, 'System service — see its unit details for its purpose.')

CORE_UNITS = frozenset({
    'alsaloop.service', 'darkice.service', 'icecast2.service',
    'plex-vinyl-pipe.service', 'plexmediaserver.service', 'threadfin.service',
    'vinyl-hls.service', 'vinyl-learning.service', 'vinyl-orchestrator.service',
    'music-bridge.service', 'pi-epaper.service', 'vinylpi-dashboard.service',
    'nginx.service', 'tailscaled.service',
})
# Units normally inactive following setup, on-demand operation or boot completion.
OPTIONAL_UNITS = frozenset({
    'NetworkManager-dispatcher.service', 'e2scrub_reap.service',
    'regenerate_ssh_host_keys.service', 'sshd-keygen.service', 'sshswitch.service',
    'cloud-config.service', 'cloud-final.service', 'cloud-init-local.service',
    'cloud-init-main.service', 'cloud-init-network.service',
    'rpi-eeprom-update.service', 'systemd-pstore.service',
    'getty@.service',
})

def health_class(unit, active, sub, service_type, result):
    if active == 'failed' or result not in ('success', '', 'unknown'):
        return 'failed', 'Failed'
    if active == 'activating' and sub == 'auto-restart':
        return 'warning', 'Restarting'
    if active == 'active' and sub == 'exited':
        return 'completed', 'Completed'
    if active == 'active':
        return 'healthy', 'Running'
    if unit == 'vinylpi-boot-notify.service' and active == 'inactive' and result == 'success':
        return 'completed', 'Completed'
    if active == 'inactive' and (unit in OPTIONAL_UNITS or service_type == 'oneshot'):
        return 'standby', 'Normal idle'
    if active == 'inactive' and unit in CORE_UNITS:
        return 'warning', 'Stopped'
    if active == 'inactive':
        return 'standby', 'Inactive'
    return 'unknown', 'Unknown'

def service_status(unit):
    try:
        proc = subprocess.run(['systemctl', 'show', unit,
            '--property=LoadState,ActiveState,SubState,UnitFileState,Type,Result,NRestarts', '--no-pager'],
            capture_output=True, text=True, timeout=3, check=False)
        if proc.returncode:
            return {'unit': unit, 'active': 'unknown', 'sub': 'unavailable', 'enabled':'unknown'}
        data = dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)
        active = data.get('ActiveState','unknown')
        sub = data.get('SubState','unknown')
        kind, label = health_class(unit, active, sub, data.get('Type',''), data.get('Result',''))
        sub = 'oneshot' if unit == 'vinylpi-boot-notify.service' and kind == 'completed' else sub
        return {'unit': unit, 'active': active, 'sub': sub,
                'enabled': data.get('UnitFileState','unknown'), 'health': kind,
                'health_label': label, 'restarts': int(data.get('NRestarts','0') or 0)}
    except (OSError, subprocess.TimeoutExpired):
        return {'unit': unit, 'active': 'unknown', 'sub': 'unavailable', 'enabled':'unknown'}


def enabled_units():
    """Read the real enabled-service inventory rather than a stale manual list."""
    try:
        result = subprocess.run(
            ['systemctl', 'list-unit-files', '--type=service', '--state=enabled',
             '--no-legend', '--no-pager', '--plain'],
            capture_output=True, text=True, check=False, timeout=8)
        if result.returncode:
            raise RuntimeError('systemctl list-unit-files failed')
        return sorted({line.split()[0] for line in result.stdout.splitlines()
                       if line.split() and UNIT_NAME.fullmatch(line.split()[0])})
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        log.warning('Cannot enumerate enabled services: %s', exc)
        return []


def all_services():
    units = set(enabled_units()) | RESTARTABLE | {'nginx.service', 'tailscaled.service', 'vinylpi-dashboard.service', 'vinylpi-control.service'}
    # unit list must remain bounded, even if host has numerous enabled services
    return [dict(service_status(unit), restartable=(unit in RESTARTABLE), description=service_description(unit)) for unit in sorted(units)][:120]


def host_metrics():
    import os as _os
    import shutil as _shutil
    out = {}
    try:
        with open('/proc/uptime') as f:
            out['uptime_hours'] = round(float(f.read().split()[0]) / 3600, 1)
        def cpu_sample():
            with open('/proc/stat') as f:
                fields = [int(v) for v in f.readline().split()[1:]]
            idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
            return sum(fields), idle
        a_total, a_idle = cpu_sample()
        time.sleep(0.15)
        b_total, b_idle = cpu_sample()
        delta = b_total - a_total
        if delta > 0:
            out['cpu_percent'] = round(100 * (1 - (b_idle-a_idle)/delta))
        memory = {}
        with open('/proc/meminfo') as f:
            for line in f:
                k, _, v = line.partition(':')
                if k in ('MemTotal', 'MemAvailable'):
                    memory[k] = int(v.strip().split()[0])
        out['memory_percent'] = round(100 * (1 - memory['MemAvailable'] / memory['MemTotal']))
        total, used, free = _shutil.disk_usage('/')
        out['disk_percent'] = round(100 * used / total)
        temp = Path('/sys/class/thermal/thermal_zone0/temp')
        out['temperature_c'] = round(int(temp.read_text().strip()) / 1000, 1) if temp.exists() else None
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        log.warning('Some host metrics unavailable')
    return out


def tailscale_status():
    """Report real daemon state, never infer connection from systemd alone."""
    try:
        r = subprocess.run(['/usr/bin/tailscale', 'status', '--json'],
                           capture_output=True, text=True, timeout=4, check=False)
        if r.returncode:
            return {'connected': False, 'label': 'Unavailable'}
        data = json.loads(r.stdout)
        connected = data.get('BackendState') == 'Running' and data.get('Self', {}).get('Online', True)
        return {'connected': bool(connected), 'label': 'Connected' if connected else 'Disconnected'}
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return {'connected': False, 'label': 'Unavailable'}

CONTROL_SOCKET = '/run/vinylpi-control.sock'
def control_request(command):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(62)
            sock.connect(CONTROL_SOCKET)
            sock.sendall((command+'\n').encode('ascii'))
            reply = sock.recv(128)
            return reply.startswith(b'OK\n')
    except (OSError, ValueError):
        return False

def context(request, **extra):
    sess = get_session(request)
    if not sess:
        return None
    return {'csrf': sess['csrf'], 'active_page': request.url.path, 'stream_ready': stream_is_configured(), **extra}


def physical_epaper_state():
    """Read the renderer's passive device heartbeat; never probe the ESP32 directly."""
    try:
        req = URLRequest('http://127.0.0.1:5001/status', headers={'User-Agent': 'VinylPi-Dashboard-Display/1'})
        with urlopen(req, timeout=2) as response:
            if response.status != 200:
                return {'state': 'unknown', 'last_seen': None}
            data = json.loads(response.read(32768))
        physical = data.get('physical_display', {})
        if not isinstance(physical, dict):
            return {'state': 'unknown', 'last_seen': None}
        state = physical.get('state', 'unknown')
        timestamp = physical.get('last_seen')
        return {'state': state if state in ('online', 'offline', 'unknown') else 'unknown',
                'last_seen': timestamp if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool) else None}
    except (OSError, TimeoutError, ValueError, TypeError):
        return {'state': 'unknown', 'last_seen': None}


@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    ctx = context(request, units=all_services(), metrics=host_metrics(), tailscale=tailscale_status(), privacy=privacy.state(), pause_message=request.query_params.get('pause_message', '')[:160], system_message=request.query_params.get('system_message', '')[:160])
    if ctx is None:
        return RedirectResponse('/login', 303)
    return page(request, 'home.html', **ctx)


@app.post('/listening/{action}')
def listening_action(request: Request, action: str, csrf: str = Form(...)):
    session = get_session(request)
    if not session:
        return RedirectResponse('/login', 303)
    if not hmac.compare_digest(csrf, session['csrf']):
        raise HTTPException(status_code=403, detail='Invalid form token')
    if action not in ('pause', 'resume'):
        raise HTTPException(status_code=404, detail='Unknown listening action')
    try:
        if action == 'pause':
            privacy.pause(timeout=90)
        else:
            privacy.resume()
    except (OSError, RuntimeError, TimeoutError) as exc:
        log.error('privacy_control action=%s success=false type=%s', action, type(exc).__name__)
        return RedirectResponse('/?pause_message=Change+not+confirmed.+Check+the+service+and+try+again.', 303)
    log.info('privacy_control action=%s actor=%s success=true', action, session['u'])
    return RedirectResponse('/?pause_message=Listening+state+updated.', 303)


@app.get('/services', response_class=HTMLResponse)
def services_page(request: Request):
    ctx = context(request, units=all_services(), message=request.query_params.get('message', '')[:150])
    if ctx is None:
        return RedirectResponse('/login', 303)
    return page(request, 'services.html', **ctx)


@app.get('/services/{unit}', response_class=HTMLResponse)
def service_detail(request: Request, unit: str):
    ctx = context(request)
    if ctx is None:
        return RedirectResponse('/login', 303)
    if unit not in {x['unit'] for x in all_services()}:
        return RedirectResponse('/services', 303)
    return page(request, 'detail.html', item=dict(service_status(unit), restartable=unit in RESTARTABLE, description=service_description(unit)), **ctx)


@app.post('/services/{unit}/restart')
def restart_service(request: Request, unit: str, csrf: str = Form(...)):
    session = get_session(request)
    if not session:
        return RedirectResponse('/login', 303)
    if not hmac.compare_digest(csrf, session['csrf']):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail='Invalid form token')
    from fastapi import HTTPException
    if unit not in RESTARTABLE:
        raise HTTPException(status_code=403, detail='This service cannot be restarted from the dashboard')
    success = control_request('restart ' + unit)
    log.warning('service_restart unit=%s actor=%s success=%s', unit, session['u'], success)
    msg = 'Restart request accepted; check service status' if success else 'Restart unavailable; verify vinylpi-control.service'
    return RedirectResponse('/services/' + unit + '?message=' + msg.replace(' ', '+'), 303)


@app.post('/system/reboot')
def reboot_system(request: Request, csrf: str = Form(...), confirmation: str = Form(...)):
    session = get_session(request)
    if not session:
        return RedirectResponse('/login', 303)
    if not hmac.compare_digest(csrf, session['csrf']):
        raise HTTPException(status_code=403, detail='Invalid form token')
    if confirmation != 'REBOOT':
        raise HTTPException(status_code=400, detail='Confirmation must be REBOOT')
    accepted = control_request('reboot')
    log.warning('system_reboot actor=%s accepted=%s', session['u'], accepted)
    if not accepted:
        return RedirectResponse('/?system_message=Reboot+not+accepted.+Check+controller+status.', 303)
    return page(request, 'rebooting.html', csrf=session['csrf'], active_page='/', stream_ready=False)




# Stage 1 e-paper monitor. Read-only and requires the existing dashboard session.
@app.get('/display', response_class=HTMLResponse)
def display_manager_page(request: Request):
    ctx = context(request)
    if ctx is None:
        return RedirectResponse('/login', 303)
    return page(request, 'display-manager.html', **ctx)

@app.get('/display/api/status')
def display_manager_status(request: Request):
    if not get_session(request):
        raise HTTPException(status_code=401, detail='Sign in required')
    try:
        with urlopen(URLRequest('http://127.0.0.1:5001/status', headers={'User-Agent': 'VinylPi-Dashboard-Display/1'}), timeout=3) as res:
            data = res.read(32768)
            if res.status != 200:
                raise ValueError('Non-200 display response')
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError('Invalid status')
        # Whitelist fields: no calendar event details or raw upstream keys.
        names = ('mode','recognition_status','lastfm_available','lastfm_last_success',
                 'weather_note','calendar_note','routing_note','corner_widget')
        clean = {k: payload.get(k) for k in names}
        for k in ('weather_note','calendar_note','routing_note','mode','recognition_status','corner_widget'):
            v = clean[k]
            clean[k] = str(v)[:240] if v is not None else None
        return {'renderer_online': True, 'physical_delivery_verified': False, 'physical_display': physical_epaper_state(), **clean}
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        log.warning('Display status unavailable: %s', type(exc).__name__)
        return {'renderer_online': False, 'physical_delivery_verified': False, 'physical_display': {'state': 'unknown', 'last_seen': None}}

@app.get('/display/api/preview')
def display_manager_preview(request: Request):
    if not get_session(request):
        raise HTTPException(status_code=401, detail='Sign in required')
    try:
        with urlopen(URLRequest('http://127.0.0.1:5001/preview.png', headers={'User-Agent': 'VinylPi-Dashboard-Display/1'}), timeout=3) as res:
            data = res.read(256001)
            if res.status != 200 or len(data) > 256000 or not data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Invalid or oversized preview')
        from fastapi.responses import Response
        return Response(data, media_type='image/png')
    except (OSError, TimeoutError, ValueError) as exc:
        log.warning('Display preview unavailable: %s', type(exc).__name__)
        raise HTTPException(status_code=503, detail='Preview unavailable') from None

# Stage 2: session + CSRF protected display configuration actions.
@app.get('/display/api/settings')
def display_read_settings(request: Request):
    if not get_session(request):
        raise HTTPException(status_code=401, detail='Sign in required')
    from .display_settings import read_public
    return read_public()

@app.post('/display/api/settings')
def display_save_settings(request: Request, payload: dict):
    sess = get_session(request)
    if not sess:
        raise HTTPException(status_code=401, detail='Sign in required')
    if not hmac.compare_digest(request.headers.get('X-VinylPi-CSRF', ''), sess['csrf']):
        raise HTTPException(status_code=403, detail='Invalid form token')
    from .display_settings import write, restore, SettingsError
    try:
        backup = write(payload)
    except SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except (OSError, ValueError) as exc:
        log.error('display_setting_save_failed type=%s', type(exc).__name__)
        raise HTTPException(status_code=503, detail='Unable to save settings') from None
    # A completed controller response alone doesn't prove the renderer is healthy.
    if not control_request('restart pi-epaper.service'):
        try: restore(backup)
        except (OSError, ValueError): log.exception('display_setting_rollback_failed')
        raise HTTPException(status_code=503, detail='Restart not accepted; settings restored. Verify renderer service.')
    healthy = False
    for _ in range(12):
        time.sleep(1)
        try:
            with urlopen('http://127.0.0.1:5001/status', timeout=1) as res:
                if res.status == 200 and json.loads(res.read(32768)).get('mode') is not None:
                    healthy = True
                    break
        except (OSError, TimeoutError, ValueError):
            pass
    if not healthy:
        try:
            restore(backup)
            control_request('restart pi-epaper.service')
        except (OSError, ValueError): log.exception('display_setting_rollback_failed')
        raise HTTPException(status_code=503, detail='Display did not recover. Previous configuration restored; check service health.')
    log.info('display_settings_changed actor=%s', sess['u'])
    from .display_settings import read_public
    return {'status':'applied','settings':read_public()}



# Google OAuth callback is cross-site: SameSite=Strict deliberately withholds
# the dashboard cookie. A single-use, 10-minute state nonce authorizes callback.
# Suppress just this endpoint from access logs: OAuth codes arrive in query strings.
class _QuietGoogleCallback(logging.Filter):
    def filter(self, record):
        try:
            return '/display/google/callback' not in record.getMessage()
        except Exception:
            return False
logging.getLogger('uvicorn.access').addFilter(_QuietGoogleCallback())

@app.post('/display/google/start')
def display_google_start(request: Request, csrf: str = Form(...)):
    sess = get_session(request)
    if not sess:
        raise HTTPException(status_code=401, detail='Sign in required')
    if not hmac.compare_digest(csrf, sess['csrf']):
        raise HTTPException(status_code=403, detail='Invalid form token')
    from .calendar_oauth import begin
    try:
        url = begin(request.cookies.get('vinylpi_session', ''))
    except Exception as exc:
        log.warning('Calendar OAuth start failed: %s', type(exc).__name__)
        return RedirectResponse('/display?google=error', 303)
    return RedirectResponse(url, 303)

@app.get('/display/google/callback')
def display_google_callback(code: str = '', state: str = '', error: str = ''):
    if error:
        return RedirectResponse('/display?google=cancelled', 303)
    from .calendar_oauth import complete, activate, restore_backup
    try:
        complete(code, state)
        backup = activate()
    except Exception as exc:
        log.warning('Calendar OAuth unsuccessful: %s', type(exc).__name__)
        return RedirectResponse('/display?google=error', 303)
    # Existing authenticated controller already has narrowly scoped restart permission.
    if not control_request('restart pi-epaper.service'):
        restore_backup(backup)
        return RedirectResponse('/display?google=restart-error', 303)
    ready = False
    for _ in range(12):
        time.sleep(1)
        try:
            with urlopen('http://127.0.0.1:5001/status', timeout=1) as res:
                status = json.loads(res.read(32768))
            if status.get('calendar_note') == '' and status.get('mode') is not None:
                ready = True
                break
        except (OSError, TimeoutError, ValueError):
            pass
    if not ready:
        restore_backup(backup)
        control_request('restart pi-epaper.service')
        return RedirectResponse('/display?google=verify-error', 303)
    log.info('Calendar OAuth reauthorization successful')
    return RedirectResponse('/display?google=success', 303)

@app.get('/health')
def health():
    return {'status':'ok', 'service':'vinylpi-dashboard'}


@app.get('/stream/audio')
def stream_audio(request: Request):
    """Same-origin, session-protected Icecast proxy; credentials stay on Pi."""
    from fastapi import HTTPException
    if not get_session(request):
        raise HTTPException(status_code=401, detail='Sign in required')
    if not stream_is_configured():
        raise HTTPException(status_code=503, detail='Stream not configured')
    try:
        upstream = urlopen(URLRequest(STREAM_URL, headers={'User-Agent': 'VinylPi-Dashboard/3'}), timeout=7)
    except Exception as exc:
        log.warning('Stream upstream unavailable: %s', type(exc).__name__)
        raise HTTPException(status_code=502, detail='Stream temporarily unavailable') from None
    mime = upstream.headers.get('Content-Type', 'audio/mpeg').split(';')[0].strip()
    if mime not in ('audio/mpeg', 'audio/aac', 'audio/aacp', 'audio/ogg', 'application/ogg', 'audio/mp4'):
        upstream.close()
        raise HTTPException(status_code=502, detail='Unsupported stream format')
    def chunks():
        try:
            while True:
                data = upstream.read(16_384)
                if not data:
                    break
                yield data
        except (OSError, TimeoutError):
            log.info('Audio stream ended')
        finally:
            upstream.close()
    return StreamingResponse(chunks(), media_type=mime, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

# Learning gateway is mounted after dashboard authentication helpers are defined.
from .learning_proxy import router as learning_router
app.include_router(learning_router)

# Review-only Music Bridge dashboard integration.
from .playlists import router as playlists_router
app.include_router(playlists_router)

# Private Notifications Center.
from .notifications import router as notifications_router
app.include_router(notifications_router)

# Authenticated full-page stream player.
from .stream_page import router as stream_router
app.include_router(stream_router)

