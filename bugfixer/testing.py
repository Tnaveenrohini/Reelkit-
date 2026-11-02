"""Module 4 – Automatic testing. The pull request's commit is tested by GitHub Actions (.github/workflows/autofix-ci.yml):
lint, unit, integration, regression and security jobs run on a fresh, isolated machine. The worker never runs the patched
code itself. It reads the check results; a patch that fails any required check is rejected."""
from datetime import datetime, timezone

from .db import jsonb


def evaluate(runs, required):
    """runs: GitHub check runs. Returns (state, checks) with state pending | passed | failed."""
    latest = {}
    for r in runs:                                   # a re-run creates a newer check with the same name
        if r["name"] not in latest or (r.get("started_at") or "") > (latest[r["name"]].get("started_at") or ""):
            latest[r["name"]] = r
    checks = [{"name": n, "status": r["status"], "conclusion": r.get("conclusion"), "url": r.get("html_url") or r.get("details_url"),
               "required": n in required, "id": r.get("id"), "summary": ((r.get("output") or {}).get("summary") or "")[:500]}
              for n, r in sorted(latest.items())]
    req = {c["name"]: c for c in checks if c["required"]}
    # a cancelled check is not a test result (superseded or re-run): keep waiting; the checks timeout still applies
    if any(c["status"] == "completed" and c["conclusion"] not in ("success", "skipped", "neutral", "cancelled") for c in req.values()):
        return "failed", checks
    if len(req) < len(required) or any(c["status"] != "completed" or c["conclusion"] == "cancelled" for c in req.values()):
        return "pending", checks
    return "passed", checks


def check(ctx, bug, repair):
    """Returns 'pending' | 'passed' | 'failed' | 'timed_out' and records the test run."""
    db, cfg = ctx.db, ctx.cfg
    run = db.one("select * from bf_test_runs where repair_id = %s and sha = %s order by id desc limit 1", (repair["id"], repair["head_sha"]))
    if not run:
        run = db.one("insert into bf_test_runs (repair_id, sha, status) values (%s, %s, 'running') returning *", (repair["id"], repair["head_sha"]))
        ctx.log(bug, f"Waiting for CI checks on {repair['head_sha'][:7]}: {', '.join(cfg.required_checks)}.", step="testing", repair_id=repair["id"])
    state, checks = evaluate(ctx.gh.check_runs(repair["head_sha"]), cfg.required_checks)
    age_min = (datetime.now(timezone.utc) - run["started_at"]).total_seconds() / 60
    if state == "pending" and age_min > cfg.checks_timeout_min:
        state = "timed_out"
    log = None
    if state == "failed":
        parts = []
        for c in checks:
            if c["required"] and c["conclusion"] not in ("success", "skipped", "neutral", None):
                parts.append(f"=== {c['name']}: {c['conclusion']} ===\n" + (ctx.gh.job_log_tail(c["id"], 5000) if c.get("id") else c["summary"]))
        log = "\n\n".join(parts)[-15000:]
    if state != "pending":
        db.exec("update bf_test_runs set status=%s, checks=%s, failure_log=%s, finished_at=now() where id=%s",
                ({"timed_out": "timed_out"}.get(state, state), jsonb(checks), log, run["id"]))
        ctx.log(bug, f"CI {state}: " + ", ".join(f"{c['name']}={c['conclusion'] or c['status']}" for c in checks if c["required"]),
                level="info" if state == "passed" else "warn", step="testing", repair_id=repair["id"])
    else:
        db.exec("update bf_test_runs set checks=%s where id=%s", (jsonb(checks), run["id"]))
    return state, log
