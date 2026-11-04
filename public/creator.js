/* Creator tools integrate with WaveRise's existing router and UI helpers. */
(function () {
  'use strict';
  const C=window.WRCreatorCore;
  const TOOLS={
    batch:['Batch downloads','Paste up to 10 public post links. Download them in order and retry individual failures.'],
    subtitles:['Auto subtitles','Upload a Reel, generate captions or type your own, then edit the timing and export.'],
    resize:['Reel size converter','Upload a saved video. Preview a vertical, square, portrait or landscape version before exporting.'],
    brand:['Brand kit','Save your name, logo, colors and font on this device. Apply them to greetings and status images.'],
    diagnostics:['Download diagnostics','Check a public post link and get a useful next step if it cannot be downloaded.']
  };
  let cleanups=[], generation=0;
  function leave() { generation++; cleanups.splice(0).forEach(fn=>{try{fn();}catch{}}); }
  function objectURL(blob) { const url=URL.createObjectURL(blob); cleanups.push(()=>URL.revokeObjectURL(url)); return url; }
  function controller() { const c=new AbortController(); cleanups.push(()=>c.abort()); return c; }
  const button=(text,fn,primary=false)=>h('button',{type:'button',class:primary?'go':'btn',onclick:fn},text);
  const note=text=>h('p',{class:'creator-note'},text);
  const status=()=>h('p',{class:'creator-status',role:'status'});
  const input=(type,label,attrs={})=>h('input',{type,'aria-label':label,...attrs});
  const field=(label,el)=>h('label',{},h('span',{},label),el);
  function say(el,text,bad=false) {el.textContent=text;el.classList.toggle('err',bad);}
  function panel(...children) {const el=h('div',{class:'card creator-card',style:'display:block'},...children);out(el);return el;}
  async function responseError(response) {
    const data=await response.json().catch(()=>({}));
    const detail=typeof data.detail==='string'?data.detail:response.status===413?'This upload exceeds the server limit. Choose a smaller file.':'The request could not finish. Please try again.';
    const err=new Error(detail);err.diagnostic=data.diagnostic;err.status=response.status;return err;
  }
  async function request(path,options={}) {
    const response=await fetch(API+path,options);
    if(!response.ok)throw await responseError(response);
    return response;
  }
  function save(blob,name,url) {
    const a=h('a',{href:url||objectURL(blob),download:name});document.body.append(a);a.click();a.remove();
  }
  async function capabilities() {return (await request('/api/creator/capabilities',{signal:controller().signal})).json();}
  function videoPicker(onfile) {
    const el=input('file','Choose video',{accept:'video/mp4,video/webm,video/quicktime,video/x-matroska,.mkv,.avi'});
    el.onchange=()=>onfile(el.files[0]);
    return field('Video file',el);
  }
  function checkFile(file,cap) {
    if(!file)throw new Error('Choose a video file first.');
    if(file.size>cap.max_upload_mb*1048576)throw new Error(`Choose a video smaller than ${cap.max_upload_mb} MB.`);
  }
  function diagnosticsBox(error,url,retry) {
    const d=error.diagnostic||{title:'The request could not finish',action:error.message||'Check your connection and try again.',retryable:true};
    const box=h('div',{class:'creator-diagnostic'},h('b',{},d.title),h('p',{},d.action));
    if(d.code)box.append(note('Error code: '+d.code));
    if(retry&&d.retryable) {
      const again=button(d.retry_after?'Retry in 2 minutes':'Retry',retry);
      if(d.retry_after){again.disabled=true;const timer=setTimeout(()=>{again.disabled=false;again.textContent='Retry';},d.retry_after*1000);cleanups.push(()=>clearTimeout(timer));}
      box.append(again);
    }
    if(url)box.append(h('a',{class:'btn',href:'/download-diagnostics?url='+encodeURIComponent(url)},'Check this link'));
    box.append(h('a',{class:'btn',href:'/contact'},'Contact'));
    return box;
  }
  window.WRDownloadError=(error,url,retry)=>out(diagnosticsBox(error,url,retry));

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
    ({batch:showBatch,subtitles:showSubtitles,resize:showResize,brand:showBrand,diagnostics:showDiagnostics})[key]();
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
      if(j.error)j.el.append(diagnosticsBox(j.error,null,null));
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
      retry.disabled=!jobs.some(j=>['error','cancelled'].includes(j.state)&&j.error?.diagnostic?.code!=='rate_limited');
      const rateLimited=jobs.some(j=>j.error?.diagnostic?.code==='rate_limited');
      if(rateLimited){retry.disabled=true;const timer=setTimeout(()=>retry.disabled=false,120000);cleanups.push(()=>clearTimeout(timer));}
      update();
    }
    panel(note('One link per line. Repeated links are removed. Keep this page open; your browser may ask you to allow multiple downloads.'),text,
      h('div',{class:'row'},start,retry,stop),summary,progress,list);
  }

  function showDiagnostics() {
    const url=input('url','Public post link',{class:'tin',placeholder:'https://www.instagram.com/reel/…'}),message=status(),result=h('div');
    const param=new URLSearchParams(location.search).get('url');if(param)url.value=param;
    const go=button('Check link',async()=>{
      if(!url.value.trim())return say(message,'Paste a public post link first.',true);
      go.disabled=true;say(message,'Checking whether the platform will share this post…');result.replaceChildren();
      try{const response=await request('/api/diagnose',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:url.value.trim()}),signal:controller().signal});
        const data=await response.json();say(message,'Check complete.');
        result.replaceChildren(data.ok?h('div',{class:'creator-job'},h('b',{},'Post is readable'),h('p',{},`${data.items} media item(s). ${data.message}`),button('Try download',()=>{const link=url.value.trim();history.pushState(null,'','/instagram-video-downloader');route();$('#u').value=link;$('#f').requestSubmit();})):diagnosticsBox({diagnostic:data.diagnostic},null,()=>go.click()));
      }catch(err){if(err.name!=='AbortError'){say(message,'Could not complete the check.',true);result.replaceChildren(diagnosticsBox(err,null,()=>go.click()));}}
      finally{go.disabled=false;}
    },true);
    panel(field('Public post link',url),h('div',{class:'row'},go),message,result,note('A public post can still be temporarily blocked by its platform. This check cannot access private posts.'));
    url.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();go.click();}};
  }

  async function showResize() {
    const message=status(),body=h('div');panel(message,body);
    let cap;try{cap=await capabilities();}catch(err){if(err.name!=='AbortError')say(message,err.message,true);return;}
    if(!body.isConnected)return;
    let file=null,previewURL=null,exportURL=null,raf=0,loaded=false;
    const video=h('video',{playsinline:'',controls:'',preload:'metadata',class:'creator-preview','aria-label':'Source video'});
    const canvas=h('canvas',{class:'creator-preview','aria-label':'Output crop preview',role:'img'});
    const shape=sel('Output shape',[['vertical','Reels / Status 9:16'],['square','Square 1:1'],['portrait','Portrait 4:5'],['landscape','Landscape 16:9']],'vertical');
    const fit=sel('Fit mode',[['blur','Fit with blurred background'],['crop','Crop to fill'],['pad','Fit with black bars']],'blur');
    const x=input('range','Horizontal crop position',{min:0,max:1,step:.01,value:.5}),y=input('range','Vertical crop position',{min:0,max:1,step:.01,value:.5});
    const result=h('div'),go=button('Export MP4',exportVideo,true),cropControls=h('div',{class:'creator-grid'},field('Horizontal crop position',x),field('Vertical crop position',y));
    function draw() {
      const [w,ht]=C.SIZES[shape.value];canvas.width=w;canvas.height=ht;
      const g=canvas.getContext('2d');g.fillStyle='#000';g.fillRect(0,0,w,ht);
      if(loaded&&video.readyState>=2){
        if(fit.value==='blur'){const r=C.placement(video.videoWidth,video.videoHeight,w,ht,true);g.save();g.filter='blur(20px)';g.drawImage(video,r.x-15,r.y-15,r.w+30,r.h+30);g.restore();}
        const r=C.placement(video.videoWidth,video.videoHeight,w,ht,fit.value==='crop',fit.value==='crop'?+x.value:.5,fit.value==='crop'?+y.value:.5);
        g.drawImage(video,r.x,r.y,r.w,r.h);
      }
      cropControls.hidden=fit.value!=='crop';
    }
    function invalidate(){result.replaceChildren();if(exportURL){URL.revokeObjectURL(exportURL);exportURL=null;}draw();}
    const picker=videoPicker(f=>{
      try{checkFile(f,cap);}catch(err){say(message,err.message,true);return;}
      file=f;loaded=false;go.disabled=true;if(previewURL)URL.revokeObjectURL(previewURL);
      previewURL=objectURL(file);video.src=previewURL;invalidate();say(message,'Reading video…');
    });
    video.onloadedmetadata=()=>{if(video.duration>cap.max_seconds||!Number.isFinite(video.duration)){loaded=false;go.disabled=true;return say(message,'Choose a video up to 3 minutes long.',true);}loaded=true;go.disabled=false;say(message,`${video.duration.toFixed(1)} seconds · ${video.videoWidth} × ${video.videoHeight} · preview below`);draw();};
    video.onloadeddata=video.onseeked=draw;video.onerror=()=>{loaded=false;go.disabled=true;say(message,'This browser cannot preview that video. Try an MP4 file.',true);};
    const animate=()=>{draw();if(!video.paused&&!video.ended)raf=requestAnimationFrame(animate);};video.onplay=animate;
    cleanups.push(()=>{cancelAnimationFrame(raf);video.pause();video.removeAttribute('src');video.load();});
    shape.onchange=fit.onchange=x.oninput=y.oninput=invalidate;
    async function exportVideo(){
      if(!loaded)return say(message,'Choose a video first.',true);
      go.disabled=true;const currentFile=file;const controls=[shape,fit,x,y,picker.querySelector('input')];controls.forEach(el=>el.disabled=true);
      say(message,'Making your MP4. This may take a minute…');result.replaceChildren();
      const form=new FormData();form.append('file',currentFile);form.append('shape',shape.value);form.append('fit',fit.value);form.append('x',x.value);form.append('y',y.value);
      try{const r=await request('/api/creator/resize',{method:'POST',body:form,signal:controller().signal}),blob=await r.blob();
        if(!result.isConnected)return;exportURL=objectURL(blob);result.replaceChildren(h('video',{class:'creator-preview',controls:'',playsinline:'',src:exportURL}),h('a',{class:'btn',href:exportURL,download:fileName(r,'waverise-resized.mp4')},'Download MP4'));say(message,'Ready. Preview and download your resized video.');
      }catch(err){if(err.name!=='AbortError')say(message,err.message,true);}
      finally{go.disabled=false;controls.forEach(el=>el.disabled=false);}
    }
    go.disabled=true;body.append(note(`Upload a saved Reel up to ${cap.max_upload_mb} MB and 3 minutes. Output is 720p MP4.`),picker,
      h('div',{class:'creator-grid'},field('Output shape',shape),field('Fit',fit)),cropControls,video,canvas,h('div',{class:'row'},go),result);
    draw();
  }

  async function showSubtitles() {
    const message=status(),body=h('div');panel(message,body);
    let cap;try{cap=await capabilities();}catch(err){if(err.name!=='AbortError')say(message,err.message,true);return;}
    if(!body.isConnected)return;
    let file=null,cues=[],url=null,trackURL=null,outputURL=null,duration=180,busy=false;
    const video=h('video',{class:'creator-preview',controls:'',playsinline:'',preload:'metadata'}),rows=h('div',{class:'creator-cues'}),result=h('div');
    const language=sel('Spoken language',[['auto','Detect language'],['en','English'],['te','Telugu'],['hi','Hindi'],['ta','Tamil']],'auto');
    const generate=button('Generate subtitles',transcribe,true),add=button('Add caption',()=>{
      const start=cues.length?+cues[cues.length-1].end:0;
      if(start>=duration)return say(message,'No space remains after the last caption. Edit its timing first.',true);
      cues.push({start:+start.toFixed(3),end:+Math.min(start+3,duration).toFixed(3),text:''});paintRows();invalidate();
    });
    const srt=button('Export SRT',()=>exportText('srt')),vtt=button('Export VTT',()=>exportText('vtt')),burn=button('Export captioned MP4',burnVideo,true);
    const picker=videoPicker(f=>{
      try{checkFile(f,cap);}catch(err){say(message,err.message,true);return;}
      if(cues.length&&!confirm('Changing the video will clear its captions. Continue?'))return;
      file=f;cues=[];paintRows();if(url)URL.revokeObjectURL(url);url=objectURL(file);video.src=url;duration=180;invalidate();say(message,'Reading video…');
    });
    video.onloadedmetadata=()=>{if(!Number.isFinite(video.duration)||video.duration>180){file=null;say(message,'Choose a video up to 3 minutes long.',true);return;}duration=video.duration;say(message,`Video ready · ${duration.toFixed(1)} seconds. Generate subtitles or add captions manually.`);};
    video.onerror=()=>{file=null;say(message,'Try an MP4 file that your browser can play.',true);};
    cleanups.push(()=>{video.pause();video.removeAttribute('src');video.load();});
    function invalidate(){result.replaceChildren();if(outputURL){URL.revokeObjectURL(outputURL);outputURL=null;}}
    function preview(){
      const old=video.querySelector('track');if(old)old.remove();if(trackURL){URL.revokeObjectURL(trackURL);trackURL=null;}
      try{const text=C.subtitles(cues,'vtt',duration);trackURL=objectURL(new Blob([text],{type:'text/vtt'}));const track=h('track',{kind:'subtitles',label:'Edited captions',srclang:language.value==='auto'?'en':language.value,src:trackURL,default:''});video.append(track);track.track.mode='showing';}catch{}
    }
    function paintRows(){
      rows.replaceChildren(...cues.map((cue,i)=>{
        const start=input('number',`Caption ${i+1} start`,{min:0,max:180,step:.01,value:cue.start});
        const end=input('number',`Caption ${i+1} end`,{min:0,max:180,step:.01,value:cue.end});
        const text=h('textarea',{'aria-label':`Caption ${i+1} text`,maxlength:500},cue.text);
        const remove=button('Remove',()=>{cues.splice(i,1);paintRows();invalidate();});
        const edit=()=>{cue.start=start.value===''?NaN:+start.value;cue.end=end.value===''?NaN:+end.value;cue.text=text.value;invalidate();preview();};
        start.oninput=end.oninput=text.oninput=edit;
        return h('div',{class:'creator-cue'},field('Start (s)',start),field('End (s)',end),h('label',{class:'creator-text'},h('span',{},'Caption '+(i+1)),text),remove);
      }));preview();
    }
    function setBusy(value){busy=value;[generate,add,srt,vtt,burn,language,picker.querySelector('input'),...rows.querySelectorAll('input,textarea,button')].forEach(el=>el.disabled=value);generate.disabled=value||!cap.transcription;burn.disabled=value||!cap.subtitle_video;}
    async function transcribe(){
      if(busy)return;try{checkFile(file,cap);}catch(err){return say(message,err.message,true);}
      if(cues.length&&!confirm('Replace the edited captions with a new transcription?'))return;
      setBusy(true);say(message,'Transcribing the speech. This can take a minute…');
      const form=new FormData();form.append('file',file);form.append('language',language.value);
      try{const r=await request('/api/creator/transcribe',{method:'POST',body:form,signal:controller().signal}),data=await r.json();
        if(!body.isConnected)return;cues=data.cues;paintRows();invalidate();say(message,'Captions generated. Check the words and timing before exporting.');
      }catch(err){if(err.name!=='AbortError')say(message,err.message,true);}finally{setBusy(false);}
    }
    function exportText(format){try{const text=C.subtitles(cues,format,duration);save(new Blob([text],{type:format==='vtt'?'text/vtt':'application/x-subrip'}),'waverise-captions.'+format);say(message,'Caption file exported.');}catch(err){say(message,err.message,true);}}
    async function burnVideo(){
      if(busy)return;let valid;try{checkFile(file,cap);valid=C.validateCues(cues,duration);}catch(err){return say(message,err.message,true);}
      setBusy(true);say(message,'Adding captions to your MP4…');const form=new FormData();form.append('file',file);form.append('cues',JSON.stringify(valid));
      try{const r=await request('/api/creator/subtitle-video',{method:'POST',body:form,signal:controller().signal}),blob=await r.blob();
        if(!result.isConnected)return;invalidate();outputURL=objectURL(blob);result.replaceChildren(h('video',{class:'creator-preview',controls:'',playsinline:'',src:outputURL}),h('a',{class:'btn',href:outputURL,download:'waverise-captioned.mp4'},'Download captioned MP4'));say(message,'Your captioned video is ready.');
      }catch(err){if(err.name!=='AbortError')say(message,err.message,true);}finally{setBusy(false);}
    }
    body.append(...[note(`Video limit: ${cap.max_upload_mb} MB · 3 minutes. Generation sends the audio to the speech service; manual captions stay on your device until MP4 export.`),picker,
      h('div',{class:'row'},language,generate),!cap.transcription?note('Automatic transcription is not configured on this server yet. You can add captions manually and export SRT or VTT.'):null,
      video,rows,h('div',{class:'row'},add),h('div',{class:'row'},srt,vtt,burn),!cap.subtitle_video?note('Captioned MP4 export needs a server with FFmpeg’s subtitles filter. SRT and VTT export are available.'):null,
      note('Video captions use the fonts installed on the server. Install Noto fonts for Telugu, Hindi and Tamil.'),result].filter(Boolean));
    setBusy(false);
  }

  const BRAND_KEY='wr_brand_v1',FONTS={'Sora':'Sora, system-ui, sans-serif','Georgia':'Georgia, serif','Arial':'Arial, sans-serif'};
  let brand=null,logo=null;
  function readBrand(){try{const saved=JSON.parse(localStorage.getItem(BRAND_KEY)||'null');if(saved&&typeof saved.name==='string'&&saved.name.length<=60&&/^#[a-f0-9]{6}$/i.test(saved.primary)&&/^#[a-f0-9]{6}$/i.test(saved.secondary)&&FONTS[saved.font]&&['top','bottom'].includes(saved.position)&&(!saved.logo||(/^data:image\/png;base64,/.test(saved.logo)&&saved.logo.length<2000000)))return saved;}catch{}return null;}
  brand=readBrand();
  function loadLogo(data){return new Promise((resolve,reject)=>{if(!data)return resolve(null);const im=new Image();im.onload=()=>resolve(im);im.onerror=reject;im.src=data;});}
  const logoReady=loadLogo(brand?.logo).then(im=>logo=im).catch(()=>logo=null);
  function drawBrand(canvas,enabled=brand?.enabled) {
    if(!brand||!enabled)return;
    const g=canvas.getContext('2d'),w=canvas.width,ht=canvas.height,pad=Math.round(w*.035),bar=Math.round(Math.min(ht*.13,w*.15));
    const top=brand.position==='top',y=top?0:ht-bar;
    g.save();g.fillStyle=brand.secondary;g.globalAlpha=.94;g.fillRect(0,y,w,bar);g.globalAlpha=1;
    g.fillStyle=brand.primary;g.fillRect(0,top?bar-5:y,w,5);
    let left=pad;
    if(logo){const k=Math.min((bar-pad*.6)/logo.height,(w*.18)/logo.width);g.drawImage(logo,left,y+(bar-logo.height*k)/2,logo.width*k,logo.height*k);left+=logo.width*k+pad*.6;}
    let fs=Math.round(w*.043);g.fillStyle=brand.primary;g.textAlign='left';g.textBaseline='middle';g.font=`700 ${fs}px ${FONTS[brand.font]||FONTS.Sora}`;
    while(g.measureText(brand.name).width>w-left-pad&&fs>10){fs--;g.font=`700 ${fs}px ${FONTS[brand.font]||FONTS.Sora}`;}
    g.fillText(brand.name,left,y+bar/2);g.restore();
  }
  window.WRBrand={draw:drawBrand,ready:logoReady};
  // Festival exports already draw through this hook, so preview and downloaded output match.
  if(window.WRFest){const original=WRFest.draw;WRFest.draw=(canvas,options)=>{original(canvas,options);drawBrand(canvas);};}
  async function showBrand() {
    await logoReady;
    if(cur!=='brand')return;
    const b=brand?{...brand}:{name:'',primary:'#ffffff',secondary:'#163365',font:'Sora',position:'bottom',logo:'',enabled:false};
    let draftLogo=logo;
    const name=input('text','Brand name',{class:'tin',maxlength:60,value:b.name,placeholder:'Your name or business'}),primary=input('color','Text and accent color',{value:b.primary}),secondary=input('color','Background color',{value:b.secondary});
    const font=sel('Brand font',Object.keys(FONTS).map(k=>[k,k]),b.font),position=sel('Brand position',[['bottom','Bottom'],['top','Top']],b.position);
    const enabled=input('checkbox','Apply brand to greetings');enabled.checked=b.enabled;
    const upload=input('file','Brand logo',{accept:'image/png,image/jpeg,image/webp'}),message=status(),canvas=h('canvas',{width:1080,height:1080,class:'creator-preview',role:'img','aria-label':'Brand preview'});
    function snapshot(){return {...b,name:name.value.trim(),primary:primary.value,secondary:secondary.value,font:font.value,position:position.value,enabled:enabled.checked};}
    function paint(){const g=canvas.getContext('2d');g.fillStyle='#f1eadc';g.fillRect(0,0,1080,1080);g.fillStyle='#163365';g.font='700 70px Georgia';g.textAlign='center';g.fillText('Your next creation',540,480);g.font='34px Sora, sans-serif';g.fillText('Your saved brand, ready to use',540,550);
      const old=brand,oldLogo=logo;brand=snapshot();logo=draftLogo;drawBrand(canvas,true);brand=old;logo=oldLogo;}
    const saveBrand=button('Save brand kit',()=>{
      const next=snapshot();if(!next.name&&!next.logo)return say(message,'Add a brand name or logo first.',true);
      try{localStorage.setItem(BRAND_KEY,JSON.stringify(next));brand=next;logo=draftLogo;dispatchEvent(new Event('wr-brand'));say(message,'Brand kit saved on this device. It will appear on new greetings and daily status images when enabled.');}catch{say(message,'This browser could not save the kit. Try a smaller logo or allow site storage.',true);}
    },true);
    upload.onchange=async()=>{const file=upload.files[0];if(!file)return;if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>5*1048576)return say(message,'Choose a PNG, JPG or WebP logo under 5 MB.',true);
      saveBrand.disabled=true;
      try{const im=await loadImg(file),[w,ht]=dims(im),cv=document.createElement('canvas'),k=Math.min(1,512/Math.max(w,ht));cv.width=Math.max(1,Math.round(w*k));cv.height=Math.max(1,Math.round(ht*k));cv.getContext('2d').drawImage(im,0,0,cv.width,cv.height);b.logo=cv.toDataURL('image/png');draftLogo=await loadLogo(b.logo);paint();say(message,'Logo ready. Save the kit to keep it.');}catch{say(message,'Could not read this logo. Try another image.',true);}finally{saveBrand.disabled=false;}};
    name.oninput=primary.oninput=secondary.oninput=font.onchange=position.onchange=paint;
    panel(note('Your kit is stored in this browser. Save it, then open Festival Frames or Daily Status to use it.'),
      h('div',{class:'creator-grid'},field('Brand name',name),field('Font',font),field('Text and accent',primary),field('Background',secondary),field('Position',position),field('Logo',upload)),
      h('label',{class:'creator-check'},enabled,'Apply to greetings and daily status images'),canvas,
      h('div',{class:'row'},saveBrand,button('Remove logo',()=>{b.logo='';draftLogo=null;upload.value='';paint();}),button('Delete saved kit',()=>{
        if(!confirm('Delete the saved brand kit from this browser?'))return;
        try{localStorage.removeItem(BRAND_KEY);brand=logo=null;showBrand();}catch{say(message,'Could not clear site storage.',true);}
      })),message,h('div',{class:'row'},h('a',{class:'btn',href:'/festival-frames'},'Make a festival greeting'),h('a',{class:'btn',href:'/daily-status-images'},'Make daily status')));
    paint();
  }

  // Expose all five tools in desktop, mobile and home navigation.
  Object.keys(TOOLS).forEach(key=>{M[key]=key;T[key]=[TOOLS[key][0],TOOLS[key][1],'Continue'];GR.toolbtn.push(key);});
  const links=()=>Object.keys(TOOLS).map(k=>h('a',{href:keyHref(k)},TOOLS[k][0]));
  const desktop=$('#toolbtn')?.nextElementSibling;
  desktop?.prepend(h('p',{class:'msec'},'Creator tools'),...links().map(a=>{a.setAttribute('role','menuitem');a.classList.add('creator-nav');return a;}));
  document.querySelector('#tsheet .tscard')?.prepend(h('p',{class:'tsg'},'Creator tools'),h('div',{class:'tsl'},...links()));
  document.querySelector('#hub .hubcard')?.prepend(h('p',{class:'hubg'},'Creator tools'),h('div',{class:'hubl creator-hub'},...links()));

  // Fetch downloads so platform failures remain actionable on the current page.
  // Inline video playback and file sharing continue through the original code.
  document.addEventListener('click',async event=>{
    const a=event.target.closest?.('a[href]');
    if(!a||event.defaultPrevented||event.button||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||a.hasAttribute('download'))return;
    let u;try{u=new URL(a.href,location.href);}catch{return;}
    if(u.origin!==new URL(API||location.origin,location.origin).origin||u.pathname!=='/api/download'||u.searchParams.get('inline')==='1')return;
    event.preventDefault();event.stopImmediatePropagation();
    if(a.dataset.busy)return;a.dataset.busy='1';a.setAttribute('aria-busy','true');const label=a.textContent;a.textContent='Downloading…';
    let errorBox=null;const go=async()=>{
      errorBox?.remove();
      try{const r=await fetch(a.href,{signal:controller().signal});if(!r.ok)throw await responseError(r);const blob=await r.blob();save(blob,fileName(r,'waverise-download'));}
      catch(err){if(err.name!=='AbortError'){errorBox=diagnosticsBox(err,null,go);a.parentElement?.after(errorBox);}}
      finally{delete a.dataset.busy;a.removeAttribute('aria-busy');a.textContent=label;}
    };await go();
  },true);
})();
