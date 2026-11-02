"""The persistent worker. Run it on an always-on host:  python -m bugfixer.worker

Every bug moves through states (shown in the dashboard):
  new / regressed → queued → analyzed → testing → staging → (awaiting_approval) → deploying → verifying → verified
  failure states: needs_human, tests_failed, staging_failed, rejected, rolling_back → rolled_back, failed
A worker leases one bug at a time (lease renewed while it works). If a worker dies, the lease expires and any worker
resumes the bug from its saved state; each step first checks what was already done (branch, pull request, merge), so
nothing is done twice. Critical bugs are always taken first. Only one production change runs at a time."""
import signal
import sys
import threading
import time
import traceback

from . import VERSION, analysis, deploy, detection, jobs, repair, testing
from .context import Ctx
from .db import DBError, jsonb

ACTIVE = ("queued", "analyzed", "testing", "staging", "awaiting_approval", "deploying", "verifying", "rolling_back", "rejecting")
WAITING_STEP = {"testing": 60, "staging": 45, "verifying": 60, "rolling_back": 30, "awaiting_approval": 30}
LEASE_SEC, MAX_STEP_FAILURES = 600, 5
_stop = threading.Event()


def claim(ctx):
    """Lease the most urgent bug that is due. Critical → High → Medium → Low, then score, then oldest."""
    return ctx.db.one("""
        update bf_bugs set lease_owner = %(w)s, lease_until = now() + make_interval(secs => %(lease)s)
        where id = (
          select id from bf_bugs
          where status = any(%(active)s) and not muted and next_action_at <= now()
            and (lease_until is null or lease_until < now())
            -- one production change at a time: a merge waits while another change is live-but-unverified or being rolled
            -- back, and the oldest waiting merge goes first; changes already merged always continue
            and (status <> 'deploying'
                 or not exists (select 1 from bf_bugs o where o.id <> bf_bugs.id
                                and (o.status in ('verifying', 'rolling_back') or (o.status = 'deploying' and o.id < bf_bugs.id))))
          order by case priority when 'critical' then 0 when 'high' then 1 when 'medium' then 2 else 3 end,
                   priority_score desc, first_seen
          limit 1 for update skip locked)
        returning *""", {"w": ctx.cfg.worker_id, "lease": LEASE_SEC, "active": list(ACTIVE)})


def release(ctx, bug_id, **fields):
    fields.update(lease_owner=None, lease_until=None)
    ctx.set_bug(bug_id, **fields)


def later(ctx, bug, seconds, **fields):
    release(ctx, bug["id"], next_action_at=("sql", f"now() + interval '{int(seconds)} seconds'"), **fields)


def latest(ctx, table, bug_id):
    return ctx.db.one(f"select * from {table} where bug_id = %s order by id desc limit 1", (bug_id,))


def step(ctx, bug):
    """Run one step for a leased bug."""
    st, bid = bug["status"], bug["id"]
    settings = ctx.db.settings()
    if st == "queued":
        if not settings.get("auto_analyze", True):
            return later(ctx, bug, 300)
        aid, ok, res = analysis.analyze(ctx, bug)
        if ok:
            return release(ctx, bid, status="analyzed", attempts=0, cycles=bug["cycles"] + 1, next_action_at="now()")
        return release(ctx, bid, status="needs_human", attempts=0, last_error=f"Analysis: {res.get('category')} (confidence {res.get('confidence')}). {res.get('summary', '')}"[:900])
    if st == "analyzed":
        if not settings.get("auto_repair", True):
            return later(ctx, bug, 300)
        an = latest(ctx, "bf_analyses", bid)
        prev = latest(ctx, "bf_repairs", bid)
        attempt = 1 if not prev or prev["analysis_id"] != an["id"] else prev["attempt"] + 1
        feedback = None
        if prev and prev["analysis_id"] == an["id"]:
            if prev["status"] == "committed":            # crash after commit: continue with that repair
                return release(ctx, bid, status="testing", next_action_at="now()")
            feedback = "; ".join((prev.get("policy") or {}).get("reasons", [])) or "previous patch could not be applied"
            if prev["status"] == "tests_failed":
                tr = ctx.db.one("select failure_log from bf_test_runs where repair_id=%s order by id desc limit 1", (prev["id"],))
                feedback = "The tests failed:\n" + ((tr or {}).get("failure_log") or "")[-6000:]
        if attempt > ctx.cfg.max_repair_attempts:
            return release(ctx, bid, status="needs_human", last_error="No acceptable patch after the allowed attempts.")
        rid, state, why = repair.make_patch(ctx, bug, an, attempt, feedback)
        if state == "committed":
            return later(ctx, bug, 90, status="testing", attempts=0)
        return release(ctx, bid, status="analyzed", next_action_at="now()", attempts=0)   # next attempt with feedback (or needs_human)
    rep = latest(ctx, "bf_repairs", bid)
    if st == "testing":
        state, log = testing.check(ctx, bug, rep)
        if state == "pending":
            return later(ctx, bug, WAITING_STEP["testing"])
        if state == "passed":
            ctx.db.exec("update bf_repairs set status='tests_passed', updated_at=now() where id=%s", (rep["id"],))
            return later(ctx, bug, 30, status="staging", attempts=0)
        ctx.db.exec("update bf_repairs set status='tests_failed', updated_at=now() where id=%s", (rep["id"],))
        try:
            ctx.gh.close_pr(rep["pr_number"], f"Closed by the WaveRise AI Bug Fixer: required checks {state}. The patch was rejected.")
        except Exception as e:  # noqa: BLE001
            ctx.log(bug, f"Could not close PR: {e}", level="warn", step="testing")
        if rep["attempt"] < ctx.cfg.max_repair_attempts:
            ctx.log(bug, "Trying a new patch with the test failures as feedback.", step="repair")
            return release(ctx, bid, status="analyzed", next_action_at="now()", attempts=0)
        return release(ctx, bid, status="tests_failed", last_error=f"Required checks {state} after {rep['attempt']} attempt(s).")
    if st == "staging":
        an = ctx.db.one("select * from bf_analyses where id=%s", (rep["analysis_id"],))
        state = deploy.staging(ctx, bug, rep, an)
        if state == "pending":
            return later(ctx, bug, WAITING_STEP["staging"])
        if state == "failed":
            ctx.db.exec("update bf_repairs set status='staging_failed', updated_at=now() where id=%s", (rep["id"],))
            try:
                ctx.gh.close_pr(rep["pr_number"], "Closed by the WaveRise AI Bug Fixer: the staging deployment was not healthy.")
            except Exception:  # noqa: BLE001
                pass
            return release(ctx, bid, status="staging_failed", last_error="Staging deployment unhealthy.")
        ctx.db.exec("update bf_repairs set status='staging_ok', updated_at=now() where id=%s", (rep["id"],))
        need, why = deploy.needs_approval(ctx, bug, rep)
        if need:
            ctx.db.exec("insert into bf_approvals (repair_id, kind, reasons) values (%s, 'production_deploy', %s)",
                        (rep["id"], jsonb({"why": why, "policy": (rep.get("policy") or {}).get("reasons", [])})))
            ctx.log(bug, f"Waiting for the owner's approval to deploy: {why}.", step="approval", repair_id=rep["id"])
            return release(ctx, bid, status="awaiting_approval", attempts=0)
        ctx.log(bug, f"Deploying automatically: {why}.", step="approval", repair_id=rep["id"])
        return release(ctx, bid, status="deploying", next_action_at="now()", attempts=0)
    if st == "awaiting_approval":
        ap = ctx.db.one("select * from bf_approvals where repair_id=%s order by id desc limit 1", (rep["id"],))
        if ap and ap["decision"] == "approve":
            return release(ctx, bid, status="deploying", next_action_at="now()")
        if ap and ap["decision"] == "reject":
            return release(ctx, bid, status="rejecting", next_action_at="now()")
        return later(ctx, bug, WAITING_STEP["awaiting_approval"])
    if st == "rejecting":
        try:
            ctx.gh.close_pr(rep["pr_number"], "Closed: the owner rejected this automatic fix.")
        except Exception:  # noqa: BLE001
            pass
        ctx.db.exec("update bf_repairs set status='rejected', updated_at=now() where id=%s", (rep["id"],))
        return release(ctx, bid, status="rejected")
    if st == "deploying":
        ap = ctx.db.one("select decided_by from bf_approvals where repair_id=%s and decision='approve' order by id desc limit 1", (rep["id"],))
        state, info = deploy.merge(ctx, bug, rep, (ap or {}).get("decided_by") or "automatic")
        if state == "busy":
            return later(ctx, bug, 60)
        if state == "failed":
            return release(ctx, bid, status="needs_human", last_error=f"Production merge failed: {info}")
        return later(ctx, bug, 60, status="verifying", attempts=0)
    if st == "verifying":
        rep = ctx.db.one("select * from bf_repairs where id=%s", (rep["id"],))
        state, why = deploy.verify_production(ctx, bug, rep)
        if state == "pending":
            return later(ctx, bug, WAITING_STEP["verifying"])
        if state == "verified":
            ctx.db.exec("update bf_repairs set status='verified', updated_at=now() where id=%s", (rep["id"],))
            ctx.log(bug, "Fix verified in production.", step="deploy", repair_id=rep["id"])
            if bug.get("sentry_issue_id") and ctx.cfg.sentry_token:
                try:
                    ctx.sentry.resolve(bug["sentry_issue_id"])
                except Exception as e:  # noqa: BLE001
                    ctx.log(bug, f"Could not resolve the Sentry issue: {e}", level="warn", step="deploy")
            return release(ctx, bid, status="verified", fixed_at="now()", last_error=None)
        deploy.start_rollback(ctx, bug, rep, why)
        return release(ctx, bid, status="rolling_back", next_action_at="now()")
    if st == "rolling_back":
        state = deploy.rollback_step(ctx, bug, rep)
        if state == "pending":
            return later(ctx, bug, WAITING_STEP["rolling_back"])
        return release(ctx, bid, status="rolled_back" if state == "done" else "needs_human",
                       last_error=None if state == "done" else "Rollback needs a person.")
    release(ctx, bid)


def promote_new(ctx):
    """Move eligible new bugs into the queue."""
    settings = ctx.db.settings()
    in_flight = ctx.db.one("select count(*) as n from bf_bugs where status in ('deploying', 'verifying', 'rolling_back')")["n"]
    for b in ctx.db.all("select * from bf_bugs where status in ('new','regressed') and not muted order by id limit 200"):
        if b["kind"] == "probe" and (in_flight or jobs.probe_failing(b["route"]) < 2):
            continue        # an outage during a deploy is handled by its rollback; a recovered site needs no code change
        ok, why = detection.eligible_for_repair(b, settings, ctx.cfg)
        if ok:
            ctx.set_bug(b["id"], status="queued", next_action_at="now()", attempts=0)
            ctx.log(b, f"Queued for analysis ({b['priority']}).", step="queue")


class Heart(threading.Thread):
    """Renews the lease of the bug being worked on and the worker heartbeat."""

    def __init__(self, ctx, bug_id):
        super().__init__(daemon=True)
        self.ctx, self.bug_id, self.done = Ctx(ctx.cfg), bug_id, threading.Event()

    def run(self):
        while not self.done.wait(60):
            try:
                self.ctx.db.exec("update bf_bugs set lease_until = now() + make_interval(secs => %s) where id = %s and lease_owner = %s",
                                 (LEASE_SEC, self.bug_id, self.ctx.cfg.worker_id))
            except DBError:
                pass


def heartbeat(ctx, current=None):
    ctx.db.exec("""insert into bf_workers (id, heartbeat, version, current, info) values (%s, now(), %s, %s, %s)
                   on conflict (id) do update set heartbeat = now(), current = excluded.current, version = excluded.version""",
                (ctx.cfg.worker_id, VERSION, current, jsonb(ctx.cfg.configured())))


def periodic(ctx, state):
    now = time.time()

    def due(name, every):
        if now - state.get(name, 0) >= every:
            state[name] = now
            return True
        return False
    for name, every, fn in (("stats", 60, lambda: detection.refresh_stats(ctx.db)), ("promote", 30, lambda: promote_new(ctx)),
                            ("probes", 300, lambda: jobs.run_probes(ctx)),
                            ("sentry", 300, lambda: jobs.sync_sentry(ctx, state.setdefault("sentry_seen", {})) if ctx.cfg.sentry_token else 0),
                            ("retention", 86400, lambda: jobs.retention(ctx.db))):
        if name == "sentry" and ctx.db.settings().get("sentry_sync_due"):
            state[name] = 0
            ctx.db.set_setting("sentry_sync_due", False)
        if due(name, every):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                ctx.db.log(f"{name} job failed: {e}", level="warn", step=name)


def run_once(ctx, state):
    """One loop iteration (used by tests too). Returns True if a bug step ran."""
    heartbeat(ctx)
    periodic(ctx, state)
    if ctx.db.settings().get("paused"):
        return False
    bug = claim(ctx)
    if not bug:
        return False
    heartbeat(ctx, f"bug {bug['id']} ({bug['status']})")
    heart = Heart(ctx, bug["id"]); heart.start()
    try:
        step(ctx, bug)
    except Exception as e:  # noqa: BLE001 – any failure: back off and retry later, never lose the bug
        tries = (bug["attempts"] or 0) + 1
        msg = f"{e.__class__.__name__}: {e}"
        ctx.log(bug, f"Step '{bug['status']}' failed (try {tries}/{MAX_STEP_FAILURES}): {msg}", level="error", step=bug["status"],
                data={"trace": traceback.format_exc()[-3000:]})
        if tries >= MAX_STEP_FAILURES:
            release(ctx, bug["id"], status="failed", attempts=tries, last_error=msg[:900])
        else:
            release(ctx, bug["id"], attempts=tries, last_error=msg[:900], next_action_at=("sql", f"now() + interval '{min(3600, 60 * 2 ** tries)} seconds'"))
    finally:
        heart.done.set()
    return True


def main():
    from .config import load
    cfg = load()
    miss = cfg.missing_for_worker()
    if miss:
        print("[bugfixer] missing settings: " + ", ".join(miss) + " (see docs/BUGFIXER.md)", file=sys.stderr)
        sys.exit(2)
    ctx = Ctx(cfg)
    ctx.db.migrate()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: _stop.set())
    print(f"[bugfixer] worker {ctx.cfg.worker_id} v{VERSION} started (repo {ctx.cfg.github_repo}, model {ctx.cfg.model})", flush=True)
    state = {}
    while not _stop.is_set():
        try:
            worked = run_once(ctx, state)
        except DBError as e:
            print(f"[bugfixer] database problem: {e}; retrying in 15 s", flush=True)
            ctx.db.close(); _stop.wait(15); continue
        _stop.wait(0.5 if worked else 5)
    print("[bugfixer] worker stopped", flush=True)


if __name__ == "__main__":
    main()
