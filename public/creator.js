/* Batch downloads integrate with WaveRise's existing router and UI helpers. */
(function () {
  'use strict';
  const C=window.WRCreatorCore;
  const TOOLS={
    batch:['Batch downloads','Paste up to 10 public post links. Download them in order and retry individual failures.']
  };
  let cleanups=[], generation=0;
  function leave() { generation++; cleanups.splice(0).forEach(fn=>{try{fn();}catch{}}); }
  function objectURL(blob) { const url=URL.createObjectURL(blob); cleanups.push(()=>URL.revokeObjectURL(url)); return url; }
  function controller() { const c=new AbortController(); cleanups.push(()=>c.abort()); return c; }
  const button=(text,fn,primary=false)=>h('button',{type:'button',class:primary?'go':'btn',onclick:fn},text);
  const note=text=>h('p',{class:'creator-note'},text);
  const status=()=>h('p',{class:'creator-status',role:'status'});
  function say(el,text,bad=false) {el.textContent=text;el.classList.toggle('err',bad);}
  function panel(...children) {const el=h('div',{class:'card creator-card',style:'display:block'},...children);out(el);return el;}
  async function responseError(response) {
    const data=await response.json().catch(()=>({}));
    const detail=typeof data.detail==='string'?data.detail:response.status===413?'This upload exceeds the server limit. Choose a smaller file.':'The request could not finish. Please try again.';
    const err=new Error(detail);err.status=response.status;return err;
  }
  async function request(path,options={}) {
    const response=await fetch(API+path,options);
    if(!response.ok)throw await responseError(response);
    return response;
  }
  function save(blob,name,url) {
    const a=h('a',{href:url||objectURL(blob),download:name});document.body.append(a);a.click();a.remove();
  }
  function open(key) {
    cur=key;document.documentElement.dataset.creator='1';document.documentElement.dataset.view='';
    document.querySelectorAll('.page,.tool').forEach(el=>el.hidden=true);
    $('#chat').hidden=false;
    $('#hub').hidden=true;$('#f').hidden=true;$('#opts').hidden=true;
    $('#mine').hidden=true;$('#qr').hidden=true;
    document.querySelectorAll('#nav a').forEach(a=>a.classList.toggle('on',a.pathname===keyHref(key)));
    document.querySelectorAll('.dd>button').forEach(b=>b.classList.toggle('on',b.id==='toolbtn'));
    closeAll();sideFor(null);adsFor(false);chatHead();
    $('#h').textContent=$('#htt').textContent=TOOLS[key][0];
    $('#p').textContent=$('#hts').textContent=TOOLS[key][1];
    $('.hback').href='/';setMeta(key);
    showBatch();
  }
  window.WRTools={open,leave,has:key=>!!TOOLS[key]};

  function showBatch() {
    const text=h('textarea',{'aria-label':'Post links, one per line',placeholder:'https://www.instagram.com/reel/…\nhttps://www.facebook.com/reel/…',rows:5});
    const summary=status(),list=h('div',{class:'creator-queue'}),progress=h('progress',{max:1,value:0,'aria-label':'Batch progress'});
    let jobs=[],running=false,active=null,stopped=false;
    const update=()=>{const n=jobs.filter(j=>['done','error','cancelled'].includes(j.state)).length;progress.max=jobs.length||1;progress.value=n;
      say(summary,`${jobs.filter(j=>j.state==='done').length} downloaded · ${jobs.filter(j=>j.state==='error').length} failed · ${jobs.filter(j=>j.state==='cancelled').length} stopped · ${jobs.length} total`);};
    const start=button('Download all',()=>run(false),true),retry=button('Retry failed / stopped',()=>run(true)),stop=button('Stop',()=>{stopped=true;active?.abort();});
    stop.disabled=retry.disabled=true;
    function paintJob(j) {
      const state=h('small',{},j.state==='done'?'Downloaded. Tap a file below if your browser blocked automatic saving.':j.state==='active'?'Downloading…':j.state==='error'?'Failed':j.state==='cancelled'?'Stopped':'Waiting');
      j.el.replaceChildren(h('b',{},`Post ${jobs.indexOf(j)+1}`),h('small',{},j.url),state,...j.files.map(f=>h('a',{class:'btn',href:f.href,download:f.name},'Save '+f.label)));
      if(j.error)j.el.append(h('p',{class:'err'},j.error.message));
    }
    async function downloadJob(j) {
      active=controller();j.state='active';j.error=null;paintJob(j);
      try {
        const response=await request('/api/info',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:j.url}),signal:active.signal});
        const data=await response.json();
        for(let i=0;i<data.items.length;i++) {
          if(stopped)throw new DOMException('Stopped','AbortError');
          // Keep completed carousel files when retrying a partially failed post.
          if(j.files.some(f=>f.index===i))continue;
          const r=await request(`/api/download?url=${encodeURIComponent(j.url)}&index=${i}`,{signal:active.signal});
          const blob=await r.blob(),name=fileName(r,`waverise-${jobs.indexOf(j)+1}-${i+1}.${data.items[i].type==='photo'?'jpg':'mp4'}`);
          const href=objectURL(blob);j.files.push({index:i,href,name,label:`${data.items[i].type} ${i+1}`});
          save(blob,name,href);paintJob(j);
        }
        j.state='done';
      }catch(err){j.state=err.name==='AbortError'?'cancelled':'error';if(err.name!=='AbortError')j.error=err;}
      paintJob(j);update();
    }
    async function run(onlyFailed) {
      if(running)return;
      if(!onlyFailed) {
        let links;try{links=C.batchLinks(text.value);}catch(err){return say(summary,err.message,true);}
        jobs.forEach(j=>j.files.forEach(f=>URL.revokeObjectURL(f.href)));
        jobs=links.map(url=>({url,state:'waiting',files:[],el:h('div',{class:'creator-job'})}));
        list.replaceChildren(...jobs.map(j=>j.el));jobs.forEach(paintJob);
      }
      running=true;stopped=false;start.disabled=retry.disabled=true;stop.disabled=false;text.disabled=true;
      const thisGeneration=generation;
      for(const job of jobs.filter(j=>onlyFailed?['error','cancelled'].includes(j.state):true)) {
        if(stopped||generation!==thisGeneration)break;
        await downloadJob(job);
      }
      jobs.filter(j=>j.state==='waiting').forEach(j=>{j.state='cancelled';paintJob(j);});
      running=false;start.disabled=false;stop.disabled=true;text.disabled=false;
      retry.disabled=!jobs.some(j=>['error','cancelled'].includes(j.state)&&j.error?.status!==429);
      const rateLimited=jobs.some(j=>j.error?.status===429);
      if(rateLimited){retry.disabled=true;const timer=setTimeout(()=>retry.disabled=false,120000);cleanups.push(()=>clearTimeout(timer));}
      update();
    }
    panel(note('One link per line. Repeated links are removed. Keep this page open; your browser may ask you to allow multiple downloads.'),text,
      h('div',{class:'row'},start,retry,stop),summary,progress,list);
  }

  // Make Batch downloads available in desktop, mobile and home navigation.
  Object.keys(TOOLS).forEach(key=>{M[key]=key;T[key]=[TOOLS[key][0],TOOLS[key][1],'Continue'];GR.toolbtn.push(key);});
  const links=()=>Object.keys(TOOLS).map(k=>h('a',{href:keyHref(k)},TOOLS[k][0]));
  const desktop=$('#toolbtn')?.nextElementSibling;
  desktop?.prepend(h('p',{class:'msec'},'Downloads'),...links().map(a=>{a.setAttribute('role','menuitem');a.classList.add('creator-nav');return a;}));
  document.querySelector('#tsheet .tscard')?.prepend(h('p',{class:'tsg'},'Downloads'),h('div',{class:'tsl'},...links()));
  document.querySelector('#hub .hubcard')?.prepend(h('p',{class:'hubg'},'Downloads'),h('div',{class:'hubl creator-hub'},...links()));

})();
