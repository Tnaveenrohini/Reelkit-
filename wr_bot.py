"""WaveRise Assistant: a human-sounding helper for visitors, an owner's assistant in the admin,
reply drafts for contact messages, and an error notebook.

Everything runs on the server. The Google Gemini key lives only in the GEMINI_API_KEY environment
variable and is never sent to a browser.
"""
import hashlib, json, os, re, time, traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from wr_store import STORE, StoreError

R = APIRouter()
IST = timezone(timedelta(hours=5, minutes=30))
LANGS = {"en": "English", "hi": "Hindi", "ta": "Tamil", "te": "Telugu"}
ERR_KEEP = 400            # distinct problems kept in the notebook


# ---------------------------------------------------------------- Gemini
class BotOff(Exception):
    """No AI key is set."""


class BotBusy(Exception):
    """The free AI quota is used up for now, or Google could not be reached."""


def ai_key():
    return os.getenv("GEMINI_API_KEY", "").strip()


def models():
    return list(dict.fromkeys(m for m in [os.getenv("GEMINI_MODEL", "").strip(), "gemini-flash-latest", "gemini-2.5-flash",
                                          "gemini-flash-lite-latest", "gemini-2.5-flash-lite", "gemini-2.0-flash"] if m))


def gemini(system, turns, temperature=0.7, max_tokens=700, json_mode=False, timeout=22, where="assistant"):
    """turns: list of (role, text) with role 'user' or 'model'. Returns the reply text."""
    key = ai_key()
    if not key:
        raise BotOff()
    contents = [{"role": "model" if r == "model" else "user", "parts": [{"text": str(t)[:6000]}]} for r, t in turns if str(t or "").strip()]
    gen = {"temperature": temperature, "maxOutputTokens": max_tokens, "thinkingConfig": {"thinkingBudget": 0}}
    if json_mode:
        gen["responseMimeType"] = "application/json"
    last = ""
    for model in models():
        for attempt in (0, 1):
            body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "generationConfig": dict(gen)}
            if attempt:
                body["generationConfig"].pop("thinkingConfig", None)
            try:
                r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                               headers={"x-goog-api-key": key, "Content-Type": "application/json"}, json=body, timeout=timeout)
            except httpx.HTTPError as e:
                last = f"{model}: could not reach Google ({type(e).__name__})"
                break
            if r.status_code == 400 and not attempt and "thinking" in r.text.lower():
                continue                                  # this model does not take a thinking setting
            if r.status_code == 200:
                try:
                    cand = r.json()["candidates"][0]
                    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought")).strip()
                except (KeyError, IndexError, ValueError, TypeError):
                    text = ""
                if text:
                    return text
                last = f"{model}: empty answer"
                break
            if r.status_code in (400, 401, 403) and "API key" in r.text:
                note("ai", where, "The Gemini key was refused by Google", r.text[:300], r.status_code)
                raise BotBusy("key")
            last = f"{model}: HTTP {r.status_code} {r.text[:160]}"
            break                                         # 404 = model gone, 429 = quota: try the next model
    note("ai", where, "The AI could not answer", last, None)
    raise BotBusy(last)


def as_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                pass
    return None


# ---------------------------------------------------------------- error notebook
NOISE = re.compile(r"ResizeObserver loop|Script error\.?$|chrome-extension://|moz-extension://|safari-extension://|"
                   r"Non-Error promise rejection|AbortError|The user aborted|Load failed$|NotAllowedError|"
                   r"cancelled|instantSearchSDKJSBridge|__gCrWeb|webkit\.messageHandlers", re.I)


def _sig(source, where, msg):
    norm = re.sub(r"\d+", "#", re.sub(r"https?://\S+", "<link>", msg.lower()))[:160]
    return hashlib.sha1(f"{source}|{where}|{norm}".encode()).hexdigest()[:12]


def note(source, where, msg, detail="", status=None):
    """Write one problem into the notebook. Same problem again = its counter goes up."""
    if not STORE.ok:
        return
    msg = re.sub(r"\s+", " ", str(msg or "")).strip()[:300] or "Unknown error"
    where = str(where or "")[:160]
    sig, now = _sig(source, where, msg), int(time.time())
    row = json.dumps({"id": sig, "source": source, "where": where, "msg": msg, "detail": str(detail or "")[:1200],
                      "status": status, "first": now}, ensure_ascii=False)
    try:
        STORE.batch([("HSETNX", STORE.k("errlog"), sig, row), ("HINCRBY", STORE.k("errcnt"), sig, 1),
                     ("ZADD", STORE.k("errlast"), now, sig), ("HINCRBY", STORE.k("errday:" + datetime.now(IST).strftime("%Y-%m-%d")), source, 1),
                     ("EXPIRE", STORE.k("errday:" + datetime.now(IST).strftime("%Y-%m-%d")), 40 * 86400)])
        if now % 20 == 0 and int(STORE.cmd("ZCARD", STORE.k("errlast")) or 0) > ERR_KEEP:
            old = STORE.cmd("ZRANGE", STORE.k("errlast"), 0, 49) or []
            if old:
                STORE.batch([("ZREM", STORE.k("errlast"), *old), ("HDEL", STORE.k("errlog"), *old), ("HDEL", STORE.k("errcnt"), *old),
                             ("HDEL", STORE.k("errfixed"), *old)])
    except StoreError:
        pass


def errors(limit=150, since=0):
    if not STORE.ok:
        return []
    flat = STORE.cmd("ZREVRANGEBYSCORE", STORE.k("errlast"), "+inf", since or "-inf", "WITHSCORES", "LIMIT", 0, limit) or []
    ids = flat[0::2]
    if not ids:
        return []
    rows, cnts, fixed = STORE.batch([("HMGET", STORE.k("errlog"), *ids), ("HMGET", STORE.k("errcnt"), *ids), ("HMGET", STORE.k("errfixed"), *ids)])
    out = []
    for i, sid in enumerate(ids):
        try:
            r = json.loads(rows[i]) if rows[i] else None
        except (ValueError, TypeError):
            r = None
        if not r:
            continue
        r["last"], r["count"] = int(float(flat[2 * i + 1])), int(cnts[i] or 0)
        fx_t, _, fx_n = str(fixed[i] or "").partition(":")
        r["fixed"] = int(fx_t) if fx_t and fx_n and r["count"] <= int(fx_n) else 0
        out.append(r)
    return out


def day_counts(days=7):
    if not STORE.ok:
        return []
    dates = [(datetime.now(IST) - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)][::-1]
    res = STORE.batch([("HGETALL", STORE.k("errday:" + d)) for d in dates])
    out = []
    for d, flat in zip(dates, res):
        flat = flat or []
        out.append({"day": d, **{flat[i]: int(flat[i + 1]) for i in range(0, len(flat), 2)}})
    return out


class ErrorNotes:
    """Notes every server crash, every 5xx answer, and failed downloads (with the reason shown to the visitor)."""
    WATCH = re.compile(r"^/(api/)?(info|download|mp3|ringtone|clip|gif|shrink|enhance|frame|bgremove|help|c/upload)\b")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path.startswith(("/api/err", "/admin/", "/_vercel")):
            return await self.app(scope, receive, send)
        state = {"status": 0, "body": bytearray()}

        async def wsend(m):
            if m["type"] == "http.response.start":
                state["status"] = m["status"]
            elif m["type"] == "http.response.body" and state["status"] >= 400 and len(state["body"]) < 1500:
                state["body"].extend(m.get("body", b"")[:1500 - len(state["body"])])
            await send(m)

        try:
            await self.app(scope, receive, wsend)
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)[-3:]
            await run_in_threadpool(note, "server", path, f"{type(e).__name__}: {e}",
                                    " ← ".join(f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno} {f.name}" for f in reversed(tb)), 500)
            raise
        st = state["status"]
        if st >= 500 or (st >= 400 and st not in (401, 404, 405, 429) and self.WATCH.match(path)):
            detail = bytes(state["body"]).decode("utf-8", "replace")
            try:
                d = json.loads(detail)
                reason = d.get("detail") if isinstance(d, dict) else None
                reason = reason if isinstance(reason, str) else json.dumps(reason)[:300] if reason else None
            except ValueError:
                reason = None
            tool = dict(scope.get("headers", [])).get(b"x-waverise-tool", b"").decode()[:30]
            await run_in_threadpool(note, "server" if st >= 500 else "visitor", path + (f" ({tool})" if tool else ""),
                                    reason or f"HTTP {st}", "" if reason else detail[:400], st)


# ---------------------------------------------------------------- visitor assistant
PERSONA = """You are the friendly person who answers chats on WaveRise (waverise.in). Talk like a real, warm human
helper from the WaveRise team: natural, relaxed, short sentences, a little personal ("Sure, let's do it", "Ah, that happens
sometimes"). Never sound like a robot, never say "As an AI". If someone asks whether you are a bot, say honestly that you
are WaveRise's automatic assistant and that a person reads the Contact page messages.

Rules:
- Reply in the visitor's language and style. If they write Telugu, Hindi or Tamil in English letters (like
  "photo size ela taggali"), reply the same way, in English letters. If they write in Telugu/Hindi/Tamil script, use
  that script. Otherwise use {lang}.
- Keep it short: 1-4 sentences, or a few numbered steps when they need steps. Ask one small question back if you
  really need to know something.
- Only talk about WaveRise and how to get the visitor's job done with it. For anything else, gently bring it back.
- Never invent features, prices or promises. If you are not sure, say so and suggest the Contact page (/contact).
- Never ask for passwords, OTPs or Instagram logins. WaveRise never needs them.
- Links: only markdown links to these site paths, like [Photo MB to KB](/photo-mb-to-kb). No other links.
"""

EXTRA_FACTS = """
MORE THAT IS NEW ON WAVERISE:
- Photo Editor (/photo-editor): looks/filters, brightness, contrast, warmth, crop, rotate, all on the phone.
- Ringtones (/ringtones) and Wallpapers (/wallpapers): free downloads shared by the community. People can sign in
  (email + password, or Google) to upload their own, like, follow and share. Uploads are checked by the community: wrong
  uploads can be reported.
- WaveRise Chat (/chat): paste a link or ask anything; the search box at the top of every page finds any tool.
"""


def visitor_system(lang):
    import app as backend                                           # facts live next to the tools
    return PERSONA.replace("{lang}", LANGS.get(lang, "English")) + "\n" + backend.HELP_FACTS + EXTRA_FACTS


def visitor_reply(q, hist, lang):
    turns = []
    for m in (hist or [])[-8:]:
        if isinstance(m, dict) and m.get("t"):
            turns.append(("user" if m.get("me") else "model", str(m["t"])[:700]))
    turns.append(("user", q))
    return gemini(visitor_system(lang), turns, temperature=0.75, max_tokens=500, where="visitor chat")[:1500]


# ---------------------------------------------------------------- owner assistant
def site_snapshot():
    import wr_manage as MG
    now = int(time.time())
    snap = {"time_now_india": datetime.now(IST).strftime("%A %d %b %Y, %I:%M %p IST"), "database_connected": STORE.ok,
            "ai_connected": bool(ai_key())}
    if not STORE.ok:
        return snap
    errs = errors(60, now - 3 * 86400)
    snap["problems_last_3_days"] = [{"what": e["msg"], "where": e["where"], "type": e["source"], "times": e["count"],
                                     "last_seen": datetime.fromtimestamp(e["last"], IST).strftime("%d %b %I:%M %p"),
                                     "marked_fixed": bool(e["fixed"]), "detail": e["detail"][:200]} for e in errs]
    snap["problems_per_day"] = day_counts(7)
    try:
        msgs = sorted(STORE.hall("contact").values(), key=lambda r: r.get("t", 0), reverse=True)
        snap["contact_messages"] = {"unread": sum(1 for m in msgs if not m.get("read")), "total": len(msgs),
                                    "latest": [{"topic": m.get("topic"), "name": m.get("name") or "no name", "read": m.get("read"),
                                                "replied": bool(m.get("replied")), "when": datetime.fromtimestamp(m.get("t", 0), IST).strftime("%d %b %I:%M %p"),
                                                "message": (m.get("message") or "")[:300]} for m in msgs[:8]]}
    except StoreError:
        pass
    try:
        st = MG.statistics(2)
        snap["downloads_today_and_yesterday_utc"] = st["platforms"]
        snap["download_alerts_now"] = [a["message"] for a in MG.alerts()]
    except Exception:
        pass
    try:
        h = STORE.get_json("health")
        if h:
            snap["last_site_check"] = {"when": datetime.fromtimestamp(h.get("checked", 0), IST).strftime("%d %b %I:%M %p"), **h.get("summary", {}),
                                       "failed_checks": [s["name"] + ": " + str(s.get("detail", "")) for s in h.get("site", []) if not s.get("ok")][:10],
                                       "broken_pages": [str(b)[:120] for b in h.get("broken", [])][:10]}
        flat = STORE.cmd("ZREVRANGE", STORE.k("404"), 0, 7, "WITHSCORES") or []
        snap["missing_pages_most_visited"] = {flat[i]: int(float(flat[i + 1])) for i in range(0, len(flat), 2)}
    except (StoreError, ValueError, TypeError):
        pass
    try:
        snap["community"] = {"reported_uploads_waiting": int(STORE.cmd("ZCARD", STORE.k("c:z:reported")) or 0)}
    except StoreError:
        pass
    return snap


OWNER = """You are the WaveRise site assistant. You look after waverise.in while the owner, Naveen, is at his day job,
and you talk to him like a trusted colleague who has been watching the site: warm, direct, human, no jargon.

- Reply in the language he writes in. He often writes Telugu in English letters ("ela undi site?"); then answer the
  same way. If he writes in Telugu script, use Telugu script.
- Use ONLY the facts in SITE DATA below. Never make up numbers or problems. If something is not in the data, say you
  don't have that information.
- Lead with what matters: anything broken or getting worse first, then messages waiting, then the good news.
- For each real problem: say what visitors see, how often, and one practical next step. Problems of type "visitor" are
  usually not bugs (private posts, wrong links, Instagram limits); say so when that is the case. Type "server" or
  "browser" problems are real bugs worth fixing. "ai" means the chat assistant could not answer.
- Keep it short and easy to read on a phone: short paragraphs or a few bullets. No tables.

SITE DATA (JSON):
"""


def owner_reply(turns):
    system = OWNER + json.dumps(site_snapshot(), ensure_ascii=False, default=str)[:24000]
    return gemini(system, turns, temperature=0.5, max_tokens=900, timeout=30, where="owner assistant")


def daily_report():
    """Once a day (from the cron job): a short morning summary kept for the admin page."""
    if not ai_key() or not STORE.ok:
        return None
    try:
        text = owner_reply([("user", "Give me today's short report on the site: what broke, what needs my attention, and how things are going. Use simple English.")])
    except (BotOff, BotBusy):
        return None
    rep = {"t": int(time.time()), "text": text[:4000]}
    STORE.set_json("bot:daily", rep)
    return rep


# ---------------------------------------------------------------- contact reply drafts
DRAFT = """You write replies, for the WaveRise team, to messages people sent from the Contact page of waverise.in.
Write like a kind, real person from the team (sign off as "Naveen, WaveRise"). Short: 2-6 sentences.
Reply in the same language and script the sender used. Be honest: never promise dates or features that the facts do not
mention. If it is a bug report, thank them, say we are looking into it, and give a workaround from the facts if there is
one. If it is a copyright/DMCA request, thank them and say the team will review it and reply. Plain text only, no
markdown, no links except waverise.in pages written like waverise.in/photo-mb-to-kb.

WAVERISE FACTS:
"""


def draft_reply(msg):
    import app as backend
    who = f"From: {msg.get('name') or 'no name'}\nTopic: {msg.get('topic')}\nMessage:\n{msg.get('message')}"
    return gemini(DRAFT + backend.HELP_FACTS + EXTRA_FACTS, [("user", who)], temperature=0.6, max_tokens=600, where="reply drafts")


# ---------------------------------------------------------------- routes
def _same_site(request):
    import wr_admin as A
    src = request.headers.get("origin") or request.headers.get("referer") or ""
    return not src or (urlparse(src).netloc or "").lower() == A.host_of(request)


@R.post("/api/err", include_in_schema=False)
async def client_error(request: Request):
    """Browsers report their JavaScript errors here (sent by the page itself)."""
    import wr_admin as A
    ok = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    raw = await request.body()
    if len(raw) > 4000 or not _same_site(request) or not STORE.ok:
        return ok
    try:
        d = json.loads(raw or b"{}")
    except ValueError:
        return ok
    if not isinstance(d, dict):
        return ok
    msg, src = str(d.get("msg") or "")[:300], str(d.get("src") or "")[:200]
    if not msg or NOISE.search(msg) or NOISE.search(src):
        return ok
    try:
        A.rate_limit("err:" + hashlib.sha256(A.client_ip(request).encode()).hexdigest()[:16], 15, 3600)
        A.rate_limit("err:all", 1500, 3600)
    except HTTPException:
        return ok
    page = urlparse(str(d.get("page") or "")).path[:120] or "?"
    ua = request.headers.get("user-agent", "")
    dev = ("Android" if "Android" in ua else "iPhone" if "iPhone" in ua or "iPad" in ua else "Windows" if "Windows" in ua else "Mac" if "Mac OS" in ua else "other") + \
          (" · Chrome" if "Chrome/" in ua else " · Safari" if "Safari/" in ua else " · Firefox" if "Firefox/" in ua else "")
    await run_in_threadpool(note, "browser", page, msg, (src + "\n" if src else "") + str(d.get("stack") or "")[:700] + "\n" + dev, None)
    return ok


def install(app):
    import wr_admin as A

    app.add_middleware(ErrorNotes)
    app.include_router(R)

    @app.get("/api/admin/bot", include_in_schema=False)
    def a_bot_state(request: Request):
        A.require(request)
        return A.admin_json({"ai": bool(ai_key()), "store": STORE.ok, "daily": STORE.get_json("bot:daily") if STORE.ok else None,
                             "open_problems": sum(1 for e in errors(200, int(time.time()) - 86400) if not e["fixed"])})

    @app.post("/api/admin/bot", include_in_schema=False)
    async def a_bot_ask(request: Request):
        A.require(request, True)
        A.rate_limit("ownerbot", 60, 3600)
        j = await request.json()
        turns = [("user" if m.get("me") else "model", str(m.get("t") or "")[:2000]) for m in (j.get("hist") or [])[-10:] if isinstance(m, dict)]
        q = str(j.get("q") or "").strip()[:2000]
        if not q:
            raise HTTPException(400, "Type a question.")
        try:
            text = await run_in_threadpool(owner_reply, turns + [("user", q)])
        except BotOff:
            raise HTTPException(409, "The assistant needs a free Google Gemini key. Add GEMINI_API_KEY in Vercel → Settings → Environment Variables, then redeploy.")
        except BotBusy:
            raise HTTPException(503, "The assistant is busy right now (free quota). Try again in a minute.")
        return A.admin_json({"answer": text})

    @app.post("/api/admin/bot/daily", include_in_schema=False)
    async def a_bot_daily(request: Request):
        A.require(request, True)
        A.rate_limit("ownerbot-daily", 10, 3600)
        rep = await run_in_threadpool(daily_report)
        if not rep:
            raise HTTPException(503, "Couldn’t write the report now. Check that GEMINI_API_KEY is set, then try again.")
        return A.admin_json({"daily": rep})

    @app.get("/api/admin/errors", include_in_schema=False)
    def a_errors(request: Request):
        A.require(request)
        return A.admin_json({"rows": errors(200), "days": day_counts(14), "store": STORE.ok})

    @app.post("/api/admin/errors", include_in_schema=False)
    async def a_errors_act(request: Request):
        s = A.require(request, True); A.need_store()
        j = await request.json()
        act, ids = j.get("action"), [str(i)[:20] for i in (j.get("ids") or [])][:200]
        if act == "fixed" and ids:
            now, cnts = int(time.time()), STORE.cmd("HMGET", STORE.k("errcnt"), *ids) or []
            STORE.batch([("HSET", STORE.k("errfixed"), i, f"{now}:{int(c or 0)}") for i, c in zip(ids, cnts)])
        elif act == "reopen" and ids:
            STORE.cmd("HDEL", STORE.k("errfixed"), *ids)
        elif act == "delete" and ids:
            STORE.batch([("ZREM", STORE.k("errlast"), *ids), ("HDEL", STORE.k("errlog"), *ids), ("HDEL", STORE.k("errcnt"), *ids), ("HDEL", STORE.k("errfixed"), *ids)])
        elif act == "clear":
            STORE.cmd("DEL", STORE.k("errlast"), STORE.k("errlog"), STORE.k("errcnt"), STORE.k("errfixed"))
            A.audit(request, s["email"], "error_log_cleared")
        else:
            raise HTTPException(400, "Unknown action")
        return A.admin_json({"ok": True})

    @app.post("/api/admin/errors/explain", include_in_schema=False)
    async def a_errors_explain(request: Request):
        A.require(request, True)
        A.rate_limit("ownerbot", 60, 3600)
        j = await request.json()
        row = next((e for e in errors(400) if e["id"] == j.get("id")), None)
        if not row:
            raise HTTPException(404, "That problem is no longer in the notebook.")
        q = ("Explain this one problem simply: what the visitor sees, whether it is a real bug, and what I should do. "
             "Problem: " + json.dumps({k: row[k] for k in ("source", "where", "msg", "detail", "count", "status")}, ensure_ascii=False))
        try:
            text = await run_in_threadpool(owner_reply, [("user", q)])
        except BotOff:
            raise HTTPException(409, "Add GEMINI_API_KEY in Vercel to get explanations.")
        except BotBusy:
            raise HTTPException(503, "The assistant is busy right now. Try again in a minute.")
        return A.admin_json({"answer": text})

    @app.post("/api/admin/messages/{mid}/draft", include_in_schema=False)
    async def a_draft(mid: str, request: Request):
        A.require(request, True); A.need_store()
        A.rate_limit("ownerbot", 60, 3600)
        row = STORE.hall("contact").get(mid)
        if not row:
            raise HTTPException(404, "That message no longer exists.")
        try:
            text = await run_in_threadpool(draft_reply, row)
        except BotOff:
            raise HTTPException(409, "Reply drafts need a free Google Gemini key. Add GEMINI_API_KEY in Vercel, then redeploy.")
        except BotBusy:
            raise HTTPException(503, "The assistant is busy right now. Try again in a minute.")
        row["draft"] = text[:3000]
        STORE.hset("contact", mid, row)
        return A.admin_json({"draft": row["draft"]})

    @app.post("/api/admin/messages/{mid}/reply", include_in_schema=False)
    async def a_replied(mid: str, request: Request):
        s = A.require(request, True); A.need_store()
        j = await request.json()
        row = STORE.hall("contact").get(mid)
        if not row:
            raise HTTPException(404, "That message no longer exists.")
        if "draft" in j:
            row["draft"] = str(j.get("draft") or "")[:3000]
        if j.get("sent"):
            row["replied"], row["read"] = int(time.time()), True
            A.audit(request, s["email"], "message_replied", {"topic": row.get("topic")})
        STORE.hset("contact", mid, row)
        return A.admin_json({"ok": True})
