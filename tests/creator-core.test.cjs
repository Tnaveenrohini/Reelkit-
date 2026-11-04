const {test}=require('node:test');
const assert=require('node:assert/strict');
const C=require('../public/creator-core.js');

test('batch links deduplicate tracking URLs and preserve Facebook video IDs',()=>{
  assert.deepEqual(C.batchLinks('https://instagram.com/reel/a/?igsh=123\nhttps://www.instagram.com/reel/a/\nhttps://facebook.com/watch/?v=1\nhttps://facebook.com/watch/?v=2'),[
    'https://instagram.com/reel/a/','https://facebook.com/watch/?v=1','https://facebook.com/watch/?v=2'
  ]);
  assert.equal(C.batchLinks('https://twitter.com/person/status/123\nhttps://x.com/person/status/123').length,1);
});
test('batch rejects unsupported hosts, credentials and oversized lists',()=>{
  for(const value of ['https://instagram.com.evil.test/reel/a','https://example.com/a','https://a:b@instagram.com/reel/a','not a URL',Array.from({length:11},(_,i)=>`https://instagram.com/reel/${i}`).join('\n')])assert.throws(()=>C.batchLinks(value));
});
test('subtitle export retains Unicode and accurate millisecond timestamps',()=>{
  const cues=[{start:0,end:1.235,text:'నమస్తే'}, {start:1.3,end:2,text:'Hello'}];
  assert.match(C.subtitles(cues),/00:00:01,235\nనమస్తే/);
  assert.match(C.subtitles(cues,'vtt'),/^WEBVTT\n\n1\n00:00:00.000/);
});
test('caption validation rejects overlap, empty text, NaN and out-of-video timing',()=>{
  const bad=[[],[{start:0,end:1,text:''}],[{start:0,end:NaN,text:'x'}],[{start:2,end:3,text:'x'},{start:1,end:2,text:'y'}],[{start:0,end:4,text:'x'}]];
  bad.forEach(cues=>assert.throws(()=>C.validateCues(cues,3)));
});
test('crop previews use the same focal positioning as the server',()=>{
  assert.deepEqual(C.placement(1920,1080,720,1280,true,0,1),{x:-0,y:0,w:1920*(1280/1080),h:1280});
  const r=C.placement(1920,1080,720,720,false);
  assert.equal(r.w,720);assert.equal(r.h,405);assert.equal(r.y,157.5);
});
