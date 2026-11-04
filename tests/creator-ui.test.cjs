/* DOM integration checks; no browser or network service is started. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {JSDOM,VirtualConsole}=require('jsdom');
const root=path.resolve(__dirname,'..');
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function page(slug='',fetcher) {
  const errors=[],draws=[];
  const console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e));
  const dom=new JSDOM(fs.readFileSync(path.join(root,'templates/index.html'),'utf8'),{
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
    return {ok:true,json:async()=>String(url).includes('capabilities')?{transcription:false,subtitle_video:true,max_upload_mb:4,max_seconds:180}:{ai:false}};
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

for(const [slug,title] of [['batch-downloads','Batch downloads'],['auto-subtitles','Auto subtitles'],['reel-size-converter','Reel size converter'],['brand-kit','Brand kit'],['download-diagnostics','Download diagnostics']]){
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

test('diagnostics query is retained rather than triggering an automatic download',async()=>{
  const link='https://www.instagram.com/reel/abc123/';
  const {dom,w,errors}=await page('download-diagnostics?url='+encodeURIComponent(link));
  try{assert.equal(w.document.querySelector('input[aria-label="Public post link"]').value,link);assert.deepEqual(errors,[]);}finally{dom.window.close();}
});

test('manual captions work when transcription is not configured',async()=>{
  const {dom,w,errors}=await page('auto-subtitles');
  try{
    const buttons=[...w.document.querySelectorAll('#out button')];
    assert.equal(buttons.find(b=>b.textContent==='Generate subtitles').disabled,true);
    buttons.find(b=>b.textContent==='Add caption').click();
    const text=w.document.querySelector('textarea[aria-label="Caption 1 text"]');assert.ok(text);
    text.value='Hello Telugu';text.dispatchEvent(new w.Event('input'));
    assert.ok(w.document.querySelector('.creator-cue'));
    buttons.find(b=>b.textContent==='Export SRT').click();
    assert.match(w.document.querySelector('.creator-status').textContent,/exported/);
    assert.deepEqual(errors,[]);
  }finally{dom.window.close();}
});

test('brand kit saves on the device and is drawn by festival exports',async()=>{
  const {dom,w,errors,draws}=await page('brand-kit');
  try{
    const name=w.document.querySelector('input[aria-label="Brand name"]');name.value='Reelkit Studio';name.dispatchEvent(new w.Event('input'));
    w.document.querySelector('input[aria-label="Apply brand to greetings"]').checked=true;
    [...w.document.querySelectorAll('#out button')].find(b=>b.textContent==='Save brand kit').click();
    assert.equal(JSON.parse(w.localStorage.getItem('wr_brand_v1')).name,'Reelkit Studio');
    w.WRFest.draw(w.document.createElement('canvas'),{fest:'morning',size:'square',lang:'en'});
    assert.ok(draws.some(args=>args[0]==='Reelkit Studio'));
    assert.deepEqual(errors,[]);
  }finally{dom.window.close();}
});

test('batch retries a failed post without re-downloading successful posts',async()=>{
  const counts=new Map();
  const {dom,w,errors}=await page('batch-downloads',async(url,opts)=>{
    if(url==='/api/info')return {ok:true,json:async()=>({items:[{type:'video'}]})};
    if(url.startsWith('/api/download?')){
      const post=new URL(url,'https://waverise.test').searchParams.get('url');const n=(counts.get(post)||0)+1;counts.set(post,n);
      if(post.includes('second')&&n===1)return {ok:false,status:502,json:async()=>({detail:'Please retry',diagnostic:{code:'source_error',title:'Source error',action:'Please retry',retryable:true}})};
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
