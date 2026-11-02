"""Module 1 – Bug detection: one entry point for every error source (the page, the backend, Sentry, site checks).
Each error becomes an event; events with the same fingerprint are one bug (no duplicate bug reports)."""
import re

from . import fingerprint, priority
from .db import jsonb
from .redact import scrub, scrub_obj, strip_query

KINDS = ("frontend", "backend", "api", "database", "upload", "download", "probe")
SOURCES = ("client", "server", "sentry", "probe")
# Messages the site shows on purpose (a private post, a limit). They are the site working correctly, not bugs.
EXPECTED = re.compile("|".join([
    r"private", r"deleted", r"no longer available", r"limiting requests", r"too many requests", r"live streams?",
    r"coming soon", r"paste (an?|the) ", r"is a photo", r"has no video", r"no video", r"longer than", r"type the largest size",
    r"smallest size must be", r"choose (360p|a )", r"not a supported", r"couldn.t read this link", r"needs? a login",
    r"protected", r"only public", r"check the link", r"isn.t a valid", r"wait a (few )?minutes?",
]), re.I)
NOISE = re.compile(r"ResizeObserver loop|Script error\.?$|Non-Error promise rejection|extension://|chrome-extension|moz-extension|"
                   r"top\.GLOBALS|__gCrWeb|webkit-masked-url|Can.t find variable: (gmo|zaraz)|AbortError|The operation was aborted|Load failed$", re.I)
OPEN_STATES = ("new", "queued", "analyzing", "analyzed", "repairing", "testing", "staging", "awaiting_approval", "deploying", "regressed")


def is_expected(ev):
    status, msg = ev.get("status_code"), str(ev.get("message") or "")
    if ev.get("kind") == "probe":
        return False
    if status and 400 <= int(status) < 500 and status not in (400, 404, 405):
        return True                       # 4xx answers with a reason for the visitor (private post, too long …)
    if status in (400, 404, 405) and ev.get("source") == "client" and EXPECTED.search(msg):
        return True
    if ev.get("source") == "client" and ev.get("error_type") == "visible_error" and EXPECTED.search(msg):
        return True
    if ev.get("source") == "server" and (EXPECTED.search(msg) or re.search(r"took too long|coming soon", msg, re.I)):
        return True                       # a deliberate answer (coming soon, free-server time limit), not a code bug
    return bool(NOISE.search(msg))


def clean(ev, secrets=()):
    kind = ev.get("kind") if ev.get("kind") in KINDS else "frontend"
    source = ev.get("source") if ev.get("source") in SOURCES else "client"
    status = ev.get("status_code")
    try:
        status = int(status) if status not in (None, "") else None
    except (TypeError, ValueError):
        status = None
    stack = scrub(ev.get("stack"), secrets)
    stack = stack[:8000] if stack else None
    frames = ev.get("frames")
    if not frames and stack:
        frames = fingerprint.parse_py_traceback(stack) if "Traceback" in stack or 'File "' in stack else fingerprint.parse_js_stack(stack, ev.get("origin", ""))
    return {"source": source, "kind": kind, "error_type": scrub(str(ev.get("error_type") or "Error"), secrets)[:120],
            "message": scrub(str(ev.get("message") or ""), secrets)[:1000], "url": strip_query(ev.get("url")),
            "route": strip_query(ev.get("route"))[:200], "status_code": status, "stack": stack, "frames": scrub_obj(frames or [], secrets)[:40],
            "context": scrub_obj(ev.get("context") or {}, secrets), "session_hash": (ev.get("session_hash") or "")[:64] or None,
            "release": str(ev.get("release") or "")[:64] or None, "sentry_issue_id": ev.get("sentry_issue_id"), "probe_failures": ev.get("probe_failures", 0)}


def title_for(ev):
    where = ev["route"] or ev["url"] or ""
    msg = ev["message"] or ev["error_type"]
    base = f"{ev['error_type']}: {msg}" if ev["error_type"] and ev["error_type"] not in msg else msg
    return (f"[{ev['kind']}] {base}" + (f" — {where}" if where else ""))[:240]


def _sentry_hints(ev):
    """A Sentry issue arrives as one event but stands for many: use Sentry's own event and user counts for the priority."""
    if ev["source"] != "sentry":
        return 0, 0
    c = ev["context"] or {}
    try:
        return int(c.get("sentry_count") or 0), int(c.get("sentry_users") or 0)
    except (TypeError, ValueError):
        return 0, 0


def ingest(db, raw, secrets=()):
    """Store one error. Returns {"bug_id", "new": bool} or {"ignored": reason}."""
    ev = clean(raw, secrets)
    if not raw.get("force") and is_expected(ev):
        return {"ignored": "expected"}
    js = ev["kind"] in ("frontend", "upload", "download") and ev["source"] == "client"
    fp = fingerprint.compute(ev["kind"], ev["error_type"], ev["message"], ev["frames"], ev["route"], js=js)
    if raw.get("fingerprint"):
        fp = str(raw["fingerprint"])[:64]
    c24, s24 = _sentry_hints(ev)
    pr, score, reasons = priority.assess(ev["kind"], ev["status_code"], ev["route"], 1, max(1, c24), max(1 if ev["session_hash"] else 0, s24),
                                         ev["probe_failures"], ev["error_type"], ev["message"])
    with db.tx() as cur:
        cur.execute("""
            insert into bf_bugs (fingerprint, title, kind, source, error_type, route, status_code, count, priority, priority_score, priority_reason, sentry_issue_id)
            values (%(fp)s, %(title)s, %(kind)s, %(source)s, %(et)s, %(route)s, %(status)s, 1, %(pr)s, %(score)s, %(why)s, %(sid)s)
            on conflict (fingerprint) do update set
              last_seen = now(), count = bf_bugs.count + 1, updated_at = now(),
              sentry_issue_id = coalesce(bf_bugs.sentry_issue_id, excluded.sentry_issue_id),
              status = case when bf_bugs.status in ('verified', 'fixed') and bf_bugs.fixed_at < now() - interval '2 minutes' then 'regressed'
                            else bf_bugs.status end,
              next_action_at = case when bf_bugs.status in ('verified', 'fixed') then now() else bf_bugs.next_action_at end
            returning id, (xmax = 0) as inserted, status""",
                    {"fp": fp, "title": title_for(ev), "kind": ev["kind"], "source": ev["source"], "et": ev["error_type"], "route": ev["route"],
                     "status": ev["status_code"], "pr": pr, "score": score, "why": "; ".join(reasons), "sid": ev["sentry_issue_id"]})
        row = cur.fetchone()
        bug_id = row["id"]
        cur.execute("""insert into bf_events (bug_id, source, kind, error_type, message, url, route, status_code, stack, frames, context, session_hash, release)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (bug_id, ev["source"], ev["kind"], ev["error_type"], ev["message"], ev["url"], ev["route"], ev["status_code"], ev["stack"],
                     jsonb(ev["frames"]), jsonb(ev["context"]), ev["session_hash"], ev["release"]))
        if ev["session_hash"]:
            cur.execute("insert into bf_bug_sessions (bug_id, session_hash) values (%s, %s) on conflict do nothing", (bug_id, ev["session_hash"]))
        if row["status"] == "regressed":
            cur.execute("insert into bf_log (bug_id, level, actor, step, message) values (%s, 'warn', 'detector', 'detect', %s)",
                        (bug_id, "The bug came back after its fix was deployed."))
        elif row["inserted"]:
            cur.execute("insert into bf_log (bug_id, actor, step, message, data) values (%s, 'detector', 'detect', %s, %s)",
                        (bug_id, f"New bug detected from {ev['source']} ({ev['kind']}).", jsonb({"priority": pr, "reasons": reasons})))
    return {"bug_id": bug_id, "new": bool(row["inserted"]), "status": row["status"]}


def refresh_stats(db):
    """Recount recent errors and visitors for open bugs and re-score their priority (manual priorities are kept)."""
    rows = db.all("""
        select b.id, b.kind, b.status_code, b.route, b.error_type, b.title, b.priority, b.priority_manual,
               extract(epoch from now() - b.last_seen) / 3600.0 as hours,
               (select count(*) from bf_events e where e.bug_id = b.id and e.received_at > now() - interval '1 hour') as c1,
               (select count(*) from bf_events e where e.bug_id = b.id and e.received_at > now() - interval '24 hours') as c24,
               (select count(*) from bf_bug_sessions s where s.bug_id = b.id and s.seen_at > now() - interval '24 hours') as s24,
               (select (context->>'consecutive_failures')::int from bf_events e where e.bug_id = b.id order by id desc limit 1) as probe_failures,
               (select context from bf_events e where e.bug_id = b.id and e.source = 'sentry' and e.received_at > now() - interval '24 hours'
                order by id desc limit 1) as sentry_ctx
        from bf_bugs b where b.status not in ('ignored') and b.last_seen > now() - interval '30 days'""")
    for r in rows:
        sc, su = _sentry_hints({"source": "sentry", "context": r["sentry_ctx"]}) if r["sentry_ctx"] else (0, 0)
        r["c24"], r["s24"] = max(r["c24"], sc), max(r["s24"], su)
        pr, score, reasons = priority.assess(r["kind"], r["status_code"], r["route"], r["c1"], r["c24"], r["s24"], r["probe_failures"] or 0,
                                             r["error_type"] or "", r["title"] or "", float(r["hours"] or 0))
        if r["priority_manual"]:
            pr = r["priority"]
        db.exec("update bf_bugs set count_1h=%s, count_24h=%s, sessions_24h=%s, priority=%s, priority_score=%s, priority_reason=%s where id=%s",
                (r["c1"], r["c24"], r["s24"], pr, score, "; ".join(reasons), r["id"]))
    return len(rows)


def eligible_for_repair(bug, settings, cfg):
    """A bug from the public page needs reports from at least N different visitors before the AI works on it
    (so one person can't steer the AI with made-up error reports)."""
    if bug["muted"] or bug["status"] not in ("new", "regressed"):
        return False, "not new"
    if priority.RANK.get(bug["priority"], 3) > priority.RANK.get(settings.get("min_priority", "medium"), 2):
        return False, f"priority {bug['priority']} is below the automatic threshold"
    if bug["source"] == "client" and (bug["sessions_24h"] or 0) < cfg.client_min_sessions:
        return False, f"waiting for reports from {cfg.client_min_sessions} different visitors"
    if bug["cycles"] >= cfg.max_cycles_per_bug:
        return False, "too many repair cycles; needs a person"
    return True, ""
