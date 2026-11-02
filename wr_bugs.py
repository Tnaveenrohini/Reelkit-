"""WaveRise AI Bug Fixer – the website side.

* Module 1 (detection) on the site: /api/errors receives browser errors (JavaScript, failed API calls, failed uploads and
  downloads); unhandled backend exceptions and server errors are recorded; Sentry's SDK reports too when SENTRY_DSN is set.
* The admin dashboard API (/api/admin/bugfixer/*), protected by the existing admin sign-in, CSRF and same-site checks.
The heavy work (AI, patches, tests, deploys) is done by the persistent worker (bugfixer/worker.py).
Without DATABASE_URL everything here quietly does nothing, so the public site never breaks because of it."""
import hashlib
import json
import os
import time
import traceback

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from bugfixer import detection, priority
from bugfixer.config import load as load_cfg
from bugfixer.db import DB, DBError
from bugfixer.redact import anon

RELEASE = (os.getenv("VERCEL_GIT_COMMIT_SHA") or "")[:12]
_db, _down_until = None, 0.0


def db():
    """One connection per warm server instance; after a failure the database is skipped for 60 s (the site keeps working)."""
    global _db, _down_until
    url = os.getenv("DATABASE_URL", "").strip()
    if not url or time.time() < _down_until:
        return None
    if _db is None:
        _db = DB(url, timeout=3)
    return _db


def _failed():
    global _down_until
    _down_until = time.time() + 60
    if _db:
        _db.close()


def secrets():
    return load_cfg().secrets


# ---------------- Sentry SDK (error monitoring) ----------------
def init_sentry():
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from bugfixer.redact import scrub_obj

        def before_send(event, hint):
            return scrub_obj(event, secrets())
        sentry_sdk.init(dsn=dsn, environment=os.getenv("VERCEL_ENV", "development"), release=RELEASE or None, send_default_pii=False,
                        traces_sample_rate=0.0, before_send=before_send, max_breadcrumbs=30)
        return True
    except Exception as e:  # noqa: BLE001
        print("[waverise] Sentry not started:", e, flush=True)
        return False


SENTRY_ON = init_sentry()


# ---------------- recording ----------------
def record(raw):
    d = db()
    if not d:
        return None
    raw.setdefault("release", RELEASE)
    try:
        return detection.ingest(d, raw, secrets())
    except DBError:
        _failed()
    except Exception as e:  # noqa: BLE001 – never let error recording break a request
        print("[waverise] could not record error:", e.__class__.__name__, flush=True)
    return None


def report(kind, message, route="", status=None, exc=None, context=None, error_type=None):
    """Called from app code at known failure points (e.g. ffmpeg failed to build a download)."""
    record({"source": "server", "kind": kind, "error_type": error_type or (exc.__class__.__name__ if exc else "ServerError"),
            "message": str(message)[:1000], "route": route, "status_code": status, "force": True,
            "stack": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else None, "context": context or {}})


def _visitor(request):
    ip = (request.headers.get("x-real-ip") or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
          or (request.client.host if request.client else ""))
    return ip, anon(ip + "|" + (request.headers.get("user-agent") or "")[:200])


def _rate_ok(d, bucket, limit, window):
    row = d.one("""insert into bf_rate (bucket, n, reset_at) values (%s, 1, now() + make_interval(secs => %s))
                   on conflict (bucket) do update set
                     n = case when bf_rate.reset_at < now() then 1 else bf_rate.n + 1 end,
                     reset_at = case when bf_rate.reset_at < now() then excluded.reset_at else bf_rate.reset_at end
                   returning n""", (bucket, window))
    return row["n"] <= limit


def _route_of(request):
    r = request.scope.get("route")
    return getattr(r, "path", None) or request.url.path


# ---------------- admin helpers ----------------
def _admin(request, write=False):
    import wr_admin
    s = wr_admin.require(request, write)
    d = db()
    if not d:
        raise HTTPException(503, "The bug fixer database isn’t connected. Add DATABASE_URL in Vercel and redeploy.")
    if not d.ready():
        raise HTTPException(503, "The bug fixer tables don’t exist yet. Start the worker once (it creates them) or run: python -m bugfixer.migrate")
    return s, d


def _j(v):
    return json.loads(json.dumps(v, default=str))


def _json(data, status=200):
    import wr_admin
    return wr_admin.admin_json(_j(data), status)


ACTIONS = ("approve", "reject", "retry", "ignore", "unignore", "priority", "rollback", "requeue")


def install(app):
    # unhandled exceptions anywhere in the backend
    @app.middleware("http")
    async def capture_exceptions(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as e:
            path = request.url.path
            if not path.startswith(("/api/errors", "/_vercel")):
                kind = "download" if any(k in path for k in ("/download", "/convert", "/mp3", "/clip", "/gif", "/shrink", "/frame", "/status", "/sticker", "/ringtone")) else \
                       "database" if e.__class__.__module__.startswith(("psycopg", "wr_store")) else "backend"
                await run_in_threadpool(record, {"source": "server", "kind": kind, "error_type": e.__class__.__name__, "message": str(e)[:1000],
                                                 "route": _route_of(request), "url": path, "status_code": 500, "force": True,
                                                 "stack": traceback.format_exc(), "context": {"method": request.method}})
            raise

    # server errors raised on purpose (5xx HTTPException) — the 4xx answers are for visitors (private post …), not bugs
    prev = app.exception_handlers.get(StarletteHTTPException)

    async def http_errors(request: Request, exc: StarletteHTTPException):
        if exc.status_code >= 500 and request.url.path.startswith("/api/"):
            await run_in_threadpool(record, {"source": "server", "kind": "api", "error_type": f"HTTP{exc.status_code}", "message": str(exc.detail)[:1000],
                                             "route": _route_of(request), "url": request.url.path, "status_code": exc.status_code,
                                             "context": {"method": request.method}})
        if prev:
            return await prev(request, exc)
        from fastapi.exception_handlers import http_exception_handler
        return await http_exception_handler(request, exc)
    app.add_exception_handler(StarletteHTTPException, http_errors)

    # browser errors
    @app.post("/api/errors", include_in_schema=False)
    async def client_errors(request: Request):
        body = await request.body()
        if len(body) > 16000:
            return Response(status_code=413)
        origin = request.headers.get("origin") or ""
        host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip().lower()
        if origin and origin.split("://")[-1].split("/")[0].lower() != host:
            return Response(status_code=403)
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return Response(status_code=400)
        if not isinstance(data, dict):
            return Response(status_code=400)
        d = db()
        if not d:
            return Response(status_code=204)
        ip, visitor = _visitor(request)

        def go():
            try:
                if not _rate_ok(d, "err:" + hashlib.sha256(ip.encode()).hexdigest()[:16], 30, 60) or not _rate_ok(d, "err:all", 1200, 60):
                    return 429
                kind = data.get("kind") if data.get("kind") in ("frontend", "api", "upload", "download") else "frontend"
                record({"source": "client", "kind": kind, "error_type": str(data.get("error_type") or "Error")[:80], "message": str(data.get("message") or "")[:1000],
                        "url": data.get("url"), "route": str(data.get("route") or "")[:200], "status_code": data.get("status_code"),
                        "stack": str(data.get("stack") or "")[:6000], "origin": "/".join(str(data.get("url") or "").split("/", 3)[:3]),
                        "context": {k: data.get("context", {}).get(k) for k in ("file", "line", "col", "tool", "lang", "ua_mobile") if isinstance(data.get("context"), dict)},
                        "session_hash": visitor})
                return 204
            except DBError:
                _failed()
                return 204
        code = await run_in_threadpool(go)
        return Response(status_code=code, headers={"Cache-Control": "no-store"})

    # Sentry alert webhook (optional): asks the worker to import from Sentry now
    @app.post("/api/bugfixer/sentry-webhook", include_in_schema=False)
    async def sentry_webhook(request: Request):
        from bugfixer.services import verify_sentry_signature
        body = await request.body()
        if not verify_sentry_signature(body, request.headers.get("sentry-hook-signature", ""), os.getenv("SENTRY_WEBHOOK_SECRET", "")):
            raise HTTPException(401, "bad signature")
        d = db()
        if d:
            await run_in_threadpool(d.set_setting, "sentry_sync_due", True, "sentry-webhook")
        return Response(status_code=204)

    # ---------------- admin dashboard API ----------------
    @app.get("/api/admin/bugfixer/state", include_in_schema=False)
    def bf_state(request: Request):
        s, d = _admin(request)
        counts = d.all("select status, priority, count(*) as n from bf_bugs where not muted group by status, priority")
        workers = d.all("select id, heartbeat, started_at, version, current, extract(epoch from now() - heartbeat) as age from bf_workers order by heartbeat desc limit 5")
        pending = d.all("""select a.id, a.repair_id, a.requested_at, a.reasons, r.bug_id, r.pr_url, r.risk, b.title, b.priority from bf_approvals a
                           join bf_repairs r on r.id = a.repair_id join bf_bugs b on b.id = r.bug_id where a.decision is null order by a.id""")
        last24 = d.one("select count(*) as events, count(distinct bug_id) as bugs from bf_events where received_at > now() - interval '24 hours'")
        return _json({"configured": load_cfg().configured(), "settings": d.settings(), "counts": counts, "workers": workers, "approvals": pending,
                      "last24": last24, "sentry_sdk": SENTRY_ON})

    @app.get("/api/admin/bugfixer/bugs", include_in_schema=False)
    def bf_bugs(request: Request, status: str = "", priority_: str = "", kind: str = "", q: str = "", limit: int = 100):
        s, d = _admin(request)
        where, args = ["true"], []
        if status == "open":
            where.append("b.status = any(%s)"); args.append(list(detection.OPEN_STATES))
        elif status:
            where.append("b.status = %s"); args.append(status)
        if priority_:
            where.append("b.priority = %s"); args.append(priority_)
        if kind:
            where.append("b.kind = %s"); args.append(kind)
        if q:
            where.append("b.title ilike %s"); args.append(f"%{q[:80]}%")
        rows = d.all(f"""select b.id, b.title, b.kind, b.source, b.status, b.priority, b.priority_score, b.priority_reason, b.priority_manual, b.count, b.count_1h,
                                b.count_24h, b.sessions_24h, b.first_seen, b.last_seen, b.muted, b.last_error, b.next_action_at, b.lease_owner, b.cycles
                         from bf_bugs b where {' and '.join(where)}
                         order by case b.priority when 'critical' then 0 when 'high' then 1 when 'medium' then 2 else 3 end, b.priority_score desc, b.last_seen desc
                         limit %s""", args + [max(1, min(limit, 500))])
        return _json({"bugs": rows})

    @app.get("/api/admin/bugfixer/bug/{bug_id}", include_in_schema=False)
    def bf_bug(bug_id: int, request: Request):
        s, d = _admin(request)
        bug = d.one("select * from bf_bugs where id = %s", (bug_id,))
        if not bug:
            raise HTTPException(404, "No such bug")
        repairs = d.all("select * from bf_repairs where bug_id = %s order by id desc", (bug_id,))
        rids = [r["id"] for r in repairs] or [0]
        return _json({"bug": bug,
                      "events": d.all("select id, received_at, source, kind, error_type, message, url, route, status_code, stack, context, release from bf_events where bug_id=%s order by id desc limit 25", (bug_id,)),
                      "analyses": d.all("select * from bf_analyses where bug_id = %s order by id desc", (bug_id,)),
                      "repairs": repairs,
                      "tests": d.all("select * from bf_test_runs where repair_id = any(%s) order by id desc", (rids,)),
                      "deployments": d.all("select * from bf_deployments where repair_id = any(%s) order by id desc", (rids,)),
                      "approvals": d.all("select * from bf_approvals where repair_id = any(%s) order by id desc", (rids,)),
                      "rollbacks": d.all("select * from bf_rollbacks where bug_id = %s order by id desc", (bug_id,)),
                      "log": d.all("select id, at, level, actor, step, message, data from bf_log where bug_id = %s order by id desc limit 200", (bug_id,))})

    @app.get("/api/admin/bugfixer/events", include_in_schema=False)
    def bf_events(request: Request, after: int = 0):
        s, d = _admin(request)
        return _json({"events": d.all("""select e.id, e.bug_id, e.received_at, e.source, e.kind, e.error_type, e.message, e.route, e.status_code, b.priority
                                         from bf_events e join bf_bugs b on b.id = e.bug_id where e.id > %s order by e.id desc limit 60""", (after,))})

    @app.get("/api/admin/bugfixer/list/{what}", include_in_schema=False)
    def bf_list(what: str, request: Request, limit: int = 100):
        s, d = _admin(request)
        lim = max(1, min(limit, 300))
        sql = {"analyses": "select a.id, a.bug_id, a.created_at, a.model, a.category, a.summary, a.root_cause, a.affected_files, a.dependencies, a.fixable, a.confidence, a.risk, a.suggested_priority, a.fix_plan, a.tokens_in, a.tokens_out, b.title from bf_analyses a join bf_bugs b on b.id=a.bug_id order by a.id desc limit %s",
               "repairs": "select r.id, r.bug_id, r.attempt, r.created_at, r.status, r.branch, r.pr_number, r.pr_url, r.summary, r.diff, r.lines_changed, r.files, r.risk, r.policy, r.needs_approval, r.merge_sha, b.title from bf_repairs r join bf_bugs b on b.id=r.bug_id order by r.id desc limit %s",
               "tests": "select t.*, r.bug_id, r.pr_url, r.branch from bf_test_runs t join bf_repairs r on r.id=t.repair_id order by t.id desc limit %s",
               "deployments": "select d.*, r.bug_id, r.pr_url from bf_deployments d join bf_repairs r on r.id=d.repair_id order by d.id desc limit %s",
               "rollbacks": "select k.*, b.title from bf_rollbacks k left join bf_bugs b on b.id=k.bug_id order by k.id desc limit %s",
               "log": "select l.id, l.at, l.bug_id, l.repair_id, l.level, l.actor, l.step, l.message from bf_log l where l.level <> 'debug' order by l.id desc limit %s"}.get(what)
        if not sql:
            raise HTTPException(404, "Unknown list")
        return _json({what: d.all(sql, (lim,))})

    @app.post("/api/admin/bugfixer/settings", include_in_schema=False)
    async def bf_settings(request: Request):
        s, d = _admin(request, write=True)
        body = await request.json()
        allowed = {"paused": bool, "auto_analyze": bool, "auto_repair": bool}
        changed = {}
        for k, t in allowed.items():
            if k in body:
                changed[k] = t(body[k])
        if "auto_deploy" in body:
            if body["auto_deploy"] not in ("approval", "low_risk", "auto"):
                raise HTTPException(400, "auto_deploy must be approval, low_risk or auto")
            changed["auto_deploy"] = body["auto_deploy"]
        if "min_priority" in body:
            if body["min_priority"] not in priority.LEVELS:
                raise HTTPException(400, "bad priority")
            changed["min_priority"] = body["min_priority"]
        for k, v in changed.items():
            d.set_setting(k, v, s["email"])
        import wr_admin
        wr_admin.audit(request, s["email"], "bugfixer.settings", changed)
        d.log(f"Settings changed: {changed}", actor=s["email"], step="settings")
        return _json({"ok": True, "settings": d.settings()})

    @app.post("/api/admin/bugfixer/bug/{bug_id}/action", include_in_schema=False)
    async def bf_action(bug_id: int, request: Request):
        s, d = _admin(request, write=True)
        body = await request.json()
        action, who = body.get("action"), s["email"]
        if action not in ACTIONS:
            raise HTTPException(400, "Unknown action")
        bug = d.one("select * from bf_bugs where id = %s", (bug_id,))
        if not bug:
            raise HTTPException(404, "No such bug")
        rep = d.one("select * from bf_repairs where bug_id = %s order by id desc limit 1", (bug_id,))
        msg = None
        if action in ("approve", "reject"):
            if bug["status"] != "awaiting_approval" or not rep:
                raise HTTPException(409, "This bug isn’t waiting for approval.")
            if action == "approve" and body.get("confirm") != f"DEPLOY {bug_id}":
                raise HTTPException(400, f"Type DEPLOY {bug_id} to confirm the production deploy.")
            d.exec("update bf_approvals set decision=%s, decided_by=%s, decided_at=now() where repair_id=%s and decision is null", (action, who, rep["id"]))
            d.exec("update bf_bugs set next_action_at=now(), updated_at=now() where id=%s", (bug_id,))
            msg = "Approved for production." if action == "approve" else "Rejected; the pull request will be closed."
        elif action == "retry":
            if bug["status"] not in ("failed", "needs_human", "tests_failed", "staging_failed", "rejected", "rolled_back"):
                raise HTTPException(409, "Only finished or failed bugs can be retried.")
            d.exec("update bf_bugs set status='queued', attempts=0, last_error=null, next_action_at=now(), lease_owner=null, lease_until=null, updated_at=now() where id=%s", (bug_id,))
            msg = "Queued again."
        elif action == "requeue":
            if bug["status"] not in ("new", "regressed"):
                raise HTTPException(409, "Only new bugs can be queued now.")
            d.exec("update bf_bugs set status='queued', next_action_at=now(), updated_at=now() where id=%s", (bug_id,))
            msg = "Queued now (owner request)."
        elif action == "ignore":
            if bug["status"] in ("deploying", "verifying", "rolling_back"):
                raise HTTPException(409, "Can’t ignore a bug while it is being deployed or rolled back.")
            d.exec("update bf_bugs set muted=true, status='ignored', updated_at=now() where id=%s", (bug_id,))
            msg = "Ignored."
        elif action == "unignore":
            d.exec("update bf_bugs set muted=false, status='new', updated_at=now() where id=%s", (bug_id,))
            msg = "Watching again."
        elif action == "priority":
            p = body.get("value")
            if p not in priority.LEVELS:
                raise HTTPException(400, "Choose critical, high, medium or low.")
            d.exec("update bf_bugs set priority=%s, priority_manual=true, priority_reason=%s, updated_at=now() where id=%s", (p, f"set by {who}", bug_id))
            msg = f"Priority set to {p}."
        elif action == "rollback":
            if bug["status"] != "verified" or not rep or not rep.get("merge_sha"):
                raise HTTPException(409, "Only a fix that is live in production can be rolled back.")
            if body.get("confirm") != f"ROLLBACK {bug_id}":
                raise HTTPException(400, f"Type ROLLBACK {bug_id} to confirm.")
            from bugfixer import deploy

            class _C:  # minimal context for start_rollback (database only)
                pass
            c = _C(); c.db = d; c.log = lambda b, m, **kw: d.log(m, bug_id=bug_id, repair_id=rep["id"], level=kw.get("level", "info"), step=kw.get("step"), actor=who)
            deploy.start_rollback(c, bug, rep, str(body.get("reason") or "Owner requested a rollback")[:500], actor=who)
            d.exec("update bf_bugs set status='rolling_back', next_action_at=now(), updated_at=now() where id=%s", (bug_id,))
            msg = "Rollback started."
        import wr_admin
        wr_admin.audit(request, who, f"bugfixer.{action}", {"bug": bug_id, "value": body.get("value")})
        d.log(f"Owner action: {action}" + (f" ({body.get('value')})" if body.get("value") else ""), bug_id=bug_id, actor=who, step="admin")
        return _json({"ok": True, "message": msg})

