"""Check every integration with the real services:  python -m bugfixer.selftest [--write] [--only db,claude,github,sentry,vercel,site]

--write also tests GitHub write access: it creates a throwaway branch autofix/selftest-<time> with one file, then deletes it
(no pull request, nothing reaches the main branch). Exit code 0 = all configured checks passed."""
import argparse
import sys
import time

from .config import load


def check_db(cfg):
    from .db import DB
    db = DB(cfg.database_url)
    db.migrate()
    row = db.one("insert into bf_log (level, actor, step, message) values ('debug', 'selftest', 'selftest', 'self-test') returning id")
    db.exec("delete from bf_log where id = %s", (row["id"],))
    return f"connected, tables ready (schema {db.one('select value from bf_meta where key = %s', ('schema_version',))['value']})"


def check_claude(cfg):
    import anthropic
    kw = {"api_key": cfg.anthropic_api_key}
    if cfg.anthropic_base_url:
        kw["base_url"] = cfg.anthropic_base_url
    c = anthropic.Anthropic(**kw)
    tool = {"name": "ok", "description": "Confirm.", "input_schema": {"type": "object", "properties": {"ready": {"type": "boolean"}}, "required": ["ready"]}}
    r = c.messages.create(model=cfg.model, max_tokens=50, messages=[{"role": "user", "content": "Call the ok tool with ready=true."}],
                          tools=[tool], tool_choice={"type": "tool", "name": "ok"})
    used = [b for b in r.content if getattr(b, "type", "") == "tool_use"]
    if not used:
        raise RuntimeError("the model did not call the tool")
    return f"model {r.model} answered with a tool call ({r.usage.input_tokens} in / {r.usage.output_tokens} out tokens)"


def check_github(cfg, write=False):
    from .github import GitHub
    gh = GitHub(cfg.github_token, cfg.github_repo, cfg.github_api, git_url=cfg.git_url, git_auth=cfg.git_auth)
    repo = gh.j("GET", "")
    perms = repo.get("permissions") or {}
    sha = gh.branch_sha(cfg.base_branch)
    runs = gh.check_runs(sha)
    msg = f"{repo['full_name']} ({cfg.base_branch} at {sha[:7]}), push={perms.get('push')}, {len(runs)} check runs on {cfg.base_branch}"
    if write:
        branch = f"autofix/selftest-{int(time.time())}"
        head = gh.commit_files(branch, sha, {"tests/.bugfixer-selftest": f"selftest {time.ctime()}\n"}, "Bug fixer self-test (temporary branch)")
        ok = gh.branch_sha(branch) == head
        gh.delete_branch(branch)
        gone = not gh.ref_exists(branch)
        msg += f"; write test: branch created {'and verified' if ok else 'BUT NOT VERIFIED'}, deleted={gone}"
        if not (ok and gone):
            raise RuntimeError(msg)
    return msg


def check_sentry(cfg):
    from .services import Sentry
    issues = Sentry(cfg.sentry_token, cfg.sentry_org, cfg.sentry_project, cfg.sentry_api).unresolved_issues(limit=5)
    return f"{len(issues)} unresolved issue(s) readable"


def check_vercel(cfg):
    from .github import GitHub
    from .services import Vercel, deployment_id_from_status
    gh = GitHub(cfg.github_token, cfg.github_repo, cfg.github_api)
    sha = gh.branch_sha(cfg.base_branch)
    st = next((s for s in gh.statuses(sha) if s.get("context", "").lower() == cfg.vercel_status_context.lower()), None)
    if not st:
        raise RuntimeError(f"no '{cfg.vercel_status_context}' status on {cfg.base_branch} – is the Vercel GitHub integration connected?")
    d = Vercel(cfg.vercel_token, cfg.vercel_team_id, cfg.vercel_api).deployment(deployment_id_from_status(st.get("target_url")) or "")
    return f"production deployment {d['id']} is {d['state']} at {d['url']}"


def check_site(cfg):
    from .services import verify_site
    ok, res = verify_site(cfg.site_url, cfg.probe_paths)
    if not ok:
        raise RuntimeError("; ".join(f"{r['path']} {r['status']}" for r in res if not r["ok"]))
    return ", ".join(f"{r['path']} {r['status']} {r['ms']} ms" for r in res)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="also test GitHub write access with a throwaway branch")
    ap.add_argument("--only", default="")
    a = ap.parse_args(argv)
    cfg = load()
    plan = [("db", bool(cfg.database_url), lambda: check_db(cfg)), ("claude", bool(cfg.anthropic_api_key), lambda: check_claude(cfg)),
            ("github", bool(cfg.github_token), lambda: check_github(cfg, a.write)), ("sentry", bool(cfg.sentry_token), lambda: check_sentry(cfg)),
            ("vercel", bool(cfg.vercel_token and cfg.github_token), lambda: check_vercel(cfg)), ("site", True, lambda: check_site(cfg))]
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    bad = 0
    for name, configured, fn in plan:
        if only and name not in only:
            continue
        if not configured:
            print(f"–  {name:7} not configured")
            continue
        try:
            print(f"✓  {name:7} {fn()}")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"✗  {name:7} {e.__class__.__name__}: {str(e)[:300]}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
