/* VinylPi same-document navigation: keep the header audio element alive. */
(()=>{
 const host=document.querySelector('main');
 if(!host)return;
 let counter=0;
 let controller=null;
 function internal(a){
   if(!a||a.hasAttribute('download')||a.target||a.dataset.fullReload!==undefined)return null;
   const url=new URL(a.href,location.href);
   if(url.origin!==location.origin)return null;
   if(!['/','/services','/learning','/stream'].includes(url.pathname)&& !/^\/services\/[a-zA-Z0-9_.@-]+\.service$/.test(url.pathname))return null;
   return url;
 }
 function updateNav(doc){
  const incoming=doc.querySelector('nav[aria-label="Primary"]');
  const current=document.querySelector('nav[aria-label="Primary"]');
  if(incoming&&current)current.replaceWith(incoming);
 }
 function learnResize(){
  const frame=document.getElementById('learning-frame');
  if(!frame)return;
  const resize=()=>{if(!frame.isConnected)return;try{
    const d=frame.contentDocument;const height=Math.max(d.documentElement.scrollHeight,d.body.scrollHeight);
    if(height>200)frame.style.height=(height+40)+'px';
  }catch(_){}};
  frame.addEventListener('load',resize);
  const timer=setInterval(()=>{if(!frame.isConnected){clearInterval(timer);return;}resize();},1500);
 }
 async function navigate(url, push=true){
  const number=++counter;
  if(controller)controller.abort();controller=new AbortController();
  try{
   const response=await fetch(url.pathname+url.search,{credentials:'same-origin',signal:controller.signal,headers:{'Accept':'text/html','X-VinylPi-Navigation':'1'}});
   if(!response.ok||response.redirected||!response.headers.get('content-type')?.includes('text/html')){location.assign(url.href);return;}
   const doc=new DOMParser().parseFromString(await response.text(),'text/html');
   if(number!==counter)return;
   const content=doc.querySelector('main');if(!content){location.assign(url.href);return;}
   host.replaceChildren(...Array.from(content.childNodes));
   document.title=doc.title;
   updateNav(doc);
   if(push)history.pushState({vinylpi:true},'',url.href);
   document.dispatchEvent(new Event('vinylpi:pagechange'));
   learnResize();
   if(url.hash){requestAnimationFrame(()=>document.getElementById(url.hash.slice(1))?.scrollIntoView());}
   else window.scrollTo({top:0,behavior:'instant'});
  }catch(e){if(e.name!=='AbortError')location.assign(url.href);}
 }
 document.addEventListener('click',e=>{
  if(e.defaultPrevented||e.button!==0||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
  const a=e.target.closest?.('a[href]');const url=internal(a);
  if(!url)return;
  if(url.pathname===location.pathname&&url.search===location.search&&url.hash){return;}
  e.preventDefault();navigate(url);
 });
 window.addEventListener('popstate',()=>navigate(new URL(location.href),false));
 document.addEventListener('DOMContentLoaded',learnResize);
})();
