"""Public SEO for WaveRise: every page is rendered on the server with its own title, description, canonical URL,
Open Graph / Twitter tags and structured data. Admin edits (saved in the store) are applied here.
The site never depends on the store: if it is missing or down, the built-in SEO is used."""
import datetime as _dt, html as _html, json, os, re, time
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from wr_store import STORE, StoreError

ROOT = Path(__file__).parent
TEMPLATE_PATH = ROOT / "templates" / "index.html"
INFO_PAGES = ("about", "contact", "faq", "privacy", "terms", "dmca")
CACHE = {"Cache-Control": "public, max-age=0, s-maxage=300, stale-while-revalidate=86400"}
GA_RX = re.compile(r"^G-[A-Z0-9]{4,16}$")
VERIFY_RX = re.compile(r"^[A-Za-z0-9_\-]{10,120}$")
esc = lambda v: _html.escape(str(v), quote=True)


def _read_template():
    for p in (TEMPLATE_PATH, ROOT / "public" / "index.html"):     # public/ copy only for old local setups
        try:
            return p.read_text("utf-8")
        except OSError:
            continue
    print("[waverise] templates/index.html is missing from the deployment", flush=True)
    return ""


TEMPLATE = _read_template()
try:
    SEO = json.loads(re.search(r'<script type="application/json" id="wr-seo">(.*?)</script>', TEMPLATE, re.S).group(1).replace("<\\/", "</"))
except Exception:
    SEO = {}
try:
    _mt = os.path.getmtime(TEMPLATE_PATH)
    BUILT = time.strftime("%Y-%m-%d", time.gmtime(_mt)) if _mt > 1.6e9 else None
except OSError:
    BUILT = None


def faq_items():
    m = re.search(r'data-page="faq"[^>]*>(.*?)</article>', TEMPLATE, re.S)
    out = []
    for q, a in re.findall(r"<details><summary>(.*?)</summary>(.*?)</details>", m.group(1) if m else "", re.S):
        strip = lambda s: _html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
        out.append((strip(q), strip(a)))
    return out


# ---------------- settings and overrides (never break the public site) ----------------
DEFAULT_SETTINGS = {"visibility": True, "gsc_verification": "", "ga_id": "", "gsc_site": "", "ga_property": "", "robots_extra": "", "default_og": ""}


def _safe(loader, default):
    try:
        return loader()
    except StoreError as e:
        print("[waverise] store read failed, using defaults:", e, flush=True)
        return default
    except Exception as e:      # noqa: BLE001 - a broken value must never take the site down
        print("[waverise] store value unreadable:", e, flush=True)
        return default


def settings():
    if not STORE.ok:
        return dict(DEFAULT_SETTINGS)
    return {**DEFAULT_SETTINGS, **(_safe(lambda: STORE.cached("settings", lambda: STORE.get_json("settings", {}) or {}), {}) or {})}


def overrides(name):
    if not STORE.ok:
        return {}
    return _safe(lambda: STORE.cached(name, lambda: STORE.hall(name)), {})


def site_base(request: Request = None):
    env = (os.getenv("SITE_URL") or "").strip().rstrip("/")
    if env:
        return env
    if request is None:
        return ""
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    return f"{proto}://{host}" if host else ""


def path_of(key):
    return "/" if key == "video" else "/" + SEO[key]["s"]


def effective(key):
    """The SEO actually used for a page: built-in values with admin overrides on top."""
    e, ov, st = SEO.get(key, {}), overrides("meta").get(key, {}), settings()
    return {"t": (ov.get("t") or e.get("t") or "WaveRise").strip(), "d": (ov.get("d") or e.get("d") or "").strip(),
            "og": (ov.get("og") or st.get("default_og") or "").strip(), "noindex": bool(ov.get("noindex")) or not st.get("visibility", True),
            "page_noindex": bool(ov.get("noindex"))}


def auto_schema(key, base, eff):
    url = base + path_of(key)
    site = {"@type": "WebSite", "@id": base + "/#website", "name": "WaveRise", "url": base + "/", "inLanguage": ["en", "hi", "ta", "te"]}
    org = {"@type": "Organization", "@id": base + "/#org", "name": "WaveRise", "url": base + "/", "logo": base + "/icon-512.png"}
    e = SEO.get(key, {})
    if key == "faq":
        items = faq_items()
        if items:
            return {"@context": "https://schema.org", "@type": "FAQPage", "url": url, "mainEntity": [
                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in items]}
    if e.get("h"):
        app = {"@type": "WebApplication", "name": e["h"], "url": url, "description": eff["d"], "applicationCategory": "MultimediaApplication",
               "operatingSystem": "Any", "isPartOf": {"@id": base + "/#website"}, "publisher": {"@id": base + "/#org"},
               "offers": {"@type": "Offer", "price": "0", "priceCurrency": "INR"}}
        return {"@context": "https://schema.org", "@graph": [site, org, app] if key == "video" else [app]}
    return {"@context": "https://schema.org", "@type": "WebPage", "name": eff["t"], "url": url, "description": eff["d"], "isPartOf": {"@id": base + "/#website"}}


def schema_for(key, base, eff):
    sc = overrides("schema").get(key) or {}
    mode = sc.get("mode", "auto")
    if mode == "off":
        return None
    if mode == "custom" and sc.get("json"):
        try:
            return json.loads(sc["json"])
        except ValueError:
            pass
    return auto_schema(key, base, eff)


def render(key, request: Request):
    page = TEMPLATE
    if not page or key not in SEO:
        return None, False
    base, e, eff, st = site_base(request), SEO[key], effective(key), settings()
    url = base + path_of(key)
    og = eff["og"] or base + "/og-image.jpg?v=12"
    t, d = esc(eff["t"]), esc(eff["d"])
    page = re.sub(r"<title>.*?</title>", lambda _: f"<title>{t}</title>", page, count=1, flags=re.S)
    page = re.sub(r'<meta name="description" content="[^"]*">', lambda _: f'<meta name="description" content="{d}">', page, count=1)
    page = re.sub(r'<link rel="canonical" href="[^"]*">', lambda _: f'<link rel="canonical" href="{esc(url)}">', page, count=1)
    page = re.sub(r'<meta property="og:title" content="[^"]*">', lambda _: f'<meta property="og:title" content="{t}">', page, count=1)
    page = re.sub(r'<meta property="og:description" content="[^"]*">', lambda _: f'<meta property="og:description" content="{d}">', page, count=1)
    page = re.sub(r'<meta property="og:url" content="[^"]*">', lambda _: f'<meta property="og:url" content="{esc(url)}">', page, count=1)
    page = page.replace('content="/og-image.jpg?v=12"', f'content="{esc(og)}"')
    if e.get("h"):
        page = page.replace('<h1 id="h"></h1><p id="p"></p>', f'<h1 id="h">{esc(e["h"])}</h1><p id="p">{esc(e["p"])}</p>', 1)
        page = page.replace('<h2 id="ah"></h2><p id="ap"></p>', f'<h2 id="ah">About {esc(e["h"])}</h2><p id="ap">{esc(e["a"])}</p>', 1)
    ld = schema_for(key, base, eff)
    ld_html = "" if ld is None else '<script type="application/ld+json" id="wr-ld">' + json.dumps(ld, ensure_ascii=False).replace("</", "<\\/") + "</script>"
    page = re.sub(r'<script type="application/ld\+json" id="wr-ld">.*?</script>', lambda _: ld_html, page, count=1, flags=re.S)
    # the page's own SEO table, so moving between tools in the browser shows the edited titles too
    meta_ov = overrides("meta")
    if meta_ov:
        table = {k: {**v, **{f: meta_ov[k][f] for f in ("t", "d") if meta_ov.get(k, {}).get(f)}} for k, v in SEO.items()}
        js = json.dumps(table, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        page = re.sub(r'(<script type="application/json" id="wr-seo">).*?(</script>)', lambda m: m.group(1) + js + m.group(2), page, count=1, flags=re.S)
    head = [f'<meta name="twitter:title" content="{t}">', f'<meta name="twitter:description" content="{d}">',
            '<meta name="robots" content="' + ("noindex, nofollow" if eff["noindex"] else "index, follow, max-image-preview:large") + '">']
    v = (st.get("gsc_verification") or "").strip()
    if v and VERIFY_RX.match(v):
        head.append(f'<meta name="google-site-verification" content="{esc(v)}">')
    ga = (st.get("ga_id") or "").strip().upper()
    if GA_RX.match(ga):
        head.append(f'<script async src="https://www.googletagmanager.com/gtag/js?id={ga}"></script>'
                    f"<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments)}}gtag('js',new Date());gtag('config','{ga}',{{anonymize_ip:true}});</script>")
    page = page.replace("</head>", "\n".join(head) + "\n</head>", 1)
    return page, eff["noindex"]


def page_response(key, request: Request):
    try:
        body, noindex = render(key, request)
    except Exception as e:      # noqa: BLE001
        print("[waverise] page render failed:", key, e, flush=True)
        body, noindex = None, False
    if body is None:
        return HTMLResponse("<!doctype html><title>WaveRise</title><p>WaveRise is updating. Please reload in a minute.</p>", status_code=503,
                            headers={"Cache-Control": "no-store", "Retry-After": "60"})
    h = dict(CACHE)
    if noindex:
        h["X-Robots-Tag"] = "noindex, nofollow"
    return HTMLResponse(body, headers=h)


# ---------------- sitemap and robots.txt ----------------
SITEMAP_MAX = 45000


def sitemap_urls(base):
    lastmod = overrides("lastmod")
    out = []
    for k in SEO:
        if effective(k)["noindex"]:
            continue
        lm = lastmod.get(k) if isinstance(lastmod.get(k), str) else BUILT
        out.append((base + path_of(k), lm, "1.0" if k == "video" else "0.3" if k in INFO_PAGES else "0.8"))
    return out


def _urlset(urls):
    rows = "".join(f"  <url><loc>{esc(u)}</loc>" + (f"<lastmod>{lm}</lastmod>" if lm else "") + f"<priority>{p}</priority></url>\n" for u, lm, p in urls)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + rows + "</urlset>\n"


def sitemap_xml(base, part=None):
    urls = sitemap_urls(base)
    if len(urls) <= SITEMAP_MAX:
        return _urlset(urls) if part is None else None
    chunks = [urls[i:i + SITEMAP_MAX] for i in range(0, len(urls), SITEMAP_MAX)]
    if part is None:     # sitemap index once the site grows past one file
        rows = "".join(f"  <sitemap><loc>{esc(base)}/sitemap-{i + 1}.xml</loc></sitemap>\n" for i in range(len(chunks)))
        return '<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + rows + "</sitemapindex>\n"
    return _urlset(chunks[part - 1]) if 1 <= part <= len(chunks) else None


ROBOTS_LINE = re.compile(r"^(User-agent|Allow|Disallow|Crawl-delay):\s*\S.{0,200}$", re.I)
ROBOTS_PROTECTED = ("Disallow: /api/", "Disallow: /admin")


def clean_robots_extra(text):
    lines, bad = [], []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        (lines if ROBOTS_LINE.match(ln) else bad).append(ln)
    return lines[:50], bad


def robots_txt(base):
    st = settings()
    if not st.get("visibility", True):
        return f"# Search engines are switched off for this site in the WaveRise admin.\nUser-agent: *\nDisallow: /\n"
    extra, _ = clean_robots_extra(st.get("robots_extra"))
    body = ["User-agent: *", "Allow: /", *ROBOTS_PROTECTED, *extra, "", f"Sitemap: {base}/sitemap.xml", ""]
    return "\n".join(body)


# ---------------- redirects and 404 monitoring ----------------
SCANNER = re.compile(r"(\.php|\.env|\.git|wp-|/cgi-bin|\.asp|xmlrpc|/vendor/|/\.well-known/(?!security))", re.I)


def norm_path(p):
    p = "/" + (p or "").strip().lstrip("/")
    return p.rstrip("/") if len(p) > 1 else p


def not_found(request: Request):
    path = request.url.path
    if request.method in ("GET", "HEAD"):
        rd = overrides("redirects").get(norm_path(path))
        if rd and rd.get("to"):
            if STORE.ok:
                try:
                    STORE.cmd("ZINCRBY", STORE.k("redirect_hits"), 1, norm_path(path))
                except StoreError:
                    pass
            return RedirectResponse(rd["to"], status_code=int(rd.get("code", 301)))
        if STORE.ok:
            try:
                if SCANNER.search(path):
                    STORE.cmd("INCR", STORE.k("404_scanners"))
                else:
                    key = path[:300]
                    STORE.cmd("ZINCRBY", STORE.k("404"), 1, key)
                    STORE.cmd("HSET", STORE.k("404meta"), key, json.dumps({"last": int(time.time()), "ref": (request.headers.get("referer") or "")[:300]}))
                    if (STORE.cmd("ZCARD", STORE.k("404")) or 0) > 600:
                        STORE.cmd("ZREMRANGEBYRANK", STORE.k("404"), 0, -501)
            except StoreError:
                pass
    body = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="robots" content="noindex"><title>Page not found – WaveRise</title><link rel="stylesheet" href="/glass.css?v=22"></head>'
            '<body style="background:#07122B;color:#F4F1EA;font-family:Sora,system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;padding:0 20px;text-align:center">'
            '<main><h1 style="font-size:28px;margin:0 0 8px">Page not found</h1><p style="color:#AEB8D0;margin:0 0 20px">This page doesn’t exist or has moved.</p>'
            '<a href="/" style="display:inline-block;padding:14px 22px;border-radius:14px;background:#F2C14E;color:#1A1405;font-weight:800;text-decoration:none">Open WaveRise</a></main></body></html>')
    return HTMLResponse(body, status_code=404, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


def install(app, taken_paths):
    """Register the public pages, sitemap, robots.txt and the 404/redirect handler on the FastAPI app."""
    from fastapi.exception_handlers import http_exception_handler
    from starlette.exceptions import HTTPException as StarletteHTTPException

    def page_route(key):
        def handler(request: Request):
            return page_response(key, request)
        return handler

    app.add_api_route("/", page_route("video"), methods=["GET", "HEAD"], include_in_schema=False)
    for k, e in SEO.items():
        p = "/" + e["s"]
        if p in taken_paths:
            continue
        app.add_api_route(p, page_route(k), methods=["GET", "HEAD"], include_in_schema=False)
        app.add_api_route(p + "/", (lambda s: (lambda: RedirectResponse("/" + s, status_code=301)))(e["s"]), methods=["GET", "HEAD"], include_in_schema=False)

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap(request: Request):
        return Response(sitemap_xml(site_base(request)), media_type="application/xml", headers=CACHE)

    @app.get("/sitemap-{n}.xml", include_in_schema=False)
    def sitemap_part(n: int, request: Request):
        body = sitemap_xml(site_base(request), n)
        return Response(body, media_type="application/xml", headers=CACHE) if body else not_found(request)

    @app.get("/robots.txt", include_in_schema=False)
    def robots(request: Request):
        return Response(robots_txt(site_base(request)), media_type="text/plain", headers=CACHE)

    @app.exception_handler(StarletteHTTPException)
    async def http_errors(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404 and exc.detail == "Not Found" and not request.url.path.startswith(("/api/", "/_vercel")):
            return not_found(request)
        return await http_exception_handler(request, exc)
