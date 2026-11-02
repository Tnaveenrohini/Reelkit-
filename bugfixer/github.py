"""GitHub REST client: read the code, create autofix branches and pull requests, read check results, merge and revert.
It never force-pushes and never writes to the base branch except through a pull-request merge."""
import base64
import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time

import httpx


class GitHubError(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status = status


class GitHub:
    def __init__(self, token, repo, api="https://api.github.com", timeout=30, git_url="", git_auth="header", tmp_root=None):
        if not token or not repo or "/" not in repo:
            raise GitHubError("GITHUB_TOKEN and GITHUB_REPO (owner/name) are required")
        self.repo, self.api, self._token = repo, api.rstrip("/"), token
        self.git_url = git_url or ("https://github.com/" if self.api == "https://api.github.com" else self.api.replace("/api/v3", "") + "/") + repo + ".git"
        # the token goes in a per-command header (never in the URL or saved git config); "none" = git credentials are set up outside
        self.git_auth_header = ("Authorization: Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()) if git_auth == "header" else ""
        self.tmp_root = tmp_root or tempfile.gettempdir()
        self.c = httpx.Client(timeout=timeout, follow_redirects=True, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "waverise-bugfixer"})

    def _req(self, method, path, ok=(200, 201, 204), **kw):
        url = path if path.startswith("http") else f"{self.api}/repos/{self.repo}{path}"
        for attempt in range(3):
            try:
                r = self.c.request(method, url, **kw)
            except httpx.HTTPError as e:
                if attempt == 2:
                    raise GitHubError(f"GitHub unreachable: {e.__class__.__name__}") from None
                time.sleep(2 * (attempt + 1)); continue
            if r.status_code in (502, 503, 504) or (r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0"):
                if attempt < 2:
                    time.sleep(min(60, 5 * (attempt + 1))); continue
            if r.status_code not in ok:
                try:
                    msg = r.json().get("message", "")
                except ValueError:
                    msg = r.text[:200]
                raise GitHubError(f"GitHub {method} {path.split('?')[0]} → {r.status_code}: {msg}", r.status_code)
            return r
        raise GitHubError("GitHub request failed")

    def j(self, method, path, **kw):
        r = self._req(method, path, **kw)
        return r.json() if r.content else {}

    # ---------- reading ----------
    def branch_sha(self, branch):
        return self.j("GET", f"/git/ref/heads/{branch}")["object"]["sha"]

    def file(self, path, ref):
        """Text of a file at a commit, or None if it doesn't exist."""
        try:
            d = self.j("GET", f"/contents/{path}", params={"ref": ref})
        except GitHubError as e:
            if e.status == 404:
                return None
            raise
        if isinstance(d, list) or d.get("type") != "file":
            return None
        if d.get("encoding") == "base64" and d.get("content") is not None and d.get("size", 0) <= 1_000_000:
            return base64.b64decode(d["content"]).decode("utf-8")
        blob = self.j("GET", f"/git/blobs/{d['sha']}")
        return base64.b64decode(blob["content"]).decode("utf-8")

    def snapshot(self, sha, dest_root):
        """Download the repository at `sha` into dest_root/sha (cached). Files are only read, never run.
        Unsafe archive entries (links, absolute paths, ..) are skipped."""
        dest = os.path.join(dest_root, sha)
        if os.path.isfile(os.path.join(dest, ".complete")):
            return dest
        os.makedirs(dest, exist_ok=True)
        r = self._req("GET", f"/tarball/{sha}")
        with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tf:
            for m in tf.getmembers():
                parts = m.name.split("/", 1)
                if len(parts) < 2 or not parts[1] or not (m.isfile() or m.isdir()):
                    continue
                rel = parts[1]
                if rel.startswith("/") or ".." in rel.split("/") or m.size > 3_000_000:
                    continue
                target = os.path.join(dest, rel)
                if m.isdir():
                    os.makedirs(target, exist_ok=True); continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with tf.extractfile(m) as src, open(target, "wb") as out:
                    out.write(src.read())
        open(os.path.join(dest, ".complete"), "w").close()
        return dest

    # ---------- writing (branches only, with the git command) ----------
    def _git(self, *args, cwd=None, check=True):
        cmd = ["git"]
        if self.git_auth_header:
            cmd += ["-c", f"http.extraHeader={self.git_auth_header}"]
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true")
        r = subprocess.run(cmd + list(args), cwd=cwd, capture_output=True, text=True, timeout=300, env=env)
        if check and r.returncode:
            err = (r.stderr or r.stdout).replace(self._token, "[secret]") if self._token else (r.stderr or r.stdout)
            raise GitHubError(f"git {args[0]} failed: {err.strip()[-400:]}")
        return r.stdout.strip()

    def _workdir(self, *shas):
        os.makedirs(self.tmp_root, exist_ok=True)
        work = tempfile.mkdtemp(prefix="bf-git-", dir=self.tmp_root)
        self._git("init", "-q", cwd=work)
        self._git("remote", "add", "origin", self.git_url, cwd=work)
        for sha, depth in shas:
            self._git("fetch", "-q", "--no-tags", f"--depth={depth}", "origin", sha, cwd=work)
        return work

    def remote_branch_sha(self, branch):
        out = self._git("ls-remote", self.git_url, f"refs/heads/{branch}")
        return out.split()[0] if out else None

    def ref_exists(self, branch):
        return self.remote_branch_sha(branch) is not None

    def commit_files(self, branch, base_sha, files, message, author=None):
        """Create the new branch `branch` (only autofix/…) with `files` {path: text} committed on top of base_sha. Never force-pushes."""
        if not branch.startswith("autofix/"):
            raise GitHubError("refusing to write outside autofix/ branches")
        existing = self.remote_branch_sha(branch)
        work = self._workdir((base_sha, 1))
        try:
            self._git("checkout", "-q", "-b", "work", base_sha, cwd=work)
            for path, text in files.items():
                full = os.path.join(work, path)
                if not os.path.realpath(full).startswith(os.path.realpath(work) + os.sep):
                    raise GitHubError(f"unsafe path {path}")
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8", newline="") as fh:
                    fh.write(text)
                self._git("add", "--", path, cwd=work)
            a = author or {"name": "WaveRise Autofix", "email": "autofix@waverise.in"}
            self._git("-c", f"user.name={a['name']}", "-c", f"user.email={a['email']}", "commit", "-q", "-m", message, cwd=work)
            head = self._git("rev-parse", "HEAD", cwd=work)
            if existing:                     # resumed after a crash: keep the branch if it already holds this exact change
                self._git("fetch", "-q", "--depth=1", "origin", existing, cwd=work)
                if self._git("rev-parse", f"{existing}^{{tree}}", cwd=work) == self._git("rev-parse", "HEAD^{tree}", cwd=work):
                    return existing
                raise GitHubError(f"branch {branch} already exists with different content")
            self._git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=work)
            return head
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def revert_commit_files(self, branch, base_sha, merge_sha, message):
        """New branch from base_sha with a commit that undoes merge_sha file by file. Fails if those files changed since the merge."""
        if not branch.startswith("autofix/"):
            raise GitHubError("refusing to write outside autofix/ branches")
        work = self._workdir((base_sha, 1), (merge_sha, 2))
        try:
            parent = self._git("rev-parse", f"{merge_sha}^", cwd=work)
            files = [f for f in self._git("diff", "--name-only", parent, merge_sha, cwd=work).splitlines() if f]
            self._git("checkout", "-q", "-b", "work", base_sha, cwd=work)
            for f in files:
                if self._blob(work, base_sha, f) != self._blob(work, merge_sha, f):
                    raise GitHubError(f"{f} changed after the fix was merged; a person must roll this back", 409)
            deletions = []
            for f in files:
                before = self._blob(work, parent, f)
                if before is None:
                    self._git("rm", "-q", "--", f, cwd=work); deletions.append(f)
                else:
                    self._git("checkout", "-q", parent, "--", f, cwd=work)
            self._git("-c", "user.name=WaveRise Autofix", "-c", "user.email=autofix@waverise.in", "commit", "-q", "-m", message, cwd=work)
            head = self._git("rev-parse", "HEAD", cwd=work)
            self._git("push", "-q", "origin", f"HEAD:refs/heads/{branch}", cwd=work)
            return head, sorted(files), deletions
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _blob(self, work, sha, path):
        r = subprocess.run(["git", "rev-parse", f"{sha}:{path}"], cwd=work, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    def delete_branch(self, branch):
        if not branch.startswith("autofix/"):
            raise GitHubError("refusing to delete a branch outside autofix/")
        if self.remote_branch_sha(branch):
            self._git("push", "-q", self.git_url, "--delete", branch)

    # ---------- pull requests ----------
    def open_pr(self, branch, base, title, body):
        existing = self.j("GET", "/pulls", params={"head": f"{self.repo.split('/')[0]}:{branch}", "state": "open"})
        if existing:
            return existing[0]
        return self.j("POST", "/pulls", json={"title": title[:250], "head": branch, "base": base, "body": body[:60000], "maintainer_can_modify": True})

    def pr(self, number):
        return self.j("GET", f"/pulls/{number}")

    def comment(self, number, body):
        return self.j("POST", f"/issues/{number}/comments", json={"body": body[:60000]})

    def close_pr(self, number, comment=None):
        if comment:
            self.comment(number, comment)
        return self.j("PATCH", f"/pulls/{number}", json={"state": "closed"})

    def merge_pr(self, number, head_sha, title):
        """Squash-merge, only if the PR head is still the commit that passed the checks."""
        return self.j("PUT", f"/pulls/{number}/merge", json={"merge_method": "squash", "sha": head_sha, "commit_title": title[:250]})

    # ---------- checks and statuses ----------
    def check_runs(self, sha):
        runs, page = [], 1
        while True:
            d = self.j("GET", f"/commits/{sha}/check-runs", params={"per_page": 100, "page": page})
            runs += d.get("check_runs", [])
            if len(runs) >= d.get("total_count", 0) or not d.get("check_runs"):
                return runs
            page += 1

    def statuses(self, sha):
        return self.j("GET", f"/commits/{sha}/status").get("statuses", [])

    def job_log_tail(self, job_id, chars=6000):
        try:
            r = self._req("GET", f"/actions/jobs/{job_id}/logs")
        except GitHubError:
            return ""
        text = r.text
        text = re.sub(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ", "", text, flags=re.M)
        return text[-chars:]
