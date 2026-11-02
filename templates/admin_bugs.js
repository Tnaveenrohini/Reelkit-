'use strict';
/* WaveRise Admin – AI Bug Fixer sections. Uses the helpers of app.js (h, api, sec, card, table, tag, toast, busy, go, when, ago).
   All text from errors, the AI or GitHub is inserted as text, never as HTML. */
(() => {
  const PR = { critical: 'bad', high: 'warn', medium: '', low: '' };
  const STATUS = { verified: 'ok', tests_failed: 'bad', staging_failed: 'bad', failed: 'bad', needs_human: 'warn', awaiting_approval: 'warn',
    rolling_back: 'bad', rolled_back: 'warn', rejected: '', ignored: '', deploying: 'warn', verifying: 'warn' };
  const t = v => v ? new Date(v).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : '–';
  const rel = v => { if (!v) return 'never'; const d = (Date.now() - new Date(v)) / 1000; return d < 90 ? 'just now' : d < 3600 ? Math.round(d / 60) + ' min ago' : d < 86400 ? Math.round(d / 3600) + ' h ago' : Math.round(d / 86400) + ' days ago'; };
  const pr = p => tag(p, PR[p] || '');
  const st = s => tag(String(s || '').replace(/_/g, ' '), STATUS[s] || '');
  const link = (href, txt) => href ? h('a', { href, target: '_blank', rel: 'noopener' }, txt) : '–';
  const pre = (txt, cls) => h('pre', { class: 'bf-pre ' + (cls || '') }, String(txt || ''));
  const bugLink = (id, title) => h('a', { href: '#bfbug', onclick: e => { e.preventDefault(); go('bfbug', id); } }, '#' + id + (title ? ' ' + String(title).slice(0, 90) : ''));
  let timer = null;
  const live = (fn, ms) => { clearInterval(timer); timer = setInterval(() => { if (document.hidden) return; const k = location.hash.slice(1); if (k.startsWith('bf')) fn().catch(() => {}); else clearInterval(timer); }, ms); };
  let BF = null;
  async function state() { BF = await api('/api/admin/bugfixer/state'); return BF; }

  function topBar() {
    const s = BF.settings, w = (BF.workers || [])[0], alive = w && w.age < 120;
    const cfg = BF.configured || {};
    const item = (ok, txt) => h('span', { class: 'pill' }, h('span', { class: 'dot ' + (ok ? 'ok' : 'bad') }), txt);
    return h('div', { class: 'bf-bar' },
      item(alive, alive ? `Worker running (${rel(w.heartbeat)})` + (w.current ? ' · ' + w.current : '') : 'Worker not running'),
      item(!s.paused, s.paused ? 'Paused' : 'Automatic'), item(cfg.claude, 'Claude API'), item(cfg.github, 'GitHub'), item(cfg.vercel, 'Vercel'),
      item(cfg.sentry_api || cfg.sentry_sdk, 'Sentry'));
  }

  function approvals() {
    if (!BF.approvals.length) return null;
    return card(h('h2', {}, 'Waiting for your approval'), ...BF.approvals.map(a => h('div', { class: 'bf-appr' },
      h('div', {}, pr(a.priority), ' ', bugLink(a.bug_id, a.title), ' · risk ', tag(a.risk || '?', a.risk === 'high' ? 'bad' : a.risk === 'medium' ? 'warn' : 'ok'),
        h('div', { class: 'muted small' }, (a.reasons && a.reasons.why) || '', (a.reasons && a.reasons.policy && a.reasons.policy.length) ? ' · ' + a.reasons.policy.join('; ') : ''),
        h('div', { class: 'small' }, link(a.pr_url, 'Review the pull request'))),
      h('div', { class: 'row' }, h('button', { class: 'btn sm', onclick: e => act(e.target, a.bug_id, 'approve') }, 'Approve deploy'),
        h('button', { class: 'btn sm danger', onclick: e => act(e.target, a.bug_id, 'reject') }, 'Reject')))));
  }

  async function act(btn, id, action, extra) {
    const body = Object.assign({ action }, extra || {});
    if (action === 'approve') { const c = prompt(`This merges the fix into production.\nType DEPLOY ${id} to confirm.`); if (!c) return; body.confirm = c.trim(); }
    if (action === 'rollback') { const c = prompt(`Roll back the live fix for bug #${id}?\nType ROLLBACK ${id} to confirm.`); if (!c) return; body.confirm = c.trim(); body.reason = prompt('Reason (optional)') || ''; }
    if (action === 'ignore' && !confirm('Ignore this bug? The worker will not touch it.')) return;
    busy(btn, true);
    try { const j = await api(`/api/admin/bugfixer/bug/${id}/action`, body); toast(j.message || 'Done.'); render(location.hash.slice(1) === 'bfbug' ? id : undefined); }
    catch (e) { toast(e.message, true); busy(btn, false); }
  }

  function bugRows(bugs) {
    return bugs.map(b => h('tr', {}, h('td', {}, pr(b.priority), b.priority_manual ? h('div', { class: 'muted small' }, 'set by owner') : null),
      h('td', {}, bugLink(b.id, b.title), h('div', { class: 'muted small' }, b.kind + ' · ' + b.source + (b.last_error ? ' · ' + String(b.last_error).slice(0, 120) : ''))),
      h('td', {}, st(b.status), b.muted ? h('div', { class: 'muted small' }, 'ignored') : null),
      h('td', { class: 'num' }, fmt(b.count)), h('td', { class: 'num' }, fmt(b.count_1h)), h('td', { class: 'num' }, fmt(b.sessions_24h)), h('td', {}, rel(b.last_seen))));
  }

  const add = (k, fn) => { SECTIONS[k] = fn; };

  /* ---------- Live Bug Monitoring ---------- */
  add('bf', async () => {
    await state();
    const flt = { status: 'open' }; const body = h('div');
    const load = async () => {
      const qs = new URLSearchParams(Object.entries(flt).filter(([, v]) => v)).toString().replace('priority=', 'priority_=');
      const [b, ev] = await Promise.all([api('/api/admin/bugfixer/bugs?' + qs), api('/api/admin/bugfixer/events')]);
      body.replaceChildren(
        table(['Priority', 'Bug', 'Status', 'Total#', 'Last hour#', 'Visitors 24 h#', 'Last seen'], bugRows(b.bugs), 'No bugs match.'),
        h('h2', { style: 'margin-top:22px' }, 'Live error stream'),
        table(['Time', 'Bug', 'Source', 'What', 'Route'], ev.events.map(e => h('tr', {}, h('td', {}, rel(e.received_at)), h('td', {}, pr(e.priority), ' ', bugLink(e.bug_id)),
          h('td', {}, e.source + ' · ' + e.kind), h('td', {}, String(e.error_type || '') + ': ' + String(e.message || '').slice(0, 160)), h('td', { class: 'mono small' }, (e.route || '') + (e.status_code != null ? ' (' + e.status_code + ')' : '')))),
          'No errors received yet.'));
    };
    const sel = (key, opts) => h('select', { onchange: e => { flt[key] = e.target.value; load(); } }, opts.map(([v, l]) => h('option', { value: v, selected: flt[key] === v }, l)));
    const counts = {}; BF.counts.forEach(c => { counts[c.priority] = (counts[c.priority] || 0) + (['verified', 'ignored', 'rejected'].includes(c.status) ? 0 : c.n); });
    await load(); live(load, 15000);
    return sec('Live Bug Monitoring', 'Errors from visitors’ browsers, the backend, Sentry and site checks, grouped into bugs. Refreshes every 15 seconds.',
      topBar(), approvals(),
      h('div', { class: 'grid g4' }, ['critical', 'high', 'medium', 'low'].map(p => stat(p[0].toUpperCase() + p.slice(1) + ' open', fmt(counts[p] || 0), null, PR[p] || 'ok')),
        stat('Errors in 24 h', fmt(BF.last24.events), `${fmt(BF.last24.bugs)} bugs`)),
      h('div', { class: 'row bf-filters' }, sel('status', [['open', 'Open'], ['', 'All'], ['awaiting_approval', 'Waiting for approval'], ['verified', 'Fixed'], ['needs_human', 'Needs a person'], ['failed', 'Failed'], ['ignored', 'Ignored']]),
        sel('priority', [['', 'Any priority'], ['critical', 'Critical'], ['high', 'High'], ['medium', 'Medium'], ['low', 'Low']]),
        sel('kind', [['', 'Any type'], ['frontend', 'Frontend'], ['backend', 'Backend'], ['api', 'API'], ['database', 'Database'], ['upload', 'Upload'], ['download', 'Download'], ['probe', 'Site check']])),
      body);
  });

  /* ---------- Bug Priority ---------- */
  add('bfpriority', async () => {
    await state();
    const b = await api('/api/admin/bugfixer/bugs?status=open&limit=200');
    const s = BF.settings;
    const minSel = h('select', { onchange: async e => { try { await api('/api/admin/bugfixer/settings', { min_priority: e.target.value }); toast('Saved.'); } catch (er) { toast(er.message, true); } } },
      ['critical', 'high', 'medium', 'low'].map(p => h('option', { value: p, selected: s.min_priority === p }, 'Repair automatically from ' + p + ' up')));
    return sec('Bug Priority', 'The worker always takes Critical first, then High, Medium and Low. Inside a level, bugs that hit more visitors go first. You can override a bug’s priority.',
      card(h('label', { class: 'f' }, 'Automatic repair threshold', minSel),
        h('p', { class: 'muted small' }, 'Critical: the site or a core download is down for many people. High: server errors on core routes, database errors, failed downloads. Medium: errors in one tool or page. Low: rare or network-only problems.')),
      table(['Order#', 'Priority', 'Bug', 'Why', 'Status', 'Change'], b.bugs.map((x, i) => h('tr', {}, h('td', { class: 'num' }, i + 1), h('td', {}, pr(x.priority), h('div', { class: 'muted small' }, 'score ' + x.priority_score)),
        h('td', {}, bugLink(x.id, x.title)), h('td', { class: 'small' }, x.priority_reason || ''), h('td', {}, st(x.status)),
        h('td', {}, h('select', { 'aria-label': 'Set priority', onchange: e => act(e.target, x.id, 'priority', { value: e.target.value }) },
          ['critical', 'high', 'medium', 'low'].map(p => h('option', { value: p, selected: x.priority === p }, p)))))), 'No open bugs.'));
  });

  /* ---------- AI Analysis ---------- */
  add('bfanalysis', async () => {
    const d = await api('/api/admin/bugfixer/list/analyses');
    return sec('AI Analysis', 'Claude reads the error reports and the real code and finds the root cause, the affected files and their dependencies.',
      ...(d.analyses.length ? d.analyses.map(a => card(h('div', { class: 'bf-head' }, bugLink(a.bug_id, a.title), tag(a.category || '?', a.category === 'code_bug' ? 'warn' : ''),
        tag(a.fixable ? 'fixable' : 'not fixable by code', a.fixable ? 'ok' : ''), tag('confidence ' + Math.round((a.confidence || 0) * 100) + '%'), tag('risk ' + (a.risk || '?'))),
        h('p', {}, a.summary || ''), h('h3', {}, 'Root cause'), pre(a.root_cause),
        h('h3', {}, 'Affected files'), h('ul', {}, (a.affected_files || []).map(f => h('li', {}, h('span', { class: 'mono' }, f.path + (f.lines ? ':' + f.lines : '')), ' — ', f.reason))),
        (a.dependencies || []).length ? h('p', { class: 'small' }, h('b', {}, 'Dependencies: '), a.dependencies.join(', ')) : null,
        a.fix_plan ? h('p', { class: 'small' }, h('b', {}, 'Fix plan: '), a.fix_plan) : null,
        h('p', { class: 'muted small' }, `${a.model} · ${t(a.created_at)} · ${fmt(a.tokens_in)} in / ${fmt(a.tokens_out)} out tokens`))) : [h('div', { class: 'empty card' }, 'No analyses yet.')]));
  });

  /* ---------- Automatic Code Repair ---------- */
  add('bfrepair', async () => {
    await state();
    const d = await api('/api/admin/bugfixer/list/repairs');
    const s = BF.settings;
    const sw = (key, label) => h('label', { class: 'switch' }, h('input', { type: 'checkbox', checked: !!s[key], onchange: async e => { try { await api('/api/admin/bugfixer/settings', { [key]: e.target.checked }); toast('Saved.'); } catch (er) { toast(er.message, true); e.target.checked = !e.target.checked; } } }), label);
    return sec('Automatic Code Repair', 'Claude writes the smallest patch. It is checked against the safety policy and committed to its own autofix/ branch with a pull request; the main branch is never written directly.',
      card(h('div', { class: 'row' }, sw('paused', 'Pause everything'), sw('auto_analyze', 'Analyse new bugs'), sw('auto_repair', 'Write patches'))),
      ...(d.repairs.length ? d.repairs.map(r => card(h('div', { class: 'bf-head' }, bugLink(r.bug_id, r.title), st(r.status), tag('attempt ' + r.attempt), tag('risk ' + (r.risk || '?'), r.risk === 'high' ? 'bad' : r.risk === 'low' ? 'ok' : 'warn'),
        r.needs_approval ? tag('needs approval', 'warn') : null),
        h('p', {}, r.summary || ''), h('p', { class: 'small' }, link(r.pr_url, r.pr_number ? 'Pull request #' + r.pr_number : null), r.branch ? h('span', { class: 'mono muted' }, '  ' + r.branch) : null,
          r.lines_changed != null ? h('span', { class: 'muted' }, ` · ${r.lines_changed} lines in ${(r.files || []).length} file(s)`) : null),
        (r.policy && r.policy.reasons && r.policy.reasons.length) ? h('div', { class: 'note warn small' }, r.policy.reasons.join(' · ')) : null,
        r.diff ? h('details', {}, h('summary', {}, 'Show the patch'), pre(r.diff, 'diff')) : null)) : [h('div', { class: 'empty card' }, 'No patches yet.')]));
  });

  /* ---------- Test Results ---------- */
  add('bftests', async () => {
    const d = await api('/api/admin/bugfixer/list/tests');
    return sec('Test Results', 'Every patch is tested by GitHub Actions on a fresh machine: lint, unit, integration, regression and security. Any failed required check rejects the patch.',
      ...(d.tests.length ? d.tests.map(r => card(h('div', { class: 'bf-head' }, bugLink(r.bug_id), tag(r.status, r.status === 'passed' ? 'ok' : r.status === 'running' ? 'warn' : 'bad'),
        h('span', { class: 'mono small muted' }, (r.sha || '').slice(0, 7)), link(r.pr_url, 'pull request')),
        h('div', { class: 'bf-checks' }, (r.checks || []).map(c => h('a', { class: 'bf-check', href: c.url || '#', target: '_blank', rel: 'noopener' },
          h('span', { class: 'dot ' + (c.conclusion === 'success' ? 'ok' : c.status !== 'completed' ? 'warn' : 'bad') }), c.name, c.required ? '' : ' (info)'))),
        r.failure_log ? h('details', {}, h('summary', {}, 'Failure log'), pre(r.failure_log)) : null,
        h('p', { class: 'muted small' }, 'Started ' + t(r.started_at) + (r.finished_at ? ' · finished ' + t(r.finished_at) : '')))) : [h('div', { class: 'empty card' }, 'No test runs yet.')]));
  });

  /* ---------- Deployment Status ---------- */
  add('bfdeploy', async () => {
    await state();
    const d = await api('/api/admin/bugfixer/list/deployments');
    const s = BF.settings;
    const mode = h('select', { onchange: async e => { try { await api('/api/admin/bugfixer/settings', { auto_deploy: e.target.value }); toast('Saved.'); } catch (er) { toast(er.message, true); } } },
      [['approval', 'Ask me before every production deploy'], ['low_risk', 'Deploy low-risk fixes automatically'], ['auto', 'Deploy automatically unless high-risk']].map(([v, l]) => h('option', { value: v, selected: s.auto_deploy === v }, l)));
    return sec('Deployment Status', 'Staging is the pull request’s Vercel preview. Production is the main branch; after a merge the live site is checked several times and watched for the bug coming back. High-risk and destructive changes always need your approval.',
      topBar(), card(h('label', { class: 'f' }, 'Production deploy permission', mode)), approvals(),
      table(['When', 'Bug', 'Where', 'Status', 'Commit', 'Health', 'Approved by'], d.deployments.map(x => h('tr', {}, h('td', {}, t(x.started_at)), h('td', {}, bugLink(x.bug_id)),
        h('td', {}, tag(x.environment, x.environment === 'production' ? 'warn' : '')), h('td', {}, tag(x.status, x.status === 'healthy' ? 'ok' : ['unhealthy', 'failed'].includes(x.status) ? 'bad' : 'warn')),
        h('td', { class: 'mono small' }, (x.sha || '').slice(0, 7), x.url ? h('div', {}, link(x.url, 'open')) : null),
        h('td', { class: 'small' }, healthText(x.health)), h('td', { class: 'small' }, x.approved_by || '–'))), 'No deployments yet.'));
  });
  function healthText(hl) {
    if (!hl) return '–';
    if (Array.isArray(hl)) return hl.map(r => `${r.path} ${r.status}`).join(', ');
    if (hl.rounds) return hl.rounds.length + ' check round(s); last: ' + (hl.rounds[hl.rounds.length - 1].site || []).map(r => `${r.path} ${r.status}`).join(', ');
    return JSON.stringify(hl).slice(0, 160);
  }

  /* ---------- Rollback History ---------- */
  add('bfrollback', async () => {
    const d = await api('/api/admin/bugfixer/list/rollbacks');
    return sec('Rollback History', 'A fix that makes production unhealthy is undone automatically through a revert pull request. You can also roll back a live fix from its bug page.',
      table(['When', 'Bug', 'Reason', 'By', 'Status', 'Commits'], d.rollbacks.map(r => h('tr', {}, h('td', {}, t(r.created_at)), h('td', {}, bugLink(r.bug_id, r.title)), h('td', { class: 'small' }, r.reason || ''),
        h('td', { class: 'small' }, r.triggered_by), h('td', {}, tag(r.status, r.status === 'done' ? 'ok' : r.status === 'needs_human' ? 'bad' : 'warn')),
        h('td', { class: 'mono small' }, (r.from_sha || '').slice(0, 7) + ' → ' + (r.to_sha || '').slice(0, 7), r.revert_pr ? h('div', {}, 'PR #' + r.revert_pr) : null))), 'No rollbacks. Good.'));
  });

  /* ---------- Repair Logs ---------- */
  add('bflogs', async () => {
    const body = h('div');
    const load = async () => { const d = await api('/api/admin/bugfixer/list/log?limit=250'); body.replaceChildren(logTable(d.log)); };
    await load(); live(load, 10000);
    return sec('Repair Logs', 'Everything the bug fixer and the owners did, newest first. Refreshes every 10 seconds.', body);
  });
  function logTable(rows) {
    return table(['Time', 'Bug', 'Step', 'By', 'Message'], rows.map(l => h('tr', { class: 'lvl-' + l.level }, h('td', { class: 'small' }, t(l.at)), h('td', {}, l.bug_id ? bugLink(l.bug_id) : '–'),
      h('td', { class: 'small' }, l.step || ''), h('td', { class: 'small' }, l.actor), h('td', { class: 'small' }, tag(l.level, l.level === 'error' ? 'bad' : l.level === 'warn' ? 'warn' : ''), ' ', l.message))), 'Nothing logged yet.');
  }

  /* ---------- one bug ---------- */
  add('bfbug', async id => {
    id = id || Number(sessionStorage.getItem('bfbug') || 0);
    if (!id) { go('bf'); return h('div'); }
    try { sessionStorage.setItem('bfbug', id); } catch (e) { /* fine */ }
    const d = await api('/api/admin/bugfixer/bug/' + id), b = d.bug, a = d.analyses[0], r = d.repairs[0];
    const btn = (label, action, cls) => h('button', { class: 'btn sm ' + (cls || 'ghost'), onclick: e => act(e.target, id, action) }, label);
    const actions = [];
    if (b.status === 'awaiting_approval') actions.push(btn('Approve deploy', 'approve', ''), btn('Reject', 'reject', 'danger'));
    if (['new', 'regressed'].includes(b.status)) actions.push(btn('Repair now', 'requeue', ''));
    if (['failed', 'needs_human', 'tests_failed', 'staging_failed', 'rejected', 'rolled_back'].includes(b.status)) actions.push(btn('Try again', 'retry'));
    if (b.status === 'verified') actions.push(btn('Roll back this fix', 'rollback', 'danger'));
    actions.push(b.muted ? btn('Stop ignoring', 'unignore') : btn('Ignore', 'ignore'));
    const ev = d.events[0];
    return sec('Bug #' + b.id, null, h('p', { class: 'lead' }, h('a', { href: '#bf' }, '← Live Bug Monitoring')),
      card(h('h2', {}, b.title), h('div', { class: 'bf-head' }, pr(b.priority), st(b.status), tag(b.kind), tag(b.source), tag(fmt(b.count) + ' errors'), tag(fmt(b.sessions_24h) + ' visitors / 24 h')),
        h('p', { class: 'small muted' }, 'First seen ' + t(b.first_seen) + ' · last seen ' + t(b.last_seen) + ' · ' + (b.priority_reason || '')),
        b.last_error ? h('div', { class: 'note warn small' }, b.last_error) : null, h('div', { class: 'row', style: 'margin-top:10px' }, actions)),
      h('div', { class: 'grid g2' },
        card(h('h3', {}, 'Latest error'), ev ? [h('p', {}, h('b', {}, ev.error_type + ': '), ev.message), h('p', { class: 'mono small' }, (ev.route || ev.url || '') + (ev.status_code != null ? ' · ' + ev.status_code : '')),
          ev.stack ? pre(ev.stack) : null] : h('p', { class: 'muted' }, 'No events.')),
        card(h('h3', {}, 'AI analysis'), a ? [h('p', {}, a.summary), pre(a.root_cause), h('p', { class: 'small' }, (a.affected_files || []).map(f => f.path).join(', '))] : h('p', { class: 'muted' }, 'Not analysed yet.'))),
      r ? card(h('h3', {}, 'Repair'), h('div', { class: 'bf-head' }, st(r.status), tag('risk ' + r.risk), link(r.pr_url, 'pull request #' + r.pr_number)), h('p', {}, r.summary || ''),
        r.diff ? h('details', {}, h('summary', {}, 'Show the patch'), pre(r.diff, 'diff')) : null,
        d.tests[0] ? h('div', { class: 'bf-checks' }, (d.tests[0].checks || []).map(c => h('span', { class: 'bf-check' }, h('span', { class: 'dot ' + (c.conclusion === 'success' ? 'ok' : c.status !== 'completed' ? 'warn' : 'bad') }), c.name))) : null,
        d.deployments.length ? h('p', { class: 'small' }, d.deployments.map(x => `${x.environment}: ${x.status}`).join(' · ')) : null) : null,
      card(h('h3', {}, 'History'), logTable(d.log)));
  });

  // menu entries
  const side = document.getElementById('side');
  if (side) {
    const grp = h('p', { class: 'grp' }, 'AI Bug Fixer');
    const links = [['bf', 'Live Bug Monitoring'], ['bfpriority', 'Bug Priority'], ['bfanalysis', 'AI Analysis'], ['bfrepair', 'Automatic Code Repair'], ['bftests', 'Test Results'],
      ['bfdeploy', 'Deployment Status'], ['bfrollback', 'Rollback History'], ['bflogs', 'Repair Logs']].map(([k, l]) => h('a', { href: '#' + k, 'data-s': k }, l));
    const sec2 = [...side.querySelectorAll('.grp')].find(g => g.textContent.trim() === 'Security');
    if (sec2) side.insertBefore(grp, sec2); else side.append(grp);
    links.forEach(a => side.insertBefore(a, sec2 || null));
  }
})();
