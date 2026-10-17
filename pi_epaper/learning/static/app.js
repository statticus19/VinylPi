'use strict';
const $ = id => document.getElementById(id);
// VinylPi theme contract. Reuse this controller and CSS tokens on future pages.
const vinylPiThemes={
  dark:{label:'Dark',short:'Dark'},
  light:{label:'Light',short:'Light'},
  'persona-5-royal':{label:'Persona 5 Royal',short:'Royal'},
  'eternal-blue':{label:'Eternal Blue',short:'Eternal Blue'},
  language:{label:'Language',short:'Language'}
};
function setTheme(value,persist=true){
  const theme=Object.prototype.hasOwnProperty.call(vinylPiThemes,value)?value:'dark';
  document.documentElement.dataset.theme=theme;
  const label=vinylPiThemes[theme].label;
  $('theme-name').textContent=vinylPiThemes[theme].short;
  $('theme-toggle').setAttribute('aria-label','Choose theme. Current theme: '+label);
  $('theme-options').querySelectorAll('[data-theme-choice]').forEach(button=>{
    button.setAttribute('aria-pressed',String(button.dataset.themeChoice===theme));
  });
  if(persist)try{localStorage.setItem('vinylpi-theme',theme);}catch(_){/* Storage may be disabled. */}
}
function themeMenu(open,returnFocus=false){
  $('theme-options').hidden=!open;
  $('theme-toggle').setAttribute('aria-expanded',String(open));
  if(returnFocus)$('theme-toggle').focus();
}
try{setTheme(localStorage.getItem('vinylpi-theme'),false);}catch(_){setTheme('dark',false);}
$('theme-toggle').onclick=()=>themeMenu($('theme-options').hidden);
$('theme-options').querySelectorAll('[data-theme-choice]').forEach(button=>{
  button.onclick=()=>{setTheme(button.dataset.themeChoice);themeMenu(false,true);};
});
document.addEventListener('click',event=>{
  if(!$('theme-control').contains(event.target))themeMenu(false);
});
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&!$('theme-options').hidden)themeMenu(false,true);
});
window.addEventListener('storage',event=>{
  if(event.key==='vinylpi-theme'||event.key===null)setTheme(event.newValue,false);
});
let state = {sessions:[],library:[]}, selected = null, draft = null, rendered = '', dirty = false;
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stamp = value => { const n=Math.max(0,Number(value)||0); return `${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`; };
const uid = () => Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');
function error(message){$('error').textContent=message; $('error').hidden=!message;}
async function api(path,body){
  const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Vinyl-Request':'1'},body:JSON.stringify(body)};
  const response=await fetch(path,options); const value=await response.json();
  if(!response.ok)throw new Error(value.error||'Request failed'); return value;
}
async function act(fn){error('');try{await fn();await refresh();}catch(e){error(e.message);}}
$('start').onclick=()=>act(async()=>{ const session=await api('/api/start',{label:$('label').value}); selected=session.id; rendered=''; });
$('stop').onclick=()=>act(()=>api('/api/stop',{}));
function showLibrary(show){
  $('library').hidden=!show;
  $('library-toggle').setAttribute('aria-expanded',String(show));
  $('library-toggle').textContent=show?'Hide learned tracks':'View learned tracks';
}
$('library-toggle').onclick=()=>showLibrary($('library').hidden);
$('library-link').onclick=()=>showLibrary(true);
if(location.hash==='#library')showLibrary(true);
window.addEventListener('hashchange',()=>{if(location.hash==='#library')showLibrary(true);});
async function refresh(){
  state=await api('/api/state');
  const recording=state.sessions.find(s=>s.id===state.recording_id);
  $('start').disabled=!!recording; $('stop').hidden=!recording;
  $('stop').textContent=recording?.status==='waiting'?'Cancel preparation':'Finish recording';
  $('recording-status').textContent=recording ? (recording.status==='recording'?`Recording · ${stamp(state.server_time-recording.started)} · discard in ${stamp(recording.expires_at-state.server_time)}`:recording.message) : 'Ready when you are.';
  $('discord-status').textContent=recording?.recording_warning?.message || (state.discord_warning_configured?'Discord reminder after 30 minutes of recording.':'30-minute Discord reminder is not configured.');
  $('notice').textContent=state.notice?.message||''; $('notice').hidden=!state.notice;
  $('flow').textContent=recording?'Learning mode · recognition paused':'Shazam first · local fallback';
  $('session-count').textContent=state.sessions.length;
  $('library-count').textContent=state.library.length;
  $('sessions').innerHTML=state.sessions.length?state.sessions.map(s=>`<button class="session ${s.id===selected?'selected':''}" aria-pressed="${s.id===selected}" data-id="${s.id}">${escapeHTML(s.label)}<small>${escapeHTML(s.status)}${s.duration?' · '+stamp(s.duration):''}</small></button>`).join(''):'<p class="note">No sessions yet.</p>';
  $('sessions').querySelectorAll('button').forEach(button=>button.onclick=()=>{
    if(dirty&&!confirm('Discard unsaved edits in this session?'))return;
    selected=button.dataset.id;rendered='';dirty=false;refresh().catch(e=>error(e.message));
  });
  if(selected&&!state.sessions.some(s=>s.id===selected)){
    selected=null;draft=null;rendered='';dirty=false;
    $('review').innerHTML='<div class="empty"><h2>No active review session.</h2><p>Start learning or select a saved session.</p></div>';
  }
  if(!selected&&state.sessions.length)selected=state.sessions[0].id;
  const session=state.sessions.find(s=>s.id===selected);
  if(session){
    const key=[session.id,session.status,session.audio_deleted].join(':');
    if(key!==rendered || ['waiting','recording','processing','saving'].includes(session.status)){
      draft=structuredClone(session);rendered=key;dirty=false;renderReview();
    }
  }
  $('library-list').innerHTML=state.library.length?state.library.map(t=>`<div class="library-row"><div><strong>${escapeHTML(t.title)}</strong><small>${escapeHTML(t.artist)}${t.album?' · '+escapeHTML(t.album):''}</small></div><button class="danger" data-id="${t.id}">Remove</button></div>`).join(''):'<p>No learned tracks yet.</p>';
  $('library-list').querySelectorAll('button').forEach(button=>button.onclick=()=>{
    if(confirm('Remove this track’s fingerprints from the local fallback database?'))act(()=>api('/api/library/delete',{id:button.dataset.id}));
  });
}
function renderReview(){
  if(!draft)return;
  const editable=['review','saved','error'].includes(draft.status)&&!draft.audio_deleted;
  const busy=['waiting','recording','processing','saving'].includes(draft.status);
  $('review').innerHTML=`<div class="review-header"><div><h2>${escapeHTML(draft.label)}</h2><p>${escapeHTML(draft.message)}</p></div><span class="pill">${escapeHTML(draft.status)}</span></div>
    ${draft.tracks.map((track,i)=>renderTrack(track,i,editable)).join('')}
    <div class="toolbar">${editable?'<button id="save-edits" class="secondary">Save edits</button><button id="fingerprint">Save fingerprints</button>':''}
    ${draft.status==='saved'&&!draft.audio_deleted?'<button id="cleanup" class="danger">Delete recording audio</button>':''}
    ${!busy&&!draft.audio_deleted?'<button id="process" class="secondary">Retry splitting & identification</button>':''}</div>
    ${draft.recording_warning?`<p class="note">${escapeHTML(draft.recording_warning.message)}</p>`:''}
    ${draft.audio_deleted?'<p class="note">Audio has been deleted. Your saved fingerprints remain available for recognition.</p>':''}`;
  $('review').querySelectorAll('.track').forEach((card,i)=>wireTrack(card,i));
  if($('save-edits'))$('save-edits').onclick=()=>act(saveEdits);
  if($('fingerprint'))$('fingerprint').onclick=()=>act(async()=>{await saveEdits();await api(`/api/session/${selected}/save`,{});rendered='';});
  if($('cleanup'))$('cleanup').onclick=()=>{if(confirm('Delete all audio for this session? Fingerprints and metadata will remain.'))act(async()=>{await api(`/api/session/${selected}/cleanup`,{});rendered='';});};
  if($('process'))$('process').onclick=()=>{if(confirm('Reprocess the original recording? This replaces the proposed track cuts and unsaved identifications.'))act(async()=>{await api(`/api/session/${selected}/process`,{});rendered='';});};
}
function renderTrack(t,i,editable){
  const id=t.id, field=(key,label)=>`<label>${label}<input data-field="${key}" value="${escapeHTML(t[key])}" ${editable?'':'disabled'}></label>`;
  return `<article class="track" data-id="${id}"><div class="track-top"><span class="track-number">${String(i+1).padStart(2,'0')}</span><div class="track-name">${escapeHTML(t.title||'Unidentified track')}<small>${escapeHTML(t.artist||'Artist unknown')} · ${stamp(t.end-t.start)}</small></div><span class="pill ${!t.title?'unidentified':''}">${escapeHTML(t.identified_by||'Manual')}</span></div>
    ${!draft.audio_deleted?`<audio controls preload="none" src="/audio/${draft.id}" aria-label="Play track ${i+1}"></audio>`:''}
    <div class="fields">${field('artist','Artist')}${field('title','Track title')}<div class="wide">${field('album','Album / release')}</div>
    ${editable?`<div class="wide"><label>Identify with a Last.fm track link</label><div class="link-row"><input data-field="lastfm_url" value="${escapeHTML(t.lastfm_url||'')}" placeholder="https://www.last.fm/music/Artist/_/Track"><button data-action="resolve" class="secondary">Use link</button></div></div>`:''}</div>
    ${editable?`<div class="boundaries"><label>Start (seconds)<input type="number" step="0.01" min="0" data-field="start" value="${t.start.toFixed(2)}"></label><label>End (seconds)<input type="number" step="0.01" min="0" data-field="end" value="${t.end.toFixed(2)}"></label><button data-action="split" class="secondary">Split at player position</button>${i<draft.tracks.length-1?'<button data-action="merge" class="secondary">Merge with next</button>':''}</div><label class="confirm"><input type="checkbox" data-field="confirmed" ${t.confirmed?'checked':''}>I confirm this track’s identity and boundaries</label>`:''}</article>`;
}
function wireTrack(card,i){
  const track=draft.tracks[i], player=card.querySelector('audio');
  if(player){
    let seekPending=true;
    player.addEventListener('loadedmetadata',()=>{player.currentTime=track.start;seekPending=false;});
    player.addEventListener('play',()=>{
      $('review').querySelectorAll('audio').forEach(a=>{if(a!==player)a.pause();});
      if(seekPending||player.currentTime<track.start||player.currentTime>=track.end)player.currentTime=track.start;
      seekPending=false;
    });
    player.addEventListener('timeupdate',()=>{if(player.currentTime>=track.end){player.pause();}});
  }
  card.querySelectorAll('[data-field]').forEach(input=>input.onchange=()=>{
    const key=input.dataset.field;
    track[key]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;
    if(key!=='confirmed')track.confirmed=false;
    dirty=true;
    if(key!=='confirmed')card.querySelector('[data-field="confirmed"]').checked=false;
  });
  const resolve=card.querySelector('[data-action="resolve"]');
  if(resolve)resolve.onclick=()=>act(async()=>{
    // Capture text even if browser focus has not yet triggered change.
    const value=await api('/api/lastfm',{url:card.querySelector('[data-field="lastfm_url"]').value});
    Object.assign(track,value,{confirmed:false,identified_by:'Last.fm link'});dirty=true;renderReview();
    if(value.notice)error(value.notice);
  });
  const split=card.querySelector('[data-action="split"]');
  if(split)split.onclick=()=>{
    const position=player.currentTime;
    if(position<=track.start+.5||position>=track.end-.5)return error('Move the player to a split point inside this track.');
    const next={...track,id:uid(),start:position,artist:'',title:'',album:'',art_url:'',lastfm_url:'',confirmed:false,identified_by:'Unidentified'};
    track.end=position;track.confirmed=false;draft.tracks.splice(i+1,0,next);dirty=true;renderReview();
  };
  const merge=card.querySelector('[data-action="merge"]');
  if(merge)merge.onclick=()=>{track.end=draft.tracks[i+1].end;track.confirmed=false;draft.tracks.splice(i+1,1);dirty=true;renderReview();};
}
async function saveEdits(){
  const value=await api(`/api/session/${selected}/edit`,{tracks:draft.tracks});draft=value;dirty=false;rendered='';
}
refresh().catch(e=>error(e.message));
setInterval(()=>refresh().catch(e=>error(e.message)),2500);

const healthLabels={starting:'Starting',listening:'Capturing audio',identifying:'Checking Shazam',local_fallback:'Checking local fingerprints',matched:'Track identified',scrobbling:'Submitting scrobble',silent:'Listening · quiet audio',no_match:'Listening · no match',error:'Error reported',learning_paused:'Paused for learning',stopped:'Stopped'};
const when=value=>value?new Date(value*1000).toLocaleString():'None recorded';
async function refreshHealth(forceLogs=false){
  try{
    const health=await api('/api/health'), snapshot=health.snapshot||{}, service=health.service||{};
    $('health-state').textContent=({healthy:'Running',paused:'Learning pause',warning:'Needs attention',error:'Service stopped',unknown:'Status unavailable'})[health.state]||health.state;
    $('health-state').className='health-badge '+health.state;
    $('health-summary').textContent=health.summary;
    $('health-stage').textContent=healthLabels[snapshot.stage]||'No live activity yet';
    $('health-service').textContent=service.available?`${service.ActiveState} / ${service.SubState} · PID ${service.MainPID} · restarts ${service.NRestarts||0}`:'System service status unavailable';
    $('health-track').textContent=snapshot.last_match?.track||'No identification in this run';
    $('health-audio').textContent=snapshot.audio_db!==undefined?`Last sample: ${snapshot.audio_db.toFixed(1)} dB`:'No audio level reported';
    $('health-scrobble').textContent=when(snapshot.last_scrobble_success);
    $('health-submission').textContent=snapshot.scrobble_status||'No submission information yet';
    $('health-updated').textContent='Snapshot: '+when(health.updated);
    if(forceLogs||$('health-auto').checked){
      const log=$('health-logs'),atBottom=log.scrollTop+log.clientHeight>=log.scrollHeight-12;
      log.textContent=(snapshot.logs||[]).map(row=>`${new Date(row.at*1000).toLocaleTimeString()} [${row.level}] ${row.message}`).join('\n')||'No recent messages. Restart the updated orchestrator to enable live diagnostics.';
      if(atBottom)log.scrollTop=log.scrollHeight;
    }
  }catch(e){
    $('health-state').textContent='Unavailable';$('health-state').className='health-badge unknown';
    $('health-summary').textContent='Could not load diagnostics: '+e.message;
  }
}
$('health-refresh').onclick=()=>refreshHealth(true);
refreshHealth(true);
setInterval(()=>refreshHealth(),5000);
