"Outbound-only manual Discord DM using existing Music Bridge bot configuration."
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

class DiscordDMError(Exception):
    pass

def _post(path, token, payload):
    data=json.dumps(payload).encode('utf-8')
    req=Request('https://discord.com/api/v10'+path,data=data,
        headers={'Authorization':'Bot '+token,'User-Agent':'DiscordBot (https://localhost/, 1.0)',
                 'Content-Type':'application/json'},method='POST')
    try:
        with urlopen(req,timeout=15) as res:
            if res.status not in (200,201): raise DiscordDMError('Discord returned unexpected status')
            return json.loads(res.read(100000))
    except HTTPError as exc:
        raise DiscordDMError('Discord HTTP '+str(exc.code)) from None
    except (URLError,OSError,ValueError):
        raise DiscordDMError('Discord connection or response failed') from None

def send_job_dm(title, detail, url):
    token=os.environ.get('MUSIC_BRIDGE_DISCORD_BOT_TOKEN','').strip()
    recipient=os.environ.get('MUSIC_BRIDGE_DISCORD_USER_ID','').strip()
    if not token or not re.fullmatch(r'[0-9]{15,22}',recipient):
        raise DiscordDMError('Discord bot environment is not configured in dashboard service')
    if '\n' in token or '\r' in token:
        raise DiscordDMError('Invalid bot configuration')
    channel=str(_post('/users/@me/channels',token,{'recipient_id':recipient}).get('id',''))
    if not re.fullmatch(r'[0-9]{15,22}',channel):
        raise DiscordDMError('Discord returned invalid channel ID')
    content='**'+title+'**\n'+detail[:1200]+'\n'+url
    _post('/channels/'+channel+'/messages',token,{'content':content,'allowed_mentions':{'parse':[]}})
    from .notification_history import record_event_safe
    record_event_safe('discord', 'sent', 'vinyl', '', content, 'playlist-details')
    # Standalone job ID message: plaintext for easy copying on iOS/desktop.
    # Never report full success if this second Discord request fails.
    job_id_match=re.search(r'Job ID: `([0-9a-f-]{36})`', detail)
    if not job_id_match:
        raise DiscordDMError('Summary sent but job ID could not be extracted')
    _post('/channels/'+channel+'/messages',token,{'content':job_id_match.group(1),'allowed_mentions':{'parse':[]}})
    record_event_safe('discord', 'sent', 'vinyl', '', job_id_match.group(1), 'playlist-id')
    return True

def send_text_dm(text):
    """Send one manual private DM to the existing configured user."""
    token=os.environ.get('MUSIC_BRIDGE_DISCORD_BOT_TOKEN','').strip()
    recipient=os.environ.get('MUSIC_BRIDGE_DISCORD_USER_ID','').strip()
    if not token or not re.fullmatch(r'[0-9]{15,22}',recipient) or '\n' in token or '\r' in token:
        raise DiscordDMError('Discord bot environment is not configured')
    if not isinstance(text,str) or not text.strip() or len(text)>1200:
        raise DiscordDMError('Message must contain 1–1200 characters')
    channel=str(_post('/users/@me/channels',token,{'recipient_id':recipient}).get('id',''))
    if not re.fullmatch(r'[0-9]{15,22}',channel):
        raise DiscordDMError('Discord returned invalid channel ID')
    _post('/channels/'+channel+'/messages',token,{'content':text,'allowed_mentions':{'parse':[]}})
    return True
