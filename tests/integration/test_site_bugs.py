"""The website side: the real app still works without the bug fixer database, errors are captured, the admin API is private."""
import json

from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.integration.conftest import TEST_DB, needs_db
from tests.integration.site_helpers import admin_env, bug_app, signed_in

JS_ERR = {"kind": "frontend", "error_type": "TypeError", "message": "Cannot read properties of null (reading 'value')",
          "url": "https://www.waverise.in/?u=1", "route": "/", "stack": "TypeError: x\n    at showDl (https://www.waverise.in/:2205:40)"}


def test_real_site_works_without_the_bug_fixer_database(monkeypatch):
    admin_env(monkeypatch)
    import app as site
    c = TestClient(site.app)
    home = c.get("/")
    assert home.status_code == 200 and 'id="chat"' in home.text and "/api/errors" in home.text
    assert c.get("/health").status_code == 200
    assert c.get("/photo-mb-to-kb").status_code == 200 and c.get("/guides").status_code == 200
    assert c.post("/api/errors", json=JS_ERR).status_code == 204          # accepted and dropped: no database
    assert c.get("/api/admin/bugfixer/state").status_code == 401           # private


def test_admin_api_is_private_and_explains_missing_database(monkeypatch):
    admin_env(monkeypatch)
    c = TestClient(bug_app())
    for path in ("/api/admin/bugfixer/state", "/api/admin/bugfixer/bugs", "/api/admin/bugfixer/bug/1", "/api/admin/bugfixer/list/log"):
        assert c.get(path).status_code == 401
    h = signed_in(c, "stranger@example.com")
    assert c.get("/api/admin/bugfixer/state").status_code == 401           # signed, but not an admin
    h = signed_in(c)
    r = c.get("/api/admin/bugfixer/state")
    assert r.status_code == 503 and "DATABASE_URL" in r.json()["detail"]
    assert c.post("/api/admin/bugfixer/settings", json={"paused": True}, headers={"origin": h["origin"]}).status_code == 403


@needs_db
def test_browser_errors_are_recorded_deduplicated_and_rate_limited(pg, monkeypatch):
    admin_env(monkeypatch, TEST_DB)
    c = TestClient(bug_app())
    for ua in ("Mozilla/5.0 A", "Mozilla/5.0 B", "Mozilla/5.0 B"):
        assert c.post("/api/errors", json=JS_ERR, headers={"user-agent": ua, "origin": "http://testserver"}).status_code == 204
    bugs = pg.all("select * from bf_bugs")
    assert len(bugs) == 1 and bugs[0]["count"] == 3 and bugs[0]["kind"] == "frontend"
    assert pg.one("select count(*) as n from bf_bug_sessions")["n"] == 2
    ev = pg.one("select * from bf_events limit 1")
    assert ev["url"] == "https://www.waverise.in/" and ev["frames"][0]["function"] == "showDl"
    assert c.post("/api/errors", json=JS_ERR, headers={"origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/errors", content=b"x" * 17000).status_code == 413
    assert c.post("/api/errors", content=b"[1,2]").status_code == 400
    assert c.post("/api/errors", json={"kind": "api", "status_code": 429, "message": "Too many requests"}).status_code == 204
    assert pg.one("select count(*) as n from bf_bugs")["n"] == 1          # an expected answer is not a bug
    codes = [c.post("/api/errors", json=dict(JS_ERR, message=f"err {i}")).status_code for i in range(40)]
    assert 429 in codes


@needs_db
def test_backend_crashes_and_server_errors_are_captured_but_visitor_errors_are_not(pg, monkeypatch):
    admin_env(monkeypatch, TEST_DB)
    app = bug_app()

    @app.get("/api/download")
    def crash():
        return {}["url"]

    @app.get("/api/info")
    def unavailable():
        raise HTTPException(503, "Upstream failed")

    @app.get("/api/private")
    def private():
        raise HTTPException(422, "This post is private")
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/api/download").status_code == 500
    assert c.get("/api/info").status_code == 503
    assert c.get("/api/private").status_code == 422
    rows = {r["kind"]: r for r in pg.all("select * from bf_bugs")}
    assert set(rows) == {"download", "api"}
    assert rows["download"]["error_type"] == "KeyError" and rows["download"]["route"] == "/api/download"
    assert rows["api"]["status_code"] == 503 and rows["api"]["priority"] in ("high", "medium")
    assert "Traceback" in pg.one("select stack from bf_events where bug_id = %s", (rows["download"]["id"],))["stack"]


@needs_db
def test_admin_dashboard_api(pg, monkeypatch):
    admin_env(monkeypatch, TEST_DB)
    c = TestClient(bug_app())
    from bugfixer import detection
    bid = detection.ingest(pg, {"source": "server", "kind": "backend", "error_type": "KeyError", "message": "'url'", "route": "/api/convert",
                                "status_code": 500, "force": True})["bug_id"]
    h = signed_in(c)
    st = c.get("/api/admin/bugfixer/state").json()
    assert st["settings"]["auto_deploy"] == "approval" and st["configured"]["database"] is True
    assert all(not isinstance(v, str) or "postgresql://" not in v for v in st["configured"].values())     # never the secret values
    assert [b["id"] for b in c.get("/api/admin/bugfixer/bugs?status=open").json()["bugs"]] == [bid]
    assert c.get("/api/admin/bugfixer/bugs?priority_=low").json()["bugs"] == []
    assert c.get(f"/api/admin/bugfixer/bug/{bid}").json()["bug"]["title"].startswith("[backend]")
    assert c.get("/api/admin/bugfixer/bug/99999").status_code == 404
    assert len(c.get("/api/admin/bugfixer/events").json()["events"]) == 1
    for what in ("analyses", "repairs", "tests", "deployments", "rollbacks", "log"):
        assert c.get(f"/api/admin/bugfixer/list/{what}").status_code == 200
    assert c.get("/api/admin/bugfixer/list/secrets").status_code == 404

    # settings (CSRF + same-site origin required)
    assert c.post("/api/admin/bugfixer/settings", json={"auto_deploy": "auto"}, headers={"x-csrf-token": h["x-csrf-token"]}).status_code == 403
    assert c.post("/api/admin/bugfixer/settings", json={"auto_deploy": "yolo"}, headers=h).status_code == 400
    r = c.post("/api/admin/bugfixer/settings", json={"auto_deploy": "low_risk", "paused": True, "min_priority": "high"}, headers=h)
    assert r.status_code == 200 and r.json()["settings"]["auto_deploy"] == "low_risk" and pg.settings()["paused"] is True

    # actions
    act = lambda body: c.post(f"/api/admin/bugfixer/bug/{bid}/action", json=body, headers=h)
    assert act({"action": "approve", "confirm": f"DEPLOY {bid}"}).status_code == 409          # nothing waiting
    assert act({"action": "rollback", "confirm": f"ROLLBACK {bid}"}).status_code == 409       # nothing live
    assert act({"action": "priority", "value": "critical"}).status_code == 200
    b = pg.one("select * from bf_bugs where id = %s", (bid,))
    assert b["priority"] == "critical" and b["priority_manual"]
    from bugfixer import detection as d2
    d2.refresh_stats(pg)
    assert pg.one("select priority from bf_bugs where id = %s", (bid,))["priority"] == "critical"     # manual priority is kept
    assert act({"action": "requeue"}).status_code == 200
    assert act({"action": "ignore"}).status_code == 200 and pg.one("select muted from bf_bugs where id = %s", (bid,))["muted"]
    assert act({"action": "unignore"}).status_code == 200
    assert act({"action": "drop_database"}).status_code == 400
    log = [r["message"] for r in pg.all("select message from bf_log where actor = 'owner@waverise.in' order by id")]
    assert any("priority" in m for m in log) and any("Settings changed" in m for m in log)


@needs_db
def test_sentry_webhook_requires_a_valid_signature(pg, monkeypatch):
    import hashlib
    import hmac
    admin_env(monkeypatch, TEST_DB)
    monkeypatch.setenv("SENTRY_WEBHOOK_SECRET", "whsec-test-123456")
    c = TestClient(bug_app())
    body = json.dumps({"action": "created", "data": {"issue": {"id": "1"}}}).encode()
    assert c.post("/api/bugfixer/sentry-webhook", content=body, headers={"sentry-hook-signature": "bad"}).status_code == 401
    sig = hmac.new(b"whsec-test-123456", body, hashlib.sha256).hexdigest()
    assert c.post("/api/bugfixer/sentry-webhook", content=body, headers={"sentry-hook-signature": sig}).status_code == 204
    assert pg.settings()["sentry_sync_due"] is True


def test_dashboard_script_is_served_only_to_admins(monkeypatch):
    admin_env(monkeypatch)
    import app as site
    c = TestClient(site.app)
    assert c.get("/admin/bugs.js").status_code == 401
    signed_in(c)
    mine = c.get("/admin/bugs.js")
    assert mine.status_code == 200 and "bfpriority" in mine.text and "noindex" in mine.headers.get("x-robots-tag", "noindex")
