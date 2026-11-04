"""Admin operations and public controls. No credentials or visitor links are stored here."""
import copy
import hashlib
import hmac
import html
import json
import re
import secrets
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qs

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from wr_store import STORE, StoreError

TOOLS = {}
SECTIONS = {
    "owner": ["*"],
    "editor": ["overview", "gsc", "ga", "sitemap", "meta", "schema", "indexing", "health", "e404", "redirects", "guides", "announcements"],
    "support": ["overview", "messages", "downloadstats", "alerts"],
}
DEFAULTS = {
    "tools": {},
    "batch": {"max_links": 10, "concurrency": 1, "cooldown": 0, "requests_per_minute": 120},
    "announcement": {"enabled": False, "text": "", "start": 0, "end": 0, "pause_downloads": False},
    "alerts": {"enabled": True, "window_minutes": 15, "min_requests": 10, "failure_percent": 50},
    "ads": {"enabled": True, "client": "", "slot": "", "pages": ["home", "reduce", "passport", "signature", "namedate", "topdf", "daily", "festival", "bgremove", "guides"],
            "placements": ["hub", "tool", "side", "guide", "guides"]},
}
ENDPOINTS = {"mp3": "mp3", "ringtone": "ringtone", "clip": "clip", "gif": "gif", "shrink": "shrink",
             "convert": "video", "frame": "frame", "status": "status", "sticker": "sticker"}
RELEASE = "-- wr-release\nif redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"
RESTORE = """-- wr-restore
local rows = cjson.decode(ARGV[1])
for i,key in ipairs(KEYS) do
 redis.call('DEL',key)
 if rows[i].kind == 'json' then redis.call('SET',key,rows[i].value)
 else for field,value in pairs(rows[i].value) do redis.call('HSET',key,field,value) end end
end return 1"""


def configure(seo):
    TOOLS.clear()
    TOOLS.update({k: {"name": row.get("h", k), "path": "/" + row["s"]} for k, row in seo.items()
                  if k not in {"home", "about", "contact", "faq", "privacy", "terms", "dmca"}})


def config():
    def load():
        saved = STORE.hall("management") if STORE.ok else {}
        return {k: {**copy.deepcopy(v), **saved.get(k, {})} for k, v in DEFAULTS.items()}
    try:
        return STORE.cached("management", load, ttl=5)
    except StoreError:
        return copy.deepcopy(DEFAULTS)


def role_for(email, owners):
    if email.lower() in owners:
        return "owner"
    try:
        return STORE.hall("admin_roles").get(email.lower()) if STORE.ok else None
    except StoreError:
        return None


def authorize(path, role, method="GET"):
    if role == "owner":
        return
    leaf = path.removeprefix("/api/admin/")
    if leaf in ("state", "logout"):
        return
    if leaf == "manage/config" and method == "GET":
        return
    if leaf.startswith("manage/"):
        section = leaf.split("/")[1]
        if leaf in ("manage/config/announcement", "manage/config/announcements"):
            section = "announcements"
        section = {"stats": "downloadstats", "config": "owner", "roles": "owner", "backups": "owner", "history": "owner"}.get(section, section)
    elif leaf.startswith("gsc/"):
        section = "gsc"
    elif leaf.startswith("ga/"):
        section = "ga"
    else:
        section = {"404s": "e404", "health": "health", "schema": "schema", "messages": "messages"}.get(leaf.split("/")[0], leaf)
    if section not in SECTIONS.get(role, []):
        raise HTTPException(403, "Your admin role does not allow this action.")


def integer(value, minimum, maximum, label):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise HTTPException(400, f"{label} must be a whole number from {minimum} to {maximum}.")
    return value


def flag(value, label):
    if not isinstance(value, bool):
        raise HTTPException(400, f"{label} must be on or off.")
    return value


def validate(section, data):
    if not isinstance(data, dict) or section not in DEFAULTS or set(data) - set(DEFAULTS[section]) and section != "tools":
        raise HTTPException(400, "Unknown management settings.")
    row = {**copy.deepcopy(DEFAULTS[section]), **data}
    if section == "tools":
        if set(data) - set(TOOLS): raise HTTPException(400, "Unknown tool.")
        return {k: flag(v, k) for k, v in data.items()}
    if section == "batch":
        for key, limits in {"max_links": (1, 20), "concurrency": (1, 3), "cooldown": (0, 3600), "requests_per_minute": (10, 600)}.items():
            row[key] = integer(row[key], *limits, key)
    if section == "announcement":
        row["enabled"] = flag(row["enabled"], "Enabled")
        row["pause_downloads"] = flag(row["pause_downloads"], "Pause downloads")
        if not isinstance(row["text"], str) or len(row["text"]) > 500 or (row["enabled"] and not row["text"].strip()):
            raise HTTPException(400, "Enter an announcement up to 500 characters.")
        row["text"] = row["text"].strip()
        for key in ("start", "end"): row[key] = integer(row[key], 0, 4102444800, key)
        if row["end"] and row["end"] <= row["start"]: raise HTTPException(400, "End time must follow start time.")
    if section == "alerts":
        row["enabled"] = flag(row["enabled"], "Enabled")
        for key, limits in {"window_minutes": (5, 60), "min_requests": (3, 500), "failure_percent": (1, 100)}.items():
            row[key] = integer(row[key], *limits, key)
    if section == "ads":
        row["enabled"] = flag(row["enabled"], "Enabled")
        if not isinstance(row["client"], str) or (row["client"] and not re.fullmatch(r"ca-pub-\d{10,20}", row["client"])):
            raise HTTPException(400, "Use an AdSense publisher ID such as ca-pub-1234567890123456.")
        if not isinstance(row["slot"], str) or (row["slot"] and not re.fullmatch(r"\d{6,14}", row["slot"])):
            raise HTTPException(400, "Use a numeric AdSense slot ID.")
        for key, allowed in [("pages", {"home", "guides", *TOOLS}), ("placements", {"hub", "tool", "side", "guide", "guides"})]:
            if not isinstance(row[key], list) or len(row[key]) > 50 or any(not isinstance(v, str) or v not in allowed for v in row[key]):
                raise HTTPException(400, "Unknown ad page or placement.")
            row[key] = list(dict.fromkeys(row[key]))
    return row


def active_announcement():
    row = config()["announcement"]
    return row if row["enabled"] and (not row["start"] or row["start"] <= time.time()) and (not row["end"] or time.time() < row["end"]) else None


def ensure_tool(key):
    if config()["tools"].get(key) is False:
        raise HTTPException(503, "This tool is temporarily unavailable.")


def ensure_download(url, video=True, count=1):
    source = platform(url)
    key = "facebook" if source == "facebook" else "x" if source == "x" else "threads" if source == "threads" else "reels" if "/reel" in url else "carousel" if count > 1 else "video" if video else "photo"
    ensure_tool(key)


def public_config():
    cfg = config()
    return {"tools": cfg["tools"], "paths": {k: v["path"] for k, v in TOOLS.items()}, "batch": cfg["batch"], "announcement": active_announcement()}


def inject(page):
    import wr_guides as WG
    page = re.sub(r'(<section class="hubguides"[^>]*><div class="hgrow">.*?</div>).*?(</section>)',
                  lambda m: m[1] + WG.home_cards_html() + m[2], page, flags=re.S)
    page = re.sub(r'(<section class="sbox"><h2 data-i="pop_g">.*?</h2>).*?(</section>)',
                  lambda m: m[1] + WG.popular_html() + m[2], page, flags=re.S)
    payload = json.dumps(public_config(), ensure_ascii=False).replace("<", "\\u003c")
    return page.replace('<script type="application/json" id="wr-management">{}</script>',
                        '<script type="application/json" id="wr-management">' + payload + '</script>')


def platform(url):
    from urllib.parse import urlparse
    host = (urlparse(str(url)).hostname or "").lower()
    return next((name for name, domains in [("instagram", ("instagram.com",)), ("facebook", ("facebook.com", "fb.watch")),
                ("x", ("x.com", "twitter.com")), ("youtube", ("youtube.com", "youtu.be")), ("threads", ("threads.net", "threads.com"))]
                 if any(host == d or host.endswith("." + d) for d in domains)), "other")


def pairs(flat):
    return flat if isinstance(flat, dict) else dict(zip((flat or [])[::2], (flat or [])[1::2]))


def record(source, operation, status, duration, size):
    if not STORE.ok: return
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key, minute = STORE.k("downloads:" + day), STORE.k("download_errors:" + str(int(time.time() // 60)))
    outcome = "success" if 200 <= status < 300 else "failure"
    fields = {"requests": 1, outcome: 1, "milliseconds": max(0, int(duration * 1000)), "bytes": size}
    commands = [("HINCRBY", key, f"{source}:{operation}:{k}", v) for k, v in fields.items()]
    commands += [("EXPIRE", key, 90 * 86400), ("HINCRBY", minute, source + ":requests", 1),
                 ("HINCRBY", minute, source + ":" + outcome, 1), ("EXPIRE", minute, 7200)]
    try: STORE.batch(commands)
    except StoreError: pass


def statistics(days):
    dates = [(datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)][::-1]
    rows, totals = [], {}
    if STORE.ok:
        for day, flat in zip(dates, STORE.batch([("HGETALL", STORE.k("downloads:" + day)) for day in dates])):
            groups = {}
            for key, value in pairs(flat).items():
                source, operation, metric = key.split(":")
                group = groups.setdefault((source, operation), {})
                group[metric] = int(value)
            for (source, operation), metrics in groups.items():
                row = {"day": day, "platform": source, "operation": operation, **metrics}
                rows.append(row)
                total = totals.setdefault(source, {"requests": 0, "success": 0, "failure": 0, "milliseconds": 0, "bytes": 0})
                for key in total: total[key] += metrics.get(key, 0)
    return {"available": STORE.ok, "days": days, "rows": rows, "platforms": totals}


def alerts():
    cfg, counts = config()["alerts"], {}
    if not STORE.ok: return [{"id": "storage", "platform": "site", "message": "Connect Redis to retain download statistics and error alerts.", "level": "warning"}]
    if not cfg["enabled"]: return []
    minutes = range(int(time.time() // 60) - cfg["window_minutes"] + 1, int(time.time() // 60) + 1)
    rows = STORE.batch([("HGETALL", STORE.k("download_errors:" + str(m))) for m in minutes])
    for flat in rows:
        for key, value in pairs(flat).items():
            source, metric = key.split(":")
            values = counts.setdefault(source, {"requests": 0, "failure": 0})
            if metric in values: values[metric] += int(value)
    acks, out = STORE.hall("alert_acks"), []
    for source, count in counts.items():
        if count["requests"] >= cfg["min_requests"] and count["failure"] * 100 / count["requests"] >= cfg["failure_percent"]:
            ident = source + ":" + str(int(time.time() // 3600))
            out.append({"id": ident, "platform": source, "level": "error", "message": f"{source}: {count['failure']} of {count['requests']} requests failed in the last {cfg['window_minutes']} minutes.",
                        "acknowledged": acks.get(ident)})
    return out


def guide_doc(raw):
    import wr_guides as WG
    if not isinstance(raw, dict): raise HTTPException(400, "Send a guide object.")
    doc = {k: raw.get(k, "") for k in ("slug", "title", "desc", "lead", "cat", "tool")}
    for key, lo, hi in [("slug", 3, 90), ("title", 3, 160), ("desc", 10, 300), ("lead", 10, 1000)]:
        if not isinstance(doc[key], str) or not lo <= len(doc[key].strip()) <= hi:
            raise HTTPException(400, f"Guide {key} must contain {lo}–{hi} characters.")
        doc[key] = doc[key].strip()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", doc["slug"]) or doc["cat"] not in WG.CAT_NAME or doc["tool"] not in TOOLS:
        raise HTTPException(400, "Choose a valid slug, category and tool.")
    sections = raw.get("sections")
    if not isinstance(sections, list) or not 1 <= len(sections) <= 12: raise HTTPException(400, "Add 1–12 guide sections.")
    doc["sections"] = []
    for section in sections:
        if not isinstance(section, dict) or not isinstance(section.get("heading"), str) or not isinstance(section.get("body"), str):
            raise HTTPException(400, "Each section needs a heading and text.")
        if not 1 <= len(section["heading"].strip()) <= 160 or not 1 <= len(section["body"].strip()) <= 3000:
            raise HTTPException(400, "Section headings allow 160 characters; section text allows 3,000.")
        doc["sections"].append({"heading": section["heading"].strip(), "body": section["body"].strip()})
    return doc


def guide_public(doc):
    return {**doc, "icon": doc["cat"], "read": max(1, sum(len(s["body"].split()) for s in doc["sections"]) // 180),
            "tool": (TOOLS.get(doc["tool"], {}).get("name", "Tool"), TOOLS.get(doc["tool"], {}).get("path", "/")), "related": [],
            "sections": [("section-" + str(i), s["heading"], "<p>" + html.escape(s["body"]).replace("\n", "<br>") + "</p>") for i, s in enumerate(doc["sections"], 1)]}


def guide_rows():
    import wr_guides as WG
    stored = STORE.hall("managed_guides") if STORE.ok else {}
    rows = {}
    for guide in WG.GUIDES:
        strip = lambda value: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value))).strip()
        doc = {k: guide[k] for k in ("slug", "title", "desc", "lead", "cat")}
        doc["tool"] = next((key for key, item in TOOLS.items() if item["path"] == guide["tool"][1]), "video")
        doc["sections"] = [{"heading": heading, "body": strip(body)} for _, heading, body in guide["sections"]]
        rows[guide["slug"]] = {"draft": doc, "builtin": True, "published": True}
    for slug, value in stored.items():
        rows[slug] = {**rows.get(slug, {}), **value, "published": bool(value.get("live")) or (slug in WG.BY_SLUG and not value.get("hidden"))}
    return rows


def snapshot():
    import wr_seo
    STORE.drop("management")
    settings = STORE.get_json("settings", {})
    payload = {"management": config(), "settings": STORE.get_json("settings", {}),
               **{key: STORE.hall(key) for key in ("meta", "schema", "redirects", "managed_guides")}}
    payload["settings"] = {k: settings[k] for k in wr_seo.DEFAULT_SETTINGS if k in settings}
    return {"version": 1, "created": int(time.time()), "payload": payload}


def signed_snapshot():
    import wr_admin as A
    row = snapshot()
    row["signature"] = hmac.new(A._key("backup"), json.dumps(row, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
    return row


def checkpoint(who, label):
    if STORE.ok:
        STORE.push("manage_history", {"id": secrets.token_hex(8), "who": who, "label": label, "backup": signed_snapshot()}, keep=20)


def restore(backup):
    import wr_admin as A
    if not isinstance(backup, dict) or set(backup) != {"version", "created", "payload", "signature"}:
        raise HTTPException(400, "Choose a WaveRise settings backup.")
    signed = {k: v for k, v in backup.items() if k != "signature"}
    expected = hmac.new(A._key("backup"), json.dumps(signed, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()
    if backup["version"] != 1 or not isinstance(backup["signature"], str) or not hmac.compare_digest(backup["signature"], expected):
        raise HTTPException(400, "This backup was changed or belongs to a different session-secret configuration.")
    payload = backup["payload"]
    if not isinstance(payload, dict) or set(payload) != {"management", "settings", "meta", "schema", "redirects", "managed_guides"}:
        raise HTTPException(400, "Unsupported backup contents.")
    for section in DEFAULTS: validate(section, payload["management"].get(section, {}))
    keys, rows = [], []
    for name, value in payload.items():
        keys.append(STORE.k(name))
        kind = "json" if name == "settings" else "hash"
        encoded = json.dumps(value) if kind == "json" else {k: json.dumps(v) for k, v in value.items()}
        rows.append({"kind": kind, "value": encoded})
    STORE.cmd("EVAL", RESTORE, len(keys), *keys, json.dumps(rows))
    STORE._cache.clear()


class PublicControls:
    def __init__(self, app): self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http": return await self.app(scope, receive, send)
        path = scope["path"].rstrip("/") or "/"
        leaf = path.removeprefix("/api/").lstrip("/")
        operation = "info" if leaf == "info" else "download" if leaf in {"download", *ENDPOINTS} else None
        query = parse_qs(scope.get("query_string", b"").decode(errors="replace"))
        headers = dict(scope.get("headers", []))
        url = (query.get("url") or [""])[0]
        start, status, size, finished, body, lease = time.monotonic(), 500, 0, False, bytearray(), None
        request = Request(scope)
        try:
            page_key = next((k for k, row in TOOLS.items() if row["path"] == path), None)
            if page_key: ensure_tool(page_key)
            if operation:
                active = active_announcement()
                if active and active["pause_downloads"]: raise HTTPException(503, active["text"])
                if leaf in ENDPOINTS: ensure_tool(ENDPOINTS[leaf])
                import wr_admin as A
                ident = hashlib.sha256(A.client_ip(request).encode()).hexdigest()[:24]
                await run_in_threadpool(A.rate_limit, "downloads:" + ident, config()["batch"]["requests_per_minute"], 60)
                token = headers.get(b"x-waverise-batch", b"").decode()
                if token:
                    ensure_tool("batch")
                    job = STORE.get_json("batch:" + token) if STORE.ok else None
                    if not job or job["ip"] != ident: raise HTTPException(403, "The batch expired. Start a new batch.")
                    if operation == "download":
                        if url not in job["urls"]: raise HTTPException(403, "This link is not in the batch.")
                        value = secrets.token_hex(12)
                        for slot in range(config()["batch"]["concurrency"]):
                            key = STORE.k("batch_slot:" + ident + ":" + str(slot))
                            if STORE.cmd("SET", key, value, "NX", "EX", 3600): lease = (key, value); break
                        if lease is None: raise HTTPException(429, "The batch processing limit was reached. Retry the post after the current download finishes.")
        except HTTPException as exc:
            status = exc.status_code
            response = JSONResponse({"detail": exc.detail}, status_code=status, headers={"Cache-Control": "no-store", "Retry-After": "60"}) if operation else HTMLResponse(
                '<!doctype html><title>Tool unavailable</title><h1>Temporarily unavailable</h1><p>' + html.escape(str(exc.detail)) + '</p><a href="/">Back to WaveRise</a>', status_code=status, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})
            await response(scope, receive, send)
            if operation: await run_in_threadpool(record, platform(url), operation, status, 0, 0)
            return
        except StoreError:
            response = JSONResponse({"detail": "The batch service is temporarily unavailable."}, status_code=503)
            return await response(scope, receive, send)
        async def read():
            message = await receive()
            if message["type"] == "http.request" and len(body) < 8192: body.extend(message.get("body", b"")[:8192 - len(body)])
            return message
        async def write(message):
            nonlocal status, size, finished
            if message["type"] == "http.response.start": status = message["status"]
            if message["type"] == "http.response.body":
                size += len(message.get("body", b"")); finished = not message.get("more_body", False)
            await send(message)
        try:
            await self.app(scope, read, write)
        finally:
            if lease:
                try: STORE.cmd("EVAL", RELEASE, 1, *lease)
                except StoreError: pass
            if operation:
                if not url and body:
                    try: url = json.loads(body).get("url", "")
                    except (ValueError, AttributeError): pass
                await run_in_threadpool(record, platform(url), operation, status if finished else 499, time.monotonic() - start, size)


def install(app, admin, backend):
    configure(backend.SEO)

    @app.get("/api/site-config", include_in_schema=False)
    def site_config(): return JSONResponse(public_config(), headers={"Cache-Control": "no-store"})

    @app.post("/api/batches", include_in_schema=False)
    async def create_batch(request: Request):
        ensure_tool("batch")
        data = await body(request)
        urls = data.get("urls") if isinstance(data, dict) else None
        cfg = config()["batch"]
        if not isinstance(urls, list) or not 1 <= len(urls) <= cfg["max_links"] or any(not isinstance(u, str) or len(u) > 2000 for u in urls):
            raise HTTPException(400, f"Add 1–{cfg['max_links']} public post links.")
        # Validate using the same supported public-post grammar as individual downloads.
        for url in urls:
            if not any(rx.match(url) for rx in (backend.IG_URL, backend.X_URL, backend.FB_URL)):
                raise HTTPException(400, "Use public Instagram, Facebook or X post links.")
        ident = hashlib.sha256(admin.client_ip(request).encode()).hexdigest()[:24]
        admin.rate_limit("batch_submit:" + ident, 20, 60)
        if cfg["cooldown"]: admin.rate_limit("batch_start:" + ident, 1, cfg["cooldown"])
        token = secrets.token_urlsafe(24) if STORE.ok else ""
        if token: STORE.set_json("batch:" + token, {"ip": ident, "urls": urls}, ex=3600)
        return JSONResponse({"token": token, "limits": cfg}, headers={"Cache-Control": "no-store"})

    async def body(request):
        raw = await request.body()
        if len(raw) > 2 * 1024 * 1024: raise HTTPException(413, "This configuration file is too large.")
        try: data = json.loads(raw)
        except ValueError: raise HTTPException(400, "Send a JSON object.")
        if not isinstance(data, dict): raise HTTPException(400, "Send a JSON object.")
        return data

    @app.get("/api/admin/manage/config", include_in_schema=False)
    def get_config(request: Request):
        admin.require(request)
        return admin.admin_json({"config": config(), "tools": TOOLS, "storage": STORE.ok})

    @app.post("/api/admin/manage/config/{section}", include_in_schema=False)
    async def save_config(section: str, request: Request):
        session = admin.require(request, True); admin.need_store()
        if section == "announcements": section = "announcement"
        # An editor may only change announcements through this shared configuration endpoint.
        if session["role"] != "owner" and section != "announcement": raise HTTPException(403, "Only an owner can change these settings.")
        row = validate(section, await body(request))
        checkpoint(session["email"], "Before " + section)
        STORE.hset("management", section, row)
        cfg = config()
        admin.audit(request, session["email"], section + "_changed")
        return admin.admin_json({"ok": True, "config": cfg})

    @app.get("/api/admin/manage/stats", include_in_schema=False)
    def get_stats(request: Request, days: int = 7):
        admin.require(request)
        return admin.admin_json(statistics(integer(days, 1, 31, "Days")))

    @app.get("/api/admin/manage/alerts", include_in_schema=False)
    def get_alerts(request: Request):
        admin.require(request)
        return admin.admin_json({"rows": alerts(), "settings": config()["alerts"]})

    @app.post("/api/admin/manage/alerts/ack", include_in_schema=False)
    async def ack_alert(request: Request):
        session = admin.require(request, True); admin.need_store(); data = await body(request)
        ident = data.get("id")
        if ident not in {a["id"] for a in alerts()}: raise HTTPException(404, "This alert is no longer active.")
        STORE.hset("alert_acks", ident, {"at": int(time.time()), "by": session["email"]})
        STORE.cmd("EXPIRE", STORE.k("alert_acks"), 7 * 86400)
        admin.audit(request, session["email"], "alert_acknowledged", {"id": ident})
        return admin.admin_json({"ok": True})

    @app.get("/api/admin/manage/roles", include_in_schema=False)
    def get_roles(request: Request):
        admin.require(request); admin.need_store()
        rows = {email: {"email": email, "role": role, "bootstrap": False} for email, role in STORE.hall("admin_roles").items()}
        rows.update({email: {"email": email, "role": "owner", "bootstrap": True} for email in admin.cfg()["admins"]})
        return admin.admin_json({"rows": list(rows.values()), "permissions": SECTIONS})

    @app.post("/api/admin/manage/roles", include_in_schema=False)
    async def save_role(request: Request):
        session = admin.require(request, True); admin.need_store(); data = await body(request)
        email, role = str(data.get("email", "")).strip().lower(), data.get("role")
        if not re.fullmatch(r"[^@\s]{1,100}@[^@\s.]+(?:\.[^@\s.]+)+", email): raise HTTPException(400, "Enter a valid Google email address.")
        if email in admin.cfg()["admins"]: raise HTTPException(400, "Environment owners cannot be changed here.")
        if email == session["email"]: raise HTTPException(400, "An owner cannot remove or change their own access.")
        if role not in {"owner", "editor", "support", "remove"}: raise HTTPException(400, "Choose Owner, Editor or Support.")
        if role == "remove": STORE.hdel("admin_roles", email)
        else: STORE.hset("admin_roles", email, role)
        admin.audit(request, session["email"], "admin_role_changed", {"email": email, "role": role})
        return admin.admin_json({"ok": True})

    @app.get("/api/admin/manage/guides", include_in_schema=False)
    def get_guides(request: Request):
        admin.require(request)
        import wr_guides as WG
        return admin.admin_json({"rows": guide_rows(), "categories": WG.CATS, "tools": TOOLS})

    @app.post("/api/admin/manage/guides", include_in_schema=False)
    async def save_guide(request: Request):
        session = admin.require(request, True); admin.need_store(); data = await body(request)
        action = data.get("action")
        if action not in {"draft", "publish", "unpublish", "delete"}: raise HTTPException(400, "Unknown guide action.")
        doc = guide_doc(data.get("guide")); slug = doc["slug"]
        if len(guide_rows()) >= 50 and slug not in guide_rows(): raise HTTPException(400, "The guide limit is 50.")
        checkpoint(session["email"], "Before guide " + slug)
        row = STORE.hall("managed_guides").get(slug, {})
        if action == "delete": STORE.hdel("managed_guides", slug)
        else:
            row.update(draft=doc, updated=int(time.time()), by=session["email"])
            if action == "publish":
                import wr_guides as WG
                today = datetime.now(timezone.utc).date().isoformat()
                doc["_published"] = (row.get("live") or {}).get("_published") or (WG.UPDATED if slug in WG.BY_SLUG else today)
                doc["_updated"] = today
                row.update(live=copy.deepcopy(doc), hidden=False)
            if action == "unpublish": row.update(live=None, hidden=True)
            all_guides = STORE.hall("managed_guides")
            all_guides[slug] = row
            if len(json.dumps(all_guides, ensure_ascii=False).encode()) > 1024 * 1024:
                raise HTTPException(413, "Guide drafts and publications together must stay under 1 MB so settings backups remain portable.")
            STORE.hset("managed_guides", slug, row)
        admin.audit(request, session["email"], "guide_" + action, {"slug": slug})
        return admin.admin_json({"ok": True, "preview": guide_public(doc)})

    @app.get("/api/admin/manage/backups", include_in_schema=False)
    def export_backup(request: Request):
        admin.require(request); admin.need_store()
        return admin.admin_json(signed_snapshot())

    @app.get("/api/admin/manage/history", include_in_schema=False)
    def history(request: Request):
        admin.require(request); admin.need_store()
        return admin.admin_json({"rows": [{"id": r["id"], "who": r["who"], "label": r["label"], "created": r["backup"]["created"]} for r in STORE.lrange("manage_history", 20)]})

    @app.post("/api/admin/manage/history", include_in_schema=False)
    async def create_checkpoint(request: Request):
        session = admin.require(request, True); admin.need_store()
        checkpoint(session["email"], "Manual restore point")
        admin.audit(request, session["email"], "restore_point_created")
        return admin.admin_json({"ok": True})

    @app.post("/api/admin/manage/backups/restore", include_in_schema=False)
    async def restore_backup(request: Request):
        session = admin.require(request, True); admin.need_store(); data = await body(request)
        backup = data.get("backup")
        if data.get("id"):
            backup = next((r["backup"] for r in STORE.lrange("manage_history", 20) if r["id"] == data["id"]), None)
        # Validate signature and content before recording or applying any restore.
        before = signed_snapshot()
        restore(backup)
        STORE.push("manage_history", {"id": secrets.token_hex(8), "who": session["email"], "label": "Before restore", "backup": before}, keep=20)
        admin.audit(request, session["email"], "settings_restored")
        return admin.admin_json({"ok": True})

    app.add_middleware(PublicControls)
