/* DOM integration checks; no browser or network service is started. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..');
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function page(slug='',fetcher,controls,ads) {
  const errors=[],draws=[];
  const console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e));
  let template=fs.readFileSync(path.join(root,'templates/index.html'),'utf8');
  if(controls)template=template.replace('id="wr-management">{}</script>','id="wr-management">'+JSON.stringify(controls).replace(/</g,'\\u003c')+'</script>');
  if(ads)template=template.replace('id="wr-ads">{}</script>','id="wr-ads">'+JSON.stringify(ads)+'</script>');
  const dom=new JSDOM(template,{
    url:'https://waverise.test/'+slug,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console
  });
  const w=dom.window;
  w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
  w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};
  w.confirm=()=>true;
  let id=0;w.URL.createObjectURL=()=>`blob:test-${++id}`;w.URL.revokeObjectURL=()=>{};
  w.HTMLMediaElement.prototype.pause=function(){};w.HTMLMediaElement.prototype.load=function(){};
  const click=w.HTMLAnchorElement.prototype.click;
  w.HTMLAnchorElement.prototype.click=function(){if(!this.hasAttribute('download'))click.call(this);};
  w.HTMLCanvasElement.prototype.getContext=()=>new Proxy({
    measureText:t=>({width:String(t).length*15}),
    fillText:(...args)=>draws.push(args),
    createLinearGradient:()=>({addColorStop(){}}),createRadialGradient:()=>({addColorStop(){}})
  },{get:(obj,key)=>key in obj?obj[key]:(()=>{})});
  w.HTMLCanvasElement.prototype.toBlob=function(fn,type){fn(new w.Blob(['image'],{type:type||'image/png'}));};
  w.HTMLCanvasElement.prototype.toDataURL=()=> 'data:image/png;base64,aGVsbG8=';
  Object.defineProperty(w.document,'fonts',{value:{ready:Promise.resolve(),load:()=>Promise.resolve([])}});
  w.fetch=async(url,opts)=>{
    if(fetcher){const response=await fetcher(String(url),opts);if(response)return response;}
    return {ok:true,json:async()=>({ai:false})};
  };
  for(const tag of w.document.querySelectorAll('script')){
    if(tag.type&&tag.type!=='text/javascript')continue;
    const src=tag.getAttribute('src');
    if(src){const name=src.split('?')[0].slice(1);if(fs.existsSync(path.join(root,'public',name)))vm.runInContext(fs.readFileSync(path.join(root,'public',name),'utf8'),dom.getInternalVMContext(),{filename:name});}
    else vm.runInContext(tag.textContent,dom.getInternalVMContext(),{filename:'inline.js'});
  }
  await wait(40);
  return {dom,w,errors,draws};
}

for(const [slug,title] of [['batch-downloads','Batch downloads']]){
  test('direct navigation renders '+slug,async()=>{
    const {dom,w,errors}=await page(slug);
    try{
      assert.equal(w.document.querySelector('#h').textContent,title);
      assert.equal(w.document.querySelector('#chat').hidden,false);
      assert.equal(w.document.querySelector('#f').hidden,true);
      assert.ok(w.document.querySelector('.creator-card'));
      assert.ok(w.document.querySelector(`#nav a[href="/${slug}"]`));
      assert.deepEqual(errors,[]);
      w.history.pushState(null,'','/instagram-video-downloader');w.route();
      assert.equal(w.document.querySelector('#f').hidden,false);
      assert.equal(w.document.documentElement.dataset.creator,undefined);
    }finally{dom.window.close();}
  });
}

test('batch retries a failed post without re-downloading successful posts',async()=>{
  const counts=new Map();
  const {dom,w,errors}=await page('batch-downloads',async(url,opts)=>{
    if(url==='/api/info')return {ok:true,json:async()=>({items:[{type:'video'}]})};
    if(url.startsWith('/api/download?')){
      const post=new URL(url,'https://waverise.test').searchParams.get('url');const n=(counts.get(post)||0)+1;counts.set(post,n);
      if(post.includes('second')&&n===1)return {ok:false,status:502,json:async()=>({detail:'Please retry'})};
      return {ok:true,blob:async()=>new Blob(['video']),headers:new Headers()};
    }
  });
  try{
    w.document.querySelector('#out textarea').value='https://instagram.com/reel/first/\nhttps://instagram.com/reel/second/';
    [...w.document.querySelectorAll('#out button')].find(b=>b.textContent==='Download all').click();await wait(30);
    assert.match(w.document.querySelector('.creator-status').textContent,/1 downloaded · 1 failed/);
    [...w.document.querySelectorAll('#out button')].find(b=>b.textContent==='Retry failed / stopped').click();await wait(30);
    assert.match(w.document.querySelector('.creator-status').textContent,/2 downloaded · 0 failed/);
    assert.equal(counts.get('https://instagram.com/reel/first/'),1);
    assert.equal(counts.get('https://instagram.com/reel/second/'),2);
    assert.deepEqual(errors,[]);
  }finally{dom.window.close();}
});

test('removed tools are absent from desktop, mobile and home navigation',async()=>{
  const {dom,w,errors}=await page();
  try{
    for(const slug of ['auto-subtitles','reel-size-converter','brand-kit','download-diagnostics']) {
      assert.equal(w.document.querySelector(`a[href="/${slug}"]`),null);
    }
    for(const parent of ['#nav','#tsheet','#hub']) {
      assert.ok(w.document.querySelector(`${parent} a[href="/batch-downloads"]`));
    }
    assert.equal(w.WRBrand,undefined);
    assert.deepEqual(errors,[]);
  }finally{dom.window.close();}
});

test('tool switches hide navigation and block a disabled tool during SPA navigation',async()=>{
  const {dom,w,errors}=await page('',null,{tools:{batch:false},paths:{batch:'/batch-downloads'}});
  try{
    assert.ok([...w.document.querySelectorAll('a[href="/batch-downloads"]')].every(a=>a.hidden));
    w.history.pushState(null,'','/batch-downloads');w.route();
    assert.equal(w.document.querySelector('#p').textContent,'This tool is temporarily unavailable.');
    assert.equal(w.document.querySelector('#out textarea'),null);assert.deepEqual(errors,[]);
  }finally{dom.window.close();}
});

test('announcements render as text and batch limits are displayed',async()=>{
  const {dom,w,errors}=await page('batch-downloads',null,{tools:{},batch:{max_links:3,concurrency:1},announcement:{text:'Maintenance <script>alert(1)</script>'}});
  try{
    assert.equal(w.document.querySelector('#site-notice').textContent,'Maintenance <script>alert(1)</script>');
    assert.equal(w.document.querySelector('#site-notice script'),null);
    assert.match(w.document.querySelector('#out').textContent,/Up to 3 links/);assert.deepEqual(errors,[]);
  }finally{dom.window.close();}
});

test('ad page and placement settings apply when navigating between tools and home',async()=>{
  const {dom,w,errors}=await page('instagram-video-downloader',null,null,{c:'ca-pub-1234567890123456',s:'1234567890',pages:['home'],placements:['hub']});
  try{
    assert.equal(w.document.querySelector('ins.adsbygoogle'),null);
    w.history.pushState(null,'','/');w.route();
    assert.ok(w.document.querySelector('.adanchor[data-ad="hub"] ins.adsbygoogle'));
    assert.equal(w.document.querySelector('.adanchor[data-ad="tool"] ins.adsbygoogle'),null);assert.deepEqual(errors,[]);
  }finally{dom.window.close();}
});
