"""WaveRise admin: Google sign-in for listed admins only, the SEO dashboard API, Google Search Console and
Google Analytics (official APIs, OAuth on the server), and the SEO health monitor.

Security: everything is checked on the server. Sessions are random IDs in an HttpOnly cookie, stored hashed;
every change needs the session's CSRF token and a same-site Origin; logins and API calls are rate limited;
every action is written to the audit log; Google tokens are stored encrypted; no secret reaches the browser."""
import base64, hashlib, hmac, json, os, re, secrets, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse, parse_qs
from xml.etree import ElementTree as ET

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

import wr_seo
from wr_store import STORE, StoreError

TPL = Path(__file__).parent / "templates"
SESSION_TTL = 8 * 3600
COOKIE, OAUTH_COOKIE = "wr_admin", "wr_oauth"
G_AUTH, G_TOKEN, G_REVOKE = "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token", "https://oauth2.googleapis.com/revoke"
SC = "https://searchconsole.googleapis.com"
SCOPE_LOGIN = "openid email"
SCOPE_DATA = "openid email https://www.googleapis.com/auth/webmasters https://www.googleapis.com/auth/analytics.readonly"
HTTP = httpx.Client(timeout=20)


def cfg():
    admins = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if "@" in e}
    return {"client_id": os.getenv("GOOGLE_CLIENT_ID", "").strip(), "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
            "admins": admins, "secret": os.getenv("SESSION_SECRET", "").strip(), "cron": os.getenv("CRON_SECRET", "").strip()}


def missing_setup():
    c, miss = cfg(), []
    if len(c["secret"]) < 32: miss.append("SESSION_SECRET (at least 32 random characters)")
    if not c["admins"]: miss.append("ADMIN_EMAILS (your Google email)")
    if not c["client_id"]: miss.append("GOOGLE_CLIENT_ID")
    if not c["client_secret"]: miss.append("GOOGLE_CLIENT_SECRET")
    return miss


# ---------------- small crypto helpers ----------------
def _key(purpose):
    return hashlib.sha256(f"wr|{purpose}|{cfg()['secret']}".encode()).digest()


def sign(value: str, purpose="cookie"):
    mac = hmac.new(_key(purpose), value.encode(), hashlib.sha256).hexdigest()
    return f"{value}.{mac}"


def unsign(signed: str, purpose="cookie"):
    if not signed or "." not in signed:
        return None
    value, mac = signed.rsplit(".", 1)
    good = hmac.new(_key(purpose), value.encode(), hashlib.sha256).hexdigest()
    return value if hmac.compare_digest(mac, good) else None


def b64u(b: bytes):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def encrypt(text: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    n = secrets.token_bytes(12)
    return b64u(n + AESGCM(_key("google-token")).encrypt(n, text.encode(), b"wr"))


def decrypt(blob: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = unb64u(blob)
    return AESGCM(_key("google-token")).decrypt(raw[:12], raw[12:], b"wr").decode()


# ---------------- request helpers ----------------
def client_ip(request: Request):
    return (request.headers.get("x-real-ip") or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else "") or "?")[:64]


def is_https(request: Request):
    return (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"


def host_of(request: Request):
    return (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip().lower()


_mem_rl = {}


def rate_limit(bucket: str, limit: int, window: int):
    slot = int(time.time() // window)
    key = f"rl:{bucket}:{slot}"
    try:
        if STORE.ok:
            n = STORE.cmd("INCR", STORE.k(key))
            if n == 1:
                STORE.cmd("EXPIRE", STORE.k(key), window + 5)
        else:
            n = _mem_rl[key] = _mem_rl.get(key, 0) + 1
            if len(_mem_rl) > 5000: _mem_rl.clear()
    except StoreError:
        n = _mem_rl[key] = _mem_rl.get(key, 0) + 1
    if int(n) > limit:
        raise HTTPException(429, "Too many requests. Wait a minute and try again.")


def audit(request: Request, who: str, action: str, detail=None):
    row = {"t": int(time.time()), "who": who or "-", "ip": client_ip(request), "action": action, "detail": detail}
    print("[waverise-admin]", json.dumps(row, ensure_ascii=False)[:500], flush=True)
    if STORE.ok:
        try:
            STORE.push("audit", row, keep=2000)
        except StoreError:
            pass


ADMIN_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
                 "Referrer-Policy": "same-origin",
                 "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                            "font-src https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'; "
                                            "base-uri 'none'; form-action 'self' https://accounts.google.com; object-src 'none'"}


def admin_json(data, status=200):
    return JSONResponse(data, status_code=status, headers=ADMIN_HEADERS)


# ---------------- sessions ----------------
def _set_cookie(resp, request, name, value, max_age, path="/"):
    resp.set_cookie(name, value, max_age=max_age, path=path, httponly=True, secure=is_https(request), samesite="lax")


def new_session(request: Request, resp: Response, email: str):
    sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    data = {"email": email, "csrf": csrf, "created": int(time.time()), "exp": int(time.time()) + SESSION_TTL, "ip": client_ip(request)}
    if STORE.ok:
        STORE.set_json("sess:" + hashlib.sha256(sid.encode()).hexdigest(), data, ex=SESSION_TTL)
        token = "r:" + sid
    else:   # no database: a signed, expiring cookie (cannot be revoked early)
        token = "s:" + b64u(json.dumps(data).encode())
    _set_cookie(resp, request, COOKIE, sign(token), SESSION_TTL)


def current_session(request: Request):
    raw = unsign(request.cookies.get(COOKIE, ""))
    if not raw or len(cfg()["secret"]) < 32:
        return None
    try:
        if raw.startswith("r:"):
            if not STORE.ok:
                return None
            data = STORE.get_json("sess:" + hashlib.sha256(raw[2:].encode()).hexdigest())
        elif raw.startswith("s:"):
            data = json.loads(unb64u(raw[2:]))
        else:
            return None
    except (StoreError, ValueError):
        return None
    if not data or data.get("exp", 0) < time.time() or data.get("email", "").lower() not in cfg()["admins"]:
        return None
    data["_token"] = raw
    return data


def end_session(request: Request, resp: Response):
    raw = unsign(request.cookies.get(COOKIE, "")) or ""
    if raw.startswith("r:") and STORE.ok:
        try:
            STORE.cmd("DEL", STORE.k("sess:" + hashlib.sha256(raw[2:].encode()).hexdigest()))
        except StoreError:
            pass
    resp.delete_cookie(COOKIE, path="/")


def require(request: Request, write=False):
    if missing_setup():
        raise HTTPException(503, "Admin is not set up yet.")
    s = current_session(request)
    if not s:
        raise HTTPException(401, "Please sign in again.")
    rate_limit("api:" + hashlib.sha256(s["_token"].encode()).hexdigest()[:16], 240, 60)
    if write:
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), s["csrf"]):
            raise HTTPException(403, "Security check failed. Reload the page and try again.")
        src = request.headers.get("origin") or request.headers.get("referer") or ""
        if not src or (urlparse(src).netloc or "").lower() != host_of(request):
            raise HTTPException(403, "Request came from another site.")
    return s


def need_store():
    if not STORE.ok:
        raise HTTPException(503, "No database is connected, so changes can’t be saved. Add Upstash Redis in Vercel → Storage, then redeploy.")


# ---------------- Google OAuth ----------------
def _redirect_uri(request: Request):
    return (wr_seo.site_base(request) or "") + "/admin/oauth/callback"


def oauth_start(request: Request, purpose: str, email_hint=""):
    c = cfg()
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    challenge = b64u(hashlib.sha256(verifier.encode()).digest())
    q = {"client_id": c["client_id"], "redirect_uri": _redirect_uri(request), "response_type": "code", "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    if purpose == "connect":
        q.update(scope=SCOPE_DATA, access_type="offline", prompt="consent", include_granted_scopes="true")
        if email_hint: q["login_hint"] = email_hint
    else:
        q.update(scope=SCOPE_LOGIN, prompt="select_account")
    resp = RedirectResponse(G_AUTH + "?" + urlencode(q), status_code=302)
    payload = b64u(json.dumps({"s": state, "v": verifier, "p": purpose, "exp": int(time.time()) + 600}).encode())
    _set_cookie(resp, request, OAUTH_COOKIE, sign(payload, "oauth"), 600, path="/admin")
    resp.headers.update({"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})
    return resp


def _claims(id_token: str):
    try:
        return json.loads(unb64u(id_token.split(".")[1]))
    except Exception:
        return {}


def exchange_code(request: Request, code: str, verifier: str):
    c = cfg()
    r = HTTP.post(G_TOKEN, data={"code": code, "client_id": c["client_id"], "client_secret": c["client_secret"], "redirect_uri": _redirect_uri(request),
                                 "grant_type": "authorization_code", "code_verifier": verifier})
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        raise ValueError(j.get("error_description") or j.get("error") or f"Google answered {r.status_code}")
    claims = _claims(j.get("id_token", ""))
    if claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com") or claims.get("aud") != c["client_id"] \
            or int(claims.get("exp", 0)) < time.time() or not claims.get("email_verified"):
        raise ValueError("Google sign-in could not be verified.")
    return j, claims["email"].lower()


_token_cache = {}


class GoogleError(Exception):
    pass


def google_link():
    if not STORE.ok:
        return None
    try:
        return STORE.get_json("google")
    except StoreError:
        return None


def access_token():
    hit = _token_cache.get("t")
    if hit and hit[1] > time.time() + 60:
        return hit[0]
    link = google_link()
    if not link or not link.get("rt"):
        raise GoogleError("Google isn’t connected yet. Open Settings and tap “Connect Google”.")
    c = cfg()
    r = HTTP.post(G_TOKEN, data={"client_id": c["client_id"], "client_secret": c["client_secret"], "refresh_token": decrypt(link["rt"]), "grant_type": "refresh_token"})
    j = r.json() if r.content else {}
    if r.status_code != 200:
        if j.get("error") == "invalid_grant":
            raise GoogleError("The Google connection expired or was removed. Connect Google again in Settings.")
        raise GoogleError(f"Google token refresh failed ({j.get('error') or r.status_code}).")
    _token_cache["t"] = (j["access_token"], time.time() + int(j.get("expires_in", 3600)))
    return j["access_token"]


def gapi(method, url, **kw):
    r = HTTP.request(method, url, headers={"Authorization": "Bearer " + access_token()}, **kw)
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", "")
        except ValueError:
            msg = r.text[:200]
        if r.status_code == 403 and "permission" in msg.lower():
            msg += " (The connected Google account needs access to this Search Console property / Analytics property.)"
        raise GoogleError(f"Google API error {r.status_code}: {msg}"[:400])
    return r.json() if r.content else {}


# ---------------- Search Console ----------------
def gsc_site():
    s = wr_seo.settings().get("gsc_site", "")
    if not s:
        raise GoogleError("Choose your Search Console property in Settings first.")
    return s


def gsc_query(site, start, end, dims=None, limit=1000):
    body = {"startDate": start, "endDate": end, "rowLimit": limit, "dataState": "all"}
    if dims: body["dimensions"] = dims
    return gapi("POST", f"{SC}/webmasters/v3/sites/{quote(site, safe='')}/searchAnalytics/query", json=body).get("rows", [])


def _day(n):
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - n * 86400))


def gsc_summary(days):
    site = gsc_site()
    start, end, pstart, pend = _day(days), _day(0), _day(2 * days), _day(days + 1)
    tot = (gsc_query(site, start, end) or [{}])[0]
    prev = (gsc_query(site, pstart, pend) or [{}])[0]
    series = [{"date": r["keys"][0], "clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0), "position": r.get("position", 0)}
              for r in gsc_query(site, start, end, ["date"])]
    q = [{"query": r["keys"][0], "clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0), "ctr": r.get("ctr", 0), "position": r.get("position", 0)}
         for r in gsc_query(site, start, end, ["query"], 25)]
    pages = [{"page": r["keys"][0], "clicks": r.get("clicks", 0), "impressions": r.get("impressions", 0), "position": r.get("position", 0)}
             for r in gsc_query(site, start, end, ["page"], 25)]
    pick = lambda d: {k: d.get(k, 0) for k in ("clicks", "impressions", "ctr", "position")}
    return {"site": site, "start": start, "end": end, "totals": pick(tot), "previous": pick(prev), "series": series, "queries": q, "pages": pages}


def gsc_sitemaps():
    site = gsc_site()
    return gapi("GET", f"{SC}/webmasters/v3/sites/{quote(site, safe='')}/sitemaps").get("sitemap", [])


def gsc_submit(feed):
    site = gsc_site()
    gapi("PUT", f"{SC}/webmasters/v3/sites/{quote(site, safe='')}/sitemaps/{quote(feed, safe='')}")


def gsc_inspect(url):
    j = gapi("POST", f"{SC}/v1/urlInspection/index:inspect", json={"inspectionUrl": url, "siteUrl": gsc_site(), "languageCode": "en-US"})
    r = (j.get("inspectionResult") or {}).get("indexStatusResult") or {}
    keep = ("verdict", "coverageState", "robotsTxtState", "indexingState", "lastCrawlTime", "pageFetchState", "googleCanonical", "userCanonical", "crawledAs")
    return {k: r.get(k) for k in keep} | {"checked": int(time.time())}


# ---------------- Google Analytics 4 ----------------
def ga_properties():
    out = []
    for acc in gapi("GET", "https://analyticsadmin.googleapis.com/v1beta/accountSummaries", params={"pageSize": 200}).get("accountSummaries", []):
        for p in acc.get("propertySummaries", []):
            out.append({"id": p["property"].split("/")[-1], "name": p.get("displayName", ""), "account": acc.get("displayName", "")})
    return out


def ga_summary(days):
    pid = wr_seo.settings().get("ga_property", "")
    if not re.fullmatch(r"\d{4,20}", pid or ""):
        raise GoogleError("Choose your Google Analytics property in Settings first.")
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport"
    rng = [{"startDate": f"{days}daysAgo", "endDate": "today"}]
    a = gapi("POST", url, json={"dateRanges": rng, "dimensions": [{"name": "date"}], "metrics": [{"name": "activeUsers"}, {"name": "sessions"}, {"name": "screenPageViews"}],
                                "orderBys": [{"dimension": {"dimensionName": "date"}}], "metricAggregations": ["TOTAL"]})
    b = gapi("POST", url, json={"dateRanges": rng, "dimensions": [{"name": "pagePath"}], "metrics": [{"name": "screenPageViews"}, {"name": "activeUsers"}],
                                "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}], "limit": 15})
    c = gapi("POST", url, json={"dateRanges": rng, "dimensions": [{"name": "sessionDefaultChannelGroup"}], "metrics": [{"name": "sessions"}],
                                "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}], "limit": 8})
    num = lambda r, i: float(r["metricValues"][i]["value"])
    tot = (a.get("totals") or [{}])[0]
    totals = {n: float(tot["metricValues"][i]["value"]) for i, n in enumerate(("users", "sessions", "views"))} if tot.get("metricValues") else {"users": 0, "sessions": 0, "views": 0}
    series = [{"date": f"{r['dimensionValues'][0]['value'][:4]}-{r['dimensionValues'][0]['value'][4:6]}-{r['dimensionValues'][0]['value'][6:]}",
               "users": num(r, 0), "sessions": num(r, 1), "views": num(r, 2)} for r in a.get("rows", [])]
    pages = [{"page": r["dimensionValues"][0]["value"], "views": num(r, 0), "users": num(r, 1)} for r in b.get("rows", [])]
    channels = [{"channel": r["dimensionValues"][0]["value"], "sessions": num(r, 0)} for r in c.get("rows", [])]
    return {"property": pid, "days": days, "totals": totals, "series": series, "pages": pages, "channels": channels}


# ---------------- SEO health monitor (real checks against the live site) ----------------
def _attr(html, rx):
    m = re.search(rx, html, re.I | re.S)
    return wr_seo._html.unescape(m.group(1)).strip() if m else None


def check_page(cl, url):
    t0 = time.time()
    try:
        r = cl.get(url)
    except httpx.HTTPError as e:
        return {"url": url, "status": 0, "error": e.__class__.__name__, "issues": [("error", "Page could not be fetched")]}
    html = r.text if "html" in r.headers.get("content-type", "") else ""
    p = {"url": url, "status": r.status_code, "ms": int((time.time() - t0) * 1000),
         "title": _attr(html, r"<title>(.*?)</title>"), "desc": _attr(html, r'<meta name="description" content="([^"]*)"'),
         "canonical": _attr(html, r'<link rel="canonical" href="([^"]*)"'), "robots": _attr(html, r'<meta name="robots" content="([^"]*)"'),
         "og_title": _attr(html, r'<meta property="og:title" content="([^"]*)"'), "og_image": _attr(html, r'<meta property="og:image" content="([^"]*)"'),
         "tw_card": _attr(html, r'<meta name="twitter:card" content="([^"]*)"'), "h1": _attr(html, r"<h1[^>]*>(.*?)</h1>"),
         "xrobots": r.headers.get("x-robots-tag", "")}
    iss = []
    if r.status_code != 200: iss.append(("error", f"Returns HTTP {r.status_code}"))
    if not p["title"]: iss.append(("error", "Missing <title>"))
    elif len(p["title"]) > 70: iss.append(("warn", f"Title is long ({len(p['title'])} characters; Google shows about 60)"))
    elif len(p["title"]) < 20: iss.append(("warn", "Title is very short"))
    if not p["desc"]: iss.append(("error", "Missing meta description"))
    elif len(p["desc"]) > 165: iss.append(("warn", f"Description is long ({len(p['desc'])} characters; about 155 show)"))
    elif len(p["desc"]) < 70: iss.append(("warn", "Description is short (aim for 70–155 characters)"))
    if not p["canonical"]: iss.append(("error", "Missing canonical URL"))
    elif p["canonical"].rstrip("/") != url.rstrip("/"): iss.append(("error", f"Canonical points elsewhere: {p['canonical']}"))
    if "noindex" in (p["robots"] or "") + p["xrobots"]: iss.append(("error", "Page is in the sitemap but marked noindex"))
    if not p["og_title"] or not p["og_image"]: iss.append(("warn", "Open Graph title or image missing"))
    if not p["tw_card"]: iss.append(("warn", "Twitter card missing"))
    lds = re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    types = []
    for raw in lds:
        try:
            ld = json.loads(raw)
            items = ld.get("@graph", [ld]) if isinstance(ld, dict) else ld
            types += [i.get("@type") for i in items if isinstance(i, dict)]
            if isinstance(ld, dict) and "schema.org" not in str(ld.get("@context", "")):
                iss.append(("error", "Structured data has no schema.org @context"))
        except ValueError:
            iss.append(("error", "Structured data is not valid JSON"))
    p["schema"] = [t for t in types if t]
    if not lds: iss.append(("warn", "No structured data"))
    p["links"] = sorted({h.split("#")[0] for h in re.findall(r'href="(/[^"/][^"]*|/)"', html) if not h.startswith(("//", "/api/"))})
    p["issues"] = iss
    return p


def run_health(base, fetch_client=None):
    started = time.time()
    cl = fetch_client or httpx.Client(timeout=15, follow_redirects=False, headers={"User-Agent": "WaveRise-SEO-Monitor/1.0 (+admin check)"})
    out = {"checked": int(started), "base": base, "site": [], "pages": [], "broken": [], "duplicates": []}
    st = wr_seo.settings()

    def item(name, ok, detail, level="error"):
        out["site"].append({"name": name, "ok": bool(ok), "level": "ok" if ok else level, "detail": detail})

    try:
        rb = cl.get(base + "/robots.txt")
        txt = rb.text
        blocks_all = re.search(r"^Disallow:\s*/\s*$", txt, re.M) is not None
        item("robots.txt is reachable", rb.status_code == 200, f"HTTP {rb.status_code}")
        item("robots.txt lists the sitemap", "Sitemap:" in txt, "Sitemap line " + ("found" if "Sitemap:" in txt else "missing"))
        item("robots.txt does not block the whole site", not blocks_all or not st.get("visibility", True),
             "Search engines are switched off in Settings" if blocks_all else "Public pages can be crawled", "error")
        item("Admin and API are kept out of search", "Disallow: /admin" in txt and "Disallow: /api/" in txt or blocks_all, "Disallow rules present")
    except httpx.HTTPError as e:
        item("robots.txt is reachable", False, e.__class__.__name__)
    urls = []
    try:
        sm = cl.get(base + "/sitemap.xml")
        root = ET.fromstring(sm.content)
        ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
        urls = [u.findtext(ns + "loc") for u in root.findall(ns + "url")]
        item("sitemap.xml is reachable and valid XML", sm.status_code == 200 and root.tag == ns + "urlset", f"{len(urls)} pages listed")
        bad_host = [u for u in urls if not u.startswith(base)]
        item("Sitemap URLs use the main domain", not bad_host, f"{len(bad_host)} use another address" if bad_host else base, "warn")
        priv = [u for u in urls if urlparse(u).path.startswith(("/admin", "/api"))]
        item("Sitemap has no private pages", not priv, f"{len(priv)} private URLs" if priv else "None listed")
    except (httpx.HTTPError, ET.ParseError) as e:
        item("sitemap.xml is reachable and valid XML", False, e.__class__.__name__)
    item("Search engine visibility", st.get("visibility", True), "On" if st.get("visibility", True) else "Switched off in Settings")
    item("Google verification tag", bool(st.get("gsc_verification")), "Added" if st.get("gsc_verification") else "Not added (fine if you verified with DNS)", "warn")
    with ThreadPoolExecutor(6) as ex:
        pages = list(ex.map(lambda u: check_page(cl, u), urls))
    known = {urlparse(u).path or "/" for u in urls}
    # duplicates
    for field, label in (("title", "title"), ("desc", "description")):
        seen = {}
        for p in pages:
            if p.get(field): seen.setdefault(p[field], []).append(p["url"])
        for val, us in seen.items():
            if len(us) > 1:
                out["duplicates"].append({"field": label, "value": val, "urls": us})
                for p in pages:
                    if p["url"] in us: p["issues"].append(("warn", f"Same {label} as {len(us) - 1} other page(s)"))
    # internal links that are not sitemap pages
    links = {}
    for p in pages:
        for l in p.get("links", []):
            if (l.rstrip("/") or "/") not in known and not re.search(r"\.(css|js|png|jpg|jpeg|svg|webp|ico|json|xml|txt|woff2?)(\?|$)", l):
                links.setdefault(l, []).append(p["url"])
    def probe(l):
        try:
            r = cl.get(base + l)
            return l, r.status_code
        except httpx.HTTPError:
            return l, 0
    with ThreadPoolExecutor(6) as ex:
        for l, code in ex.map(probe, list(links)[:150]):
            if code >= 400 or code == 0:
                out["broken"].append({"link": l, "status": code, "from": links[l][:5]})
    ogs = {p["og_image"] for p in pages if p.get("og_image")}
    for img in list(ogs)[:5]:
        try:
            r = cl.get(img if img.startswith("http") else base + img)
            item(f"Share image loads ({urlparse(img).path})", r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"), f"HTTP {r.status_code}", "warn")
        except httpx.HTTPError as e:
            item("Share image loads", False, e.__class__.__name__, "warn")
    for p in pages:
        p.pop("links", None)
        p["issues"] = [{"level": a, "text": b} for a, b in p["issues"]]
    out["pages"] = pages
    errs = sum(1 for s in out["site"] if s["level"] == "error") + sum(1 for p in pages for i in p["issues"] if i["level"] == "error") + len(out["broken"])
    warns = sum(1 for s in out["site"] if s["level"] == "warn") + sum(1 for p in pages for i in p["issues"] if i["level"] == "warn")
    out["summary"] = {"pages": len(pages), "errors": errs, "warnings": warns, "score": max(0, 100 - errs * 6 - warns), "seconds": round(time.time() - started, 1)}
    if STORE.ok:
        try:
            STORE.set_json("health", out)
            STORE.push("health_hist", {"t": out["checked"], **out["summary"]}, keep=60)
        except StoreError:
            pass
    return out


# ---------------- validation ----------------
def v_text(v, lo, hi, name):
    v = re.sub(r"\s+", " ", str(v or "")).strip()
    if v and not lo <= len(v) <= hi:
        raise HTTPException(400, f"{name} must be {lo}–{hi} characters (now {len(v)}).")
    return v


def v_url(v, name):
    v = str(v or "").strip()
    if not v:
        return ""
    if len(v) > 500 or not (re.fullmatch(r"https://[^\s\"'<>]+", v) or re.fullmatch(r"/[^\s\"'<>]*", v)):
        raise HTTPException(400, f"{name} must be a https:// address or a path starting with /.")
    return v


def page_paths():
    return {wr_seo.path_of(k): k for k in wr_seo.SEO}


def v_redirect(frm, to, code):
    frm = wr_seo.norm_path(frm)
    if len(frm) > 200 or not re.fullmatch(r"/[A-Za-z0-9/_\-.~%]*", frm):
        raise HTTPException(400, "“From” must be a path like /old-page (letters, numbers, - _ . / only).")
    if frm.startswith(("/api", "/admin", "/_vercel")) or frm in ("/", "/sitemap.xml", "/robots.txt"):
        raise HTTPException(400, "That path is reserved and can’t be redirected.")
    if frm in page_paths():
        raise HTTPException(400, "That path is a live WaveRise page. Redirects only apply to missing pages.")
    to = v_url(to, "“To”")
    if not to:
        raise HTTPException(400, "Enter where it should go.")
    if wr_seo.norm_path(urlparse(to).path if to.startswith("http") else to) == frm and not to.startswith("http"):
        raise HTTPException(400, "A page can’t redirect to itself.")
    code = int(code or 301)
    if code not in (301, 302, 307, 308):
        raise HTTPException(400, "Type must be 301, 302, 307 or 308.")
    existing = wr_seo.overrides("redirects")
    hop, seen = to, {frm}
    for _ in range(6):     # loop / chain check
        if hop.startswith("http"): break
        hp = wr_seo.norm_path(hop)
        if hp in seen: raise HTTPException(400, "This would create a redirect loop.")
        seen.add(hp)
        nxt = existing.get(hp)
        if not nxt: break
        hop = nxt["to"]
    return frm, to, code


def v_schema(mode, raw):
    if mode not in ("auto", "custom", "off"):
        raise HTTPException(400, "Unknown structured data mode.")
    if mode != "custom":
        return mode, ""
    raw = str(raw or "")
    if len(raw) > 20000:
        raise HTTPException(400, "Structured data is too long (20,000 characters max).")
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise HTTPException(400, f"Structured data is not valid JSON: {e}") from None
    items = data if isinstance(data, list) else [data]
    for it in items:
        if not isinstance(it, dict) or "schema.org" not in str(it.get("@context", "")) or not (it.get("@type") or it.get("@graph")):
            raise HTTPException(400, "Each item needs \"@context\": \"https://schema.org\" and an \"@type\".")
    if "</script" in raw.lower():
        raise HTTPException(400, "Structured data can’t contain </script>.")
    return mode, json.dumps(data, ensure_ascii=False)


def validate_schema_text(raw):
    """Used by the Schema Manager's “Validate” button: checks JSON-LD and common required fields."""
    out = []
    try:
        data = json.loads(raw)
    except ValueError as e:
        return [{"level": "error", "text": f"Not valid JSON: {e}"}]
    items = data.get("@graph", [data]) if isinstance(data, dict) else data
    if isinstance(data, dict) and "schema.org" not in str(data.get("@context", "")):
        out.append({"level": "error", "text": "Missing \"@context\": \"https://schema.org\""})
    need = {"WebApplication": ("name", "url"), "SoftwareApplication": ("name", "offers"), "FAQPage": ("mainEntity",), "Organization": ("name", "url"),
            "WebSite": ("name", "url"), "WebPage": ("name", "url"), "BreadcrumbList": ("itemListElement",)}
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            out.append({"level": "error", "text": "Every item must be an object"}); continue
        t = it.get("@type")
        if not t:
            out.append({"level": "error", "text": "An item has no @type"}); continue
        for f in need.get(t if isinstance(t, str) else "", ()):
            if f not in it: out.append({"level": "warn", "text": f"{t}: “{f}” is recommended"})
        if t == "FAQPage":
            for q in it.get("mainEntity", []):
                if not (isinstance(q, dict) and q.get("name") and isinstance(q.get("acceptedAnswer"), dict) and q["acceptedAnswer"].get("text")):
                    out.append({"level": "error", "text": "FAQPage: each question needs a name and acceptedAnswer.text"}); break
    return out or [{"level": "ok", "text": "Looks valid. For a full check use Google’s Rich Results Test."}]


# ---------------- pages ----------------
def _page(name):
    try:
        return (TPL / name).read_text("utf-8")
    except OSError:
        return ""


def simple_page(title, body_html, status=200):
    html = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex, nofollow"><title>{wr_seo.esc(title)} – WaveRise Admin</title>'
            '<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;800&display=swap" rel="stylesheet">'
            '<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(80% 60% at 50% 0%,#11295c 0%,#07122B 60%);color:#F4F1EA;font-family:Sora,system-ui,sans-serif;padding:20px}'
            '.box{width:min(440px,100%);padding:34px 28px;border-radius:24px;background:rgba(13,29,64,.82);border:1px solid #1B2F5A;box-shadow:0 30px 80px rgba(0,0,0,.45);text-align:center}'
            'h1{font-size:24px;margin:14px 0 8px}p{color:#AEB8D0;line-height:1.6;margin:0 0 18px}.b{display:inline-flex;align-items:center;gap:10px;padding:14px 22px;border-radius:14px;background:#F2C14E;color:#1A1405;font-weight:800;text-decoration:none}'
            '.logo{font-weight:800;font-size:22px}.logo b{color:#F2C14E}ul{text-align:left;color:#D7DCE8;line-height:1.8}code{color:#F2C14E}</style></head>'
            f'<body><main class="box"><div class="logo">Wave<b>Rise</b> Admin</div>{body_html}</main></body></html>')
    return HTMLResponse(html, status_code=status, headers=ADMIN_HEADERS)


def login_page(msg=""):
    note = f'<p style="color:#ffb4a8">{wr_seo.esc(msg)}</p>' if msg else ""
    return simple_page("Sign in", f'<h1>Admin sign in</h1><p>Only WaveRise administrators can open this area.</p>{note}'
                                  '<a class="b" href="/admin/login">Sign in with Google</a>')


# ---------------- routes ----------------
def install(app):
    def canonical_host(request):
        base = (os.getenv("SITE_URL") or "").strip().rstrip("/")
        if base and urlparse(base).netloc.lower() != host_of(request):
            return RedirectResponse(base + request.url.path, status_code=302)
        return None

    @app.get("/admin", include_in_schema=False)
    def admin_home(request: Request):
        r = canonical_host(request)
        if r: return r
        miss = missing_setup()
        if miss:
            return simple_page("Setup needed", "<h1>Admin isn’t set up yet</h1><p>Add these environment variables in Vercel → Settings → Environment Variables, then redeploy:</p><ul>"
                               + "".join(f"<li><code>{wr_seo.esc(m)}</code></li>" for m in miss) + "</ul>", 503)
        s = current_session(request)
        if not s:
            return login_page()
        html = _page("admin.html")
        if not html:
            return simple_page("Missing files", "<h1>Admin files are missing</h1><p>Upload <code>templates/admin.html</code>, <code>admin.js</code> and <code>admin.css</code>.</p>", 500)
        return HTMLResponse(html, headers=ADMIN_HEADERS)

    @app.get("/admin/{asset}", include_in_schema=False)
    def admin_asset(asset: str, request: Request):
        if asset in ("login", "connect-google", "logout"):
            return admin_actions(asset, request)
        if asset not in ("app.js", "app.css"):
            return wr_seo.not_found(request)
        if not current_session(request):
            return Response("Sign in first.", status_code=401, headers=ADMIN_HEADERS)
        body = _page("admin.js" if asset == "app.js" else "admin.css")
        return Response(body, media_type="application/javascript" if asset == "app.js" else "text/css", headers=ADMIN_HEADERS)

    def admin_actions(action, request):
        r = canonical_host(request)
        if r: return r
        if missing_setup():
            return RedirectResponse("/admin", 302)
        if action == "login":
            rate_limit("login:" + client_ip(request), 20, 600)
            return oauth_start(request, "login")
        if action == "connect-google":
            s = current_session(request)
            if not s: return RedirectResponse("/admin", 302)
            return oauth_start(request, "connect", s["email"])
        if action == "logout":
            resp = RedirectResponse("/admin", 302)
            s = current_session(request)
            if s: audit(request, s["email"], "logout")
            end_session(request, resp)
            return resp

    @app.get("/admin/oauth/callback", include_in_schema=False)
    def oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        rate_limit("cb:" + client_ip(request), 30, 600)
        raw = unsign(request.cookies.get(OAUTH_COOKIE, ""), "oauth")
        try:
            st = json.loads(unb64u(raw)) if raw else {}
        except ValueError:
            st = {}
        if error:
            return login_page("Google sign-in was cancelled.")
        if not st or not code or not hmac.compare_digest(state, st.get("s", "")) or st.get("exp", 0) < time.time():
            audit(request, "-", "oauth_state_rejected")
            return login_page("That sign-in link expired. Please try again.")
        try:
            tok, email = exchange_code(request, code, st["v"])
        except (ValueError, httpx.HTTPError) as e:
            audit(request, "-", "login_failed", str(e)[:120])
            return login_page("Google sign-in failed. Please try again.")
        if st.get("p") == "connect":
            s = current_session(request)
            if not s:
                return login_page("Sign in as an admin before connecting Google.")
            if not tok.get("refresh_token"):
                return simple_page("Connect Google", "<h1>Google didn’t send a long-term key</h1><p>Remove WaveRise from <b>myaccount.google.com → Security → Third-party access</b>, then connect again.</p><a class='b' href='/admin'>Back</a>")
            try:
                STORE.set_json("google", {"rt": encrypt(tok["refresh_token"]), "email": email, "scopes": tok.get("scope", "").split(), "at": int(time.time()), "by": s["email"]})
            except (StoreError, ImportError) as e:
                return simple_page("Connect Google", f"<h1>Couldn’t save the connection</h1><p>{wr_seo.esc(str(e))}</p><a class='b' href='/admin'>Back</a>")
            _token_cache.clear()
            audit(request, s["email"], "google_connected", {"google_account": email, "scopes": tok.get("scope", "")})
            resp = RedirectResponse("/admin#settings", 302)
            resp.delete_cookie(OAUTH_COOKIE, path="/admin")
            return resp
        if email not in cfg()["admins"]:
            audit(request, email, "login_denied")
            return login_page("This Google account isn’t a WaveRise admin.")
        resp = RedirectResponse("/admin", 302)
        try:
            new_session(request, resp, email)
        except StoreError as e:
            return login_page(f"Sign-in couldn’t be saved: {e}")
        resp.delete_cookie(OAUTH_COOKIE, path="/admin")
        audit(request, email, "login")
        return resp

    # ---------- admin API ----------
    def pages_state():
        meta, schema, ins = wr_seo.overrides("meta"), wr_seo.overrides("schema"), wr_seo.overrides("inspect")
        base = wr_seo.site_base()
        out = []
        for k, e in wr_seo.SEO.items():
            eff = wr_seo.effective(k)
            out.append({"key": k, "path": wr_seo.path_of(k), "url": (base or "") + wr_seo.path_of(k), "name": e.get("h") or e.get("t", k).split(" – ")[0].split(" | ")[0],
                        "default": {"t": e.get("t", ""), "d": e.get("d", "")}, "override": meta.get(k, {}), "effective": {"t": eff["t"], "d": eff["d"], "og": eff["og"], "noindex": eff["noindex"]},
                        "schema": schema.get(k, {"mode": "auto"}), "inspect": ins.get(k)})
        return out

    @app.get("/api/admin/state", include_in_schema=False)
    def a_state(request: Request):
        s = require(request)
        c, link = cfg(), google_link()
        st = wr_seo.settings()
        try:
            n404 = STORE.cmd("ZCARD", STORE.k("404")) if STORE.ok else 0
        except StoreError:
            n404 = None
        return admin_json({"email": s["email"], "csrf": s["csrf"], "store": {"ok": STORE.ok, "kind": STORE.kind},
                           "google": {"configured": bool(c["client_id"] and c["client_secret"]), "connected": bool(link and link.get("rt")),
                                      "email": (link or {}).get("email"), "scopes": (link or {}).get("scopes", []), "at": (link or {}).get("at")},
                           "settings": st, "site_url": wr_seo.site_base(request), "site_url_env": bool(os.getenv("SITE_URL")),
                           "pages": pages_state(), "redirects": wr_seo.overrides("redirects"), "n404": n404, "cron": bool(c["cron"])})

    async def body(request: Request):
        try:
            j = await request.json()
        except Exception:
            raise HTTPException(400, "Send JSON.") from None
        if not isinstance(j, dict):
            raise HTTPException(400, "Send a JSON object.")
        return j

    @app.post("/api/admin/settings", include_in_schema=False)
    async def a_settings(request: Request):
        s = require(request, True); need_store(); j = await body(request)
        cur = wr_seo.settings()
        new = dict(cur)
        if "visibility" in j: new["visibility"] = bool(j["visibility"])
        if "gsc_verification" in j:
            v = str(j["gsc_verification"] or "").strip()
            m = re.search(r'content="([^"]+)"', v)
            v = m.group(1) if m else v
            if v and not wr_seo.VERIFY_RX.match(v):
                raise HTTPException(400, "That doesn’t look like a Google verification code.")
            new["gsc_verification"] = v
        if "ga_id" in j:
            v = str(j["ga_id"] or "").strip().upper()
            if v and not wr_seo.GA_RX.match(v):
                raise HTTPException(400, "A Google Analytics 4 Measurement ID looks like G-XXXXXXXXXX.")
            new["ga_id"] = v
        if "gsc_site" in j:
            v = str(j["gsc_site"] or "").strip()
            if v and not re.fullmatch(r"(sc-domain:[a-z0-9.\-]+|https?://[^\s]+/)", v):
                raise HTTPException(400, "Search Console property must look like sc-domain:waverise.in or https://www.waverise.in/")
            new["gsc_site"] = v
        if "ga_property" in j:
            v = str(j["ga_property"] or "").strip()
            if v and not re.fullmatch(r"\d{4,20}", v):
                raise HTTPException(400, "The Analytics property ID is a number.")
            new["ga_property"] = v
        if "robots_extra" in j:
            lines, bad = wr_seo.clean_robots_extra(j["robots_extra"])
            if bad:
                raise HTTPException(400, "These robots.txt lines aren’t allowed: " + "; ".join(bad[:3]))
            if any(re.fullmatch(r"Disallow:\s*/\s*", ln, re.I) for ln in lines):
                raise HTTPException(400, "“Disallow: /” would hide the whole site. Use the Search engine visibility switch instead.")
            new["robots_extra"] = "\n".join(lines)
        if "default_og" in j: new["default_og"] = v_url(j["default_og"], "Default share image")
        STORE.set_json("settings", new)
        changed = {k: new[k] for k in new if new.get(k) != cur.get(k)}
        audit(request, s["email"], "settings_changed", changed)
        return admin_json({"ok": True, "settings": new})

    @app.post("/api/admin/meta", include_in_schema=False)
    async def a_meta(request: Request):
        s = require(request, True); need_store(); j = await body(request)
        k = j.get("key")
        if k not in wr_seo.SEO:
            raise HTTPException(400, "Unknown page.")
        if j.get("reset"):
            STORE.hdel("meta", k); action = "meta_reset"; val = None
        else:
            val = {"t": v_text(j.get("t"), 15, 120, "Title"), "d": v_text(j.get("d"), 50, 320, "Description"), "og": v_url(j.get("og"), "Share image"),
                   "noindex": bool(j.get("noindex")), "updated": int(time.time()), "by": s["email"]}
            titles = {p: wr_seo.effective(p)["t"].lower() for p in wr_seo.SEO if p != k}
            if val["t"] and val["t"].lower() in titles.values():
                raise HTTPException(400, "Another page already uses this title. Each page needs its own.")
            STORE.hset("meta", k, val); action = "meta_saved"
        STORE.hset("lastmod", k, time.strftime("%Y-%m-%d", time.gmtime()))
        audit(request, s["email"], action, {"page": k, **({kk: val[kk] for kk in ("t", "d", "noindex")} if val else {})})
        return admin_json({"ok": True, "page": next(p for p in pages_state() if p["key"] == k)})

    @app.post("/api/admin/schema", include_in_schema=False)
    async def a_schema(request: Request):
        s = require(request, True); need_store(); j = await body(request)
        k = j.get("key")
        if k not in wr_seo.SEO:
            raise HTTPException(400, "Unknown page.")
        mode, data = v_schema(j.get("mode", "auto"), j.get("json"))
        if mode == "auto":
            STORE.hdel("schema", k)
        else:
            STORE.hset("schema", k, {"mode": mode, "json": data, "updated": int(time.time())})
        STORE.hset("lastmod", k, time.strftime("%Y-%m-%d", time.gmtime()))
        audit(request, s["email"], "schema_saved", {"page": k, "mode": mode})
        return admin_json({"ok": True})

    @app.post("/api/admin/schema/validate", include_in_schema=False)
    async def a_schema_validate(request: Request):
        require(request, True); j = await body(request)
        return admin_json({"results": validate_schema_text(str(j.get("json", ""))[:20000])})

    @app.get("/api/admin/schema/{key}", include_in_schema=False)
    def a_schema_get(key: str, request: Request):
        require(request)
        if key not in wr_seo.SEO:
            raise HTTPException(404, "Unknown page.")
        base = wr_seo.site_base(request)
        eff = wr_seo.effective(key)
        return admin_json({"auto": wr_seo.auto_schema(key, base, eff), "current": wr_seo.overrides("schema").get(key, {"mode": "auto"})})

    @app.post("/api/admin/redirects", include_in_schema=False)
    async def a_redirect(request: Request):
        s = require(request, True); need_store(); j = await body(request)
        if j.get("delete"):
            frm = wr_seo.norm_path(j.get("from"))
            STORE.hdel("redirects", frm)
            audit(request, s["email"], "redirect_deleted", {"from": frm})
            return admin_json({"ok": True, "redirects": wr_seo.overrides("redirects")})
        if len(wr_seo.overrides("redirects")) >= 500:
            raise HTTPException(400, "You can keep up to 500 redirects.")
        frm, to, code = v_redirect(j.get("from"), j.get("to"), j.get("code"))
        STORE.hset("redirects", frm, {"to": to, "code": code, "created": int(time.time()), "by": s["email"]})
        audit(request, s["email"], "redirect_saved", {"from": frm, "to": to, "code": code})
        return admin_json({"ok": True, "redirects": wr_seo.overrides("redirects")})

    @app.get("/api/admin/404s", include_in_schema=False)
    def a_404(request: Request):
        require(request); need_store()
        flat = STORE.cmd("ZREVRANGE", STORE.k("404"), 0, 99, "WITHSCORES") or []
        meta = STORE.hall("404meta")
        hits = STORE.cmd("ZREVRANGE", STORE.k("redirect_hits"), 0, 99, "WITHSCORES") or []
        return admin_json({"rows": [{"path": flat[i], "count": int(float(flat[i + 1])), **(meta.get(flat[i]) or {})} for i in range(0, len(flat), 2)],
                           "scanners": int(STORE.cmd("GET", STORE.k("404_scanners")) or 0),
                           "redirect_hits": {hits[i]: int(float(hits[i + 1])) for i in range(0, len(hits), 2)}})

    @app.post("/api/admin/404s/clear", include_in_schema=False)
    def a_404_clear(request: Request):
        s = require(request, True); need_store()
        STORE.cmd("DEL", STORE.k("404"), STORE.k("404meta"), STORE.k("404_scanners"))
        audit(request, s["email"], "404_log_cleared")
        return admin_json({"ok": True})

    @app.get("/api/admin/audit", include_in_schema=False)
    def a_audit(request: Request):
        require(request); need_store()
        return admin_json({"rows": STORE.lrange("audit", 300)})

    @app.get("/api/admin/health", include_in_schema=False)
    def a_health(request: Request):
        require(request)
        if not STORE.ok:
            return admin_json({"report": None, "history": []})
        return admin_json({"report": STORE.get_json("health"), "history": STORE.lrange("health_hist", 60)})

    @app.post("/api/admin/health/run", include_in_schema=False)
    def a_health_run(request: Request):
        s = require(request, True)
        rate_limit("health-run", 20, 3600)
        rep = run_health(wr_seo.site_base(request))
        audit(request, s["email"], "seo_check_run", rep["summary"])
        return admin_json({"report": rep, "history": STORE.lrange("health_hist", 60) if STORE.ok else []})

    def gwrap(fn):
        try:
            return admin_json(fn())
        except GoogleError as e:
            return admin_json({"error": str(e)}, 409)
        except httpx.HTTPError as e:
            return admin_json({"error": f"Couldn’t reach Google ({e.__class__.__name__}). Try again."}, 502)

    @app.get("/api/admin/gsc/sites", include_in_schema=False)
    def a_gsc_sites(request: Request):
        require(request)
        return gwrap(lambda: {"sites": gapi("GET", f"{SC}/webmasters/v3/sites").get("siteEntry", [])})

    @app.get("/api/admin/gsc/summary", include_in_schema=False)
    def a_gsc_summary(request: Request, days: int = 28):
        require(request)
        return gwrap(lambda: gsc_summary(min(max(days, 7), 480)))

    @app.get("/api/admin/gsc/sitemaps", include_in_schema=False)
    def a_gsc_sitemaps(request: Request):
        require(request)
        return gwrap(lambda: {"sitemaps": gsc_sitemaps(), "ours": wr_seo.site_base(request) + "/sitemap.xml"})

    @app.post("/api/admin/gsc/sitemaps/submit", include_in_schema=False)
    def a_gsc_submit(request: Request):
        s = require(request, True)
        feed = wr_seo.site_base(request) + "/sitemap.xml"
        def go():
            gsc_submit(feed); audit(request, s["email"], "sitemap_submitted", {"feed": feed}); return {"ok": True, "feed": feed}
        return gwrap(go)

    @app.post("/api/admin/gsc/inspect", include_in_schema=False)
    async def a_gsc_inspect(request: Request):
        s = require(request, True); need_store(); j = await body(request)
        rate_limit("inspect", 30, 3600)
        keys = [k for k in (j.get("keys") or list(wr_seo.SEO)) if k in wr_seo.SEO][:60]
        base = wr_seo.site_base(request)
        def go():
            res = {}
            def one(k):
                return k, gsc_inspect(base + wr_seo.path_of(k))
            with ThreadPoolExecutor(4) as ex:
                for k, r in ex.map(one, keys):
                    res[k] = r; STORE.hset("inspect", k, r)
            audit(request, s["email"], "index_inspection", {"pages": len(res)})
            return {"results": res}
        return gwrap(go)

    @app.get("/api/admin/ga/properties", include_in_schema=False)
    def a_ga_props(request: Request):
        require(request)
        return gwrap(lambda: {"properties": ga_properties()})

    @app.get("/api/admin/ga/summary", include_in_schema=False)
    def a_ga_summary(request: Request, days: int = 28):
        require(request)
        return gwrap(lambda: ga_summary(min(max(days, 7), 365)))

    @app.post("/api/admin/google/disconnect", include_in_schema=False)
    def a_google_disconnect(request: Request):
        s = require(request, True); need_store()
        link = google_link()
        if link and link.get("rt"):
            try:
                HTTP.post(G_REVOKE, data={"token": decrypt(link["rt"])})
            except Exception:
                pass
        STORE.cmd("DEL", STORE.k("google")); _token_cache.clear()
        audit(request, s["email"], "google_disconnected")
        return admin_json({"ok": True})

    @app.post("/api/admin/logout", include_in_schema=False)
    def a_logout(request: Request):
        s = require(request, True)
        resp = admin_json({"ok": True})
        audit(request, s["email"], "logout")
        end_session(request, resp)
        return resp

    @app.get("/api/cron/seo-check", include_in_schema=False)
    def cron(request: Request):
        sec = cfg()["cron"]
        if not sec or not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {sec}"):
            raise HTTPException(401, "Unauthorized")
        rep = run_health(wr_seo.site_base(request))
        return JSONResponse({"ok": True, "summary": rep["summary"]}, headers={"Cache-Control": "no-store"})
