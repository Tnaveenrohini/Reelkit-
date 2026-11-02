"""Module 5 – Automatic deployment with rollback.
Staging: the pull request's Vercel preview deployment is checked for health (and for the bug's failing request).
Production: the pull request is squash-merged (only if its head is still the tested commit); Vercel deploys main; the live
site is checked several times and watched for the bug coming back. Any failure → automatic rollback through a revert
pull request (no force-push, history kept)."""
import time
from datetime import datetime, timezone

from . import policy
from .db import jsonb
from .github import GitHubError
from .services import deployment_id_from_status, verify_site

PROD_LOCK = 724002


def _vercel_status(ctx, sha):
    for s in ctx.gh.statuses(sha):
        if s.get("context", "").lower() == ctx.cfg.vercel_status_context.lower():
            return s
    return None


def _age_min(ts):
    return (datetime.now(timezone.utc) - ts).total_seconds() / 60


def _resolve_deployment(ctx, sha):
    """Returns ('pending'|'failed'|'ready', info)."""
    st = _vercel_status(ctx, sha)
    if not st or st["state"] == "pending":
        return "pending", None
    if st["state"] in ("failure", "error"):
        return "failed", {"target_url": st.get("target_url"), "description": st.get("description")}
    dep_id = deployment_id_from_status(st.get("target_url"))
    if not dep_id:
        return "failed", {"error": "no deployment id in the Vercel status", "target_url": st.get("target_url")}
    d = ctx.vercel.deployment(dep_id)
    if d["state"] in ("ERROR", "CANCELED"):
        return "failed", d
    if d["state"] != "READY":
        return "pending", d
    return "ready", d


def staging(ctx, bug, repair, analysis):
    """Returns 'pending' | 'healthy' | 'failed'."""
    db, cfg = ctx.db, ctx.cfg
    dep = db.one("select * from bf_deployments where repair_id=%s and environment='staging' and sha=%s order by id desc limit 1", (repair["id"], repair["head_sha"]))
    if not dep:
        dep = db.one("insert into bf_deployments (repair_id, environment, sha, status) values (%s,'staging',%s,'waiting') returning *", (repair["id"], repair["head_sha"]))
        ctx.log(bug, "Waiting for the staging (preview) deployment.", step="staging", repair_id=repair["id"])
    state, info = _resolve_deployment(ctx, repair["head_sha"])
    if state == "pending":
        if _age_min(dep["started_at"]) > cfg.deploy_timeout_min:
            db.exec("update bf_deployments set status='failed', health=%s, finished_at=now() where id=%s", (jsonb({"error": "timed out"}), dep["id"]))
            ctx.log(bug, "Staging deployment timed out.", level="warn", step="staging", repair_id=repair["id"])
            return "failed"
        return "pending"
    if state == "failed":
        db.exec("update bf_deployments set status='failed', health=%s, finished_at=now() where id=%s", (jsonb(info), dep["id"]))
        ctx.log(bug, "Staging deployment failed to build.", level="warn", step="staging", repair_id=repair["id"], data=info)
        return "failed"
    extra = [{"path": v["path"], "method": v.get("method") if v.get("method") in ("GET", "HEAD") else "GET", "expect_not_5xx": True} for v in (analysis or {}).get("verification") or []]
    ok, results = verify_site(info["url"], cfg.probe_paths, cfg.vercel_bypass, extra)
    db.exec("update bf_deployments set status=%s, url=%s, deployment_id=%s, health=%s, finished_at=now() where id=%s",
            ("healthy" if ok else "unhealthy", info["url"], info["id"], jsonb(results), dep["id"]))
    ctx.log(bug, f"Staging {'healthy' if ok else 'UNHEALTHY'} at {info['url']}: " + ", ".join(f"{r['path']} {r['status']}" for r in results),
            level="info" if ok else "warn", step="staging", repair_id=repair["id"])
    return "healthy" if ok else "failed"


def needs_approval(ctx, bug, repair):
    settings = ctx.db.settings()
    return policy.deploy_needs_approval(settings.get("auto_deploy", "approval"), repair.get("policy") or {}, bug["priority"])


def merge(ctx, bug, repair, approved_by=None):
    """Merge the tested pull request into the base branch (one production change at a time). Returns 'merged' | 'busy' | 'failed'."""
    db, cfg, gh = ctx.db, ctx.cfg, ctx.gh
    got = db.one("select pg_try_advisory_lock(%s) as ok", (PROD_LOCK,))["ok"]
    if not got:
        return "busy", "another production deploy is running"
    try:
        pr = gh.pr(repair["pr_number"])
        if pr.get("merged"):                        # resumed after a crash: the merge already happened
            merge_sha = pr.get("merge_commit_sha")
            if not db.one("select id from bf_deployments where repair_id=%s and environment='production'", (repair["id"],)):
                db.exec("insert into bf_deployments (repair_id, environment, sha, previous_sha, status, approved_by) values (%s,'production',%s,%s,'waiting',%s)",
                        (repair["id"], merge_sha, (pr.get("base") or {}).get("sha"), approved_by))
        else:
            if pr["state"] != "open":
                return "failed", "the pull request was closed"
            if pr["head"]["sha"] != repair["head_sha"]:
                return "failed", "the pull request changed after testing"
            prev = gh.branch_sha(cfg.base_branch)
            dep = db.one("insert into bf_deployments (repair_id, environment, sha, previous_sha, status, approved_by) values (%s,'production',%s,%s,'waiting',%s) returning id",
                         (repair["id"], repair["head_sha"], prev, approved_by))
            try:
                merge_sha = gh.merge_pr(repair["pr_number"], repair["head_sha"], f"Autofix #{bug['id']}: {(repair.get('summary') or '')[:80]} (#{repair['pr_number']})")["sha"]
            except GitHubError as e:
                db.exec("update bf_deployments set status='failed', health=%s, finished_at=now() where id=%s", (jsonb({"error": str(e)}), dep["id"]))
                return "failed", f"merge refused: {e}"
            db.exec("update bf_deployments set sha=%s where id=%s", (merge_sha, dep["id"]))
        db.exec("update bf_repairs set status='merged', merge_sha=%s, updated_at=now() where id=%s", (merge_sha, repair["id"]))
        ctx.log(bug, f"Merged pull request #{repair['pr_number']} into {cfg.base_branch} ({merge_sha[:7]}). Waiting for the production deployment.",
                step="deploy", repair_id=repair["id"])
        return "merged", merge_sha
    finally:
        db.one("select pg_advisory_unlock(%s)", (PROD_LOCK,))


def verify_production(ctx, bug, repair, checks_needed=3):
    """Returns 'pending' | 'verified' | 'unhealthy' (→ rollback)."""
    db, cfg = ctx.db, ctx.cfg
    dep = db.one("select * from bf_deployments where repair_id=%s and environment='production' order by id desc limit 1", (repair["id"],))
    state, info = _resolve_deployment(ctx, repair["merge_sha"])
    if state == "pending":
        if _age_min(dep["started_at"]) > cfg.deploy_timeout_min:
            db.exec("update bf_deployments set status='unhealthy', health=%s where id=%s", (jsonb({"error": "production deployment timed out"}), dep["id"]))
            return "unhealthy", "production deployment did not become ready"
        return "pending", None
    if state == "failed":
        db.exec("update bf_deployments set status='failed', health=%s, finished_at=now() where id=%s", (jsonb(info), dep["id"]))
        return "unhealthy", "production build failed"
    health = dep["health"] or {"rounds": []}
    ok_live, live = verify_site(cfg.site_url, cfg.probe_paths)
    ok_dep, direct = verify_site(info["url"], cfg.probe_paths[:2], cfg.vercel_bypass)
    new_events = db.one("select count(*) as n from bf_events where bug_id=%s and received_at > %s", (bug["id"], dep["started_at"]))["n"]
    health["rounds"].append({"at": time.time(), "site": live, "deployment": direct, "bug_events_since": new_events})
    rounds = health["rounds"]
    db.exec("update bf_deployments set url=%s, deployment_id=%s, health=%s, status=%s where id=%s",
            (info["url"], info["id"], jsonb(health), "ready", dep["id"]))
    if not (ok_live and ok_dep):
        db.exec("update bf_deployments set status='unhealthy', finished_at=now() where id=%s", (dep["id"],))
        return "unhealthy", "health check failed: " + ", ".join(f"{r['path']} {r['status']}" for r in live + direct if not r["ok"])
    if new_events >= 3:
        db.exec("update bf_deployments set status='unhealthy', finished_at=now() where id=%s", (dep["id"],))
        return "unhealthy", f"the bug happened {new_events} more times after the deploy"
    if len(rounds) < checks_needed:
        return "pending", None
    db.exec("update bf_deployments set status='healthy', finished_at=now() where id=%s", (dep["id"],))
    return "verified", None


def start_rollback(ctx, bug, repair, reason, actor="worker"):
    db = ctx.db
    dep = db.one("select id from bf_deployments where repair_id=%s and environment='production' order by id desc limit 1", (repair["id"],))
    row = db.one("insert into bf_rollbacks (deployment_id, repair_id, bug_id, reason, triggered_by, from_sha, status) values (%s,%s,%s,%s,%s,%s,'requested') returning id",
                 (dep["id"] if dep else None, repair["id"], bug["id"], reason[:1000], actor, repair.get("merge_sha")))
    ctx.log(bug, f"Rollback started ({actor}): {reason}", level="warn", step="rollback", repair_id=repair["id"])
    return row["id"]


def rollback_step(ctx, bug, repair):
    """Advance the open rollback. Returns 'pending' | 'done' | 'needs_human'."""
    db, cfg, gh = ctx.db, ctx.cfg, ctx.gh
    rb = db.one("select * from bf_rollbacks where repair_id=%s and status in ('requested','running') order by id desc limit 1", (repair["id"],))
    if not rb:
        return "done"
    if rb["status"] == "requested":
        got = db.one("select pg_try_advisory_lock(%s) as ok", (PROD_LOCK,))["ok"]
        if not got:
            return "pending"
        try:
            base = gh.branch_sha(cfg.base_branch)
            branch = f"autofix/revert-{repair['id']}-{int(time.time())}"
            try:
                head, files, _ = gh.revert_commit_files(branch, base, repair["merge_sha"], f"Revert autofix #{bug['id']} (#{repair['pr_number']})\n\nReason: {rb['reason']}")
                pr = gh.open_pr(branch, cfg.base_branch, f"Revert autofix #{bug['id']}", f"Automatic rollback.\n\n**Reason:** {rb['reason']}\n\nReverts #{repair['pr_number']} ({repair['merge_sha']}). Files: {', '.join(files)}")
                to_sha = gh.merge_pr(pr["number"], head, f"Revert autofix #{bug['id']} (#{pr['number']})")["sha"]
            except GitHubError as e:
                db.exec("update bf_rollbacks set status='needs_human', finished_at=now(), reason = reason || %s where id=%s", (f" | {e}", rb["id"]))
                ctx.log(bug, f"Rollback needs a person: {e}", level="error", step="rollback", repair_id=repair["id"])
                return "needs_human"
            db.exec("update bf_rollbacks set status='running', to_sha=%s, revert_pr=%s where id=%s", (to_sha, pr["number"], rb["id"]))
            db.exec("insert into bf_deployments (repair_id, environment, sha, previous_sha, status) values (%s,'production',%s,%s,'waiting')",
                    (repair["id"], to_sha, repair["merge_sha"]))
            ctx.log(bug, f"Revert merged ({to_sha[:7]}, PR #{pr['number']}). Waiting for production to redeploy.", level="warn", step="rollback", repair_id=repair["id"])
            return "pending"
        finally:
            db.one("select pg_advisory_unlock(%s)", (PROD_LOCK,))
    state, info = _resolve_deployment(ctx, rb["to_sha"])
    if state == "pending":
        return "pending"
    ok, results = (False, [{"error": "revert build failed"}]) if state == "failed" else verify_site(cfg.site_url, cfg.probe_paths)
    db.exec("update bf_deployments set status=%s, url=%s, health=%s, finished_at=now() where repair_id=%s and sha=%s",
            ("healthy" if ok else "unhealthy", (info or {}).get("url"), jsonb(results), repair["id"], rb["to_sha"]))
    db.exec("update bf_rollbacks set status=%s, finished_at=now() where id=%s", ("done" if ok else "needs_human", rb["id"]))
    db.exec("update bf_repairs set status='rolled_back', updated_at=now() where id=%s", (repair["id"],))
    ctx.log(bug, "Rollback finished: the site is healthy again." if ok else "Rollback deployed but the site is still unhealthy. A person must look now.",
            level="warn" if ok else "error", step="rollback", repair_id=repair["id"])
    return "done" if ok else "needs_human"
