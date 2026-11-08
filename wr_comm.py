"""WaveRise Ringtones & Wallpapers: our own small backend (no Supabase).

People sign in with Google (the same Google client as the admin area), files go to a public Vercel Blob store,
and records live in the Upstash Redis database the admin area already uses.
Switched on when the database, the Blob token, the Google client and SESSION_SECRET are all present.
"""
import hashlib, json, os, re, secrets, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

import wr_admin as A
import wr_seo
from wr_store import STORE, StoreError

R = APIRouter()
UCOOKIE, UOAUTH = "wr_user", "wr_uoauth"
UTTL = 60 * 60 * 24 * 60
HTTP = httpx.Client(timeout=30)
BLOB_API = "https://vercel.com/api/blob"
LOCAL_DIR = Path("/tmp/wr-blob")
PAGE = 24
CATS = {"ringtone": {"bgm", "love", "devotional", "mass", "sad", "funny", "instrumental", "notification", "other"},
        "wallpaper": {"nature", "god", "love", "quotes", "abstract", "amoled", "festival", "cars", "other"}}
AUDIO = {"audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac", "audio/ogg": "ogg"}
MAX_AUDIO, MAX_IMG, MAX_THUMB = 4 * 1024 * 1024, 3 * 1024 * 1024, 400 * 1024
REASONS = {"copyright", "nudity", "hate", "spam", "other"}
UNAME = re.compile(r"^[a-z0-9_.]{3,20}$")


def hosted():
    return bool(os.getenv("VERCEL") or os.getenv("K_SERVICE"))


def blob_token():
    t = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
    if not t:   # Vercel lets you pick a custom prefix when connecting a Blob store
        for k in sorted(os.environ):
            if k.endswith("_READ_WRITE_TOKEN") and os.environ[k].strip().startswith("vercel_blob_rw_"):
                return os.environ[k].strip()
    return t


def ready():
    c = A.cfg()
    return bool(STORE.ok and c["client_id"] and c["client_secret"] and len(c["secret"]) >= 32 and (blob_token() or not hosted()))


def k(name):
    return STORE.k("c:" + name)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def need_ready():
    if not ready():
        raise HTTPException(503, "Ringtones and wallpapers are being set up. Please check back soon.")


def same_site(request: Request):
    """Writes must come from our own pages (the cookie is SameSite=Lax too)."""
    o = request.headers.get("origin")
    if o and o.split("://", 1)[-1].lower() != A.host_of(request):
        raise HTTPException(403, "Please use the WaveRise website.")


# ---------------- files (Vercel Blob, or a local folder when testing) ----------------
def blob_put(path, data: bytes, ctype: str):
    tok = blob_token()
    if not tok:
        if hosted():
            raise HTTPException(503, "File storage isn't connected yet.")
        p = LOCAL_DIR / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return "/api/c/devfile/" + path
    store_id = (tok.split("_") + ["", "", "", ""])[3]
    try:
        r = HTTP.put(BLOB_API + "/?" + urlencode({"pathname": path}), content=data, headers={
            "authorization": f"Bearer {tok}", "x-api-version": "13", "x-vercel-blob-store-id": store_id,
            "x-content-type": ctype, "x-add-random-suffix": "1", "x-vercel-blob-access": "public",
            "x-cache-control-max-age": "31536000", "x-content-length": str(len(data))})
        if r.status_code == 200:
            return r.json()["url"]
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    raise HTTPException(502, "Couldn't save the file. Please try again.")


def blob_del(urls):
    urls = [u for u in urls if u]
    if not urls:
        return
    tok = blob_token()
    if not tok:
        for u in urls:
            if u.startswith("/api/c/devfile/"):
                (LOCAL_DIR / u[len("/api/c/devfile/"):]).unlink(missing_ok=True)
        return
    store_id = (tok.split("_") + ["", "", "", ""])[3]
    try:
        HTTP.post(BLOB_API + "/delete", json={"urls": urls}, headers={
            "authorization": f"Bearer {tok}", "x-api-version": "13", "x-vercel-blob-store-id": store_id})
    except httpx.HTTPError:
        pass


# ---------------- people ----------------
def get_user(uid):
    return STORE.get_json(f"c:user:{uid}") if uid else None


def save_user(u):
    STORE.set_json(f"c:user:{u['id']}", u)


def current(request: Request):
    raw = A.unsign(request.cookies.get(UCOOKIE, ""), "user")
    if not raw or "|" not in raw or len(A.cfg()["secret"]) < 32:
        return None
    uid, exp = raw.rsplit("|", 1)
    if not exp.isdigit() or int(exp) < time.time():
        return None
    try:
        return get_user(uid)
    except StoreError:
        return None


def need_user(request: Request):
    u = current(request)
    if not u:
        raise HTTPException(401, "Please sign in first.")
    return u


def is_admin(u):
    if not u:
        return False
    try:
        e = STORE.cmd("GET", k(f"email:{u['id']}")) or ""
    except StoreError:
        return False
    return e.lower() in A.cfg()["admins"]


def public_profile(u, viewer=None):
    uid = u["id"]
    rows = STORE.batch([["SCARD", k(f"followers:{uid}")], ["SCARD", k(f"following:{uid}")], ["ZCARD", k(f"uitems:{uid}")]]
                       + ([["SISMEMBER", k(f"following:{viewer['id']}"), uid]] if viewer else []))
    return {"id": uid, "username": u["username"], "display_name": u.get("display_name", ""), "avatar_url": u.get("avatar_url"),
            "bio": u.get("bio", ""), "followers_count": int(rows[0] or 0), "following_count": int(rows[1] or 0),
            "uploads_count": int(rows[2] or 0), "following": bool(viewer and int(rows[3] or 0))}


def make_username(seed):
    base = re.sub(r"[^a-z0-9]+", "_", (seed or "").lower()).strip("_")[:14] or "user"
    if len(base) < 3:
        base = "user"
    for i in range(50):
        name = base if i == 0 else f"{base}{secrets.randbelow(9000) + 100}"
        if UNAME.match(name) and STORE.cmd("SET", k(f"uname:{name}"), "pending", "NX"):
            return name
    raise HTTPException(500, "Couldn't create your account. Try again.")


# ---------------- items ----------------
def get_items(ids):
    ids = [str(i) for i in ids]
    if not ids:
        return []
    out = []
    for i in range(0, len(ids), 100):
        part = ids[i:i + 100]
        rows = STORE.cmd("MGET", *[k(f"item:{x}") for x in part]) or []
        for v in rows:
            try:
                out.append(json.loads(v) if v else None)
            except ValueError:
                out.append(None)
    return out


def decorate(items, viewer=None):
    items = [it for it in items if it]
    if not items:
        return []
    uids = sorted({it["user_id"] for it in items})
    cmds = [["MGET"] + [k(f"user:{u}") for u in uids],
            ["HMGET", k("cnt")] + [f"l:{it['id']}" for it in items] + [f"d:{it['id']}" for it in items]]
    if viewer:
        cmds.append(["SMISMEMBER", k(f"ulikes:{viewer['id']}")] + [str(it["id"]) for it in items])
    res = STORE.batch(cmds)
    rows = list(res[0] or []) + list(res[1] or []) + (list(res[2] or []) if viewer else [])
    users = {}
    for u, v in zip(uids, rows[:len(uids)]):
        try:
            p = json.loads(v) if v else {}
        except ValueError:
            p = {}
        users[u] = {"username": p.get("username", "unknown"), "display_name": p.get("display_name", ""), "avatar_url": p.get("avatar_url")}
    n, out = len(items), []
    for i, it in enumerate(items):
        o = {key: it.get(key) for key in ("id", "kind", "title", "category", "tags", "url", "thumb_url", "mime", "duration", "width", "height", "user_id", "status", "created_at")}
        o["likes_count"] = max(0, int(rows[len(uids) + i] or 0))
        o["downloads_count"] = max(0, int(rows[len(uids) + n + i] or 0))
        o["liked"] = bool(viewer and int(rows[len(uids) + 2 * n + i] or 0))
        o["profiles"] = users.get(it["user_id"], {"username": "unknown"})
        out.append(o)
    return out


def item_or_404(item_id):
    it = (get_items([item_id]) or [None])[0]
    if not it:
        raise HTTPException(404, "This upload was removed or isn’t available.")
    return it


def remove_item(it):
    iid = str(it["id"])
    likers = STORE.cmd("SMEMBERS", k(f"likes:{iid}")) or []
    cmds = [["DEL", k(f"item:{iid}")], ["DEL", k(f"likes:{iid}")], ["DEL", k(f"reports:{iid}")],
            ["ZREM", k(f"z:{it['kind']}:new"), iid], ["ZREM", k(f"z:{it['kind']}:pop"), iid],
            ["ZREM", k(f"z:{it['kind']}:{it['category']}:new"), iid], ["ZREM", k(f"z:{it['kind']}:{it['category']}:pop"), iid],
            ["ZREM", k(f"uitems:{it['user_id']}"), iid], ["ZREM", k("z:reported"), iid],
            ["HDEL", k("cnt"), f"l:{iid}", f"d:{iid}"]] + [["SREM", k(f"ulikes:{u}"), iid] for u in likers]
    STORE.batch(cmds)
    blob_del([it.get("url"), it.get("thumb_url")])


def sniff_audio(b: bytes):
    return b[:3] == b"ID3" or (len(b) > 1 and b[0] == 0xFF and (b[1] & 0xE0) == 0xE0) or b[4:8] == b"ftyp" or b[:4] == b"OggS"


# ---------------- routes ----------------
@R.get("/api/c/status")
def c_status():
    return {"on": ready()}


@R.get("/api/c/devfile/{path:path}", include_in_schema=False)
def c_devfile(path: str):
    if hosted() or ".." in path:
        raise HTTPException(404)
    p = LOCAL_DIR / path
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@R.get("/api/auth/google/start")
def g_start(request: Request, next: str = "/ringtones"):
    need_ready()
    c = A.cfg()
    nxt = next if next.startswith("/") and not next.startswith("//") else "/ringtones"
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    q = {"client_id": c["client_id"], "redirect_uri": wr_seo.site_base(request) + "/api/auth/google/callback", "response_type": "code",
         "scope": "openid email profile", "state": state, "prompt": "select_account",
         "code_challenge": A.b64u(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256"}
    resp = RedirectResponse(A.G_AUTH + "?" + urlencode(q), status_code=302)
    payload = A.b64u(json.dumps({"s": state, "v": verifier, "n": nxt[:300], "exp": int(time.time()) + 600}).encode())
    A._set_cookie(resp, request, UOAUTH, A.sign(payload, "uoauth"), 600, path="/api/auth")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@R.get("/api/auth/google/callback")
def g_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    need_ready()
    raw = A.unsign(request.cookies.get(UOAUTH, ""), "uoauth")
    try:
        st = json.loads(A.unb64u(raw)) if raw else {}
    except Exception:
        st = {}
    nxt = st.get("n") or "/ringtones"
    if error or not code or not st or st.get("s") != state or st.get("exp", 0) < time.time():
        return RedirectResponse(nxt.split("#")[0] + "#signin-failed", status_code=302)
    c = A.cfg()
    r = HTTP.post(A.G_TOKEN, data={"code": code, "client_id": c["client_id"], "client_secret": c["client_secret"],
                                   "redirect_uri": wr_seo.site_base(request) + "/api/auth/google/callback",
                                   "grant_type": "authorization_code", "code_verifier": st["v"]})
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    claims = A._claims(j.get("id_token", "")) if r.status_code == 200 else {}
    if claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com") or claims.get("aud") != c["client_id"] \
            or int(claims.get("exp", 0)) < time.time() or not claims.get("email_verified") or not claims.get("sub"):
        return RedirectResponse(nxt.split("#")[0] + "#signin-failed", status_code=302)
    uid = "g" + str(claims["sub"])
    u = get_user(uid)
    if not u:
        name = (claims.get("name") or claims.get("given_name") or "").strip()
        uname = make_username(name)
        pic = claims.get("picture") or None
        u = {"id": uid, "username": uname, "display_name": name[:40], "avatar_url": pic if pic and pic.startswith("https://") else None,
             "bio": "", "created_at": now_iso()}
        save_user(u)
        STORE.cmd("SET", k(f"uname:{uname}"), uid)
    STORE.cmd("SET", k(f"email:{uid}"), claims["email"].lower())
    resp = RedirectResponse(nxt, status_code=302)
    A._set_cookie(resp, request, UCOOKIE, A.sign(f"{uid}|{int(time.time()) + UTTL}", "user"), UTTL)
    resp.delete_cookie(UOAUTH, path="/api/auth")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@R.post("/api/c/logout")
def c_logout(request: Request):
    same_site(request)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(UCOOKIE, path="/")
    return resp


@R.get("/api/c/me")
def c_me(request: Request):
    need_ready()
    u = current(request)
    if not u:
        return JSONResponse({"user": None}, headers={"Cache-Control": "no-store"})
    return JSONResponse({"user": public_profile(u), "is_admin": is_admin(u)}, headers={"Cache-Control": "no-store"})


@R.get("/api/c/items")
def c_items(request: Request, kind: str = "ringtone", sort: str = "popular", cat: str = "", q: str = "", start: int = 0):
    need_ready()
    if kind not in CATS:
        raise HTTPException(400, "Unknown kind.")
    me = current(request)
    if cat and cat not in CATS[kind]:
        cat = ""
    words = [w for w in re.sub(r"[^\w ]+", " ", q.lower()).split() if w][:5]
    start = max(0, start)
    match = lambda x: x and x["kind"] == kind and x.get("status") == "live" and (not cat or x["category"] == cat) \
        and all(w in (x["title"].lower() + " " + " ".join(x.get("tags") or [])) for w in words)
    if sort == "following":
        if not me:
            raise HTTPException(401, "Sign in to see uploads from people you follow.")
        ids = []
        for f in (STORE.cmd("SMEMBERS", k(f"following:{me['id']}")) or [])[:100]:
            ids += STORE.cmd("ZREVRANGE", k(f"uitems:{f}"), 0, 99) or []
        items = sorted([x for x in get_items(ids) if match(x)], key=lambda x: x["created_at"], reverse=True)
        return {"items": decorate(items[start:start + PAGE], me), "done": start + PAGE >= len(items), "next": start + PAGE}
    zkey = k(f"z:{kind}:{cat + ':' if cat else ''}{'pop' if sort == 'popular' else 'new'}")
    if words:   # search looks through the newest 400 in this list
        items = [x for x in get_items(STORE.cmd("ZREVRANGE", zkey, 0, 399) or []) if match(x)]
        return {"items": decorate(items[start:start + PAGE], me), "done": start + PAGE >= len(items), "next": start + PAGE}
    got, pos = [], start   # walk the sorted list, skipping hidden uploads, so one page costs only a few database reads
    while len(got) < PAGE:
        ids = STORE.cmd("ZREVRANGE", zkey, pos, pos + PAGE * 2 - 1) or []
        if not ids:
            break
        for i, x in zip(ids, get_items(ids)):
            pos += 1
            if match(x):
                got.append(x)
                if len(got) == PAGE:
                    break
        if len(ids) < PAGE * 2:
            break
    done = not (STORE.cmd("ZREVRANGE", zkey, pos, pos) or [])
    return {"items": decorate(got, me), "done": done, "next": pos}


@R.get("/api/c/items/{item_id}")
def c_item(item_id: int, request: Request):
    need_ready()
    me = current(request)
    it = item_or_404(item_id)
    if it.get("status") != "live" and not (me and (me["id"] == it["user_id"] or is_admin(me))):
        raise HTTPException(404, "This upload was removed or isn’t available.")
    o = decorate([it], me)[0]
    owner = get_user(it["user_id"])
    if owner:
        o["profiles"] = public_profile(owner, me)
    return o


@R.post("/api/c/upload")
async def c_upload(request: Request, kind: str = Form(...), title: str = Form(...), category: str = Form(...), tags: str = Form(""),
                   duration: float = Form(0), width: int = Form(0), height: int = Form(0),
                   file: UploadFile = File(...), thumb: UploadFile | None = File(None)):
    need_ready()
    same_site(request)
    me = need_user(request)
    A.rate_limit("c-up-ip:" + A.client_ip(request), 30, 3600)
    day = time.strftime("%Y%m%d")
    n = STORE.cmd("INCR", k(f"daily:{me['id']}:{day}"))
    if int(n) == 1:
        STORE.cmd("EXPIRE", k(f"daily:{me['id']}:{day}"), 90000)
    if int(n) > 20:
        raise HTTPException(429, "You can upload 20 files a day. Please try again tomorrow.")
    if kind not in CATS or category not in CATS[kind]:
        raise HTTPException(400, "Choose a category.")
    title = re.sub(r"\s+", " ", title).strip()[:60]
    if len(title) < 2:
        raise HTTPException(400, "Give it a title.")
    tg = [t for t in (re.sub(r"[^\w]+", "", x.lower())[:24] for x in re.split(r"[,#]", tags)) if t][:5]
    data = await file.read(MAX_AUDIO + 1 if kind == "ringtone" else MAX_IMG + 1)
    if kind == "ringtone":
        mime = (file.content_type or "").lower()
        if mime not in AUDIO or not sniff_audio(data):
            raise HTTPException(400, "Use an MP3, M4A, AAC or OGG file.")
        if len(data) > MAX_AUDIO:
            raise HTTPException(400, "This file is over 4 MB. Ringtones should be short: up to 60 seconds.")
        if duration and duration > 61:
            raise HTTPException(400, "Ringtones can be up to 60 seconds.")
        ext, thumb_bytes = AUDIO[mime], None
    else:
        mime = "image/jpeg"
        if data[:3] != b"\xff\xd8\xff" or len(data) > MAX_IMG:
            raise HTTPException(400, "Couldn’t read this picture. Try another one.")
        ext = "jpg"
        thumb_bytes = await thumb.read(MAX_THUMB + 1) if thumb else None
        if thumb_bytes and (thumb_bytes[:3] != b"\xff\xd8\xff" or len(thumb_bytes) > MAX_THUMB):
            thumb_bytes = None
    if len(data) < 200:
        raise HTTPException(400, "This file is empty.")
    key = secrets.token_hex(8)
    url = blob_put(f"c/{me['id']}/{key}.{ext}", data, mime)
    turl = blob_put(f"c/{me['id']}/{key}-thumb.jpg", thumb_bytes, "image/jpeg") if thumb_bytes else None
    iid = int(STORE.cmd("INCR", k("seq")))
    ts = time.time()
    it = {"id": iid, "kind": kind, "title": title, "category": category, "tags": tg, "url": url, "thumb_url": turl, "mime": mime,
          "size": len(data), "duration": round(duration, 1) if kind == "ringtone" and duration else None,
          "width": width or None, "height": height or None, "user_id": me["id"], "status": "live", "created_at": now_iso()}
    STORE.batch([["SET", k(f"item:{iid}"), json.dumps(it, ensure_ascii=False)],
                 ["ZADD", k(f"z:{kind}:new"), ts, iid], ["ZADD", k(f"z:{kind}:pop"), ts, iid], ["ZADD", k(f"uitems:{me['id']}"), ts, iid],
                 ["ZADD", k(f"z:{kind}:{category}:new"), ts, iid], ["ZADD", k(f"z:{kind}:{category}:pop"), ts, iid]])
    return {"id": iid}


@R.post("/api/c/items/{item_id}/like")
def c_like(item_id: int, request: Request):
    need_ready()
    same_site(request)
    me = need_user(request)
    it = item_or_404(item_id)
    iid = str(it["id"])
    on = int(STORE.cmd("SISMEMBER", k(f"likes:{iid}"), me["id"]) or 0)
    d = -1 if on else 1
    rows = STORE.batch([["SREM" if on else "SADD", k(f"likes:{iid}"), me["id"]], ["SREM" if on else "SADD", k(f"ulikes:{me['id']}"), iid],
                        ["HINCRBY", k("cnt"), f"l:{iid}", d], ["ZINCRBY", k(f"z:{it['kind']}:pop"), d * 1e10, iid],
                        ["ZINCRBY", k(f"z:{it['kind']}:{it['category']}:pop"), d * 1e10, iid]])
    return {"liked": not on, "likes_count": max(0, int(rows[2] or 0))}


@R.post("/api/c/items/{item_id}/download")
def c_download(item_id: int, request: Request):
    need_ready()
    A.rate_limit("c-dl:" + A.client_ip(request), 120, 3600)
    it = item_or_404(item_id)
    return {"downloads_count": int(STORE.cmd("HINCRBY", k("cnt"), f"d:{it['id']}", 1) or 0)}


@R.post("/api/c/items/{item_id}/report")
async def c_report(item_id: int, request: Request):
    need_ready()
    same_site(request)
    me = need_user(request)
    body = await request.json()
    reason = str(body.get("reason") or "other")
    if reason not in REASONS:
        reason = "other"
    it = item_or_404(item_id)
    iid = str(it["id"])
    if STORE.cmd("HGET", k(f"reports:{iid}"), me["id"]):
        return {"ok": True, "already": True}
    STORE.cmd("HSET", k(f"reports:{iid}"), me["id"], reason)
    n = int(STORE.cmd("HLEN", k(f"reports:{iid}")) or 0)
    STORE.cmd("ZADD", k("z:reported"), n, iid)
    if n >= 3 and it.get("status") == "live":
        it["status"] = "hidden"
        STORE.cmd("SET", k(f"item:{iid}"), json.dumps(it, ensure_ascii=False))
    return {"ok": True}


@R.post("/api/c/items/{item_id}/restore")
def c_restore(item_id: int, request: Request):
    need_ready()
    same_site(request)
    me = need_user(request)
    if not is_admin(me):
        raise HTTPException(403, "Only WaveRise admins can do this.")
    it = item_or_404(item_id)
    it["status"] = "live"
    STORE.batch([["SET", k(f"item:{it['id']}"), json.dumps(it, ensure_ascii=False)], ["DEL", k(f"reports:{it['id']}")], ["ZREM", k("z:reported"), str(it["id"])]])
    return {"ok": True}


@R.delete("/api/c/items/{item_id}")
def c_delete(item_id: int, request: Request):
    need_ready()
    same_site(request)
    me = need_user(request)
    it = item_or_404(item_id)
    if me["id"] != it["user_id"] and not is_admin(me):
        raise HTTPException(403, "You can only delete your own uploads.")
    remove_item(it)
    return {"ok": True}


@R.get("/api/c/users/{username}")
def c_user(username: str, request: Request, kind: str = "ringtone"):
    need_ready()
    uid = STORE.cmd("GET", k(f"uname:{username.lower()}"))
    u = get_user(uid) if uid and uid != "pending" else None
    if not u:
        raise HTTPException(404, "No one uses this name.")
    me = current(request)
    see_all = bool(me and (me["id"] == u["id"] or is_admin(me)))
    ids = STORE.cmd("ZREVRANGE", k(f"uitems:{u['id']}"), 0, 299) or []
    items = [x for x in get_items(ids) if x and x["kind"] == kind and (see_all or x.get("status") == "live")]
    return {"profile": public_profile(u, me), "items": decorate(items, me)}


@R.post("/api/c/users/{username}/follow")
def c_follow(username: str, request: Request):
    need_ready()
    same_site(request)
    me = need_user(request)
    uid = STORE.cmd("GET", k(f"uname:{username.lower()}"))
    if not uid or uid == "pending" or uid == me["id"]:
        raise HTTPException(400, "You can’t follow this account.")
    on = int(STORE.cmd("SISMEMBER", k(f"following:{me['id']}"), uid) or 0)
    STORE.batch([["SREM" if on else "SADD", k(f"following:{me['id']}"), uid], ["SREM" if on else "SADD", k(f"followers:{uid}"), me["id"]]])
    return {"following": not on, "followers_count": int(STORE.cmd("SCARD", k(f"followers:{uid}")) or 0)}


@R.post("/api/c/profile")
async def c_profile(request: Request):
    need_ready()
    same_site(request)
    me = need_user(request)
    body = await request.json()
    uname = str(body.get("username") or me["username"]).strip().lower()
    if not UNAME.match(uname):
        raise HTTPException(400, "Username: 3–20 letters, numbers, _ or .")
    if uname != me["username"]:
        if not STORE.cmd("SET", k(f"uname:{uname}"), me["id"], "NX"):
            raise HTTPException(409, "That username is taken. Try another.")
        STORE.cmd("DEL", k(f"uname:{me['username']}"))
        me["username"] = uname
    me["display_name"] = re.sub(r"\s+", " ", str(body.get("display_name") or "")).strip()[:40]
    me["bio"] = re.sub(r"\s+", " ", str(body.get("bio") or "")).strip()[:160]
    save_user(me)
    return {"user": public_profile(me)}


@R.post("/api/c/account/delete")
def c_account_delete(request: Request):
    need_ready()
    same_site(request)
    me = need_user(request)
    uid = me["id"]
    for it in get_items(STORE.cmd("ZREVRANGE", k(f"uitems:{uid}"), 0, -1) or []):
        if it:
            remove_item(it)
    cmds = [["DEL", k(f"user:{uid}")], ["DEL", k(f"uname:{me['username']}")], ["DEL", k(f"email:{uid}")], ["DEL", k(f"uitems:{uid}")],
            ["DEL", k(f"ulikes:{uid}")], ["DEL", k(f"following:{uid}")], ["DEL", k(f"followers:{uid}")]]
    for f in STORE.cmd("SMEMBERS", k(f"following:{uid}")) or []:
        cmds.append(["SREM", k(f"followers:{f}"), uid])
    for f in STORE.cmd("SMEMBERS", k(f"followers:{uid}")) or []:
        cmds.append(["SREM", k(f"following:{f}"), uid])
    STORE.batch(cmds)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(UCOOKIE, path="/")
    return resp


@R.get("/api/c/admin/queue")
def c_admin_queue(request: Request):
    need_ready()
    me = need_user(request)
    if not is_admin(me):
        raise HTTPException(403, "Only WaveRise admins can open this page.")
    ids = STORE.cmd("ZREVRANGE", k("z:reported"), 0, 99) or []
    items = decorate([x for x in get_items(ids) if x], me)
    reasons = STORE.batch([["HGETALL", k(f"reports:{x['id']}")] for x in items]) if items else []
    for x, flat in zip(items, reasons):
        vals = list(flat.values()) if isinstance(flat, dict) else (flat or [])[1::2]
        x["reports"] = vals
    return {"items": items}
