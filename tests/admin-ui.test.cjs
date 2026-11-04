const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..'),wait=()=>new Promise(resolve=>setTimeout(resolve,35));
const tools={video:{name:'Instagram Video',path:'/instagram-video-downloader'},batch:{name:'Batch downloads',path:'/batch-downloads'}};
const config=()=>({tools:{},batch:{max_links:10,concurrency:1,cooldown:0,requests_per_minute:120},announcement:{enabled:false,text:'',start:0,end:0,pause_downloads:false},
  alerts:{enabled:true,window_minutes:15,min_requests:10,failure_percent:50},ads:{enabled:true,client:'',slot:'',pages:['home'],placements:['hub']}});

async function adminPage(section,role='owner'){
  const errors=[],calls=[],cfg=config(),console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e));
  const dom=new JSDOM(fs.readFileSync(path.join(root,'templates/admin.html'),'utf8'),{url:'https://testserver/admin#'+section,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  const w=dom.window;w.confirm=()=>true;w.scrollTo=()=>{};
  w.URL.createObjectURL=()=> 'blob:backup';w.URL.revokeObjectURL=()=>{};
  w.HTMLAnchorElement.prototype.click=function(){};
  const permissions=role==='owner'?['*']:role==='editor'?['overview','guides','announcements','meta','schema','gsc','ga','sitemap','indexing','health','e404','redirects']:['overview','messages','downloadstats','alerts'];
  w.fetch=async(url,options={})=>{
    const body=options.body?JSON.parse(options.body):undefined;calls.push({url,body,headers:options.headers});
    let data={};
    if(url==='/api/admin/state')data={email:role+'@example.com',csrf:'csrf-value',role,sections:permissions,store:{ok:true,kind:'memory'},google:{configured:true,connected:false},settings:{visibility:true},pages:[],redirects:{},n404:0,site_url:'https://testserver',cron:false};
    else if(url==='/api/admin/manage/config')data={config:cfg,tools,storage:true};
    else if(url.startsWith('/api/admin/manage/config/')){cfg[url.split('/').at(-1)]=body;data={ok:true,config:cfg};}
    else if(url.startsWith('/api/admin/manage/stats'))data={available:true,platforms:{instagram:{requests:2,success:1,failure:1,milliseconds:400,bytes:1024}},rows:[{day:'2026-11-04',platform:'instagram',operation:'download',requests:2,success:1,failure:1}]};
    else if(url==='/api/admin/manage/alerts')data={rows:[],settings:cfg.alerts};
    else if(url==='/api/admin/manage/roles')data={rows:[{email:'owner@example.com',role:'owner',bootstrap:true}]};
    else if(url==='/api/admin/manage/guides')data=body?{ok:true}:{rows:{},categories:[['photos','Photos']],tools};
    else if(url==='/api/admin/manage/history')data={rows:[]};
    else if(url==='/api/admin/manage/backups')data={version:1,created:1,payload:{},signature:'test-signature'};
    else if(url==='/api/admin/messages')data={rows:[],unread:0};
    else if(url==='/api/admin/health')data={report:null,history:[]};
    return {ok:true,status:200,json:async()=>data};
  };
  vm.runInContext(fs.readFileSync(path.join(root,'templates/admin.js'),'utf8'),dom.getInternalVMContext(),{filename:'admin.js'});
  vm.runInContext(fs.readFileSync(path.join(root,'templates/admin-manage.js'),'utf8'),dom.getInternalVMContext(),{filename:'admin-manage.js'});
  await wait();return {dom,w,calls,errors};
}

for(const [section,title] of [['tools','Tool manager'],['downloadstats','Download statistics'],['batchsettings','Batch settings'],['announcements','Announcements'],['guides','Guide editor'],['roles','Admin roles'],['backups','Settings backup & restore'],['alerts','Error alerts'],['ads','Ads manager']]){
  test('admin screen renders '+section,async()=>{
    const {dom,w,errors}=await adminPage(section);
    try{assert.equal(w.document.querySelector('#main h1')?.textContent,title);assert.deepEqual(errors,[]);assert.ok(!w.document.querySelector('#main').textContent.includes('null'));}
    finally{dom.window.close();}
  });
}

test('saving tool availability includes CSRF and persists the chosen switch',async()=>{
  const {dom,w,calls}=await adminPage('tools');
  try{
    w.document.querySelector('input[aria-label="Batch downloads"]').checked=false;
    [...w.document.querySelectorAll('#main button')].find(b=>b.textContent==='Save tool availability').click();await wait();
    const call=calls.find(c=>c.url==='/api/admin/manage/config/tools');assert.equal(call.body.batch,false);assert.equal(call.headers['X-CSRF-Token'],'csrf-value');
  }finally{dom.window.close();}
});

test('support navigation hides owner settings and rejects manual owner-section navigation',async()=>{
  const {dom,w,errors}=await adminPage('downloadstats','support');
  try{
    assert.equal(w.document.querySelector('a[data-s="roles"]').hidden,true);
    assert.equal(w.document.querySelector('a[data-s="messages"]').hidden,false);
    w.location.hash='roles';await wait();await wait();
    assert.equal(w.document.querySelector('#main h1').textContent,'Support overview');assert.deepEqual(errors,[]);
  }finally{dom.window.close();}
});

test('editor can save an announcement without seeing owner-only settings',async()=>{
  const {dom,w,calls}=await adminPage('announcements','editor');
  try{
    const text=w.document.querySelector('textarea[aria-label="Announcement text"]');text.value='Scheduled maintenance';text.dispatchEvent(new w.Event('input'));
    w.document.querySelector('input[aria-label="Show announcement"]').checked=true;
    [...w.document.querySelectorAll('#main button')].find(b=>b.textContent==='Save announcement').click();await wait();
    const call=calls.find(c=>c.url==='/api/admin/manage/config/announcement');assert.equal(call.body.text,'Scheduled maintenance');assert.equal(call.body.enabled,true);
    assert.equal(w.document.querySelector('a[data-s="roles"]').hidden,true);
  }finally{dom.window.close();}
});

test('guide editor previews text safely and submits a publish action',async()=>{
  const {dom,w,calls,errors}=await adminPage('guides');
  try{
    const fill=(selector,value)=>{const el=w.document.querySelector(selector);el.value=value;el.dispatchEvent(new w.Event('input'));};
    fill('input[aria-label="Guide address"]','new-guide');fill('input[aria-label="Guide title"]','A helpful guide');
    fill('textarea[aria-label="Short description"]','A helpful guide description.');fill('textarea[aria-label="Introduction"]','An introduction to this tool.');
    fill('textarea[aria-label="Section 1 text"]','<img src=x onerror=alert(1)>');
    [...w.document.querySelectorAll('#main button')].find(b=>b.textContent==='Preview').click();await wait();
    assert.equal(w.document.querySelector('.manage-preview img'),null);
    [...w.document.querySelectorAll('#main button')].find(b=>b.textContent==='Publish').click();await wait();
    const call=calls.find(c=>c.url==='/api/admin/manage/guides'&&c.body);assert.equal(call.body.action,'publish');assert.equal(call.body.guide.slug,'new-guide');assert.deepEqual(errors,[]);
  }finally{dom.window.close();}
});
