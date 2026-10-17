"""Narrow, authenticated e-paper settings storage. No secrets returned to clients."""
import json
import os
import re
import hashlib
import time
import threading
from pathlib import Path

CONFIG = Path('/home/vinyl/pi_epaper/config.json')
BACKUPS = Path('/var/lib/vinylpi-dashboard/display-config-backups')
LOCK = threading.RLock()
KINDS = ('uk', 'stats', 'sun', 'pokemon')
DEFAULT_EXCLUSIONS = ['Out of Office', 'GROW TRAINIG']
CAL_ID = re.compile(r'^[^\s\x00-\x1f]{1,300}$')

class SettingsError(ValueError):
    pass

def load():
    with CONFIG.open() as f:
        return json.load(f)

def public(config):
    corner = config.get('corner') or {}
    calendars = config.get('google', {}).get('calendars', [])
    return {
        'idle_seconds': config['idle_seconds'],
        'lastfm_poll_seconds': config['lastfm_poll_seconds'],
        'corner': {'enabled': corner.get('enabled', True), 'seconds': corner.get('seconds', 900), **{k: corner.get(k, True) for k in KINDS}},
        'calendars': [{'id': c['id'], 'label': c.get('label', 'Calendar'), 'enabled': c.get('enabled', True)} for c in calendars],
        'excluded_event_titles': config.get('excluded_event_titles', DEFAULT_EXCLUSIONS),
        'etag': hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
    }

def validate(payload, current):
    if not isinstance(payload, dict) or set(payload) != {'idle_seconds','lastfm_poll_seconds','corner','calendars','excluded_event_titles','etag'}:
        raise SettingsError('Unexpected settings fields')
    for key, lo, hi in (('idle_seconds',0,7200), ('lastfm_poll_seconds',5,300)):
        val = payload[key]
        if type(val) is not int or not lo <= val <= hi:
            raise SettingsError('Invalid '+key)
    corner = payload['corner']
    if not isinstance(corner, dict) or set(corner) != {'enabled', 'seconds', *KINDS}:
        raise SettingsError('Invalid corner settings')
    if type(corner['seconds']) is not int or not 60 <= corner['seconds'] <= 86400:
        raise SettingsError('Rotation must be between 60 and 86400 seconds')
    if any(type(corner[k]) is not bool for k in ('enabled', *KINDS)):
        raise SettingsError('Invalid widget switch')
    calendars = payload['calendars']
    if not isinstance(calendars, list) or len(calendars)>15:
        raise SettingsError('Maximum 15 calendars')
    seen=set(); cleaned=[]
    for row in calendars:
        if not isinstance(row,dict) or set(row) != {'id','label','enabled'}:
            raise SettingsError('Invalid calendar record')
        ident,label,enabled=row['id'],row['label'],row['enabled']
        if not isinstance(ident,str) or not CAL_ID.fullmatch(ident) or ident in seen:
            raise SettingsError('Invalid or repeated calendar ID')
        if not isinstance(label,str) or not 1 <= len(label.strip()) <= 50 or any(ord(c)<32 for c in label):
            raise SettingsError('Calendar label must be 1–50 characters')
        if type(enabled) is not bool: raise SettingsError('Invalid calendar switch')
        seen.add(ident)
        cleaned.append({'id':ident,'label':label.strip(),'enabled':enabled})
    exclusions = payload['excluded_event_titles']
    if not isinstance(exclusions, list) or len(exclusions) > 50:
        raise SettingsError('Maximum 50 event exclusions')
    unique = set(); clean_titles = []
    for title in exclusions:
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or any(ord(x) < 32 for x in title):
            raise SettingsError('Excluded titles must be 1–120 characters')
        cleaned_title = ' '.join(title.split())
        if cleaned_title.casefold() in unique:
            raise SettingsError('Duplicate excluded event title')
        unique.add(cleaned_title.casefold()); clean_titles.append(cleaned_title)
    # Keep all unrelated fields, including credentials and routing settings, unchanged.
    out=json.loads(json.dumps(current))
    out['idle_seconds']=payload['idle_seconds']; out['lastfm_poll_seconds']=payload['lastfm_poll_seconds']
    out['excluded_event_titles'] = clean_titles
    out['corner']={**out.get('corner',{}), **corner}
    previous={c['id']:c for c in out['google']['calendars']}
    out['google']['calendars']=[{**previous.get(c['id'],{}),**c} for c in cleaned if c['enabled']]
    # Disabled calendars are persisted separately; renderer receives only enabled calendars.
    out['dashboard_disabled_calendars']=[c for c in cleaned if not c['enabled']]
    return out

def read_public():
    cfg=load()
    result=public(cfg)
    by_id={c['id']:c for c in result['calendars']}
    for c in cfg.get('dashboard_disabled_calendars',[]):
        if c['id'] not in by_id: result['calendars'].append(dict(c))
    return result

def write(payload):
    with LOCK:
        original=CONFIG.read_bytes()
        current=json.loads(original)
        expected=public(current)['etag']
        if payload.get('etag')!=expected:
            raise SettingsError('Settings changed since page loaded; reload before saving')
        new=validate(payload,current)
        BACKUPS.mkdir(mode=0o700,parents=True,exist_ok=True)
        name=time.strftime('%Y%m%d-%H%M%S')+'-'+hashlib.sha256(original).hexdigest()[:8]+'.json'
        backup=BACKUPS/name
        with backup.open('xb') as b:
            b.write(original); b.flush(); os.fsync(b.fileno())
        os.chmod(backup,0o600)
        newbytes=(json.dumps(new,indent=2,ensure_ascii=False)+'\n').encode()
        # systemd grants write to this *file only*, so replace-in-place; preserve backup first.
        try:
            with CONFIG.open('r+b') as f:
                f.seek(0); f.write(newbytes); f.truncate(); f.flush(); os.fsync(f.fileno())
        except Exception:
            with CONFIG.open('r+b') as f:
                f.seek(0); f.write(original); f.truncate(); f.flush(); os.fsync(f.fileno())
            raise
        return name

def restore(name):
    with LOCK:
        if not re.fullmatch(r'\d{8}-\d{6}-[0-9a-f]{8}\.json',name):
            raise SettingsError('Invalid backup ID')
        content=(BACKUPS/name).read_bytes()
        restored=json.loads(content)
        if not isinstance(restored,dict) or 'google' not in restored: raise SettingsError('Invalid backup')
        with CONFIG.open('r+b') as f:
            f.seek(0); f.write(content); f.truncate(); f.flush(); os.fsync(f.fileno())
