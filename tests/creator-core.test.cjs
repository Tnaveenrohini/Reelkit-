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
