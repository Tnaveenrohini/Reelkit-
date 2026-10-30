/* WaveRise service worker. It only handles files shared to the installed app (Share → WaveRise) and keeps them on the phone. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'POST' || u.origin !== location.origin || u.pathname !== '/share-target') return;
  e.respondWith((async () => {
    let fd;
    try { fd = await e.request.formData(); } catch (err) { return Response.redirect('/whatsapp-status-saver?share_failed=1', 303); }
    const files = fd.getAll('media').filter(f => f && typeof f === 'object' && f.size);
    if (files.length) {
      await caches.delete('wr-share');
      const c = await caches.open('wr-share');
      let i = 0;
      for (const f of files) {
        await c.put('/shared/' + (i++) + '-' + Date.now(), new Response(f, { headers: {
          'content-type': f.type || 'application/octet-stream', 'x-name': encodeURIComponent(f.name || 'status'), 'x-time': String(f.lastModified || Date.now()) } }));
      }
      return Response.redirect('/whatsapp-status-saver?shared=' + files.length, 303);
    }
    const q = new URLSearchParams();
    for (const k of ['title', 'text', 'url']) { const v = fd.get(k); if (v) q.set(k, String(v)); }
    return Response.redirect('/' + (q.toString() ? '?' + q : ''), 303);
  })());
});
