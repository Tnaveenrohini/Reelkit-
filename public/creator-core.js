/* Shared, dependency-free validation for the creator tools and their tests. */
(function (root) {
  'use strict';
  const SIZES = {vertical: [720,1280], square: [720,720], landscape: [1280,720], portrait: [720,900]};
  function batchLinks(text) {
    const lines = String(text).split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    const seen = new Set(), links = [];
    for (const line of lines) {
      let u;
      try { u = new URL(line); } catch { throw new Error('Paste one complete https:// post link per line.'); }
      if (!['https:', 'http:'].includes(u.protocol) || u.username || u.password) throw new Error('Use public post links without login details.');
      u.hash = '';
      const host = u.hostname.toLowerCase().replace(/^(www|mobile|m|web|mbasic|touch)\./,'');
      if (!['instagram.com','facebook.com','fb.watch','x.com','twitter.com'].includes(host)) throw new Error('Batch downloads support Instagram, Facebook and X post links.');
      // Instagram/X tracking parameters do not identify a post; Facebook query IDs do.
      if (['instagram.com','x.com','twitter.com'].includes(host)) u.search = '';
      const key = host.replace('twitter.com','x.com') + u.pathname.replace(/\/$/,'') + u.search;
      if (!seen.has(key)) { seen.add(key); links.push(u.href); }
    }
    if (!links.length || links.length > 10) throw new Error('Add between 1 and 10 different post links.');
    return links;
  }
  function validateCues(cues, duration=180) {
    if (!Array.isArray(cues) || !cues.length || cues.length > 300) throw new Error('Add between 1 and 300 captions.');
    let previous = 0;
    return cues.map(c => {
      const start=Number(c.start), end=Number(c.end), text=String(c.text || '').trim();
      if (!Number.isFinite(start) || !Number.isFinite(end) || start < previous || end <= start || end > Math.min(duration,180) || !text || text.length>500) {
        throw new Error('Each caption needs text and non-overlapping start/end times within the video.');
      }
      previous=end;
      return {start,end,text};
    });
  }
  function stamp(seconds, sep=',') {
    let ms=Math.round(seconds*1000);
    return [Math.floor(ms/3600000),Math.floor(ms/60000)%60,Math.floor(ms/1000)%60].map(x=>String(x).padStart(2,'0')).join(':')+sep+String(ms%1000).padStart(3,'0');
  }
  function subtitles(cues, format='srt', duration=180) {
    return (format==='vtt'?'WEBVTT\n\n':'')+validateCues(cues,duration).map((c,i)=>`${i+1}\n${stamp(c.start,format==='vtt'?'.':',')} --> ${stamp(c.end,format==='vtt'?'.':',')}\n${c.text.replace(/<[^>]*>|\{[^}]*\}/g,'').replace(/\\/g,'')}`).join('\n\n')+'\n';
  }
  function placement(iw,ih,w,h,cover=false,x=.5,y=.5) {
    const scale=(cover?Math.max:Math.min)(w/iw,h/ih), dw=iw*scale, dh=ih*scale;
    return {x:(w-dw)*x,y:(h-dh)*y,w:dw,h:dh};
  }
  const api={SIZES,batchLinks,validateCues,subtitles,stamp,placement};
  if (typeof module==='object' && module.exports) module.exports=api;
  else root.WRCreatorCore=api;
})(typeof window==='object'?window:globalThis);
