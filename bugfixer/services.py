"""Sentry API, Vercel API and HTTP health verification."""
import hashlib
import hmac
import time

import httpx


class ServiceError(Exception):
    pass


# ---------------- Sentry ----------------
class Sentry:
    def __init__(self, token, org, project, api="https://sentry.io"):
        if not (token and org and project):
            raise ServiceError("SENTRY_AUTH_TOKEN, SENTRY_ORG and SENTRY_PROJECT are required")
        self.org, self.project, self.api = org, project, api.rstrip("/")
        self.c = httpx.Client(timeout=20, headers={"Authorization": f"Bearer {token}"})

    def _get(self, path, **params):
        try:
            r = self.c.get(self.api + path, params=params)
        except httpx.HTTPError as e:
            raise ServiceError(f"Sentry unreachable: {e.__class__.__name__}") from None
        if r.status_code != 200:
            raise ServiceError(f"Sentry {path} → {r.status_code}")
        return r.json()

    def unresolved_issues(self, period="24h", limit=50):
        return self._get(f"/api/0/projects/{self.org}/{self.project}/issues/", query="is:unresolved", statsPeriod=period, limit=limit)[:limit]

    def latest_event(self, issue_id):
        return self._get(f"/api/0/organizations/{self.org}/issues/{issue_id}/events/latest/")

    def resolve(self, issue_id):
        try:
            r = self.c.put(f"{self.api}/api/0/organizations/{self.org}/issues/{issue_id}/", json={"status": "resolved"})
        except httpx.HTTPError as e:
            raise ServiceError(f"Sentry unreachable: {e.__class__.__name__}") from None
        if r.status_code not in (200, 202):
            raise ServiceError(f"Sentry resolve → {r.status_code}")


def sentry_event_to_report(issue, event):
    """Map a Sentry issue + its latest event to our error report format."""
    exc_values, frames = [], []
    for entry in event.get("entries", []):
        if entry.get("type") == "exception":
            exc_values = (entry.get("data") or {}).get("values") or []
    if not exc_values and event.get("exception"):
        exc_values = event["exception"].get("values") or []
    etype, msg = issue.get("metadata", {}).get("type") or "Error", issue.get("metadata", {}).get("value") or issue.get("title", "")
    if exc_values:
        last = exc_values[-1]
        etype, msg = last.get("type") or etype, last.get("value") or msg
        for f in ((last.get("stacktrace") or {}).get("frames") or []):
            fname = (f.get("filename") or f.get("absPath") or f.get("module") or "")
            frames.append({"file": fname.split("/")[-1] if fname.endswith(".py") else fname[:200], "function": (f.get("function") or "?")[:80],
                           "line": f.get("lineNo") or f.get("lineno") or 0, "in_app": bool(f.get("inApp", f.get("in_app")))})
    tags = {t.get("key"): t.get("value") for t in event.get("tags", []) if isinstance(t, dict)}
    url = tags.get("url") or (event.get("request") or {}).get("url") or ""
    route = tags.get("transaction") or event.get("transaction") or (event.get("culprit") or "")
    platform = event.get("platform") or issue.get("platform") or ""
    kind = "frontend" if platform == "javascript" else ("database" if "psycopg" in str(etype).lower() or "StoreError" in str(etype) else "backend")
    stack = "\n".join(f'  File "{f["file"]}", line {f["line"]}, in {f["function"]}' for f in frames)
    return {"source": "sentry", "kind": kind, "error_type": etype, "message": msg, "url": url, "route": route if str(route).startswith("/") else "",
            "status_code": None, "stack": ("Traceback (most recent call last):\n" + stack) if stack else None, "frames": frames,
            "context": {"sentry_issue": issue.get("id"), "sentry_count": issue.get("count"), "sentry_users": issue.get("userCount"),
                        "permalink": issue.get("permalink"), "level": issue.get("level")},
            "session_hash": f"sentry-{issue.get('id')}", "sentry_issue_id": str(issue.get("id"))}


def verify_sentry_signature(body: bytes, signature: str, secret: str):
    if not secret or not signature:
        return False
    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, signature.strip())


# ---------------- Vercel ----------------
class Vercel:
    def __init__(self, token, team_id="", api="https://api.vercel.com"):
        if not token:
            raise ServiceError("VERCEL_TOKEN is required")
        self.api, self.team = api.rstrip("/"), team_id
        self.c = httpx.Client(timeout=20, headers={"Authorization": f"Bearer {token}"})

    def deployment(self, id_or_url):
        dep = id_or_url if id_or_url.startswith("dpl_") or "." in id_or_url else f"dpl_{id_or_url}"
        params = {"teamId": self.team} if self.team else {}
        try:
            r = self.c.get(f"{self.api}/v13/deployments/{dep}", params=params)
        except httpx.HTTPError as e:
            raise ServiceError(f"Vercel unreachable: {e.__class__.__name__}") from None
        if r.status_code != 200:
            raise ServiceError(f"Vercel deployment {dep} → {r.status_code}")
        d = r.json()
        url = d.get("url") or ""
        return {"id": d.get("id") or d.get("uid"), "url": (url if url.startswith(("https://", "http://")) else "https://" + url) if url else None,
                "state": d.get("readyState") or d.get("state"), "target": d.get("target"), "alias": d.get("alias") or []}


def deployment_id_from_status(target_url):
    """Vercel's GitHub commit status links to https://vercel.com/<team>/<project>/<deploymentId>."""
    if not target_url:
        return None
    tail = target_url.rstrip("/").split("/")[-1]
    return tail if tail and len(tail) >= 20 and tail.isalnum() else None


# ---------------- health verification ----------------
def verify_site(base, paths, bypass="", extra=None, timeout=25, markers=None):
    """GET each path; healthy = HTTP 200 (or the expected status) and, for pages, the WaveRise page markers.
    extra: [{"path": "/api/x", "method": "GET", "expect_not_5xx": True}] from the analysis (the failing route)."""
    headers = {"User-Agent": "waverise-bugfixer-health/1.0", "Cache-Control": "no-cache"}
    if bypass:
        headers["x-vercel-protection-bypass"] = bypass
        headers["x-vercel-set-bypass-cookie"] = "true"
    markers = markers or {"/": 'id="chat"', "/guides": 'class="gpage'}
    results, ok = [], True
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as c:
        for p in list(paths) + [x["path"] for x in (extra or [])]:
            spec = next((x for x in (extra or []) if x["path"] == p), {})
            t0 = time.time()
            try:
                r = c.request(spec.get("method", "GET"), base.rstrip("/") + p)
                code, body = r.status_code, r.text[:400000]
            except httpx.HTTPError as e:
                code, body = 0, f"{e.__class__.__name__}"
            ms = int((time.time() - t0) * 1000)
            if spec.get("expect_not_5xx"):
                good = 0 < code < 500
            else:
                good = code == 200 and (markers.get(p) is None or markers[p] in body)
                if p == "/health" and code == 200:
                    good = '"ok": true' in body.replace('"ok":true', '"ok": true')
            ok = ok and good
            results.append({"path": p, "status": code, "ms": ms, "ok": good})
    return ok, results
