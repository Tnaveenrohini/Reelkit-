"""Contract fakes for the services the worker talks to, served over real HTTP on 127.0.0.1:

* GitHub REST (refs, contents, tarball, pull requests, squash merge, check runs, commit statuses, job logs) backed by a real
  bare git repository, which is also the git remote the worker pushes to (file:// URL);
* the Claude Messages API (tool use), scripted per test;
* the Vercel deployments API, a fake website per deployment and the "live" site (health depends on the code at that commit);
* the Sentry issues API.

The request and response shapes follow the public API documentation of each service, so the worker's real HTTP clients
(httpx, the anthropic SDK, the git command) are exercised end to end."""
import base64
import itertools
import os
import socket
import subprocess
import threading
import time

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

OWNER, NAME = "waverise", "site"
REPO = f"{OWNER}/{NAME}"
BREAKS_PROD = "# BREAKS-PROD"

SEED = {
    "app.py": '''from fastapi import FastAPI

app = FastAPI()


def convert(params):
    url = params["url"]
    return url.strip()


@app.get("/api/convert")
def api_convert(url: str = ""):
    return {"ok": True, "url": convert({"url": url} if url else {})}
''',
    "wr_admin.py": "ADMIN = True\n",
    "templates/index.html": '<!doctype html><html><body><div id="chat"></div><script>var ready = true;</script></body></html>\n',
    ".github/workflows/ci.yml": "name: ci\non: [pull_request]\n",
    "README.md": "# test site\n",
}


def git(*args, cwd=None, input=None):
    r = subprocess.run(["git", "-c", "user.name=Seed", "-c", "user.email=seed@example.com", *args], cwd=cwd, capture_output=True,
                       input=input, timeout=60)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode()[-500:]}")
    return r.stdout


class World:
    """All fake state for one test."""

    def __init__(self, tmp):
        self.tmp = str(tmp)
        self.bare = os.path.join(self.tmp, "remote.git")
        git("init", "-q", "--bare", "-b", "main", self.bare)
        git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=self.bare)
        seed = os.path.join(self.tmp, "seed")
        git("init", "-q", "-b", "main", seed)
        for path, text in SEED.items():
            os.makedirs(os.path.dirname(os.path.join(seed, path)) or seed, exist_ok=True)
            with open(os.path.join(seed, path), "w") as fh:
                fh.write(text)
        git("add", "-A", cwd=seed)
        git("commit", "-q", "-m", "seed", cwd=seed)
        git("push", "-q", self.bare, "main", cwd=seed)
        self.git_url = "file://" + self.bare
        self.lock = threading.Lock()
        self.prs, self.comments, self.numbers = {}, [], itertools.count(1)
        self.checks = lambda sha: [check(n, "success") for n in ("lint", "unit", "integration", "regression", "security")]
        self.job_logs = {}
        self.vercel_ready_after = 0          # polls before a deployment reports READY
        self._polls = {}
        self.staging_breaks = False
        self.claude = None                   # callable(body) -> list of content blocks
        self.claude_requests = []
        self.sentry_issues, self.sentry_events, self.sentry_resolved = [], {}, []
        self.calls = []

    # ---------- repository helpers ----------
    def rev(self, ref):
        try:
            return git("rev-parse", "--verify", "-q", ref + "^{commit}", cwd=self.bare).decode().strip()
        except RuntimeError:
            return None

    def show(self, sha, path):
        try:
            return git("show", f"{sha}:{path}", cwd=self.bare).decode()
        except RuntimeError:
            return None

    def branches(self, prefix=""):
        out = git("for-each-ref", "--format=%(refname:short)", "refs/heads/", cwd=self.bare).decode().split()
        return [b for b in out if b.startswith(prefix)]

    def squash_merge(self, branch, title):
        work = os.path.join(self.tmp, f"merge-{time.time_ns()}")
        git("clone", "-q", self.bare, work)
        git("merge", "-q", "--squash", f"origin/{branch}", cwd=work)
        git("commit", "-q", "-m", title, cwd=work)
        git("push", "-q", "origin", "main", cwd=work)
        return git("rev-parse", "HEAD", cwd=work).decode().strip()


def check(name, conclusion, status="completed", started="2026-01-01T00:00:00Z", id_=None):
    return {"id": id_ or abs(hash(name)) % 10_000_000, "name": name, "status": status, "conclusion": conclusion if status == "completed" else None,
            "started_at": started, "html_url": f"https://github.com/{REPO}/runs/{name}", "output": {"summary": f"{name} {conclusion}"}}


def make_app(w: World, port_ref):
    app = FastAPI()
    base = f"/gh/repos/{OWNER}/{NAME}"

    def site_url(*parts):
        return f"http://127.0.0.1:{port_ref[0]}/" + "/".join(parts)

    def pr_view(pr):
        if pr["state"] == "open":
            pr["head"]["sha"] = w.rev(f"refs/heads/{pr['head']['ref']}") or pr["head"]["sha"]
        return pr

    @app.middleware("http")
    async def record(request: Request, call_next):
        w.calls.append(f"{request.method} {request.url.path}")
        return await call_next(request)

    # ---------------- GitHub REST ----------------
    @app.get(base)
    def repo():
        return {"full_name": REPO, "default_branch": "main", "permissions": {"push": True, "pull": True}}

    @app.get(base + "/git/ref/heads/{branch:path}")
    def ref(branch: str):
        sha = w.rev(f"refs/heads/{branch}")
        if not sha:
            raise HTTPException(404, "Not Found")
        return {"ref": f"refs/heads/{branch}", "object": {"sha": sha, "type": "commit"}}

    @app.get(base + "/contents/{path:path}")
    def contents(path: str, ref: str = "main"):
        text = w.show(ref, path)
        if text is None:
            raise HTTPException(404, "Not Found")
        return {"type": "file", "encoding": "base64", "size": len(text), "content": base64.b64encode(text.encode()).decode(), "sha": "x"}

    @app.get(base + "/tarball/{sha}")
    def tarball(sha: str):
        data = git("archive", "--format=tar.gz", f"--prefix={OWNER}-{NAME}-{sha[:7]}/", sha, cwd=w.bare)
        return Response(data, media_type="application/x-gzip")

    @app.get(base + "/pulls")
    def list_pulls(head: str = "", state: str = "open"):
        ref_ = head.split(":", 1)[-1]
        return [pr_view(p) for p in w.prs.values() if (not ref_ or p["head"]["ref"] == ref_) and (state == "all" or p["state"] == state)]

    @app.post(base + "/pulls", status_code=201)
    async def create_pull(request: Request):
        b = await request.json()
        head = w.rev(f"refs/heads/{b['head']}")
        if not head:
            raise HTTPException(422, "Validation Failed: head does not exist")
        n = next(w.numbers)
        w.prs[n] = {"number": n, "title": b["title"], "body": b.get("body", ""), "state": "open", "merged": False, "merge_commit_sha": None,
                    "html_url": f"https://github.com/{REPO}/pull/{n}", "head": {"ref": b["head"], "sha": head},
                    "base": {"ref": b["base"], "sha": w.rev("refs/heads/" + b["base"])}}
        return w.prs[n]

    @app.get(base + "/pulls/{n}")
    def get_pull(n: int):
        if n not in w.prs:
            raise HTTPException(404, "Not Found")
        return pr_view(w.prs[n])

    @app.patch(base + "/pulls/{n}")
    async def patch_pull(n: int, request: Request):
        b = await request.json()
        w.prs[n]["state"] = b.get("state", w.prs[n]["state"])
        return w.prs[n]

    @app.put(base + "/pulls/{n}/merge")
    async def merge_pull(n: int, request: Request):
        b = await request.json()
        with w.lock:
            pr = pr_view(w.prs[n])
            if pr["state"] != "open":
                return JSONResponse({"message": "Pull Request is not mergeable"}, 405)
            if b.get("sha") and b["sha"] != pr["head"]["sha"]:
                return JSONResponse({"message": "Head branch was modified. Review and try the merge again."}, 409)
            try:
                sha = w.squash_merge(pr["head"]["ref"], b.get("commit_title") or pr["title"])
            except RuntimeError as e:
                return JSONResponse({"message": f"Merge conflict: {e}"}, 405)
            pr.update(state="closed", merged=True, merge_commit_sha=sha)
            return {"sha": sha, "merged": True, "message": "Pull Request successfully merged"}

    @app.post(base + "/issues/{n}/comments", status_code=201)
    async def comment(n: int, request: Request):
        w.comments.append((n, (await request.json())["body"]))
        return {"id": len(w.comments)}

    @app.get(base + "/commits/{sha}/check-runs")
    def check_runs(sha: str):
        runs = w.checks(sha)
        return {"total_count": len(runs), "check_runs": runs}

    @app.get(base + "/commits/{sha}/status")
    def status(sha: str):
        return {"state": "success", "statuses": [
            {"context": "Vercel", "state": "success", "description": "Deployment has completed", "target_url": f"https://vercel.com/waverise/site/{sha}"}]}

    @app.get(base + "/actions/jobs/{job_id}/logs")
    def job_logs(job_id: int):
        return PlainTextResponse(w.job_logs.get(job_id, "2026-01-01T00:00:00.0000000Z FAILED tests/unit/test_x.py::test_convert - KeyError: 'url'\n"))

    # ---------------- Vercel ----------------
    @app.get("/vercel/v13/deployments/{dep}")
    def deployment(dep: str):
        sha = dep.removeprefix("dpl_")
        if not w.rev(sha):
            raise HTTPException(404, "deployment not found")
        w._polls[sha] = w._polls.get(sha, 0) + 1
        state = "READY" if w._polls[sha] > w.vercel_ready_after else "BUILDING"
        return {"id": dep, "url": site_url("site", "dep", sha), "readyState": state, "target": None, "alias": []}

    # ---------------- the website (per deployment, and live = main) ----------------
    def render(sha, path, live):
        code = w.show(sha, "app.py") or ""
        if BREAKS_PROD in code and (live or w.staging_breaks):
            return PlainTextResponse("Internal Server Error", 500)
        if path == "health":
            return JSONResponse({"ok": True, "sha": sha})
        if path == "":
            return Response(w.show(sha, "templates/index.html") or "", media_type="text/html")
        if path == "api/convert":
            return JSONResponse({"ok": True}) if 'params.get("url")' in code else PlainTextResponse("KeyError: 'url'", 500)
        return PlainTextResponse("ok")

    @app.api_route("/site/live/{path:path}", methods=["GET", "HEAD"])
    def live(path: str):
        return render(w.rev("refs/heads/main"), path, True)

    @app.api_route("/site/dep/{sha}/{path:path}", methods=["GET", "HEAD"])
    def dep_site(sha: str, path: str):
        return render(sha, path, False)

    # ---------------- Claude Messages API ----------------
    ids = itertools.count(1)

    @app.post("/claude/v1/messages")
    async def messages(request: Request):
        key = request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ")
        if key != "test-anthropic-key":
            return JSONResponse({"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}, 401)
        body = await request.json()
        w.claude_requests.append(body)
        blocks = w.claude(body)
        for b in blocks:
            if b["type"] == "tool_use":
                b.setdefault("id", f"toolu_{next(ids):04d}")
        return {"id": f"msg_{next(ids)}", "type": "message", "role": "assistant", "model": body["model"], "content": blocks,
                "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 1200, "output_tokens": 300}}

    # ---------------- Sentry ----------------
    @app.get("/sentry/api/0/projects/{org}/{project}/issues/")
    def issues(org: str, project: str, request: Request):
        if request.headers.get("authorization") != "Bearer test-sentry-token":
            raise HTTPException(401)
        return w.sentry_issues

    @app.get("/sentry/api/0/organizations/{org}/issues/{iid}/events/latest/")
    def latest(org: str, iid: str):
        return w.sentry_events[iid]

    @app.put("/sentry/api/0/organizations/{org}/issues/{iid}/")
    def resolve(org: str, iid: str):
        w.sentry_resolved.append(iid)
        return {"id": iid, "status": "resolved"}

    return app


class Server:
    def __init__(self, world):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(make_app(world, [self.port]), host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                return self
            time.sleep(0.02)
        raise RuntimeError("fake server did not start")

    def __exit__(self, *a):
        self.server.should_exit = True
        self.thread.join(5)


# ---------------- scripted Claude ----------------
def tool_use(name, inp):
    return [{"type": "tool_use", "name": name, "input": inp}]


class ScriptedClaude:
    """Behaves like the model in the tool loop: explores the code once with a real tool, then submits.
    `analysis` is the submit_analysis input; `patches` is a list of submit_patch inputs (one per attempt)."""

    def __init__(self, analysis, patches):
        self.analysis, self.patches, self.patch_i = analysis, list(patches), 0

    def __call__(self, body):
        names = {t["name"] for t in body["tools"]}
        explored = len(body["messages"]) > 1
        if "submit_analysis" in names:
            return tool_use("submit_analysis", self.analysis) if explored else tool_use("search_code", {"pattern": r"def convert"})
        if not explored:
            return tool_use("read_file", {"path": "app.py"})
        p = self.patches[min(self.patch_i, len(self.patches) - 1)]
        self.patch_i += 1
        return tool_use("submit_patch", p)


ANALYSIS = {"category": "code_bug", "summary": "Converting without a link crashes with KeyError.",
            "root_cause": "app.py convert() reads params['url'] without checking it exists (line 7).",
            "affected_files": [{"path": "app.py", "lines": "6-8", "reason": "convert() indexes a missing key"}],
            "dependencies": ["api_convert", "/api/convert"], "fixable": True, "confidence": 0.9, "risk": "low",
            "suggested_priority": "high", "fix_plan": "Use params.get('url') with an empty default.",
            "verification": [{"path": "/api/convert", "method": "GET"}, {"path": "https://evil.example/x"}]}

GOOD_PATCH = {"summary": "Handle a missing url parameter in convert()", "risk": "low", "test_plan": "New regression test calls convert({}).",
              "edits": [{"path": "./app.py", "old": '    url = params["url"]\n', "new": '    url = params.get("url") or ""\n'},
                        {"path": "tests/regression_fixes/test_convert_missing_url.py", "old": "",
                         "new": "from app import convert\n\n\ndef test_missing_url():\n    assert convert({}) == ''\n"}]}
