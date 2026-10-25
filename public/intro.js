/* WaveRise opening sequence (about 4 s). Timeline: 0-0.5 dark + first light | 0.5-1.2 first wave | 1.2-1.8 wave rise
 * 1.8-2.3 W forms | 2.3-2.8 brand reveal | 2.8-3.5 loading | 3.5-4.3 ripple into the homepage.
 * Runs once per session (add ?intro to replay). Skip button, reduced-motion and slow-device fallbacks included. */
(function () {
  const root = document.getElementById('wr-intro');
  if (!root) return;
  const $ = s => root.querySelector(s), html = document.documentElement;
  if (html.classList.contains('wr-seen')) { root.remove(); return; }   // already played this session: do nothing at all
  const cv = $('canvas'), ctx = cv.getContext('2d'), stage = $('.wr-stage');
  const name = $('.wr-name'), tag = $('.wr-tag'), load = $('.wr-load'), fill = $('.wr-bar i'), bar = $('.wr-bar'), sweep = $('.wr-sweep'), skipBtn = $('.wr-skip');
  const guard = [...document.querySelectorAll('header,main,footer')];
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x)), ease = x => x * x * (3 - 2 * x), lerp = (a, b, t) => a + (b - a) * t;

  html.classList.add('wr-lock'); document.body.classList.add('wr-pre'); guard.forEach(e => { e.inert = true; });
  let raf = 0, done = false, skip = false, loaded = document.readyState === 'complete';
  if (!loaded) addEventListener('load', () => { loaded = true; });
  const ready = () => loaded || skip;
  function finish() {
    if (done) return; done = true; cancelAnimationFrame(raf);
    try { sessionStorage.setItem('wr_intro', '1'); } catch (e) { /* private mode */ }
    document.body.classList.remove('wr-pre'); html.classList.remove('wr-lock'); guard.forEach(e => { e.inert = false; }); root.remove();
  }
  skipBtn.onclick = () => { skip = true; };
  setTimeout(() => skipBtn.classList.add('on'), 800);

  // ---- geometry ----
  const G = {};
  let W, H, dpr, cx, cy, lw, lite = innerWidth < 700 || matchMedia('(pointer:coarse)').matches, ultra = false, L;
  function size() {
    W = innerWidth; H = innerHeight; dpr = ultra ? 0.7 : Math.min(devicePixelRatio || 1, lite ? 1 : 1.25);
    cv.width = W * dpr; cv.height = H * dpr; cx = W / 2; cy = H * 0.36; lw = Math.min(W * 0.52, H * 0.46, 360); L = ultra ? 2 : lite ? 3 : 5;
    stage.style.transformOrigin = '50% 36%';
    const lg = (x0, y0, x1, y1, st) => { const g = ctx.createLinearGradient(x0, y0, x1, y1); st.forEach(([o, c]) => g.addColorStop(o, c)); return g; };
    const rg = (x, y, r, st) => { const g = ctx.createRadialGradient(x, y, 0, x, y, r); st.forEach(([o, c]) => g.addColorStop(o, c)); return g; };
    G.sg = lg(0, 0, W, 0, [[0, 'rgb(47,139,255)'], [0.5, 'rgb(130,224,255)'], [1, 'rgb(255,214,150)']]);
    G.fg = lg(0, H * 0.3, 0, H, [[0, 'rgba(70,150,255,.1)'], [1, 'rgba(2,6,18,0)']]);
    G.fl = lg(0, H * 0.86, 0, H, [[0, 'rgba(2,5,14,0)'], [1, 'rgba(1,3,9,.85)']]);
    G.g0 = rg(cx, H * 1.05, H * 0.9, [[0, 'rgba(30,90,220,.3)'], [1, 'rgba(2,4,10,0)']]);
    G.eg = rg(cx, H * 0.9, W * 0.32, [[0, 'rgba(255,200,120,.22)'], [0.5, 'rgba(60,140,255,.12)'], [1, 'rgba(0,0,0,0)']]);
  }
  size(); addEventListener('resize', size);

  // ---- the W: one continuous ribbon drawn from the wave ----
  const NS = 'http://www.w3.org/2000/svg', D = 'M16 24C40 24 44 100 62 100C84 100 84 44 100 44C116 44 116 100 138 100C158 100 164 40 184 12';
  const P = new Path2D(D), pe = document.createElementNS(NS, 'path'); pe.setAttribute('d', D); const LEN = pe.getTotalLength();
  function drawLogo(pg, T) {
    const s = lw / 200, pulse = 1 + 0.012 * Math.sin(T * 2);
    ctx.save(); ctx.translate(cx, cy); ctx.scale(s * pulse, s * pulse); ctx.translate(-100, -56);
    ctx.setLineDash([LEN, LEN]); ctx.lineDashOffset = LEN * (1 - pg); ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    const g = ctx.createLinearGradient(16, 24, 184, 12);
    g.addColorStop(0, '#1a63ff'); g.addColorStop(0.38, '#43b8ff'); g.addColorStop(0.7, '#cfe9ff'); g.addColorStop(1, '#ffd7a0');
    ctx.save(); ctx.translate(5, 8); ctx.lineWidth = 15; ctx.strokeStyle = 'rgba(6,20,70,.55)'; ctx.stroke(P); ctx.restore();   // depth
    ctx.save(); ctx.lineWidth = 26; ctx.strokeStyle = 'rgba(70,160,255,.18)'; if (!lite) { ctx.shadowColor = 'rgba(70,160,255,.9)'; ctx.shadowBlur = 30; } ctx.stroke(P); ctx.restore();   // bloom
    ctx.lineWidth = 15; ctx.strokeStyle = g; ctx.stroke(P);                                                                   // glass body
    ctx.save(); ctx.translate(-2.4, -3.4); ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(255,255,255,.6)'; ctx.stroke(P); ctx.restore();   // specular edge
    ctx.save(); ctx.translate(3, 4); ctx.lineWidth = 1.6; ctx.strokeStyle = 'rgba(255,214,150,.55)'; ctx.stroke(P); ctx.restore();     // warm rim
    ctx.restore();
  }

  // ---- liquid glass waves ----
  function waveY(i, x, T, bump) {
    const dir = i % 2 ? 1 : -1, k = (1.25 + i * 0.5) * (W < 700 ? 0.6 : 1) * Math.PI * 2 / W, sp = 0.9 + i * 0.25, AH = Math.min(H, W * 0.75);
    const grow = ease(clamp((T - 0.5) / 1.3)), settle = ease(clamp((T - 1.8) / 0.9));
    const base = H * (1.08 - 0.46 * grow + 0.2 * settle) - i * H * 0.018 * (1 + settle);
    const amp = AH * (0.012 + 0.075 * grow - 0.04 * settle) * (1 - i * 0.12);
    return base + amp * Math.sin(x * k + T * sp * dir * 2) + amp * 0.45 * Math.sin(x * k * 2.1 - T * sp * dir * 1.4 + i) + bump(x);
  }
  const drops = [], dust = Array.from({ length: 44 }, () => ({ x: Math.random(), y: 0.35 + Math.random() * 0.65, v: 0.01 + Math.random() * 0.03, r: 0.6 + Math.random() * 1.4, ph: Math.random() * 6 }));
  function spawn(T) {
    const x = Math.random() * W, k = H / 800;
    drops.push({ x, y: waveY(L - 1, x, T, () => 0), vx: (Math.random() - 0.5) * 90 * k, vy: -(160 + Math.random() * 260) * k, r: (1.2 + Math.random() * 2.4) * (lite ? 1.2 : 1), a: 1 });
  }

  function draw(T, dt, rp, R) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 1;
    ctx.fillStyle = `rgba(2,4,10,${T < 0.05 ? 1 : 0.42})`; ctx.fillRect(0, 0, W, H);          // trails = motion blur
    ctx.globalAlpha = ease(clamp((T - 0.3) / 1.5)); ctx.fillStyle = G.g0; ctx.fillRect(0, H * 0.2, W, H * 0.8); ctx.globalAlpha = 1;
    const li = ease(clamp(T / 0.5)) * (1 - ease(clamp((T - 0.9) / 0.6)) * 0.6), lr = 26 + 40 * ease(clamp((T - 0.2) / 1));   // the first tiny light
    const lg = ctx.createRadialGradient(cx, H * 0.97, 0, cx, H * 0.97, lr); lg.addColorStop(0, `rgba(190,225,255,${0.75 * li})`); lg.addColorStop(1, 'rgba(60,140,255,0)'); ctx.fillStyle = lg; ctx.fillRect(cx - lr, H * 0.97 - lr, lr * 2, lr * 2);
    const bump = rp > 0 && rp < 1 ? x => Math.exp(-(((Math.abs(x - cx) - R) / 70) ** 2)) * 26 * (1 - rp) * Math.sin(x * 0.05) : () => 0;   // waves answer the ripple
    const step = lite ? 16 : 9;
    for (let i = 0; i < L; i++) {
      const a = i === 0 ? ease(clamp((T - 0.5) / 0.5)) : ease(clamp((T - (1.0 + i * 0.12)) / 0.6)); if (a < 0.01) continue;
      const p = new Path2D(); for (let x = 0; x <= W + step; x += step) { const y = waveY(i, x, T, bump); x ? p.lineTo(x, y) : p.moveTo(x, y); }
      if (!ultra && (!lite || i >= L - 2)) { const fp = new Path2D(p); fp.lineTo(W, H); fp.lineTo(0, H); fp.closePath(); ctx.globalAlpha = a; ctx.fillStyle = G.fg; ctx.fill(fp); }
      ctx.save(); ctx.globalAlpha = a; ctx.lineWidth = i === 0 && T < 1.2 ? 1.4 : 2.2 - i * 0.18; ctx.strokeStyle = G.sg;
      if (!lite && i === L - 1) { ctx.shadowColor = 'rgba(60,150,255,.8)'; ctx.shadowBlur = 14; }
      ctx.stroke(p); ctx.shadowBlur = 0; ctx.translate(0, 9); ctx.lineWidth = 1; ctx.globalAlpha = a * 0.28; ctx.strokeStyle = '#d2ebff'; ctx.stroke(p);   // refraction edge
      ctx.translate(0, 11); ctx.globalAlpha = a * 0.16; ctx.strokeStyle = '#ffd696'; ctx.stroke(p); ctx.restore();
    }
    ctx.globalAlpha = 1; ctx.fillStyle = G.fl; ctx.fillRect(0, H * 0.86, W, H * 0.14);   // dark water floor
    ctx.globalAlpha = ease(clamp((T - 2.0) / 0.8)); ctx.fillStyle = G.eg; ctx.fillRect(0, H * 0.78, W, H * 0.22); ctx.globalAlpha = 1;
    if (T > 1.15 && T < 2.15 && drops.length < (lite ? 7 : 16) && Math.random() < dt * (lite ? 8 : 18)) spawn(T);
    for (let i = drops.length - 1; i >= 0; i--) {
      const d = drops[i]; d.vy += 700 * (H / 800) * dt; d.x += d.vx * dt; d.y += d.vy * dt; d.a -= dt * 0.7; if (d.a <= 0 || d.y > H) { drops.splice(i, 1); continue; }
      ctx.globalAlpha = d.a; ctx.fillStyle = 'rgba(190,228,255,.85)'; ctx.beginPath(); ctx.arc(d.x, d.y, d.r * 1.5, 0, 6.3); ctx.fill();
      ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(d.x - d.r * 0.4, d.y - d.r * 0.4, d.r * 0.5, 0, 6.3); ctx.fill();
    }
    const da = ease(clamp((T - 1.0) / 0.8)) * 0.55, n = lite ? 16 : dust.length;
    for (let i = 0; i < n; i++) { const q = dust[i]; q.y -= q.v * dt; if (q.y < 0.2) q.y = 1; ctx.globalAlpha = da * (0.5 + 0.5 * Math.sin(q.ph + T)); ctx.fillStyle = i % 3 ? '#aad7ff' : '#ffdcaa'; ctx.beginPath(); ctx.arc(q.x * W, q.y * H, q.r, 0, 6.3); ctx.fill(); }
    ctx.globalAlpha = 1;
    const pg = ease(clamp((T - 1.8) / 0.5)); if (pg > 0.001) drawLogo(pg, T);
    if (rp > 0 && rp < 1) {   // ripple rings leaving the logo
      ctx.save(); ctx.lineWidth = 2.5 + (1 - rp) * 6; ctx.strokeStyle = `rgba(190,228,255,${0.9 * (1 - rp * 0.85)})`; if (!lite) { ctx.shadowColor = 'rgba(80,170,255,.9)'; ctx.shadowBlur = 24; }
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, 6.3); ctx.stroke(); ctx.shadowBlur = 0; ctx.lineWidth = 1.6; ctx.strokeStyle = `rgba(255,214,150,${0.55 * (1 - rp)})`;
      ctx.beginPath(); ctx.arc(cx, cy, R * 0.86, 0, 6.3); ctx.stroke(); ctx.restore();
    }
  }

  // ---- reduced motion: one still frame, short fade ----
  if (reduce) {
    root.classList.add('rm'); draw(2.6, 0.016, 0, 0); [name, tag].forEach(e => e.classList.add('on'));
    setTimeout(() => { root.classList.add('fade'); setTimeout(finish, 450); }, 1000); return;
  }

  // ---- timeline ----
  let last = performance.now(), el = 0, hold = 0, frames = 0, sum = 0;
  const flag = {};
  const once = (k, c, fn) => { if (c && !flag[k]) { flag[k] = 1; fn(); } };
  function frame(now) {
    const dt = Math.min(0.1, (now - last) / 1000); last = now; el += dt;
    if (++frames <= 36) { sum += dt; if (frames === 12 && sum / 12 > 0.022 && !lite) { lite = true; size(); sum = 0; } if (frames === 36 && lite && sum / 24 > 0.04) { ultra = true; size(); } }   // slow device: lighter, then lightest scene   // slow device: lighter scene
    let T = el - hold;
    if (skip && T < 3.5) { el += 3.5 - T; T = 3.5; }
    if (T >= 3.45 && !ready() && hold < 4) { hold += dt; T = el - hold; }                              // wait for the page to really finish loading
    once('name', T >= 2.3, () => name.classList.add('on')); once('tag', T >= 2.45, () => tag.classList.add('on')); once('load', T >= 2.8, () => load.classList.add('on'));
    const target = clamp((T - 2.8) / 0.65), prog = ready() ? target : Math.min(target, 0.92);
    fill.style.transform = `scaleX(${prog})`; bar.setAttribute('aria-valuenow', Math.round(prog * 100));
    once('out', T >= 3.45, () => { [name, tag, load].forEach(e => e.classList.add('gone')); skipBtn.classList.remove('on'); });
    once('sweep', T >= 3.55, () => sweep.classList.add('go')); once('enter', T >= 3.5, () => document.body.classList.remove('wr-pre'));
    const rp = clamp((T - 3.5) / 0.8), R = ease(rp) * (Math.hypot(Math.max(cx, W - cx), Math.max(cy, H - cy)) + 90);
    const cam = 1 + 0.06 * ease(clamp(T / 2.8)) + 0.5 * ease(rp);
    stage.style.transform = `scale(${cam})`; root.style.setProperty('--r', rp > 0 ? R * cam + 'px' : '-300px');
    draw(T, dt, rp, R);
    if (rp >= 1) return finish();
    raf = requestAnimationFrame(frame);
  }
  root.style.setProperty('--cx', '50%'); root.style.setProperty('--cy', '36%');
  raf = requestAnimationFrame(frame);
})();
