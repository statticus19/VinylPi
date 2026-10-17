document.addEventListener('click',function(e){const b=e.target.closest('.playlist-load-embed');if(!b)return;const host=b.closest('.playlist-embed');const url=host?.dataset.embed;if(!url||!/^https:\/\/www\.youtube-nocookie\.com\/embed\/[A-Za-z0-9_-]{11}$/.test(url))return;const f=document.createElement('iframe');f.src=url+'?autoplay=1';f.title=b.getAttribute('aria-label')||'YouTube preview';f.allow='accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture';f.referrerPolicy='strict-origin-when-cross-origin';f.allowFullscreen=true;host.replaceChildren(f);});

// Confirm only the explicit final creation submission, never ordinary review actions.
document.addEventListener('submit', function(event) {
  const form = event.target;
  const button = form.querySelector('button[data-create-playlist]');
  if (!button) return;
  const name = button.dataset.playlistName || 'this playlist';
  const selected = Number(button.dataset.selectedCount || 0);
  const skipped = Number(button.dataset.skippedCount || 0);
  const unselected = Number(button.dataset.unselectedCount || 0);
  const message = 'Create private YouTube playlist "' + name + '"?\n\n' +
    selected + ' videos selected\n' +
    skipped + ' explicitly skipped\n' +
    unselected + ' without selections\n' +
    (skipped + unselected) + ' songs will be omitted.\n\n' +
    'This will write to YouTube. Continue?';
  if (!window.confirm(message)) {
    event.preventDefault();
    return;
  }
  // Only a confirmed click unlocks the server-side confirmation gate.
  form.querySelector('input[name="confirmation"]').value = 'CREATE';
  // Prevent accidental repeat clicks while the browser navigates.
  button.disabled = true;
});

// Soundiiz confirmations are explicit, independent of YouTube creation confirmation.
document.addEventListener('submit', function(event) {
  const form = event.target;
  const regen = form.querySelector('[data-soundiiz-regenerate]');
  const done = form.querySelector('[data-soundiiz-confirm]');
  if (regen && !window.confirm('Regenerate this handoff? This submits a new request to Soundiiz. Check for a previous handoff before continuing.')) event.preventDefault();
  if (done && !window.confirm('Have you completed this playlist part in Soundiiz? This only records your confirmation in VinylPi.')) event.preventDefault();
  if (!event.defaultPrevented && (regen || done || form.querySelector('[data-soundiiz-request]'))) {
    const button = regen || done || form.querySelector('[data-soundiiz-request]');
    button.disabled = true;
  }
});


// Launcher: avoid repeated taps; authenticated server and Bridge idempotency remain authoritative.
document.addEventListener('submit', function(e) {
  if (!e.target.matches('[data-playlist-launcher]')) return;
  const button=e.target.querySelector('button[type=submit]');
  if(button){button.disabled=true;button.textContent='Submitting…';}
});
document.addEventListener('click', function(e){
  const button=e.target.closest('[data-copy-job],[data-copy-share]');
  if(!button)return;
  const id=button.dataset.copyJob||button.dataset.copyShare;
  const el=document.getElementById(id);if(!el)return;
  const value=el.value;
  if(navigator.clipboard && window.isSecureContext){
    navigator.clipboard.writeText(value).then(()=>button.textContent='Copied').catch(()=>{el.select();button.textContent='Select and copy';});
  } else {el.select();button.textContent='Select and copy';}
});

document.addEventListener('click',function(e){
 const button=e.target.closest('[data-copy-id]');if(!button)return;
 const value=button.dataset.copyId;
 if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(value).then(()=>{button.textContent='Copied';}).catch(()=>{button.textContent='Copy failed';});}
 else {button.textContent='Use HTTPS to copy';}
});
document.addEventListener('submit',function(e){
 if(!e.target.matches('[data-confirm-discord]'))return;
 if(!confirm('Send this job ID and summary to your private Discord messages?')){e.preventDefault();return;}
 const button=e.target.querySelector('button[type=submit]');if(button){button.disabled=true;button.textContent='Sending…';}
});
