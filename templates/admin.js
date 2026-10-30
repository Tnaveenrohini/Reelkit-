'use strict';
/* WaveRise Admin dashboard. All data comes from /api/admin/* after the server has checked the admin session.
   Text from Google or visitors is always inserted as text (never as HTML). */
const $ = s => document.querySelector(s);
const NS = 'http://www.w3.org/2000/svg';
function h(t, a = {}, ...c) {
  const e = document.createElement(t);
  for (const k in a) { const v = a[k]; if (v == null || v === false) continue;
    if (k === 'class') e.className = v; else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
    else if (k === 'value') e.value = v; else if (k === 'checked') e.checked = !!v; else e.setAttribute(k, v === true ? '' : v); }
  e.append(...c.flat(Infinity).filter(x => x != null && x !== false).map(x => typeof x === 'object' ? x : String(x)));
  return e;
}
function s(t, a = {}, ...c) { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); e.append(...c.flat().filter(Boolean)); return e; }
let S = null, CUR = 'overview';
const cache = {};
const fmt = n => n == null || isNaN(n) ? '–' : Number(n) >= 1000 ? Math.round(n).toLocaleString('en-IN') : (Math.round(n * 10) / 10).toLocaleString('en-IN');
const pct = n => n == null ? '–' : (n * 100).toFixed(1) + '%';
const when = t => t ? new Date(t * 1000).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : '–';
const ago = t => { if (!t) return 'never'; const d = Date.now() / 1000 - t; return d < 90 ? 'just now' : d < 3600 ? Math.round(d / 60) + ' min ago' : d < 86400 ? Math.round(d / 3600) + ' h ago' : Math.round(d / 86400) + ' days ago'; };
function toast(msg, bad) { const t = $('#toast'); t.textContent = msg; t.className = 'toast' + (bad ? ' bad' : ''); t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, 4200); }
async function api(path, body, method) {
  const r = await fetch(path, { method: method || (body !== undefined ? 'POST' : 'GET'), credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': S ? S.csrf : '' }, body: body !== undefined ? JSON.stringify(body) : undefined });
  let j = {}; try { j = await r.json(); } catch (e) { /* not JSON */ }
  if (r.status === 401) { location.href = '/admin'; throw new Error('Signed out'); }
  if (!r.ok) throw new Error(j.detail || j.error || ('Error ' + r.status));
  return j;
}
const busy = (btn, on, label) => { if (!btn) return; if (on) { btn._l = btn.textContent; btn.disabled = true; btn.replaceChildren(h('span', { class: 'spin' }), ' ', label || 'Working…'); } else { btn.disabled = false; btn.textContent = btn._l; } };
const card = (...c) => h('div', { class: 'card' }, ...c);
const stat = (k, v, sub, dot) => h('div', { class: 'card stat hover' }, h('div', { class: 'k' }, dot ? h('span', { class: 'dot ' + dot }) : null, k), h('div', { class: 'v' }, v), sub ? h('div', { class: 's' }, sub) : null);
const tag = (txt, lvl) => h('span', { class: 'tag ' + (lvl || '') }, txt);
const sec = (title, lead, ...c) => h('section', { class: 'sec' }, h('h1', {}, title), lead ? h('p', { class: 'lead' }, lead) : null, ...c);
const table = (heads, rows, empty) => rows.length ? h('div', { class: 'tbl-wrap' }, h('table', {}, h('thead', {}, h('tr', {}, heads.map(x => h('th', { class: x.endsWith('#') ? 'num' : '' }, x.replace(/#$/, ''))))), h('tbody', {}, rows))) : h('div', { class: 'empty card' }, empty || 'Nothing here yet.');
const errBox = e => h('div', { class: 'note bad' }, e.message || String(e));
const needGoogle = () => !S.google.connected ? h('div', { class: 'note warn' }, 'Google isn’t connected yet. ', h('a', { href: '#settings' }, 'Open Settings → Connect Google'), '.') : null;

/* ---------- charts ---------- */
function lineChart(rows, series, opt = {}) {
  const W = 760, H = opt.h || 230, P = { l: 46, r: 46, t: 14, b: 28 };
  if (!rows.length) return h('div', { class: 'empty' }, 'No data for this period yet. Google usually shows search data 2–3 days late.');
  const svg = s('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart', role: 'img', 'aria-label': opt.label || 'Chart' });
  const x = i => P.l + (rows.length === 1 ? (W - P.l - P.r) / 2 : i * (W - P.l - P.r) / (rows.length - 1));
  for (let g = 0; g <= 4; g++) { const y = P.t + g * (H - P.t - P.b) / 4; svg.append(s('line', { x1: P.l, x2: W - P.r, y1: y, y2: y, stroke: '#1B2F5A', 'stroke-width': 1 })); }
  [...series].reverse().forEach((se, ri) => { const si = series.length - 1 - ri;   // first series drawn last, so it sits on top
    const max = Math.max(1, ...rows.map(r => r[se.key] || 0)), y = v => P.t + (1 - (v || 0) / max) * (H - P.t - P.b);
    const d = rows.map((r, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(r[se.key]).toFixed(1)).join(' ');
    if (si === 0) svg.append(s('path', { d: d + ` L${x(rows.length - 1)} ${H - P.b} L${x(0)} ${H - P.b} Z`, fill: se.color, opacity: '.10' }));
    svg.append(s('path', { d, fill: 'none', stroke: se.color, 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    const tx = s('text', { x: si === 0 ? 6 : W - 6, y: P.t + 10, fill: se.color, 'font-size': 11, 'text-anchor': si === 0 ? 'start' : 'end' }); tx.textContent = fmt(max); svg.append(tx);
  });
  [0, Math.floor((rows.length - 1) / 2), rows.length - 1].filter((v, i, a) => a.indexOf(v) === i).forEach(i => { const t = s('text', { x: x(i), y: H - 8, fill: '#7F8BA8', 'font-size': 11, 'text-anchor': 'middle' }); t.textContent = (rows[i].date || '').slice(5); svg.append(t); });
  return h('div', {}, svg, h('div', { class: 'legend' }, series.map(se => h('span', {}, h('i', { style: `background:${se.color}` }), se.label))));
}
function ring(score) {
  const c = 2 * Math.PI * 46, col = score >= 85 ? '#5FD39A' : score >= 60 ? '#F2C14E' : '#FF7A6B';
  const t = s('text', { x: 55, y: 62, 'text-anchor': 'middle', 'font-size': 28, 'font-weight': 800, fill: '#F4F1EA' }); t.textContent = score == null ? '–' : score;
  return s('svg', { viewBox: '0 0 110 110', class: 'score', role: 'img', 'aria-label': 'SEO score ' + score },
    s('circle', { cx: 55, cy: 55, r: 46, fill: 'none', stroke: '#1B2F5A', 'stroke-width': 10 }),
    s('circle', { cx: 55, cy: 55, r: 46, fill: 'none', stroke: col, 'stroke-width': 10, 'stroke-linecap': 'round', 'stroke-dasharray': `${c * (score || 0) / 100} ${c}`, transform: 'rotate(-90 55 55)' }), t);
}

/* ---------- top status ---------- */
function topStatus() {
  const g = S.google, st = S.settings;
  $('#topStatus').replaceChildren(
    h('span', { class: 'pill' }, h('span', { class: 'dot ' + (st.visibility ? 'ok' : 'bad') }), st.visibility ? 'Visible to search engines' : 'Hidden from search engines'),
    h('span', { class: 'pill' }, h('span', { class: 'dot ' + (g.connected ? 'ok' : 'warn') }), g.connected ? 'Google connected' : 'Google not connected'),
    h('span', { class: 'pill' }, h('span', { class: 'dot ' + (S.store.ok ? 'ok' : 'bad') }), S.store.ok ? 'Database connected' : 'No database'));
  $('#who').textContent = S.email;
}

/* ---------- sections ---------- */
const SECTIONS = {
  async overview() {
    const st = S.settings, pages = S.pages, idx = pages.filter(p => p.inspect), indexed = idx.filter(p => p.inspect.verdict === 'PASS').length;
    const health = await api('/api/admin/health').catch(() => ({}));
    const rep = health.report;
    const box = h('div', { class: 'grid g4' },
      stat('Search engine visibility', st.visibility ? 'On' : 'Off', st.visibility ? 'Public pages can be indexed' : 'Every page says noindex', st.visibility ? 'ok' : 'bad'),
      stat('SEO health score', rep ? rep.summary.score : '–', rep ? `${rep.summary.errors} errors · ${rep.summary.warnings} warnings · ${ago(rep.checked)}` : 'Run your first check', rep ? (rep.summary.errors ? 'bad' : rep.summary.warnings ? 'warn' : 'ok') : ''),
      stat('Pages indexed by Google', idx.length ? `${indexed} / ${pages.length}` : '–', idx.length ? `${idx.length} pages inspected` : 'Use Indexing Monitor', idx.length ? (indexed === pages.length ? 'ok' : 'warn') : ''),
      stat('Public pages', pages.filter(p => !p.effective.noindex).length, `${pages.filter(p => p.effective.noindex).length} hidden with noindex`),
      stat('404 errors logged', S.n404 == null ? '–' : S.n404, 'Missing pages people tried to open', S.n404 ? 'warn' : 'ok'),
      stat('Redirects', Object.keys(S.redirects).length, 'Old links sent to new pages'));
    const gsc = h('div', { class: 'card' }, h('h2', {}, 'Google Search (last 28 days)'), S.google.connected && st.gsc_site ? h('p', { class: 'muted' }, h('span', { class: 'spin' }), ' Loading…') : needGoogle() || h('p', { class: 'muted' }, 'Choose your Search Console property in ', h('a', { href: '#settings' }, 'Settings'), '.'));
    if (S.google.connected && st.gsc_site) api('/api/admin/gsc/summary?days=28').then(d => gsc.replaceChildren(h('h2', {}, 'Google Search (last 28 days)'),
      h('div', { class: 'grid g4' }, stat('Clicks', fmt(d.totals.clicks), change(d.totals.clicks, d.previous.clicks)), stat('Impressions', fmt(d.totals.impressions), change(d.totals.impressions, d.previous.impressions)),
        stat('Average CTR', pct(d.totals.ctr)), stat('Average position', fmt(d.totals.position))), lineChart(d.series, [{ key: 'clicks', color: '#F2C14E', label: 'Clicks' }, { key: 'impressions', color: '#6FA8FF', label: 'Impressions' }]))).catch(e => gsc.replaceChildren(h('h2', {}, 'Google Search'), errBox(e)));
    const steps = [
      ['Verify ownership in Google Search Console', !!(st.gsc_site), 'Settings → verification tag or DNS TXT, then choose the property'],
      ['Submit sitemap.xml', null, 'Sitemap Manager → Submit to Google'],
      ['Keep public pages crawlable', st.visibility, 'Visibility is ' + (st.visibility ? 'on' : 'off')],
      ['Link pages to each other', true, 'Menus and footer link every tool page'],
      ['Use canonical URLs', rep ? !rep.pages.some(p => p.issues.some(i => /Canonical/.test(i.text))) : null, 'Checked by SEO Health'],
      ['Fix indexing errors', rep ? rep.summary.errors === 0 : null, rep ? rep.summary.errors + ' errors in the last check' : 'Run SEO Health'],
      ['Watch Search Console reports', S.google.connected, S.google.connected ? 'Connected' : 'Connect Google in Settings']];
    const flow = card(h('h2', {}, 'Google indexing checklist'), h('p', { class: 'muted small' }, 'Google decides when to index pages. These steps help it find and trust WaveRise; there is no instant indexing.'),
      h('ol', { class: 'steps' }, steps.map(([t, ok, d]) => h('li', {}, h('div', {}, h('b', {}, t), ' ', ok === true ? tag('Done', 'ok') : ok === false ? tag('To do', 'warn') : tag('Check'), h('div', { class: 'muted small' }, d))))));
    const actions = card(h('h2', {}, 'Quick actions'), h('div', { class: 'row' },
      h('button', { class: 'btn', onclick: () => go('health', true) }, 'Run SEO check'),
      h('a', { class: 'btn ghost', href: '#sitemap' }, 'Sitemap'),
      h('a', { class: 'btn ghost', href: 'https://search.google.com/search-console', target: '_blank', rel: 'noopener' }, 'Open Search Console ↗')),
      !S.store.ok ? h('div', { class: 'note bad', style: 'margin-top:12px' }, 'No database is connected, so settings can’t be saved. In Vercel open Storage → Create → Upstash (Redis), connect it to this project and redeploy.') : null);
    return sec('Overview', 'Live status of WaveRise in Google search. Everything here is private to admins.', box, h('div', { class: 'grid g2', style: 'margin-top:14px' }, gsc, flow), h('div', { style: 'margin-top:14px' }, actions));
  },

  async gsc() {
    const out = sec('Google Search Console', 'Clicks, impressions, average position and the searches that bring people to WaveRise. Data comes straight from Google.');
    const ng = needGoogle(); if (ng) { out.append(ng); return out; }
    if (!S.settings.gsc_site) { out.append(h('div', { class: 'note warn' }, 'Choose your Search Console property in ', h('a', { href: '#settings' }, 'Settings'), '.')); return out; }
    let days = cache.gscDays || 28;
    const body = h('div', {}, h('p', { class: 'muted' }, h('span', { class: 'spin' }), ' Loading…'));
    const seg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Period' }, [7, 28, 90].map(d => h('button', { type: 'button', class: d === days ? 'on' : '', onclick: () => { cache.gscDays = d; render(); } }, d + ' days')));
    out.append(h('div', { class: 'row', style: 'margin-bottom:14px' }, seg, h('span', { class: 'muted small' }, 'Property: ', h('span', { class: 'mono' }, S.settings.gsc_site))), body);
    try {
      const d = await api('/api/admin/gsc/summary?days=' + days);
      body.replaceChildren(
        h('div', { class: 'grid g4' }, stat('Clicks', fmt(d.totals.clicks), change(d.totals.clicks, d.previous.clicks)), stat('Impressions', fmt(d.totals.impressions), change(d.totals.impressions, d.previous.impressions)),
          stat('Average CTR', pct(d.totals.ctr), change(d.totals.ctr, d.previous.ctr)), stat('Average position', fmt(d.totals.position), d.previous.position ? 'was ' + fmt(d.previous.position) : '')),
        card(lineChart(d.series, [{ key: 'clicks', color: '#F2C14E', label: 'Clicks' }, { key: 'impressions', color: '#6FA8FF', label: 'Impressions' }], { label: 'Clicks and impressions by day' })),
        h('div', { class: 'grid g2', style: 'margin-top:14px' },
          h('div', {}, h('h2', {}, 'Top searches'), table(['Search', 'Clicks#', 'Impr.#', 'Pos.#'], d.queries.map(q => h('tr', {}, h('td', {}, q.query), h('td', { class: 'num' }, fmt(q.clicks)), h('td', { class: 'num' }, fmt(q.impressions)), h('td', { class: 'num' }, fmt(q.position)))), 'No searches yet.')),
          h('div', {}, h('h2', {}, 'Top pages'), table(['Page', 'Clicks#', 'Impr.#'], d.pages.map(p => h('tr', {}, h('td', { class: 'mono' }, p.page.replace(/^https?:\/\/[^/]+/, '') || '/'), h('td', { class: 'num' }, fmt(p.clicks)), h('td', { class: 'num' }, fmt(p.impressions)))), 'No pages with impressions yet.'))));
    } catch (e) { body.replaceChildren(errBox(e)); }
    const sm = card(h('h2', {}, 'Sitemaps in Search Console'), h('p', { class: 'muted' }, h('span', { class: 'spin' }), ' Loading…'));
    out.append(h('div', { style: 'margin-top:14px' }, sm));
    loadSitemaps(sm);
    return out;
  },

  async ga() {
    const out = sec('Google Analytics', 'Visitors, sessions and page views from Google Analytics 4. Vercel Web Analytics keeps running alongside it.');
    const st = S.settings;
    out.append(card(h('h2', {}, 'Tracking on the public site'), st.ga_id ? h('p', {}, tag('On', 'ok'), ' Measurement ID ', h('span', { class: 'mono' }, st.ga_id), ' is added to every public page.') : h('p', {}, tag('Off'), ' Add your Measurement ID (G-…) in ', h('a', { href: '#settings' }, 'Settings'), ' to start collecting.')));
    const ng = needGoogle(); if (ng) { out.append(h('div', { style: 'margin-top:14px' }, ng)); return out; }
    if (!st.ga_property) { out.append(h('div', { class: 'note warn', style: 'margin-top:14px' }, 'Choose the Analytics property for reports in ', h('a', { href: '#settings' }, 'Settings'), '.')); return out; }
    const body = h('div', { style: 'margin-top:14px' }, h('p', { class: 'muted' }, h('span', { class: 'spin' }), ' Loading…')); out.append(body);
    try {
      const d = await api('/api/admin/ga/summary?days=28');
      body.replaceChildren(h('div', { class: 'grid g4' }, stat('Users (28 days)', fmt(d.totals.users)), stat('Sessions', fmt(d.totals.sessions)), stat('Page views', fmt(d.totals.views))),
        card(lineChart(d.series, [{ key: 'users', color: '#F2C14E', label: 'Users' }, { key: 'views', color: '#6FA8FF', label: 'Page views' }], { label: 'Users and page views by day' })),
        h('div', { class: 'grid g2', style: 'margin-top:14px' },
          h('div', {}, h('h2', {}, 'Top pages'), table(['Page', 'Views#', 'Users#'], d.pages.map(p => h('tr', {}, h('td', { class: 'mono' }, p.page), h('td', { class: 'num' }, fmt(p.views)), h('td', { class: 'num' }, fmt(p.users)))), 'No data yet.')),
          h('div', {}, h('h2', {}, 'Where visitors come from'), table(['Channel', 'Sessions#'], d.channels.map(c => h('tr', {}, h('td', {}, c.channel), h('td', { class: 'num' }, fmt(c.sessions)))), 'No data yet.'))));
    } catch (e) { body.replaceChildren(errBox(e)); }
    return out;
  },

  async sitemap() {
    const out = sec('Sitemap Manager', 'WaveRise builds sitemap.xml automatically from every public page. Pages you hide with noindex are left out, and admin or API addresses are never listed.');
    const url = S.site_url + '/sitemap.xml';
    const live = card(h('h2', {}, 'Live sitemap'), h('p', { class: 'muted' }, h('span', { class: 'spin' }), ' Checking…'));
    out.append(h('div', { class: 'row', style: 'margin-bottom:14px' }, h('a', { class: 'btn ghost', href: '/sitemap.xml', target: '_blank', rel: 'noopener' }, 'Open sitemap.xml ↗'),
      S.google.connected && S.settings.gsc_site ? h('button', { class: 'btn', onclick: async e => { busy(e.target, true, 'Submitting…'); try { await api('/api/admin/gsc/sitemaps/submit', {}); toast('Sitemap submitted to Google.'); } catch (er) { toast(er.message, true); } busy(e.target, false); } }, 'Submit to Google') : null), live);
    try {
      const r = await fetch('/sitemap.xml', { cache: 'no-store' }), txt = await r.text(), doc = new DOMParser().parseFromString(txt, 'application/xml');
      const bad = doc.querySelector('parsererror'), locs = [...doc.getElementsByTagName('loc')].map(x => x.textContent);
      const inc = new Set(locs.map(u => new URL(u).pathname));
      live.replaceChildren(h('h2', {}, 'Live sitemap'), h('p', {}, r.ok && !bad ? tag('Valid XML', 'ok') : tag('Problem', 'bad'), ' ', h('span', { class: 'mono' }, url), ` · ${locs.length} pages · HTTP ${r.status}`),
        table(['Page', 'Address', 'In sitemap'], S.pages.map(p => h('tr', {}, h('td', {}, p.name), h('td', { class: 'mono' }, p.path), h('td', {}, inc.has(p.path) ? tag('Included', 'ok') : tag(p.effective.noindex ? 'Hidden (noindex)' : 'Missing', p.effective.noindex ? '' : 'bad'))))));
    } catch (e) { live.replaceChildren(h('h2', {}, 'Live sitemap'), errBox(e)); }
    if (S.google.connected && S.settings.gsc_site) { const sm = card(h('h2', {}, 'What Google has read'), h('p', { class: 'muted' }, h('span', { class: 'spin' }), ' Loading…')); out.append(h('div', { style: 'margin-top:14px' }, sm)); loadSitemaps(sm); }
    out.append(h('p', { class: 'muted small', style: 'margin-top:14px' }, 'The sitemap updates by itself when you change a page’s settings here. Changes reach visitors and Google within about 5 minutes. If WaveRise grows past 45,000 pages it automatically becomes a sitemap index.'));
    return out;
  },

  async robots() {
    const out = sec('Robots.txt Manager', 'robots.txt tells search engines what not to crawl. It is not a lock: the admin area is protected by sign-in on the server, and robots.txt only keeps it out of search results.');
    const ta = h('textarea', { 'aria-label': 'Extra robots.txt rules', placeholder: 'Disallow: /some-private-path\nCrawl-delay: 5', value: S.settings.robots_extra || '' });
    const pre = h('pre', {}, 'Loading…');
    fetch('/robots.txt', { cache: 'no-store' }).then(r => r.text()).then(t => pre.textContent = t).catch(() => pre.textContent = 'Couldn’t load robots.txt');
    out.append(h('div', { class: 'grid g2' },
      card(h('h2', {}, 'Live robots.txt'), pre, h('p', { class: 'muted small', style: 'margin-top:10px' }, 'Always included: ', h('span', { class: 'mono' }, 'Allow: /, Disallow: /api/, Disallow: /admin'), ' and the sitemap address.')),
      card(h('h2', {}, 'Extra rules'), h('p', { class: 'muted small' }, 'One rule per line. Allowed: User-agent, Allow, Disallow, Crawl-delay. “Disallow: /” is blocked here — use the visibility switch in Settings instead.'), ta,
        h('div', { class: 'row', style: 'margin-top:12px' }, h('button', { class: 'btn', onclick: async e => { busy(e.target, true, 'Saving…'); try { const j = await api('/api/admin/settings', { robots_extra: ta.value }); S.settings = j.settings; toast('robots.txt saved. Live within 5 minutes.'); render(); } catch (er) { toast(er.message, true); busy(e.target, false); } } }, 'Save rules')))));
    return out;
  },

  async meta() {
    const out = sec('Meta Tags Manager', 'Each public page has its own title, description, canonical URL, Open Graph and Twitter card. Edit any page here; leave a field empty to use the built-in text.');
    const rows = S.pages.map(p => {
      const tl = p.effective.t.length, dl = p.effective.d.length;
      return h('tr', {}, h('td', {}, h('b', {}, p.name), h('div', { class: 'mono muted' }, p.path)),
        h('td', {}, p.effective.t, h('div', { class: 'count ' + (tl > 70 ? 'bad' : tl > 60 ? 'warn' : 'ok') }, tl + ' characters')),
        h('td', { class: 'small' }, p.effective.d, h('div', { class: 'count ' + (dl > 165 || dl < 70 ? 'warn' : 'ok') }, dl + ' characters')),
        h('td', {}, p.effective.noindex ? tag('noindex', 'bad') : Object.keys(p.override || {}).length ? tag('Edited', 'ok') : tag('Default')),
        h('td', {}, h('button', { class: 'btn ghost sm', onclick: () => editMeta(p) }, 'Edit')));
    });
    out.append(table(['Page', 'Title', 'Description', 'Status', ''], rows));
    return out;
  },

  async schema() {
    const out = sec('Schema Manager', 'Structured data (JSON-LD) helps Google understand each page. WaveRise adds the right type automatically: WebSite and Organization on the home page, WebApplication on tools, FAQPage on the FAQ.');
    const pick = h('select', { 'aria-label': 'Page' }, S.pages.map(p => h('option', { value: p.key }, `${p.name} (${p.path})`)));
    pick.value = cache.schemaKey || S.pages[0].key;
    const panel = h('div', { style: 'margin-top:14px' });
    const load = async () => {
      cache.schemaKey = pick.value; panel.replaceChildren(h('p', { class: 'muted' }, h('span', { class: 'spin' }), ' Loading…'));
      const d = await api('/api/admin/schema/' + pick.value), cur = d.current || { mode: 'auto' };
      const mode = h('div', { class: 'seg', role: 'radiogroup' }); let m = cur.mode;
      const ta = h('textarea', { style: 'min-height:260px', 'aria-label': 'Custom JSON-LD', value: cur.mode === 'custom' && cur.json ? JSON.stringify(JSON.parse(cur.json), null, 2) : JSON.stringify(d.auto, null, 2) });
      const res = h('div', { style: 'margin-top:10px' });
      const draw = () => { mode.replaceChildren(...[['auto', 'Automatic'], ['custom', 'Custom'], ['off', 'Off']].map(([k, l]) => h('button', { type: 'button', class: m === k ? 'on' : '', onclick: () => { m = k; draw(); } }, l))); ta.readOnly = m !== 'custom'; ta.style.opacity = m === 'off' ? .4 : 1; };
      draw();
      const page = S.pages.find(p => p.key === pick.value);
      panel.replaceChildren(card(h('div', { class: 'row', style: 'justify-content:space-between;margin-bottom:12px' }, mode,
        h('a', { class: 'btn ghost sm', href: 'https://search.google.com/test/rich-results?url=' + encodeURIComponent(S.site_url + page.path), target: '_blank', rel: 'noopener' }, 'Google Rich Results Test ↗')),
        h('p', { class: 'muted small' }, 'Automatic shows what WaveRise adds now. Choose Custom to edit it; Off removes structured data from this page.'), ta, res,
        h('div', { class: 'row', style: 'margin-top:12px' },
          h('button', { class: 'btn ghost', onclick: async () => { const j = await api('/api/admin/schema/validate', { json: ta.value }).catch(e => ({ results: [{ level: 'error', text: e.message }] })); res.replaceChildren(...j.results.map(r => h('div', { class: 'issue' }, tag(r.level === 'ok' ? 'OK' : r.level === 'warn' ? 'Tip' : 'Error', r.level === 'ok' ? 'ok' : r.level === 'warn' ? 'warn' : 'bad'), r.text))); } }, 'Validate'),
          h('button', { class: 'btn', onclick: async e => { busy(e.target, true, 'Saving…'); try { await api('/api/admin/schema', { key: pick.value, mode: m, json: ta.value }); toast('Structured data saved.'); } catch (er) { toast(er.message, true); } busy(e.target, false); } }, 'Save'))));
    };
    pick.addEventListener('change', () => load().catch(e => panel.replaceChildren(errBox(e))));
    out.append(h('label', { class: 'f' }, 'Page', pick), panel);
    load().catch(e => panel.replaceChildren(errBox(e)));
    return out;
  },

  async indexing() {
    const out = sec('Indexing Monitor', 'Ask Google whether each page is in its index, using the official URL Inspection API (up to 2,000 checks a day).');
    const ng = needGoogle(); if (ng) out.append(ng);
    const runAll = h('button', { class: 'btn', disabled: !S.google.connected || !S.settings.gsc_site, onclick: async e => { busy(e.target, true, 'Asking Google…'); try { const j = await api('/api/admin/gsc/inspect', {}); S.pages.forEach(p => { if (j.results[p.key]) p.inspect = j.results[p.key]; }); toast('Index status updated.'); render(); } catch (er) { toast(er.message, true); busy(e.target, false); } } }, 'Check all pages with Google');
    const done = S.pages.filter(p => p.inspect), yes = done.filter(p => p.inspect.verdict === 'PASS').length;
    out.append(h('div', { class: 'grid g4', style: 'margin:14px 0' }, stat('Indexed', done.length ? yes : '–', done.length ? `of ${done.length} checked` : 'Not checked yet', done.length ? (yes === done.length ? 'ok' : 'warn') : ''),
      stat('Not indexed', done.length ? done.length - yes : '–', 'Discovered or crawled but not in Google yet')), h('div', { class: 'row', style: 'margin-bottom:12px' }, runAll));
    const lvl = v => v === 'PASS' ? 'ok' : v === 'NEUTRAL' ? 'warn' : v ? 'bad' : '';
    out.append(table(['Page', 'Google says', 'Coverage', 'Last crawl', 'Google canonical', ''], S.pages.map(p => { const r = p.inspect || {};
      return h('tr', {}, h('td', {}, h('b', {}, p.name), h('div', { class: 'mono muted' }, p.path)), h('td', {}, r.verdict ? tag(r.verdict === 'PASS' ? 'Indexed' : r.verdict === 'NEUTRAL' ? 'Not indexed' : 'Problem', lvl(r.verdict)) : tag('Not checked')),
        h('td', { class: 'small' }, r.coverageState || '–', r.robotsTxtState && r.robotsTxtState !== 'ALLOWED' ? h('div', { class: 'count bad' }, 'robots.txt: ' + r.robotsTxtState) : null),
        h('td', { class: 'small' }, r.lastCrawlTime ? new Date(r.lastCrawlTime).toLocaleDateString('en-IN') : '–'), h('td', { class: 'mono small' }, r.googleCanonical ? r.googleCanonical.replace(/^https?:\/\/[^/]+/, '') : '–'),
        h('td', {}, S.google.connected && S.settings.gsc_site ? h('button', { class: 'btn ghost sm', onclick: async e => { busy(e.target, true, '…'); try { const j = await api('/api/admin/gsc/inspect', { keys: [p.key] }); p.inspect = j.results[p.key]; render(); } catch (er) { toast(er.message, true); busy(e.target, false); } } }, 'Check') : null)); })));
    return out;
  },

  async health(runNow) {
    const out = sec('SEO Health', 'Real checks of the live site: robots.txt, sitemap, every page’s title, description, canonical, social tags and structured data, duplicate text and broken internal links. It also runs daily on its own.');
    const runBtn = h('button', { class: 'btn', onclick: async () => { busy(runBtn, true, 'Checking every page…'); try { cache.health = await api('/api/admin/health/run', {}); toast('SEO check finished.'); render(); } catch (er) { toast(er.message, true); busy(runBtn, false); } } }, 'Run check now');
    out.append(h('div', { class: 'row', style: 'margin-bottom:14px' }, runBtn));
    const d = cache.health || await api('/api/admin/health').catch(e => ({ error: e })); cache.health = null;
    if (runNow === true) { setTimeout(() => runBtn.click(), 50); }
    const rep = d.report;
    if (!rep) { out.append(h('div', { class: 'empty card' }, 'No check has run yet. Tap “Run check now”.')); return out; }
    const sum = rep.summary;
    out.append(card(h('div', { class: 'row', style: 'gap:22px' }, ring(sum.score), h('div', {}, h('h2', {}, sum.errors ? 'Needs attention' : sum.warnings ? 'Good, with tips' : 'Excellent'),
      h('p', { class: 'muted', style: 'margin:0' }, `${sum.pages} pages checked ${ago(rep.checked)} in ${sum.seconds}s · ${sum.errors} errors · ${sum.warnings} warnings`),
      (d.history || []).length > 1 ? h('p', { class: 'muted small' }, 'Previous scores: ', d.history.slice(1, 8).map(x => x.score).join(' · ')) : null))));
    out.append(h('h2', { style: 'margin-top:20px' }, 'Site checks'), table(['Check', 'Result', 'Details'], rep.site.map(c => h('tr', {}, h('td', {}, c.name), h('td', {}, tag(c.ok ? 'Pass' : c.level === 'warn' ? 'Warning' : 'Fail', c.ok ? 'ok' : c.level === 'warn' ? 'warn' : 'bad')), h('td', { class: 'small muted' }, c.detail)))));
    if (rep.broken.length) out.append(h('h2', { style: 'margin-top:20px' }, 'Broken internal links'), table(['Link', 'Status', 'Found on'], rep.broken.map(b => h('tr', {}, h('td', { class: 'mono' }, b.link), h('td', {}, tag(b.status || 'No answer', 'bad')), h('td', { class: 'small mono' }, b.from.map(u => u.replace(/^https?:\/\/[^/]+/, '')).join(', '))))));
    if (rep.duplicates.length) out.append(h('h2', { style: 'margin-top:20px' }, 'Duplicate titles or descriptions'), table(['Field', 'Text', 'Pages'], rep.duplicates.map(x => h('tr', {}, h('td', {}, x.field), h('td', { class: 'small' }, x.value), h('td', { class: 'small mono' }, x.urls.map(u => u.replace(/^https?:\/\/[^/]+/, '')).join(', '))))));
    out.append(h('h2', { style: 'margin-top:20px' }, 'Pages'), table(['Page', 'HTTP', 'Issues', 'Schema'], rep.pages.map(p => h('tr', {},
      h('td', { class: 'mono' }, p.url.replace(/^https?:\/\/[^/]+/, '') || '/', h('div', { class: 'muted small' }, (p.ms || '–') + ' ms')),
      h('td', {}, tag(p.status, p.status === 200 ? 'ok' : 'bad')),
      h('td', {}, p.issues.length ? p.issues.map(i => h('div', { class: 'issue' }, tag(i.level === 'error' ? 'Error' : 'Tip', i.level === 'error' ? 'bad' : 'warn'), i.text)) : tag('All good', 'ok')),
      h('td', { class: 'small' }, (p.schema || []).join(', ') || '–')))));
    return out;
  },

  async redirects() {
    const out = sec('Redirects', 'Send old or mistyped addresses to the right page. Redirects only apply to addresses that don’t exist, so they can never hide a live page.');
    const from = h('input', { type: 'text', placeholder: '/old-page', 'aria-label': 'From' }), to = h('input', { type: 'text', placeholder: '/instagram-reels-downloader or https://…', 'aria-label': 'To' });
    const code = h('select', { 'aria-label': 'Type' }, h('option', { value: '301' }, '301 · Moved permanently (best for SEO)'), h('option', { value: '302' }, '302 · Temporary'), h('option', { value: '308' }, '308 · Permanent (keeps method)'), h('option', { value: '307' }, '307 · Temporary (keeps method)'));
    if (cache.prefill) { from.value = cache.prefill; cache.prefill = null; }
    const hits = await api('/api/admin/404s').then(j => j.redirect_hits).catch(() => ({}));
    out.append(card(h('div', { class: 'grid g2' }, h('label', { class: 'f' }, 'From (missing address)', from), h('label', { class: 'f' }, 'To', to)), h('label', { class: 'f' }, 'Type', code),
      h('button', { class: 'btn', onclick: async e => { busy(e.target, true, 'Saving…'); try { const j = await api('/api/admin/redirects', { from: from.value, to: to.value, code: +code.value }); S.redirects = j.redirects; toast('Redirect saved.'); render(); } catch (er) { toast(er.message, true); busy(e.target, false); } } }, 'Add redirect')));
    const rows = Object.entries(S.redirects).map(([f, r]) => h('tr', {}, h('td', { class: 'mono' }, f), h('td', { class: 'mono' }, r.to), h('td', {}, tag(r.code)), h('td', { class: 'num' }, fmt(hits[f] || 0)), h('td', { class: 'small muted' }, when(r.created)),
      h('td', {}, h('button', { class: 'btn danger sm', onclick: async () => { if (!confirm('Delete the redirect from ' + f + '?')) return; try { const j = await api('/api/admin/redirects', { delete: true, from: f }); S.redirects = j.redirects; toast('Redirect deleted.'); render(); } catch (er) { toast(er.message, true); } } }, 'Delete'))));
    out.append(h('div', { style: 'margin-top:14px' }, table(['From', 'To', 'Type', 'Used#', 'Added', ''], rows, 'No redirects yet.')));
    return out;
  },

  async e404() {
    const out = sec('404 Errors', 'Addresses people (or search engines) tried to open that don’t exist on WaveRise. Fix them with a redirect. Hacking scanners (wp-admin, .php, .env…) are counted separately.');
    try {
      const d = await api('/api/admin/404s');
      out.append(h('div', { class: 'row', style: 'margin-bottom:12px' }, tag(`${d.scanners} scanner requests ignored`), h('button', { class: 'btn danger sm', onclick: async () => { if (!confirm('Clear the 404 log?')) return; await api('/api/admin/404s/clear', {}); S.n404 = 0; toast('404 log cleared.'); render(); } }, 'Clear log')),
        table(['Address', 'Times#', 'Last seen', 'Came from', ''], d.rows.map(r => h('tr', {}, h('td', { class: 'mono' }, r.path), h('td', { class: 'num' }, fmt(r.count)), h('td', { class: 'small muted' }, when(r.last)), h('td', { class: 'small mono' }, r.ref || '–'),
          h('td', {}, h('button', { class: 'btn ghost sm', onclick: () => { cache.prefill = r.path; go('redirects'); } }, 'Redirect')))), 'No missing pages logged. 🎉'));
    } catch (e) { out.append(errBox(e)); }
    return out;
  },

  async audit() {
    const out = sec('Security Logs', 'Every sign-in, refused sign-in and admin change, with time, account and IP address. Kept for the latest 2,000 events.');
    try {
      const d = await api('/api/admin/audit');
      const lvl = a => /denied|rejected|failed/.test(a) ? 'bad' : /login|logout/.test(a) ? '' : 'ok';
      out.append(table(['When', 'Who', 'Action', 'Details', 'IP'], d.rows.map(r => h('tr', {}, h('td', { class: 'small' }, when(r.t)), h('td', { class: 'small' }, r.who), h('td', {}, tag(r.action.replace(/_/g, ' '), lvl(r.action))),
        h('td', { class: 'small mono' }, r.detail ? JSON.stringify(r.detail).slice(0, 220) : ''), h('td', { class: 'small mono' }, r.ip))), 'No events yet.'));
    } catch (e) { out.append(errBox(e)); }
    return out;
  },

  async settings() {
    const st = S.settings, g = S.google;
    const out = sec('Settings', 'Google connection, verification, analytics and search engine visibility. Secrets stay on the server; this page only shows what is safe to show.');
    // Google connection
    out.append(card(h('h2', {}, 'Google connection'), !g.configured ? h('div', { class: 'note bad' }, 'GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set in Vercel.') :
      g.connected ? h('div', {}, h('p', {}, tag('Connected', 'ok'), ' as ', h('b', {}, g.email || '?'), h('span', { class: 'muted small' }, ' · since ' + when(g.at))),
        h('p', { class: 'muted small' }, 'Access: ', (g.scopes || []).filter(x => /webmasters|analytics/.test(x)).map(x => x.split('/').pop()).join(', ') || '–'),
        h('div', { class: 'row' }, h('a', { class: 'btn ghost', href: '/admin/connect-google' }, 'Reconnect'), h('button', { class: 'btn danger', onclick: async () => { if (!confirm('Disconnect Google? Reports stop until you connect again.')) return; await api('/api/admin/google/disconnect', {}); toast('Google disconnected.'); await refresh(); } }, 'Disconnect')))
        : h('div', {}, h('p', { class: 'muted' }, 'Connect the Google account that owns WaveRise in Search Console and Analytics. Read access for Analytics; Search Console access lets you submit the sitemap.'), h('a', { class: 'btn', href: '/admin/connect-google' }, 'Connect Google'))));
    // Search Console property & verification
    const site = h('select', { 'aria-label': 'Search Console property' }, h('option', { value: '' }, '— Choose —'));
    const siteTxt = h('input', { type: 'text', placeholder: 'sc-domain:waverise.in  or  https://www.waverise.in/', value: st.gsc_site || '' });
    if (g.connected) api('/api/admin/gsc/sites').then(j => { site.replaceChildren(h('option', { value: '' }, '— Choose —'), ...j.sites.map(x => h('option', { value: x.siteUrl }, `${x.siteUrl} (${x.permissionLevel.replace('site', '').toLowerCase()})`))); site.value = st.gsc_site || ''; }).catch(() => {});
    site.addEventListener('change', () => siteTxt.value = site.value);
    const ver = h('input', { type: 'text', placeholder: 'Paste the content code, or the whole <meta> tag', value: st.gsc_verification || '' });
    out.append(h('div', { class: 'grid g2', style: 'margin-top:14px' },
      card(h('h2', {}, 'Search Console verification'), h('p', { class: 'muted small' }, 'Option A (recommended): a Domain property verified by a DNS TXT record — covers www and non-www. Option B: a URL-prefix property verified with an HTML tag, added here to every page.'),
        h('ol', { class: 'steps small', style: 'margin-bottom:14px' }, h('li', {}, h('div', {}, 'Open ', h('a', { href: 'https://search.google.com/search-console', target: '_blank', rel: 'noopener' }, 'Search Console'), ' → Add property.')),
          h('li', {}, h('div', {}, h('b', {}, 'Domain'), ': enter waverise.in, copy the ', h('span', { class: 'mono' }, 'google-site-verification=…'), ' TXT value, add it at your domain registrar’s DNS (type TXT, host @), then tap Verify. DNS can take up to a day.')),
          h('li', {}, h('div', {}, h('b', {}, 'URL prefix'), ': enter https://www.waverise.in/, pick “HTML tag”, paste the code below, save, wait 5 minutes, then tap Verify.'))),
        h('label', { class: 'f' }, 'HTML tag verification code', ver)),
      card(h('h2', {}, 'Properties for reports'), h('label', { class: 'f' }, 'Search Console property', g.connected ? site : null, siteTxt),
        (() => { const gp = h('select', { 'aria-label': 'Analytics property' }, h('option', { value: '' }, '— Choose —')), gpTxt = h('input', { type: 'text', placeholder: 'Analytics property ID (numbers)', value: st.ga_property || '' });
          if (g.connected) api('/api/admin/ga/properties').then(j => { gp.replaceChildren(h('option', { value: '' }, '— Choose —'), ...j.properties.map(p => h('option', { value: p.id }, `${p.name} · ${p.account} (${p.id})`))); gp.value = st.ga_property || ''; }).catch(() => {});
          gp.addEventListener('change', () => gpTxt.value = gp.value); cache.gpTxt = gpTxt; return h('label', { class: 'f' }, 'Google Analytics property (reports)', g.connected ? gp : null, gpTxt); })(),
        (() => { const ga = h('input', { type: 'text', placeholder: 'G-XXXXXXXXXX', value: st.ga_id || '' }); cache.gaId = ga; return h('label', { class: 'f' }, 'GA4 Measurement ID (adds tracking to public pages)', ga); })())));
    const og = h('input', { type: 'text', placeholder: '/og-image.jpg?v=12 or https://…', value: st.default_og || '' });
    out.append(h('div', { class: 'row', style: 'margin-top:14px' }, h('button', { class: 'btn', onclick: async e => { busy(e.target, true, 'Saving…');
      try { const j = await api('/api/admin/settings', { gsc_verification: ver.value, gsc_site: siteTxt.value, ga_property: cache.gpTxt.value, ga_id: cache.gaId.value, default_og: og.value }); S.settings = j.settings; toast('Settings saved. Public pages update within 5 minutes.'); render(); }
      catch (er) { toast(er.message, true); busy(e.target, false); } } }, 'Save settings')));
    // Visibility
    const vis = h('input', { type: 'checkbox', checked: st.visibility, 'aria-label': 'Search engine visibility' });
    vis.addEventListener('change', async () => { const on = vis.checked; if (!on && !confirm('Hide the whole site from Google? Every page will say noindex and robots.txt will block crawling. Only do this for maintenance.')) { vis.checked = true; return; }
      try { const j = await api('/api/admin/settings', { visibility: on }); S.settings = j.settings; topStatus(); toast(on ? 'Search engines can index WaveRise again.' : 'WaveRise is now hidden from search engines.'); } catch (er) { vis.checked = !on; toast(er.message, true); } });
    out.append(h('div', { class: 'grid g2', style: 'margin-top:14px' },
      card(h('h2', {}, 'Search engine visibility'), h('label', { class: 'switch' }, vis, 'Let search engines index WaveRise'), h('p', { class: 'muted small' }, 'Turning this off adds noindex to every page and blocks crawling in robots.txt. Keep it on for the public site.')),
      card(h('h2', {}, 'Default share image'), h('p', { class: 'muted small' }, 'Used for Open Graph and Twitter cards when a page has no image of its own. 1200 × 630 px works best.'), og)));
    out.append(card(h('h2', { style: 'margin-top:4px' }, 'Environment'), h('div', { class: 'grid g4' },
      stat('Main address', S.site_url || '–', S.site_url_env ? 'From SITE_URL' : 'Set SITE_URL in Vercel', S.site_url_env ? 'ok' : 'warn'),
      stat('Database', S.store.ok ? 'Connected' : 'Missing', S.store.kind, S.store.ok ? 'ok' : 'bad'),
      stat('Daily SEO check', S.cron ? 'On' : 'Off', S.cron ? 'Vercel Cron, 03:00 UTC' : 'Set CRON_SECRET', S.cron ? 'ok' : 'warn'))));
    return out;
  }
};
function change(now, before) { if (!before) return null; const d = (now - before) / before * 100; return h('span', { class: d >= 0 ? 'up' : 'down' }, (d >= 0 ? '▲ ' : '▼ ') + Math.abs(d).toFixed(0) + '% vs previous'); }
async function loadSitemaps(box) {
  try {
    const j = await api('/api/admin/gsc/sitemaps');
    box.replaceChildren(h('h2', {}, 'Sitemaps in Search Console'), table(['Sitemap', 'Last read by Google', 'Submitted pages#', 'Errors#', 'Warnings#'], j.sitemaps.map(m => h('tr', {}, h('td', { class: 'mono' }, m.path),
      h('td', { class: 'small' }, m.lastDownloaded ? new Date(m.lastDownloaded).toLocaleString('en-IN') : (m.isPending ? 'Pending' : '–')),
      h('td', { class: 'num' }, fmt((m.contents || []).reduce((a, c) => a + Number(c.submitted || 0), 0))), h('td', { class: 'num' }, fmt(Number(m.errors || 0))), h('td', { class: 'num' }, fmt(Number(m.warnings || 0))))),
      'Not submitted yet. Use “Submit to Google” in Sitemap Manager.'));
  } catch (e) { box.replaceChildren(h('h2', {}, 'Sitemaps in Search Console'), errBox(e)); }
}
function editMeta(p) {
  const o = p.override || {}, t = h('input', { type: 'text', value: o.t || '', placeholder: p.default.t }), d = h('textarea', { style: 'min-height:90px;font-family:inherit', value: o.d || '', placeholder: p.default.d });
  const og = h('input', { type: 'text', value: o.og || '', placeholder: 'Default share image' }), ni = h('input', { type: 'checkbox', checked: !!o.noindex });
  const tc = h('span', { class: 'count' }), dc = h('span', { class: 'count' }), prev = h('div', { class: 'serp', 'aria-label': 'Google preview' });
  const upd = () => { const tv = t.value || p.default.t, dv = d.value || p.default.d; tc.textContent = tv.length + ' / 60'; tc.className = 'count ' + (tv.length > 70 ? 'bad' : tv.length > 60 ? 'warn' : 'ok'); dc.textContent = dv.length + ' / 155'; dc.className = 'count ' + (dv.length > 165 ? 'bad' : dv.length > 155 || dv.length < 70 ? 'warn' : 'ok');
    prev.replaceChildren(h('div', { class: 'u' }, (S.site_url || '').replace(/^https?:\/\//, '') + (p.path === '/' ? '' : ' › ' + p.path.slice(1))), h('div', { class: 't' }, tv.length > 62 ? tv.slice(0, 60) + '…' : tv), h('div', { class: 'd' }, dv.length > 158 ? dv.slice(0, 155) + '…' : dv)); };
  [t, d].forEach(x => x.addEventListener('input', upd)); upd();
  const main = $('#main');
  main.replaceChildren(h('section', { class: 'sec' }, h('p', {}, h('a', { href: '#meta' }, '← All pages')), h('h1', {}, p.name), h('p', { class: 'lead mono' }, S.site_url + p.path),
    h('div', { class: 'grid g2' }, card(h('label', { class: 'f' }, h('span', {}, 'SEO title ', tc), t), h('label', { class: 'f' }, h('span', {}, 'Meta description ', dc), d), h('label', { class: 'f' }, 'Share image (Open Graph & Twitter)', og),
      h('label', { class: 'switch', style: 'margin-bottom:14px' }, ni, 'Hide this page from Google (noindex)'),
      h('p', { class: 'muted small' }, 'Canonical URL is set automatically to ', h('span', { class: 'mono' }, S.site_url + p.path), '. Empty fields use the built-in text shown in grey.'),
      h('div', { class: 'row' }, h('button', { class: 'btn', onclick: async e => { busy(e.target, true, 'Saving…'); try { const j = await api('/api/admin/meta', { key: p.key, t: t.value, d: d.value, og: og.value, noindex: ni.checked }); Object.assign(p, j.page); toast('Saved. Live within 5 minutes.'); go('meta'); } catch (er) { toast(er.message, true); busy(e.target, false); } } }, 'Save'),
        h('button', { class: 'btn ghost', onclick: async () => { if (!confirm('Go back to the built-in title and description?')) return; try { const j = await api('/api/admin/meta', { key: p.key, reset: true }); Object.assign(p, j.page); toast('Back to default.'); go('meta'); } catch (er) { toast(er.message, true); } } }, 'Reset to default'))),
      card(h('h2', {}, 'Google preview'), prev, h('p', { class: 'muted small', style: 'margin-top:10px' }, 'Google may still rewrite titles and descriptions. Keep titles around 50–60 characters and descriptions around 120–155.')))));
  main.focus();
}

/* ---------- router ---------- */
async function render(arg) {
  const key = (location.hash.slice(1) || 'overview'); CUR = SECTIONS[key] ? key : 'overview';
  document.querySelectorAll('.side a').forEach(a => a.classList.toggle('on', a.dataset.s === CUR));
  $('#side').classList.remove('open'); $('#menuBtn').setAttribute('aria-expanded', 'false');
  const main = $('#main'); main.replaceChildren(h('p', { class: 'muted pad' }, h('span', { class: 'spin' }), ' Loading…'));
  try { const node = await SECTIONS[CUR](arg); if ((location.hash.slice(1) || 'overview') === key || !SECTIONS[key]) main.replaceChildren(node); }
  catch (e) { main.replaceChildren(sec('Something went wrong', null, errBox(e))); }
  document.title = (document.querySelector('.side a.on') || {}).textContent + ' · WaveRise Admin';
}
function go(k, arg) { if (location.hash.slice(1) === k) render(arg); else { cache.arg = arg; location.hash = k; } }
async function refresh() { S = await api('/api/admin/state'); topStatus(); await render(); }
window.addEventListener('hashchange', () => { const a = cache.arg; cache.arg = undefined; render(a); });
$('#menuBtn').addEventListener('click', () => { const o = $('#side').classList.toggle('open'); $('#menuBtn').setAttribute('aria-expanded', String(o)); });
$('#logout').addEventListener('click', async () => { try { await api('/api/admin/logout', {}); } catch (e) { /* already signed out */ } location.href = '/admin'; });
refresh().catch(e => $('#main').replaceChildren(errBox(e)));
