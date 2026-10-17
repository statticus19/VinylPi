/* Single audio source: controls existing header #vinyl-player; no second <audio>. */
(()=>{
 let timer=null, audio=null, dispose=[];
 const byId=id=>document.getElementById(id);
 function clear(){if(timer)clearInterval(timer);timer=null;for(const fn of dispose)fn();dispose=[];}
 function init(){clear();const panel=byId('stream-panel');if(!panel)return;
  audio=byId('vinyl-player');const toggle=byId('stream-toggle'),vol=byId('stream-volume');
  const updateAudio=()=>{if(!panel.isConnected)return;toggle.textContent=audio&&!audio.paused?'Pause stream':'Play stream';toggle.disabled=!audio; if(audio){vol.value=Math.round(audio.volume*100);byId('stream-volume-label').textContent=vol.value+'%';byId('stream-play-state').textContent=audio.error?'Playback error; try again.':!audio.paused?'Listening to the live stream':'Stream paused';}};
  toggle.addEventListener('click',async()=>{if(!audio)return;if(audio.paused){try{await audio.play()}catch(_){byId('stream-play-state').textContent='Browser blocked playback. Tap Play again.'}}else audio.pause();updateAudio();});
  vol.addEventListener('input',()=>{if(audio){audio.volume=Number(vol.value)/100;updateAudio()}});
  if(audio){for(const ev of ['play','pause','volumechange','error']){audio.addEventListener(ev,updateAudio);dispose.push(()=>audio.removeEventListener(ev,updateAudio))}}
  updateAudio();
  byId('stream-copy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(byId('stream-lan-url').textContent);byId('stream-copy-result').textContent='Address copied.'}catch(_){byId('stream-copy-result').textContent='Select the address above to copy it.'}});
  async function refresh(){if(!panel.isConnected)return;try{const res=await fetch('/stream/now-playing',{credentials:'same-origin',cache:'no-store'});if(!res.ok)throw new Error('Unavailable');const x=await res.json();if(!panel.isConnected)return;
   byId('stream-status').textContent=!x.online?'Offline':x.idle?'Idle':'Live · Now playing';
   byId('stream-track').textContent=x.title||'Waiting for vinyl';byId('stream-artist').textContent=x.artist||(x.online?'Nothing playing right now':'Stream unavailable');byId('stream-album').textContent=x.album||'';
   byId('stream-availability').textContent=x.online?'Online':'Unavailable';byId('stream-bitrate').textContent=x.bitrate?x.bitrate+' kbps':'—';
   const art=byId('stream-art');if(art.dataset.src!==x.art_url){art.dataset.src=x.art_url||'';art.replaceChildren();if(x.art_url){const img=document.createElement('img');img.alt='Album artwork';img.referrerPolicy='no-referrer';img.src=x.art_url;img.onerror=()=>{if(art.isConnected){art.replaceChildren();art.textContent='♫'}};art.appendChild(img)}else art.textContent='♫'}
  }catch(_){if(!panel.isConnected)return;byId('stream-status').textContent='Metadata unavailable';byId('stream-availability').textContent='Unknown'}}
  async function refreshPrevious(){if(!panel.isConnected)return;try{const res=await fetch('/stream/previous',{credentials:'same-origin',cache:'no-store'});if(!res.ok)throw new Error('Unavailable');const d=await res.json();if(!panel.isConnected)return;const t=d.previous;byId('stream-previous-title').textContent=t?.title||'No completed scrobbles yet';byId('stream-previous-artist').textContent=t?.artist||'';byId('stream-previous-album').textContent=t?.album||'';byId('stream-previous-message').textContent=d.error||'';const cover=byId('stream-previous-art');if(cover.dataset.src!==(t?.art_url||'')){cover.dataset.src=t?.art_url||'';cover.replaceChildren();if(t?.art_url){const img=document.createElement('img');img.alt='Previous album artwork';img.referrerPolicy='no-referrer';img.src=t.art_url;img.onerror=()=>{if(cover.isConnected){cover.textContent='♫'}};cover.appendChild(img)}else cover.textContent='♫'}}catch(_){if(panel.isConnected)byId('stream-previous-message').textContent='Listening history temporarily unavailable';}}
  byId('stream-copy-guest').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(byId('stream-guest-url').textContent);byId('stream-guest-copy-result').textContent='Guest link copied.'}catch(_){byId('stream-guest-copy-result').textContent='Select the address above to copy it.'}});
  refresh();refreshPrevious();timer=setInterval(()=>{refresh();refreshPrevious()},15000);
 }
 document.addEventListener('vinylpi:pagechange',init);if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();