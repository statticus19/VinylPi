"""Authenticated notifications activity and manual Discord sending."""
import hmac
from urllib.parse import quote
from fastapi import APIRouter,Form,HTTPException,Request
from fastapi.responses import RedirectResponse
from .main import context,get_session,page
from .notification_history import recent,record_event_safe
from .playlist_discord_dm import send_text_dm,DiscordDMError

router=APIRouter()

@router.get('/notifications')
def notifications_page(request:Request):
    ctx=context(request,discord_events=recent('discord'),login_events=recent('login'),
                system_events=recent('system'),message=request.query_params.get('message','')[:150])
    if ctx is None: return RedirectResponse('/login',303)
    return page(request,'notifications.html',**ctx)

@router.post('/notifications/send')
def send_manual(request:Request,message:str=Form(...),csrf:str=Form(...)):
    session=get_session(request)
    if not session: return RedirectResponse('/login',303)
    if not hmac.compare_digest(csrf,session['csrf']): raise HTTPException(403,'Invalid form token')
    if not message.strip() or len(message)>1200: raise HTTPException(422,'Message must be 1–1200 characters')
    ip=request.client.host if request.client else ''
    try:
        send_text_dm(message)
    except DiscordDMError as exc:
        record_event_safe('discord','failed','vinyl',ip,'Manual Discord message failed: '+str(exc),'manual')
        return RedirectResponse('/notifications?message='+quote('Discord send failed: '+str(exc)),303)
    record_event_safe('discord','sent','vinyl',ip,message,'manual')
    return RedirectResponse('/notifications?message=Discord+message+sent',303)
