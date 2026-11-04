/* Public controls supplied by the server. Admin accounts and secrets are never included. */
(function () {
  'use strict';
  let state={};
  try{state=JSON.parse(document.getElementById('wr-management')?.textContent||'{}');}catch{}
  const defaults={max_links:10,concurrency:1,cooldown:0,requests_per_minute:120};
  function update(){
    const paths=state.paths||{},tools=state.tools||{};
    document.querySelectorAll('a[href^="/"]').forEach(a=>{
      const key=Object.keys(paths).find(k=>paths[k]===a.getAttribute('href'));
      if(key){a.hidden=tools[key]===false;if(a.hidden)a.setAttribute('aria-disabled','true');else a.removeAttribute('aria-disabled');}
    });
    const banner=document.getElementById('site-notice');
    if(banner){const announcement=state.announcement;banner.hidden=!announcement;if(announcement)banner.textContent=announcement.text;}
  }
  function blockRoute(key){
    if(state.tools?.[key]!==false)return false;
    document.querySelectorAll('.page,.tool').forEach(el=>el.hidden=true);
    $('#chat').hidden=false;$('#hub').hidden=true;$('#f').hidden=true;$('#opts').hidden=true;
    $('#h').textContent=SEO[key]?.h||'Tool unavailable';$('#p').textContent='This tool is temporarily unavailable.';
    $('#htt').textContent=$('#h').textContent;$('#hts').textContent='Please try again later.';
    out(h('p',{class:'note'},'Choose another tool from the menu.'));closeAll();return true;
  }
  window.WRManagement={batch:()=>({...defaults,...state.batch}),blockRoute};
  addEventListener('wr-route',update);update();
  // Refresh controls in an open tab without interfering with an active batch.
  setInterval(async()=>{try{const r=await fetch(API+'/api/site-config',{cache:'no-store'});if(r.ok){state=await r.json();update();}}catch{}},60000);
})();
