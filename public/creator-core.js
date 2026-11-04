/* Shared, dependency-free validation for batch downloads and their tests. */
(function (root) {
  'use strict';
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
  const api={batchLinks};
  if (typeof module==='object' && module.exports) module.exports=api;
  else root.WRCreatorCore=api;
})(typeof window==='object'?window:globalThis);
