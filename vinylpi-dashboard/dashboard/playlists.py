"""Authenticated Music Bridge playlist review and Soundiiz part-management gateway."""
import hmac
import json
import logging
import os
import secrets
import re
import time
import datetime as dt
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request as URLRequest, urlopen
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from .main import context, get_session, page

router = APIRouter()
log = logging.getLogger('vinylpi.dashboard.playlists')
BRIDGE = 'http://127.0.0.1:8765'
JOB_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
JOB_AGE_LIMIT_DAYS = 2
MAX_VISIBLE_JOBS = 12
VISIBLE_STATUSES = frozenset({'queued', 'running', 'awaiting_approval',
    'waiting_for_quota', 'waiting_for_rate_limit', 'creation_queued', 'creating_review', 'needs_review', 'interrupted', 'failed', 'handoff_pending'})


def auth(request):
    session = get_session(request)
    if not session:
        raise HTTPException(401, 'Sign in to VinylPi first')
    return session


def bridge_request(job_id=None, decision=None, create=False):
    if job_id is not None and not JOB_ID.fullmatch(job_id):
        raise HTTPException(404, 'Invalid playlist job ID')
    token = os.environ.get('VINYLPI_MUSIC_BRIDGE_TOKEN', '')
    if not token:
        raise HTTPException(503, 'Music Bridge dashboard connection not configured')
    endpoint = BRIDGE + '/v1/playlist/jobs' + ('/' + job_id if job_id else '')
    if decision is not None:
        endpoint += '/review'
    elif create:
        endpoint += '/create-reviewed'
    payload = (json.dumps(decision).encode() if decision is not None else b'') if (decision is not None or create) else None
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    req = URLRequest(endpoint, data=payload, headers=headers,
                     method='POST' if payload is not None else 'GET')
    try:
        with urlopen(req, timeout=15) as response:
            data = response.read(2_000_001)
    except HTTPError as exc:
        # Do not reflect potentially sensitive upstream diagnostic bodies to browsers.
        if exc.code == 404:
            raise HTTPException(404, 'Playlist job not found') from None
        if exc.code in (409, 422):
            raise HTTPException(exc.code, 'Review decision rejected by Music Bridge') from None
        log.warning('Music Bridge HTTP failure: %d', exc.code)
        raise HTTPException(502, 'Music Bridge request failed') from None
    except (URLError, TimeoutError, OSError):
        raise HTTPException(502, 'Music Bridge is unavailable') from None
    if len(data) > 2_000_000:
        raise HTTPException(502, 'Music Bridge response is too large')
    try:
        reply = json.loads(data)
        if not isinstance(reply, dict):
            raise ValueError('Not an object')
        return reply
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(502, 'Music Bridge returned invalid data') from None



def soundiiz_bridge_request(job_id, number, action, payload=None):
    """Allowlist-only gateway: no arbitrary path, URLs, or provider credentials."""
    if not JOB_ID.fullmatch(job_id) or type(number) is not int or not 1 <= number <= 5:
        raise HTTPException(404, 'Invalid Soundiiz job or part')
    if action not in ('handoff', 'transferred'):
        raise HTTPException(404, 'Invalid Soundiiz action')
    token = os.environ.get('VINYLPI_MUSIC_BRIDGE_TOKEN', '')
    if not token:
        raise HTTPException(503, 'Music Bridge dashboard connection not configured')
    endpoint = f'{BRIDGE}/v1/playlist/jobs/{job_id}/soundiiz-parts/{number}/{action}'
    req = URLRequest(endpoint, data=json.dumps(payload or {}).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                 'Content-Type': 'application/json'}, method='POST')
    try:
        # Remote handoff can take longer than ordinary review requests.
        with urlopen(req, timeout=40) as response:
            raw = response.read(2_000_001)
    except HTTPError as exc:
        if exc.code in (404, 409, 422):
            raise HTTPException(exc.code, 'Soundiiz action rejected; check part status and refresh') from None
        log.warning('Soundiiz handoff proxy HTTP failure: %d', exc.code)
        raise HTTPException(502, 'Soundiiz handoff unavailable; inspect the job before retrying') from None
    except (URLError, TimeoutError, OSError):
        raise HTTPException(502, 'Music Bridge response uncertain; inspect the job before retrying') from None
    if len(raw) > 2_000_000:
        raise HTTPException(502, 'Music Bridge response too large')
    try:
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get('job_id') != job_id:
            raise ValueError('Invalid job response')
        return result
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(502, 'Music Bridge returned invalid data') from None


def soundiiz_parts(job):
    result = job.get('result') or {}
    if result.get('workflow') != 'soundiiz_transfer':
        return []
    now = time.time()
    parts = []
    for part in result.get('parts', []):
        if not isinstance(part, dict):
            continue
        p = dict(part)
        url = p.get('handoff_url')
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            valid = bool(parsed and parsed.scheme == 'https' and parsed.hostname == 'soundiiz.com'
                and not parsed.username and not parsed.password and parsed.port in (None,443)
                and not parsed.query and not parsed.fragment
                and re.fullmatch(r'/go/import-playlist/[A-Za-z0-9_-]+', parsed.path))
        except ValueError:
            valid = False
        p['safe_url'] = url if valid else None
        p['link_valid'] = bool(valid and p.get('status') == 'ready'
            and isinstance(p.get('expires_at'), (int, float)) and p['expires_at'] > now)
        p['can_generate'] = p.get('status') == 'not_generated' and not p.get('marked_transferred')
        p['can_regenerate'] = (p.get('status') in ('ready', 'uncertain')
            and not p['link_valid'] and not p.get('marked_transferred'))
        p['can_confirm'] = p.get('status') == 'ready' and not p.get('marked_transferred')
        parts.append(p)
    return parts


def redirect_job(job_id, message):
    return RedirectResponse('/playlists?' + urlencode({'job_id': job_id, 'message': message}), 303)

def annotate(job):
    """Annotate historical and newer jobs without mutating persisted results."""
    result = job.get('result') or {}
    decisions = result.get('review_decisions') or {}
    tracks = []
    for row in result.get('review_candidates') or []:
        index = row.get('source_index')
        if type(index) is not int:
            continue
        candidates = []
        for cand in row.get('candidates') or []:
            vid = cand.get('video_id', '')
            if not isinstance(vid, str) or not VIDEO_ID.fullmatch(vid):
                continue
            c = dict(cand)
            c['safe_url'] = 'https://www.youtube.com/watch?v=' + vid
            c['embed_url'] = 'https://www.youtube-nocookie.com/embed/' + vid
            c['official_label'] = ('Verified official' if c.get('official_status') == 'verified'
                                    and c.get('uploader_verified') is True else 'Unverified uploader')
            candidates.append(c)
        decision = decisions.get(str(index))
        chosen_id = (decision.get('video_id') if decision and decision.get('action') == 'approve' else None)
        suggested = row.get('suggested_video_id')
        if not suggested:
            suggested = next((c['video_id'] for c in candidates
                              if c.get('metadata_eligible') and c.get('kind') in ('music_video','lyric_video')), None)
        selected_id = chosen_id if decision else suggested
        selected = next((c for c in candidates if c['video_id'] == selected_id), None)
        # Never hide the selection, even when outside the first few ranked results.
        visible = candidates[:5]
        if selected and all(c['video_id'] != selected_id for c in visible):
            visible = [selected] + visible[:4]
        tracks.append({'index': index, 'title': row.get('title', ''),
                       'artists': ', '.join(row.get('artists') or []),
                       'candidates': visible, 'candidate_count': len(candidates),
                       'decision': decision, 'selected': selected,
                       'auto_suggested': selected is not None and not decision,
                       'is_skipped': bool(decision and decision.get('action') == 'skip')})
    return tracks


@router.get('/playlists')
def playlists_home(request: Request, job_id: str = ''):
    if not get_session(request):
        # Leave all POST endpoints protected by their existing session + CSRF checks.
        destination = '/playlists'
        if job_id and JOB_ID.fullmatch(job_id):
            destination += '?job_id=' + job_id
        return RedirectResponse('/login?next=' + quote(destination, safe=''), 303)
    jobs = bridge_request().get('jobs', [])
    if not isinstance(jobs, list):
        raise HTTPException(502, 'Invalid job listing')
    # Filter only the dashboard listing, never delete or change stored jobs.
    cutoff = time.time() - JOB_AGE_LIMIT_DAYS * 86400
    jobs = [j for j in jobs if isinstance(j.get('created'), (int, float))
            and j['created'] >= cutoff][:MAX_VISIBLE_JOBS]
    if not job_id and jobs:
        job_id = jobs[0].get('job_id', '')
    job = bridge_request(job_id) if job_id else None
    tracks = annotate(job) if job else []
    parts = soundiiz_parts(job) if job else []
    apple_job = bool(job and (job.get('mode') == 'apple_video_plan'
        or (job.get('result') or {}).get('workflow') == 'apple_video_plan'
        or (job.get('payload') or {}).get('mode') == 'apple_video_plan'))
    ctx = context(request, launcher_key=secrets.token_urlsafe(24), apple_job=apple_job, job=job, tracks=tracks, soundiiz_parts=parts, jobs=jobs, job_id=job_id,
                  message=request.query_params.get('message', '')[:120])
    return page(request, 'playlists.html', **ctx)


@router.post('/playlists/{job_id}/review')
def save_review(request: Request, job_id: str, source_index: int = Form(...),
                action: str = Form(...), video_id: str = Form(''), csrf: str = Form(...)):
    session = auth(request)
    if not hmac.compare_digest(csrf, session['csrf']):
        raise HTTPException(403, 'Invalid form token')
    if action not in ('approve', 'skip') or not 0 <= source_index <= 999:
        raise HTTPException(422, 'Invalid review decision')
    if action == 'approve' and not VIDEO_ID.fullmatch(video_id):
        raise HTTPException(422, 'Invalid video ID')
    decision = {'source_index': source_index, 'action': action}
    if action == 'approve':
        decision['video_id'] = video_id
    bridge_request(job_id, decision)
    log.info('Music Bridge review saved for track %d, action %s', source_index, action)
    return RedirectResponse('/playlists?job_id=' + job_id + '&message=Decision+saved', 303)


@router.post('/playlists/{job_id}/create-reviewed')
def create_reviewed(request: Request, job_id: str,
                    confirmation: str = Form(...), csrf: str = Form(...)):
    session = auth(request)
    if not hmac.compare_digest(csrf, session['csrf']):
        raise HTTPException(403, 'Invalid form token')
    if confirmation != 'CREATE':
        raise HTTPException(422, 'Type CREATE to confirm playlist creation')
    # Protect against clicks that could target non-review jobs or already-created work.
    job = bridge_request(job_id)
    if job.get('status') != 'awaiting_approval' or not (job.get('result') or {}).get('review_required'):
        raise HTTPException(409, 'Job is not awaiting review')
    bridge_request(job_id, create=True)
    log.info('Explicit reviewed-playlist creation requested for job %s', job_id)
    return RedirectResponse('/playlists?job_id=' + job_id + '&message=Creation+queued', 303)


@router.post('/playlists/{job_id}/soundiiz/{part_number}/handoff')
def dashboard_soundiiz_handoff(request: Request, job_id: str, part_number: int,
                              csrf: str = Form(...), regenerate: str = Form('')):
    session = auth(request)
    if not hmac.compare_digest(csrf, session['csrf']):
        raise HTTPException(403, 'Invalid form token')
    if regenerate not in ('', 'yes'):
        raise HTTPException(422, 'Invalid regeneration choice')
    job = bridge_request(job_id)
    parts = soundiiz_parts(job)
    if not 1 <= part_number <= len(parts) or job.get('status') != 'handoff_pending':
        raise HTTPException(409, 'Soundiiz part not available for handoff')
    part = parts[part_number-1]
    if part.get('part_number') != part_number or part.get('marked_transferred'):
        raise HTTPException(409, 'Soundiiz part cannot be regenerated')
    if regenerate == 'yes':
        if not part['can_regenerate']:
            raise HTTPException(409, 'Regeneration is not currently available')
    elif not (part['can_generate'] or part['link_valid']):
        raise HTTPException(409, 'Inspect status before requesting a handoff')
    soundiiz_bridge_request(job_id, part_number, 'handoff', {'regenerate': regenerate == 'yes'})
    log.info('Soundiiz handoff requested for job %s part %d', job_id, part_number)
    return redirect_job(job_id, 'Soundiiz handoff updated')


@router.post('/playlists/{job_id}/soundiiz/{part_number}/transferred')
def dashboard_soundiiz_transferred(request: Request, job_id: str, part_number: int,
                                  csrf: str = Form(...), destination: str = Form(...)):
    session = auth(request)
    if not hmac.compare_digest(csrf, session['csrf']):
        raise HTTPException(403, 'Invalid form token')
    destination = destination.strip().lower()
    if not re.fullmatch(r'[a-z0-9_-]{2,50}', destination):
        raise HTTPException(422, 'Enter a valid destination service name')
    job = bridge_request(job_id)
    parts = soundiiz_parts(job)
    if not 1 <= part_number <= len(parts) or parts[part_number-1].get('part_number') != part_number:
        raise HTTPException(409, 'Invalid Soundiiz part')
    if not parts[part_number-1]['can_confirm']:
        raise HTTPException(409, 'This part is not awaiting a transfer confirmation')
    soundiiz_bridge_request(job_id, part_number, 'transferred', {'destination': destination})
    log.info('Soundiiz transfer user-confirmed for job %s part %d', job_id, part_number)
    return redirect_job(job_id, 'Transfer confirmation saved')


# Dashboard-only launcher: allowlisted bridge operations and relative browser destinations.
def launcher_post(endpoint, payload, idempotency_key=None):
    if endpoint not in ('/v1/share', '/v1/playlist/resolve', '/v1/playlist/jobs'):
        raise HTTPException(404, 'Unknown Music Bridge operation')
    token = os.environ.get('VINYLPI_MUSIC_BRIDGE_TOKEN', '')
    if not token:
        raise HTTPException(503, 'Music Bridge dashboard connection not configured')
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
               'Content-Type': 'application/json'}
    if idempotency_key is not None:
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', idempotency_key):
            raise HTTPException(422, 'Invalid submission key')
        headers['Idempotency-Key'] = idempotency_key
    raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    if len(raw) > 1_500_000:
        raise HTTPException(413, 'Playlist submission is too large')
    req = URLRequest(BRIDGE + endpoint, data=raw, headers=headers, method='POST')
    try:
        with urlopen(req, timeout=75) as response:
            answer = response.read(3_000_001)
    except HTTPError as exc:
        if exc.code in (400, 404, 409, 422):
            raise HTTPException(exc.code, 'Music Bridge rejected the request. Check the playlist URL and status.') from None
        log.warning('Launcher API returned HTTP %d on %s', exc.code, endpoint)
        raise HTTPException(502, 'Music Bridge could not complete this request') from None
    except (URLError, TimeoutError, OSError):
        raise HTTPException(502, 'Bridge connection uncertain. Check recent jobs before submitting again.') from None
    if len(answer) > 3_000_000:
        raise HTTPException(502, 'Music Bridge response too large')
    try:
        result = json.loads(answer)
        if not isinstance(result, dict):
            raise ValueError('Unexpected response')
        return result
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(502, 'Invalid response from Music Bridge') from None


@router.post('/playlists/launch')
def dashboard_launcher(request: Request, url: str = Form(...), workflow: str = Form(...),
                       submission_key: str = Form(...), csrf: str = Form(...)):
    session = auth(request)
    if not hmac.compare_digest(csrf, session['csrf']):
        raise HTTPException(403, 'Invalid form token')
    # Only accept URLs to Apple's public music domain; Bridge performs final source validation.
    url = url.strip()
    parsed = urlsplit(url)
    if (len(url) > 2048 or parsed.scheme != 'https' or parsed.hostname != 'music.apple.com'
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise HTTPException(422, 'Enter an HTTPS music.apple.com URL')
    workflows = {'share': None, 'apple_video_plan': 'apple_video_plan',
                 'youtube_video_playlist': 'youtube_video_playlist',
                 'soundiiz_transfer': 'soundiiz_transfer'}
    if workflow not in workflows:
        raise HTTPException(422, 'Unknown workflow')
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,128}', submission_key):
        raise HTTPException(422, 'Invalid submission key')
    if workflow == 'share':
        result = launcher_post('/v1/share', {'url': url})
        share_text = result.get('share_text')
        if not isinstance(share_text, str) or len(share_text) > 16000:
            raise HTTPException(502, 'Music Bridge did not return valid sharing text')
        return page(request, 'musiclink-result.html', **context(request,
                    share_text=share_text, share_title=str(result.get('title') or 'MusicLink')[:160]))
    resolved = launcher_post('/v1/playlist/resolve', {'url': url, 'mode': workflow})
    job_payload = resolved.get('request')
    if not isinstance(job_payload, dict) or job_payload.get('mode') != workflow:
        raise HTTPException(502, 'Music Bridge returned an unexpected playlist plan')
    job = launcher_post('/v1/playlist/jobs', job_payload, submission_key)
    job_id = job.get('job_id', '')
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise HTTPException(502, 'Music Bridge returned an invalid job ID')
    log.info('Dashboard created/retrieved %s job %s', workflow, job_id)
    return RedirectResponse('/playlists?job_id=' + job_id + '&message=Job+ready', 303)


# Minimal job history and manual Discord sharing. No changes to job state.
def _history_date(timestamp):
    return dt.datetime.fromtimestamp(timestamp).strftime('%A, %B %-d, %Y')


def _history_jobs(rows, days=30):
    cutoff = time.time() - days * 86400
    selected = [j for j in rows if isinstance(j, dict)
                and JOB_ID.fullmatch(str(j.get('job_id', '')))
                and isinstance(j.get('created'), (int, float))
                and cutoff <= j['created'] <= time.time() + 60]
    selected.sort(key=lambda j: j['created'], reverse=True)
    groups = []
    for job in selected:
        label = _history_date(job['created'])
        if not groups or groups[-1]['date'] != label:
            groups.append({'date': label, 'jobs': []})
        groups[-1]['jobs'].append({
            'job_id': job['job_id'], 'title': str(job.get('title') or 'Untitled')[:160],
            'mode': str(job.get('mode') or 'unknown')[:50],
            'status': str(job.get('status') or 'unknown')[:50],
            'created_time': dt.datetime.fromtimestamp(job['created']).strftime('%-I:%M %p'),
        })
    return groups


@router.get('/playlists/history')
def playlist_history(request: Request):
    # A redirect, not a 401, so browser deep links behave like /playlists.
    if not get_session(request):
        return RedirectResponse('/login?next=%2Fplaylists%2Fhistory', 303)
    rows = bridge_request().get('jobs', [])
    if not isinstance(rows, list):
        raise HTTPException(502, 'Invalid job listing')
    return page(request, 'playlist-history.html', **context(request,
                groups=_history_jobs(rows), history_total=sum(len(g['jobs']) for g in _history_jobs(rows))))


def _discord_job_content(job):
    jid = str(job.get('job_id', ''))
    if not JOB_ID.fullmatch(jid):
        raise HTTPException(404, 'Invalid job ID')
    title = str(job.get('title') or 'Music Bridge job').replace('@', '@\u200b')[:160]
    mode = str(job.get('mode') or 'unknown').replace('_', ' ')[:60]
    status = str(job.get('status') or 'unknown').replace('_', ' ')[:60]
    created = job.get('created')
    created_text = _history_date(created) if isinstance(created, (int, float)) else 'Unknown'
    link = os.getenv('VINYLPI_DASHBOARD_PUBLIC_URL', '').rstrip('/') + '/playlists?job_id=' + jid if os.getenv('VINYLPI_DASHBOARD_PUBLIC_URL') else ''
    detail = f'Playlist: {title}\nWorkflow: {mode}\nStatus: {status}\nCreated: {created_text}\nJob ID: `{jid}`'
    if job.get('mode') == 'apple_video_plan':
        detail += '\nApple Music: Use Resume Apple Music Video Playlist on your iPhone with the job ID.'
    return detail, link


@router.post('/playlists/{job_id}/send-discord')
def dashboard_send_job_discord(request: Request, job_id: str, csrf: str = Form(...),
                             return_to: str = Form('recent')):
    session = auth(request)
    if not hmac.compare_digest(csrf, session['csrf']):
        raise HTTPException(403, 'Invalid form token')
    if not JOB_ID.fullmatch(job_id):
        raise HTTPException(404, 'Invalid job ID')
    if return_to not in ('recent','history'):
        raise HTTPException(422, 'Invalid return page')
    job = bridge_request(job_id)
    detail, link = _discord_job_content(job)
    # This uses the SAME bot credentials as the existing Pi notification system,
    # injected into the dashboard service environment by the operator.
    from .playlist_discord_dm import send_job_dm, DiscordDMError
    try:
        send_job_dm('VinylPi Music Bridge — Playlist job', detail, link)
    except DiscordDMError as exc:
        log.warning('Manual playlist Discord DM failed: %s', exc)
        raise HTTPException(502, 'Discord delivery failed; no confirmation recorded') from None
    log.info('User requested playlist job Discord DM: %s', job_id)
    destination = '/playlists/history' if return_to == 'history' else '/playlists?job_id=' + job_id
    glue = '?' if '?' not in destination else '&'
    return RedirectResponse(destination + glue + 'message=Discord+message+sent', 303)
