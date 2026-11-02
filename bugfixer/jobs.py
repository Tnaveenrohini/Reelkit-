"""Detection jobs run by the worker: live site checks (synthetic monitoring) and Sentry synchronisation."""
from . import detection
from .services import ServiceError, sentry_event_to_report, verify_site

_probe_streak = {}


def run_probes(ctx):
    """Check the live site. Two failures in a row of the same address → a 'probe' bug (critical)."""
    ok, results = verify_site(ctx.cfg.site_url, ctx.cfg.probe_paths)
    for r in results:
        key = r["path"]
        if r["ok"]:
            if _probe_streak.get(key):
                n = ctx.db.exec("""update bf_bugs set status = 'fixed', fixed_at = now(), updated_at = now(),
                                   last_error = 'The site check passes again (recovered without a code change).'
                                   where fingerprint = %s and status in ('new', 'regressed')""", (f"probe:{key}",))
                if n:
                    ctx.db.log(f"Site check {key} passes again.", level="info", step="probe", actor="detector")
            _probe_streak[key] = 0
            continue
        _probe_streak[key] = _probe_streak.get(key, 0) + 1
        if _probe_streak[key] >= 2:
            detection.ingest(ctx.db, {"source": "probe", "kind": "probe", "error_type": "SiteCheckFailed",
                                      "message": f"Site check failed: {r['path']} answered {r['status'] or 'no response'}",
                                      "route": r["path"], "status_code": r["status"] or None,
                                      "context": {"consecutive_failures": _probe_streak[key], "ms": r["ms"], "site": ctx.cfg.site_url},
                                      "probe_failures": _probe_streak[key], "fingerprint": f"probe:{r['path']}"}, ctx.cfg.secrets)
    return ok, results


def probe_failing(path):
    """How many site checks of this path have failed in a row (in this worker)."""
    return _probe_streak.get(path or "", 0)


def sync_sentry(ctx, seen):
    """Import unresolved Sentry issues (new ones, or ones whose count grew) as error events."""
    n = 0
    for issue in ctx.sentry.unresolved_issues():
        iid, count = str(issue.get("id")), int(issue.get("count") or 0)
        if seen.get(iid) == count:
            continue
        try:
            event = ctx.sentry.latest_event(iid)
        except ServiceError:
            continue
        report = sentry_event_to_report(issue, event)
        r = detection.ingest(ctx.db, report, ctx.cfg.secrets)
        if r.get("bug_id"):
            ctx.db.exec("update bf_bugs set sentry_issue_id = %s where id = %s and sentry_issue_id is null", (iid, r["bug_id"]))
        seen[iid] = count
        n += 1
    return n


def retention(db):
    """Keep the database small: old events are trimmed (bugs, analyses, repairs and logs are kept as history)."""
    db.exec("delete from bf_events where received_at < now() - interval '60 days'")
    db.exec("delete from bf_events e using (select id, row_number() over (partition by bug_id order by id desc) as rn from bf_events) x "
            "where e.id = x.id and x.rn > 500")
    db.exec("delete from bf_bug_sessions where seen_at < now() - interval '7 days'")
    db.exec("delete from bf_rate where reset_at < now() - interval '1 hour'")
