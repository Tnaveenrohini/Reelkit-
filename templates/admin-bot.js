'use strict';
{
/* WaveRise Assistant: owner chat, error notebook and contact-reply drafts. Uses admin.js helpers. */
const mdBox=t=>{const box=h('div',{class:'botmd'});let ul=null;
  const inline=s=>{const out=[];String(s).split(/(\*\*[^*]+\*\*|`[^`]+`)/g).forEach(p=>{if(/^\*\*[^*]+\*\*$/.test(p))out.push(h('b',{},p.slice(2,-2)));else if(/^`[^`]+`$/.test(p))out.push(h('code',{},p.slice(1,-1)));else if(p)out.push(p)});return out};
  for(const raw of String(t||'').split('\n')){const line=raw.trim();if(!line){ul=null;continue}
    const li=line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
    if(li){if(!ul){ul=h(/^\d/.test(line)?'ol':'ul',{});box.append(ul)}ul.append(h('li',{},...inline(li[1])))}
    else{ul=null;box.append(h(/^#+\s/.test(line)?'h3':'p',{},...inline(line.replace(/^#+\s*/,''))))}}
  return box};
const BOT_KEY='wr_ownerbot';
const botHist=()=>{try{const a=JSON.parse(sessionStorage.getItem(BOT_KEY)||'[]');return Array.isArray(a)?a:[]}catch{return[]}};
const botSave=a=>{try{sessionStorage.setItem(BOT_KEY,JSON.stringify(a.slice(-20)))}catch{}};
const srcName={server:'Server bug',browser:'Page (browser) bug',visitor:'Visitor problem',ai:'Assistant',check:'Daily check'};
const srcLvl={server:'bad',browser:'bad',visitor:'warn',ai:'warn',check:'bad'};
const ago=t=>{const s=Math.max(0,Date.now()/1000-t);return s<90?'just now':s<3600?Math.round(s/60)+' min ago':s<86400?Math.round(s/3600)+' h ago':Math.round(s/86400)+' days ago'};
const setupNote=()=>card(h('h2',{},'Switch on the assistant (free)'),
  h('ol',{},h('li',{},'Open ',h('a',{href:'https://aistudio.google.com/apikey',target:'_blank',rel:'noopener'},'aistudio.google.com/apikey'),' and sign in with your Google account.'),
    h('li',{},'Tap ',h('b',{},'Create API key'),' and copy it. Keep it private: don’t paste it in any chat.'),
    h('li',{},'In Vercel → your project → ',h('b',{},'Settings → Environment Variables'),', add ',h('code',{},'GEMINI_API_KEY'),' with that key (all environments).'),
    h('li',{},'Deployments → newest → ⋯ → ',h('b',{},'Redeploy'),'. The visitor chat, this page and reply drafts then work.')),
  h('p',{class:'muted small'},'The error log below works without the key.'));

SECTIONS.bot=async()=>{
  const st=await api('/api/admin/bot');
  const out=sec('Assistant','Your helper while you are away. It reads the error log, messages, downloads and the daily site check, and tells you what matters. Ask in Telugu (English letters are fine) or English.');
  if(st.engine==='own')out.append(h('p',{class:'note'},'Running on WaveRise’s own assistant: no key, no cost, no limits. It answers from your site’s real data.'));
  const daily=h('div',{});
  const paintDaily=d=>daily.replaceChildren(card(h('div',{class:'row',style:'justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px'},
      h('h2',{style:'margin:0'},'Today’s report'),h('span',{class:'muted small'},d?('Written '+when(d.t)):'Not written yet')),
    d?mdBox(d.text):h('p',{class:'muted'},'A report is written every morning after the daily site check. You can also write one now.'),
    st.ai?mb(d?'Write a fresh report':'Write the report now',async()=>{const r=await api('/api/admin/bot/daily',{});paintDaily(r.daily)},'ghost'):null));
  paintDaily(st.daily);out.append(daily);
  if(st.open_problems)out.append(h('p',{class:'note bad'},`${st.open_problems} problem(s) noted in the last 24 hours. `,h('a',{href:'#errors'},'Open the error log')));
  let hist=botHist();
  const log=h('div',{class:'botlog'});
  const bubble=(t,me)=>{const b=h('div',{class:'botmsg'+(me?' me':'')},me?h('p',{},t):mdBox(t));log.append(b);b.scrollIntoView({block:'nearest'});return b};
  const paint=()=>{log.replaceChildren();if(!hist.length)log.append(h('p',{class:'muted small'},'Try: “ఈరోజు site ela undi?” or “What should I fix first?”'));hist.forEach(x=>bubble(x.t,x.me))};
  const inp=h('textarea',{rows:2,placeholder:'Ask the assistant…','aria-label':'Ask the assistant',maxlength:2000});
  const send=async q=>{q=String(q||'').trim();if(!q)return;inp.value='';if(!hist.length)log.replaceChildren();bubble(q,true);
    const w=h('div',{class:'botmsg'},h('span',{class:'spin'}),' Thinking…');log.append(w);
    try{const r=await api('/api/admin/bot',{q,hist});w.remove();hist.push({me:1,t:q},{t:r.answer});botSave(hist);bubble(r.answer)}
    catch(e){w.remove();bubble('⚠️ '+e.message)}};
  inp.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send(inp.value)}};
  const quick=['ఈరోజు site ela undi?','What broke today?','Any messages waiting?','What should I fix first?'];
  out.append(card(h('div',{class:'row',style:'justify-content:space-between;align-items:center'},h('h2',{style:'margin:0'},'Chat'),
      h('button',{type:'button',class:'btn ghost sm',onclick:()=>{hist=[];botSave(hist);paint()}},'New chat')),
    log,h('div',{class:'row',style:'flex-wrap:wrap;gap:6px;margin:8px 0'},...quick.map(q=>h('button',{type:'button',class:'btn ghost sm',disabled:!st.ai,onclick:()=>send(q)},q))),
    h('div',{class:'botin'},inp,h('button',{type:'button',class:'btn',disabled:!st.ai,onclick:()=>send(inp.value)},'Send'))));
  paint();return out};

SECTIONS.errors=async()=>{
  const d=await api('/api/admin/errors');
  const out=sec('Error log','Every problem the site noticed, newest first. The same problem is counted, not repeated. “Visitor problems” are usually not bugs (private posts, wrong links, Instagram limits).');
  if(!d.store){out.append(h('p',{class:'note bad'},'Connect the Upstash database to keep the error log.'));return out}
  const filt=cache.errFilt||'open';
  const rows=d.rows.filter(r=>filt==='all'||(filt==='open'?!r.fixed:filt==='bugs'?(!r.fixed&&/server|browser|check/.test(r.source)):!!r.fixed));
  const today=d.days[d.days.length-1]||{},sum=o=>Object.entries(o).filter(([k])=>k!=='day').reduce((n,[,v])=>n+v,0);
  out.append(h('div',{class:'grid g4'},stat('Problems today',fmt(sum(today))),stat('Bugs today',fmt((today.server||0)+(today.browser||0)+(today.check||0))),
    stat('Open problems',fmt(d.rows.filter(r=>!r.fixed).length)),stat('Last 14 days',fmt(d.days.reduce((n,x)=>n+sum(x),0)))));
  const sel=ms('Show',[['open','Open problems'],['bugs','Bugs only'],['fixed','Marked fixed'],['all','Everything']],filt);sel.onchange=()=>{cache.errFilt=sel.value;render()};
  const act=(action,ids)=>api('/api/admin/errors',{action,ids}).then(()=>render());
  out.append(h('div',{class:'row',style:'flex-wrap:wrap;gap:8px'},sel,mb('Refresh',()=>render(),'ghost'),
    rows.length&&filt!=='fixed'?mb('Mark all shown as fixed',()=>act('fixed',rows.map(r=>r.id)),'ghost'):null,
    mb('Clear the whole log',async()=>{if(confirm('Delete every noted problem? This can’t be undone.'))await act('clear',[])},'danger')));
  if(!rows.length)out.append(h('div',{class:'empty card'},filt==='open'?'Nothing broken right now. 🎉':'Nothing here.'));
  rows.forEach(r=>{const exp=h('div',{});
    out.append(card(h('div',{class:'row',style:'justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center'},
        h('div',{},tag(srcName[r.source]||r.source,srcLvl[r.source]),' ',r.fixed?tag('Fixed','ok'):null,' ',h('b',{},'×'+fmt(r.count)),' ',h('span',{class:'muted small'},'last '+ago(r.last)+' · first '+when(r.first))),
        h('div',{class:'row',style:'gap:6px'},
          mb('Explain',async()=>{const x=await api('/api/admin/errors/explain',{id:r.id});exp.replaceChildren(h('div',{class:'note'},mdBox(x.answer)))},'ghost sm'),
          r.fixed?mb('Reopen',()=>act('reopen',[r.id]),'ghost sm'):mb('Mark fixed',()=>act('fixed',[r.id]),'sm'),
          mb('Delete',()=>act('delete',[r.id]),'danger sm'))),
      h('p',{style:'margin:10px 0 4px;overflow-wrap:anywhere'},h('b',{},r.msg)),
      h('p',{class:'muted small mono',style:'margin:0;overflow-wrap:anywhere'},r.where+(r.status?' · HTTP '+r.status:'')),
      r.detail?h('details',{},h('summary',{class:'small'},'Details'),h('pre',{class:'mono small',style:'white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0 0'},r.detail)):null,exp))});
  return out};

SECTIONS.messages=async()=>{
  const out=sec('Messages','Messages people sent from the Contact page. The assistant can draft a reply; you check it, then send it from your email or WhatsApp.');
  try{
    const [d,st]=await Promise.all([api('/api/admin/messages'),api('/api/admin/bot').catch(()=>({ai:false}))]);
    const badge=$('#msgCount');if(badge){badge.textContent=d.unread||'';badge.hidden=!d.unread}
    const act=async(m,action,btn)=>{if(action==='delete'&&!confirm('Delete this message? This can’t be undone.'))return;
      busy(btn,true);try{await api('/api/admin/messages/'+encodeURIComponent(m.id),{action});toast(action==='delete'?'Message deleted.':'Saved.');render()}catch(e){busy(btn,false);toast(e.message,true)}};
    const lvl=t=>/Bug/.test(t)?'bad':/DMCA/.test(t)?'warn':/Feature/.test(t)?'ok':'';
    const sendLinks=(m,txt)=>{const r=m.reply||'';if(!r)return [h('span',{class:'muted small'},'No reply address. Copy the text if you need it.')];
      if(/@/.test(r))return [h('a',{class:'btn sm',href:'mailto:'+r+'?subject='+encodeURIComponent('Re: your message to WaveRise')+'&body='+encodeURIComponent(txt()),onclick:()=>mark(m)},'Send by email')];
      let n=r.replace(/[^\d]/g,'');if(n.length===10)n='91'+n;
      return [h('a',{class:'btn sm',href:'https://wa.me/'+n+'?text='+encodeURIComponent(txt()),target:'_blank',rel:'noopener',onclick:()=>mark(m)},'Send on WhatsApp')]};
    const mark=m=>{api('/api/admin/messages/'+encodeURIComponent(m.id)+'/reply',{sent:true}).then(()=>setTimeout(render,600)).catch(()=>{})};
    out.append(h('p',{class:'muted small'},`${d.rows.length} message(s) · ${d.unread} unread · ${d.rows.filter(m=>m.replied).length} replied`));
    if(!st.ai)out.append(h('p',{class:'note'},'Reply drafts need the free Gemini key. ',h('a',{href:'#bot'},'See how to switch it on')));
    if(!d.rows.length)out.append(h('div',{class:'empty card'},'No messages yet.'));
    d.rows.forEach(m=>{const area=h('div',{});
      const editor=text=>{const ta=h('textarea',{rows:6,style:'width:100%;margin-top:6px;font-family:inherit;font-size:14.5px','aria-label':'Reply'},text);
        const save=()=>api('/api/admin/messages/'+encodeURIComponent(m.id)+'/reply',{draft:ta.value}).catch(()=>{});ta.onchange=save;
        const links=h('div',{class:'row',style:'gap:6px;flex-wrap:wrap;margin-top:6px'});
        const relink=()=>links.replaceChildren(...sendLinks(m,()=>ta.value),h('button',{type:'button',class:'btn ghost sm',onclick:async()=>{try{await navigator.clipboard.writeText(ta.value);toast('Copied.')}catch{toast('Select the text and copy it.',true)}}},'Copy'),
          ...(m.reply?[]:[mb('Mark as replied',async()=>{await save();mark(m)},'ghost sm')]));
        ta.oninput=relink;relink();area.replaceChildren(h('p',{class:'small',style:'margin:12px 0 0'},h('b',{},'Reply draft'),' ',h('span',{class:'muted'},'(edit it before sending)')),ta,links)};
      if(m.draft)editor(m.draft);
      out.append(card(
        h('div',{class:'row',style:'justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap'},
          h('div',{},m.read?null:tag('New','ok'),' ',m.replied?tag('Replied','ok'):null,' ',tag(m.topic,lvl(m.topic)),' ',h('span',{class:'muted small'},when(m.t)+(m.lang?' · '+m.lang.toUpperCase():''))),
          h('div',{class:'row'},
            st.ai?mb(m.draft?'New draft':'Draft a reply',async()=>{const r=await api('/api/admin/messages/'+encodeURIComponent(m.id)+'/draft',{});m.draft=r.draft;editor(r.draft)},'sm'):null,
            h('button',{class:'btn ghost sm',onclick:e=>act(m,m.read?'unread':'read',e.target)},m.read?'Mark unread':'Mark read'),
            h('button',{class:'btn danger sm',onclick:e=>act(m,'delete',e.target)},'Delete'))),
        h('p',{style:'white-space:pre-wrap;overflow-wrap:anywhere;margin:12px 0'},m.message),
        h('p',{class:'muted small',style:'margin:0'},h('b',{},m.name||'No name'),' · ',m.reply||'No reply address'),area))});
  }catch(e){out.append(errBox(e))}
  return out};

/* badge for new problems in the menu */
setTimeout(()=>{const b=$('#errCount');if(!b||!S||!(S.sections.includes('*')||S.sections.includes('errors')))return;
  api('/api/admin/errors').then(d=>{const n=d.rows.filter(r=>!r.fixed&&Date.now()/1000-r.last<86400&&/server|browser|check/.test(r.source)).length;b.textContent=n||'';b.hidden=!n}).catch(()=>{})},1500);
if(['bot','errors','messages'].includes(location.hash.slice(1))&&S)render();else if(['bot','errors'].includes(location.hash.slice(1)))setTimeout(()=>{if(S)render()},800);

}
