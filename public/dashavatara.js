/* ReelKit DashavataraBackground
 * Layers: AuraLayer, ChakraLayer, ParticleLayer, AvatarController. Animation is CSS-driven (transform/opacity only);
 * JS only swaps the active avatar on a timer. Artwork is independent of the code:
 * drop WebP files into /assets/dashavatara/ and set useImages:true. Until then a gold line emblem is drawn.
 * Override any option before this script runs: window.REELKIT_DASHAVATARA_CONFIG = { duration: 7000, ... } */
(function () {
  const A = '/assets/dashavatara/';
  const DEFAULTS = {
    useImages: false,
    duration: 6000,          // ms each avatar stays (can be set per avatar)
    transition: 1600,        // ms crossfade
    opacity: 0.4, mobileOpacity: 0.26, scale: 1,
    aura: 0.3,               // golden aura intensity 0..1
    chakraOpacity: 0.05, mobileChakraOpacity: 0.03,
    position: { x: '72%', y: '46%', size: 'min(58vw,720px)' },
    mobile: { x: '70%', y: '34%', size: '96vw' },
    particles: { desktop: 26, mobile: 10 },
    avatars: ['Matsya', 'Kurma', 'Varaha', 'Narasimha', 'Vamana', 'Parashurama', 'Rama', 'Krishna', 'Buddha', 'Kalki']
      .map(n => ({ name: n, image: A + n.toLowerCase() + '.webp', duration: 6000 }))
  };
  const cfg = Object.assign({}, DEFAULTS, window.REELKIT_DASHAVATARA_CONFIG || {});
  const mk = (t, c) => { const e = document.createElement(t); e.className = c; return e; };
  const rays = (n, a, b, c = 100) => Array.from({ length: n }, (_, i) => {
    const t = i / n * 6.2832, x = Math.cos(t), y = Math.sin(t), f = v => v.toFixed(1);
    return `M${f(c + a * x)} ${f(c + a * y)}L${f(c + b * x)} ${f(c + b * y)}`;
  }).join('');
  const WAVE = '<path d="M14 168q16-14 32 0t32 0t32 0t32 0t32 0"/>';
  const EMB = {
    Matsya: '<path d="M28 98Q78 44 138 98Q78 152 28 98Z"/><path d="M138 98L176 66L166 98L176 130Z"/><circle cx="56" cy="92" r="3.5"/><path d="M84 70Q100 98 84 126M104 66Q120 98 104 130"/>' + WAVE,
    Kurma: '<path d="M40 128Q100 28 160 128Z"/><path d="M66 106H134M100 62V128M80 84L68 128M120 84L132 128"/><path d="M160 118Q186 112 186 130Q176 138 160 132"/><path d="M62 128V148H80V128M120 128V148H138V128"/>' + WAVE,
    Varaha: '<circle cx="100" cy="64" r="30"/><path d="M70 64Q100 52 130 64M70 64Q100 76 130 64M100 34V94"/><path d="M44 168Q50 118 100 112Q150 118 156 168"/><path d="M82 168Q70 150 78 138M118 168Q130 150 122 138"/>',
    Narasimha: `<circle cx="100" cy="100" r="38"/><path d="${rays(20, 48, 78)}"/><path d="M80 92Q88 86 94 94M106 94Q112 86 120 92M100 98V112M92 118Q100 124 108 118"/>`,
    Vamana: '<path d="M56 86Q100 28 144 86Z"/><path d="M100 86V150"/><path d="M30 176H76V156H112V136H148V116H176"/>',
    Parashurama: '<path d="M70 176L134 30"/><path d="M126 44Q176 52 164 104Q148 84 124 82Z"/><path d="M118 62L136 70"/>',
    Rama: '<path d="M64 24Q164 100 64 176"/><path d="M64 24V176"/><path d="M30 100H172M172 100L158 90M172 100L158 110M40 100L30 92M40 100L30 108"/>',
    Krishna: '<path d="M34 164L166 46L172 52L38 170Z"/><circle cx="76" cy="126" r="2.6"/><circle cx="92" cy="112" r="2.6"/><circle cx="108" cy="98" r="2.6"/><circle cx="124" cy="84" r="2.6"/><path d="M150 170Q120 130 158 104Q186 134 150 170Z"/><circle cx="154" cy="136" r="7"/>',
    Buddha: '<circle cx="100" cy="74" r="42"/><circle cx="100" cy="74" r="16"/><path d="M72 150Q100 96 128 150"/><path d="M52 158Q100 134 148 158Q100 176 52 158Z"/><path d="M40 178Q60 162 78 178M82 180Q100 158 118 180M122 178Q140 162 160 178"/>',
    Kalki: '<path d="M100 18V128M86 128H114M100 128V148M100 18L94 34M100 18L106 34"/><path d="M56 176Q50 140 70 120L66 96L82 108Q104 100 122 118Q140 146 124 176"/><circle cx="104" cy="126" r="2.6"/><path d="M30 64Q100 -6 170 64"/>'
  };

  class AuraLayer { constructor(root) { this.el = mk('div', 'dsh-aura'); root.append(this.el); } }

  class ChakraLayer {
    constructor(root) {
      const el = this.el = mk('div', 'dsh-ch');
      el.innerHTML = '<svg viewBox="0 0 200 200"><circle cx="100" cy="100" r="98"/><circle cx="100" cy="100" r="94"/><circle cx="100" cy="100" r="76"/><circle cx="100" cy="100" r="60"/><circle cx="100" cy="100" r="30"/><circle cx="100" cy="100" r="10"/>' +
        `<path d="${rays(108, 76, 94)}"/><path d="${rays(24, 30, 76)}"/><path d="${rays(12, 10, 30)}"/></svg>`;
      root.append(el);
    }
  }

  class ParticleLayer {
    constructor(root) { this.root = root; this.list = []; }
    set(n) {
      this.list.forEach(p => p.remove()); this.list = [];
      for (let i = 0; i < n; i++) {
        const p = mk('span', 'dsh-p'), r = Math.random, s = p.style;
        s.setProperty('--x', (r() * 100).toFixed(1) + '%'); s.setProperty('--y', (20 + r() * 80).toFixed(1) + '%');
        s.setProperty('--s', (1 + r() * 2).toFixed(1) + 'px'); s.setProperty('--o', (0.25 + r() * 0.4).toFixed(2));
        s.setProperty('--d', (14 + r() * 16).toFixed(1) + 's'); s.setProperty('--dl', (-r() * 20).toFixed(1) + 's');
        s.setProperty('--dx', ((r() - 0.5) * 60).toFixed(0) + 'px');
        this.root.append(p); this.list.push(p);
      }
    }
  }

  class AvatarController {
    constructor(root, c) {
      this.cfg = c; this.i = -1; this.t = null;
      this.items = c.avatars.map(a => {
        const el = mk('div', 'dsh-av'); el.dataset.name = a.name; el.setAttribute('aria-hidden', 'true');
        if (a.opacity != null) el.style.setProperty('--op', a.opacity);
        if (a.scale != null) el.style.setProperty('--sc', a.scale);
        root.append(el); return { a, el, built: false };
      });
    }
    build(k) {  // lazy: content is created (and image requested) only just before it is needed
      const it = this.items[k]; if (it.built) return; it.built = true;
      const fl = mk('div', 'dsh-fl'); it.el.append(fl);
      const emblem = () => { fl.innerHTML = '<svg viewBox="0 0 200 200">' + (EMB[it.a.name] || EMB.Matsya) + '</svg>'; };
      if (this.cfg.useImages && it.a.image) {
        const im = new Image(); im.alt = ''; im.loading = 'lazy'; im.decoding = 'async'; im.onerror = emblem; im.src = it.a.image; fl.append(im);
      } else emblem();
    }
    show(k) {
      const cur = this.items[this.i]; if (cur) cur.el.classList.remove('on');
      this.i = k; this.build(k); this.items[k].el.classList.add('on'); this.build((k + 1) % this.items.length);
    }
    start() {
      this.stop();
      const tick = () => { this.show((this.i + 1) % this.items.length); this.t = setTimeout(tick, this.items[this.i].a.duration || this.cfg.duration); };
      tick();
    }
    stop() { clearTimeout(this.t); this.t = null; }
  }

  class DashavataraBackground {
    constructor(root, c) {
      this.root = root; this.cfg = c;
      this.mq = matchMedia('(max-width:640px)'); this.rm = matchMedia('(prefers-reduced-motion: reduce)');
      root.append(mk('div', 'dsh-wash'));
      this.aura = new AuraLayer(root); this.chakra = new ChakraLayer(root); this.particles = new ParticleLayer(root);
      this.avatars = new AvatarController(root, c);
      this.vars(); this.sync();
      const again = () => { this.vars(); this.sync(); };
      this.mq.addEventListener && this.mq.addEventListener('change', again);
      this.rm.addEventListener && this.rm.addEventListener('change', again);
      document.addEventListener('visibilitychange', () => this.sync());
    }
    vars() {
      const c = this.cfg, m = this.mq.matches, p = m ? Object.assign({}, c.position, c.mobile) : c.position, s = this.root.style;
      s.setProperty('--ax', p.x); s.setProperty('--ay', p.y); s.setProperty('--as', p.size);
      s.setProperty('--op', m ? c.mobileOpacity : c.opacity); s.setProperty('--sc', c.scale);
      s.setProperty('--tr', c.transition + 'ms'); s.setProperty('--aura', c.aura);
      s.setProperty('--cho', m ? c.mobileChakraOpacity : c.chakraOpacity);
    }
    sync() {
      const calm = this.rm.matches, c = this.cfg;
      this.particles.set(calm ? 0 : this.mq.matches ? c.particles.mobile : c.particles.desktop);
      if (calm) { this.avatars.stop(); if (this.avatars.i < 0) this.avatars.show(0); }   // one static, subtle avatar
      else if (document.hidden) this.avatars.stop();
      else if (this.avatars.t === null) this.avatars.start();
    }
  }

  const root = document.getElementById('dsh');
  if (root) window.ReelKitDashavatara = new DashavataraBackground(root, cfg);
})();
