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
