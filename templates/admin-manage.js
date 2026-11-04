'use strict';
/* Management screens use the existing session, CSRF and rendering helpers. */
const mf=(label,control,help)=>h('label',{class:'f'},h('span',{},label),control,help?h('small',{class:'muted'},help):null);
const mi=(label,value,type='text',extra={})=>h('input',{type,value,'aria-label':label,...extra});
const mb=(label,action,kind='')=>h('button',{type:'button',class:'btn '+kind,onclick:async e=>{
  const btn=e.currentTarget;if(btn.disabled)return;busy(btn,true);
  try{await action(btn);}catch(error){toast(error.message,true);}finally{if(btn.isConnected)busy(btn,false);}
}},label);
const ms=(label,values,value)=>{const select=h('select',{'aria-label':label},values.map(([key,text])=>h('option',{value:key},text)));select.value=value;return select;};
const check=(label,value)=>{const input=h('input',{type:'checkbox',checked:value,'aria-label':label});return {input,el:h('label',{class:'switch'},input,label)};};
const management=()=>api('/api/admin/manage/config');
async function saveManagement(section,value){await api('/api/admin/manage/config/'+section,value);toast('Saved. Public pages update after refresh or within a minute.');}
function checks(values,selected){const fields=values.map(([id,label])=>({id,...check(label,selected.includes(id))}));return {el:h('div',{class:'manage-checks'},fields.map(x=>x.el)),value:()=>fields.filter(x=>x.input.checked).map(x=>x.id)};}
function storageNote(){return !S.store.ok?h('p',{class:'note bad'},'Connect Redis in Vercel Storage to save settings and retain reports.'):null;}

SECTIONS.tools=async()=>{
  const data=await management(),values={};
  const out=sec('Tool manager','Switch tools on or off. Disabled tools disappear from navigation, leave the sitemap, and their server operations are blocked.');
  const rows=Object.entries(data.tools).map(([key,tool])=>{
    values[key]=check(tool.name,data.config.tools[key]!==false);
    return h('tr',{},h('td',{},tool.name),h('td',{class:'mono'},tool.path),h('td',{},values[key].el));
  });
  if(!S.store.ok)out.append(storageNote());
  out.append(table(['Tool','Address','Available'],rows),h('div',{class:'row'},mb('Save tool availability',()=>saveManagement('tools',Object.fromEntries(Object.entries(values).map(([key,item])=>[key,item.input.checked]))))));
  out.append(h('p',{class:'muted small'},'Tools already loaded on a visitor’s device may remain visible until refresh. Backend switches apply within the configuration cache interval.'));
  return out;
};

SECTIONS.downloadstats=async()=>{
  const days=cache.statsDays||7,data=await api('/api/admin/manage/stats?days='+days);
  const select=ms('Report period',[[1,'Today (UTC)'],[7,'Last 7 days'],[30,'Last 30 days']],days);
  select.onchange=()=>{cache.statsDays=+select.value;render();};
  const all=Object.values(data.platforms),sum=key=>all.reduce((n,p)=>n+(p[key]||0),0);
  const out=sec('Download statistics','Aggregated metadata and media requests by platform. Successful server responses do not prove a file was saved on the visitor’s device.');
  if(!S.store.ok)out.append(storageNote());
  out.append(h('div',{class:'row'},select,mb('Refresh',()=>render(),'ghost')),h('div',{class:'grid g4'},
    stat('Requests',fmt(sum('requests'))),stat('Successful responses',fmt(sum('success'))),stat('Failed responses',fmt(sum('failure'))),stat('Bytes served',(sum('bytes')/1048576).toFixed(1)+' MB')),
    table(['Platform','Requests#','Success#','Failed#','Average time#'],Object.entries(data.platforms).map(([source,p])=>h('tr',{},h('td',{},source),h('td',{},fmt(p.requests)),h('td',{},fmt(p.success)),h('td',{},fmt(p.failure)),h('td',{},(p.milliseconds/Math.max(p.requests,1)/1000).toFixed(2)+' s'))),'No download requests recorded yet.'),
    h('h2',{},'Daily detail (UTC)'),table(['Date','Platform','Operation','Requests#','Success#','Failed#'],data.rows.map(r=>h('tr',{},h('td',{},r.day),h('td',{},r.platform),h('td',{},r.operation),h('td',{},fmt(r.requests)),h('td',{},fmt(r.success||0)),h('td',{},fmt(r.failure||0))))));
  return out;
};

SECTIONS.batchsettings=async()=>{
  const {config}=await management(),settings=config.batch,fields={};
  const labels={max_links:['Maximum links per batch',1,20],concurrency:['Concurrent downloads per visitor',1,3],cooldown:['Cooldown between batch starts (seconds)',0,3600],requests_per_minute:['Metadata/media requests per IP per minute',10,600]};
  const out=sec('Batch settings','Control batch size, server processing slots, start cooldowns and request limits. Cooldowns also apply when starting a retry batch.');
  const grid=h('div',{class:'grid g2'},Object.entries(labels).map(([key,[label,min,max]])=>{fields[key]=mi(label,settings[key],'number',{min,max,step:1});return mf(label,fields[key]);}));
  const save=mb('Save batch settings',()=>saveManagement('batch',Object.fromEntries(Object.entries(fields).map(([key,input])=>[key,Number(input.value)]))));
  if(!S.store.ok)out.append(storageNote());
  out.append(card(grid,h('div',{class:'row'},save)));
  out.append(h('p',{class:'muted small'},'Redis shares cooldowns and active download slots between server instances. Download slots are released when a request finishes or disconnects.'));
  return out;
};

function dateValue(seconds){if(!seconds)return '';const date=new Date(seconds*1000);return new Date(date-date.getTimezoneOffset()*60000).toISOString().slice(0,16);}
SECTIONS.announcements=async()=>{
  const {config}=await management(),a=config.announcement;
  const enabled=check('Show announcement',a.enabled),pause=check('Pause downloads while this announcement is active',a.pause_downloads);
  const text=h('textarea',{'aria-label':'Announcement text',maxlength:500,value:a.text}),start=mi('Start time',dateValue(a.start),'datetime-local'),end=mi('End time',dateValue(a.end),'datetime-local');
  const preview=h('div',{class:'manage-banner',role:'status'},a.text||'Announcement preview');text.oninput=()=>preview.textContent=text.value||'Announcement preview';
  return sec('Announcements','Publish a plain-text notice, optionally scheduled. Times use your browser’s local timezone; empty dates start immediately or continue until disabled.',storageNote(),
    card(enabled.el,mf('Announcement text',text),h('div',{class:'grid g2'},mf('Start (optional)',start),mf('End (optional)',end)),pause.el,preview,
      h('div',{class:'row'},mb('Save announcement',()=>saveManagement('announcement',{enabled:enabled.input.checked,text:text.value,start:start.value?Math.floor(new Date(start.value).getTime()/1000):0,end:end.value?Math.floor(new Date(end.value).getTime()/1000):0,pause_downloads:pause.input.checked})))));
};

SECTIONS.guides=async()=>{
  const data=await api('/api/admin/manage/guides');
  const out=sec('Guide editor','Draft, preview and publish help articles. Text is treated as plain text; saved drafts do not change a published article.');
  const list=ms('Choose a guide',[['','New guide'],...Object.entries(data.rows).map(([slug,row])=>[slug,`${row.draft.title} (${row.published?'published':'draft / hidden'})`])],cache.guideSlug||'');
  const editor=h('div');
  function edit(){
    const selected=data.rows[list.value],doc=selected?.draft||{slug:'',title:'',desc:'',lead:'',cat:'photos',tool:Object.keys(data.tools)[0],sections:[{heading:'Getting started',body:''}]};
    const slug=mi('Guide address',doc.slug,'text',{maxlength:90,placeholder:'my-new-guide',readonly:!!list.value}),title=mi('Guide title',doc.title,'text',{maxlength:160}),description=h('textarea',{'aria-label':'Short description',maxlength:300,value:doc.desc}),lead=h('textarea',{'aria-label':'Introduction',maxlength:1000,value:doc.lead});
    const category=ms('Guide category',data.categories,doc.cat),tool=ms('Related tool',Object.entries(data.tools).map(([key,row])=>[key,row.name]),doc.tool),sectionList=h('div',{class:'manage-sections'}),preview=h('div',{class:'manage-preview'});
    let sections=doc.sections.map(s=>({...s}));
    function sectionRows(){sectionList.replaceChildren(...sections.map((section,i)=>{
      const heading=mi('Section '+(i+1)+' heading',section.heading,'text',{maxlength:160}),body=h('textarea',{'aria-label':'Section '+(i+1)+' text',maxlength:3000,value:section.body});
      heading.oninput=()=>section.heading=heading.value;body.oninput=()=>section.body=body.value;
      return card(mf('Section '+(i+1)+' heading',heading),mf('Text',body),mb('Remove section',()=>{if(sections.length>1){sections.splice(i,1);sectionRows();}},'ghost'));
    }));}
    const current=()=>({slug:slug.value,title:title.value,desc:description.value,lead:lead.value,cat:category.value,tool:tool.value,sections});
    async function save(action){if(action==='delete'&&!confirm(selected?.builtin?'Reset this built-in guide to its original article?':'Delete this guide and its draft?'))return;
      await api('/api/admin/manage/guides',{action,guide:current()});cache.guideSlug=action==='delete'?'':slug.value;toast(action==='publish'?'Guide published.':action==='draft'?'Draft saved.':'Guide updated.');await render();}
    const addSection=mb('Add section',()=>{if(sections.length>=12)throw new Error('Use at most 12 sections.');sections.push({heading:'',body:''});sectionRows();},'ghost');
    const previewButton=mb('Preview',()=>{
      preview.replaceChildren(h('h2',{},title.value),h('p',{},lead.value),...sections.map(s=>h('section',{},h('h3',{},s.heading),h('p',{style:'white-space:pre-wrap'},s.body))));
    });
    const actions=h('div',{class:'row'},mb('Save draft',()=>save('draft'),'ghost'),mb('Publish',()=>save('publish')),
      selected?mb('Unpublish',()=>save('unpublish'),'ghost'):null,
      selected?mb(selected.builtin?'Reset built-in article':'Delete guide',()=>save('delete'),'danger'):null);
    editor.replaceChildren(card(h('div',{class:'grid g2'},mf('Address slug',slug),mf('Title',title),mf('Category',category),mf('Related tool',tool)),mf('Short description',description),mf('Introduction',lead)),
      sectionList,h('div',{class:'row'},addSection,previewButton),preview,actions);
    sectionRows();
  }
  list.onchange=()=>{cache.guideSlug=list.value;edit();};if(!S.store.ok)out.append(storageNote());
  out.append(mf('Article',list),editor);edit();return out;
};

SECTIONS.roles=async()=>{
  const data=await api('/api/admin/manage/roles');
  const email=mi('Google email','','email',{placeholder:'person@example.com'}),role=ms('Role',[['support','Support'],['editor','Editor'],['owner','Owner']],'support');
  const out=sec('Admin roles','Owners manage all settings and access. Editors manage SEO, guides and announcements. Support staff can view download reports and alerts, acknowledge alerts, and handle messages.');
  out.append(table(['Account','Role','Actions'],data.rows.map(row=>{
    const locked=row.bootstrap||row.email===S.email,pick=ms('Role for '+row.email,[['owner','Owner'],['editor','Editor'],['support','Support']],row.role);pick.disabled=locked;
    return h('tr',{},h('td',{},row.email,row.bootstrap?h('small',{class:'muted'},' · environment owner'):null),h('td',{},pick),h('td',{},locked?tag('Protected'):h('div',{class:'row'},mb('Save',async()=>{await api('/api/admin/manage/roles',{email:row.email,role:pick.value});toast('Role updated.');render();}),mb('Remove',async()=>{if(!confirm('Remove admin access for '+row.email+'?'))return;await api('/api/admin/manage/roles',{email:row.email,role:'remove'});render();},'danger'))));
  })),card(h('h2',{},'Add an admin'),mf('Google email address',email),mf('Role',role),mb('Grant access',async()=>{await api('/api/admin/manage/roles',{email:email.value,role:role.value});toast('Access granted. The person can sign in with Google.');render();})),
    h('p',{class:'muted small'},'ADMIN_EMAILS remains the recovery owner list. Those accounts cannot be removed here. Role changes apply on the next request; no invitation email is sent.'));
  return out;
};

SECTIONS.backups=async()=>{
  const history=await api('/api/admin/manage/history'),out=sec('Settings backup & restore','Back up SEO settings, tool controls, announcements, ads and guide drafts/publications. Google tokens, sessions, admin accounts, visitor messages and reports are excluded.');
  let selected=null;
  const file=mi('Backup file','','file',{accept:'.json,application/json'}),preview=h('p',{class:'muted'},'Choose an exported backup to inspect it before restoring.');
  file.onchange=async()=>{selected=null;try{const item=file.files[0];if(!item)return;if(item.size>2*1024*1024)throw new Error('Use a backup under 2 MB.');const data=JSON.parse(await item.text());if(data.version!==1||!data.signature||!data.payload)throw new Error('Choose a WaveRise backup export.');selected=data;preview.textContent=`Backup created ${when(data.created)}. Signature will be checked by the server before restore.`;}catch(error){preview.textContent=error.message;}};
  async function apply(payload){if(!confirm('Replace current settings and guides with this backup? A restore point of the current settings will be kept.'))return;await api('/api/admin/manage/backups/restore',payload);toast('Settings restored.');await refresh();}
  out.append(h('div',{class:'row'},mb('Download backup',async()=>{const data=await api('/api/admin/manage/backups'),blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=h('a',{href:url,download:'waverise-settings-'+new Date().toISOString().slice(0,10)+'.json'});document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}),mb('Create restore point',async()=>{await api('/api/admin/manage/history',{});toast('Restore point created.');render();},'ghost')),
    card(h('h2',{},'Restore an exported file'),file,preview,mb('Restore this backup',()=>{if(!selected)throw new Error('Choose a valid backup file first.');return apply({backup:selected});},'danger'),h('p',{class:'muted small'},'Backups are signed with this deployment’s session secret. Editing a file or rotating that secret invalidates its signature.')),
    h('h2',{},'Previous settings (latest 20)'),table(['Created','Account','Change','Action'],history.rows.map(row=>h('tr',{},h('td',{},when(row.created)),h('td',{},row.who),h('td',{},row.label),h('td',{},mb('Restore',()=>apply({id:row.id}),'ghost'))))));
  return out;
};

SECTIONS.alerts=async()=>{
  const data=await api('/api/admin/manage/alerts'),out=sec('Error alerts','Alerts appear when recent request failures cross your threshold. Low-traffic platforms do not trigger until enough requests are recorded.');
  out.append(h('div',{class:'row'},mb('Refresh',()=>render(),'ghost')),...data.rows.map(row=>card(h('p',{},tag(row.level,row.level==='error'?'bad':'warn'),' ',row.message),row.acknowledged?h('p',{class:'muted small'},'Acknowledged by '+row.acknowledged.by+' · '+when(row.acknowledged.at)):row.id!=='storage'?mb('Acknowledge',async()=>{await api('/api/admin/manage/alerts/ack',{id:row.id});render();},'ghost'):null)));
  if(!data.rows.length)out.append(h('p',{class:'note ok'},'No active alerts.'));
  if(S.role==='owner'){
    const cfg=data.settings,enabled=check('Enable error alerts',cfg.enabled),window=mi('Window in minutes',cfg.window_minutes,'number',{min:5,max:60}),minimum=mi('Minimum requests',cfg.min_requests,'number',{min:3,max:500}),percent=mi('Failure percentage',cfg.failure_percent,'number',{min:1,max:100});
    out.append(card(h('h2',{},'Thresholds'),enabled.el,h('div',{class:'grid g2'},mf('Window (minutes)',window),mf('Minimum requests per platform',minimum),mf('Failure percentage',percent)),mb('Save alert settings',async()=>{await saveManagement('alerts',{enabled:enabled.input.checked,window_minutes:+window.value,min_requests:+minimum.value,failure_percent:+percent.value});render();})));
  }
  return out;
};

SECTIONS.ads=async()=>{
  const {config,tools}=await management(),cfg=config.ads,enabled=check('Enable ads',cfg.enabled),client=mi('AdSense publisher ID',cfg.client,'text',{placeholder:'ca-pub-1234567890123456'}),slot=mi('AdSense slot ID',cfg.slot,'text',{placeholder:'1234567890'});
  const pages=checks([['home','Home'],['guides','Guides'],...Object.entries(tools).map(([key,row])=>[key,row.name])],cfg.pages),placements=checks([['hub','Home tool hub'],['tool','Tool content'],['side','Desktop sidebar'],['guide','Guide article'],['guides','Guide list']],cfg.placements);
  return sec('Ads manager','Choose pages and placements for AdSense. This controls ad requests; the ad provider decides whether an ad is actually served.',storageNote(),card(enabled.el,
    h('div',{class:'grid g2'},mf('Publisher ID',client,'Leave blank to use ADSENSE_CLIENT from the server environment.'),mf('Slot ID',slot,'Leave blank to use ADSENSE_SLOT from the server environment.')),
    h('h2',{},'Pages'),pages.el,h('h2',{},'Placements'),placements.el,h('div',{class:'row'},mb('Save ad settings',()=>saveManagement('ads',{enabled:enabled.input.checked,client:client.value.trim(),slot:slot.value.trim(),pages:pages.value(),placements:placements.value()})))));
};

const originalOverview=SECTIONS.overview;
SECTIONS.overview=async()=>S.role!=='support'?originalOverview():sec('Support overview','Handle messages and monitor download requests.',
  h('div',{class:'grid g2'},card(h('h2',{},'Messages'),h('a',{class:'btn',href:'#messages'},'Open inbox')),card(h('h2',{},'Download activity'),h('a',{class:'btn',href:'#downloadstats'},'View statistics'),h('a',{class:'btn ghost',href:'#alerts'},'View alerts'))));

refresh().catch(error=>$('#main').replaceChildren(errBox(error)));
