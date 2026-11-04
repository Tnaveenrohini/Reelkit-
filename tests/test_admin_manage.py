"""Exercise real sessions, role checks, public controls and persistent configuration."""
import copy
import json
from http.cookies import SimpleCookie

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient

import app as backend
import wr_admin as A
import wr_manage as M
import wr_seo
from wr_store import STORE, _Memory


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(STORE, "r", _Memory())
    monkeypatch.setattr(STORE, "kind", "memory")
    monkeypatch.setattr(STORE, "_cache", {})
    monkeypatch.setenv("SESSION_SECRET", "test-only-session-secret-" * 3)
    monkeypatch.setenv("ADMIN_EMAILS", "owner@example.com")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    A._mem_rl.clear()


def login(email="owner@example.com", role=None):
    if role: STORE.hset("admin_roles", email, role)
    scope = {"type": "http", "method": "GET", "scheme": "https", "server": ("testserver", 443),
             "path": "/admin", "headers": [(b"host", b"testserver")], "query_string": b"", "client": ("testclient", 1)}
    response = Response()
    A.new_session(Request(scope), response, email)
    cookie = SimpleCookie(); cookie.load(response.headers["set-cookie"])
    client = TestClient(backend.app, base_url="https://testserver")
    client.cookies.set(A.COOKIE, cookie[A.COOKIE].value)
    state = client.get("/api/admin/state")
    assert state.status_code == 200, state.text
    client.headers.update({"Origin": "https://testserver", "X-CSRF-Token": state.json()["csrf"]})
    return client


def save(client, section, value):
    return client.post("/api/admin/manage/config/" + section, json=value)


def test_unauthenticated_and_csrf_requests_cannot_mutate():
    client = TestClient(backend.app, base_url="https://testserver")
    assert client.get("/api/admin/manage/config").status_code == 401
    client = login()
    assert client.post("/api/admin/manage/config/tools", json={"batch": False}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert M.config()["tools"].get("batch") is None
    assert client.post("/api/admin/manage/config/tools", json={"batch": False}, headers={"Origin": "https://evil.example"}).status_code == 403


@pytest.mark.parametrize("role,allowed", [("editor", ["guides"]), ("support", ["stats", "alerts"])])
def test_roles_enforced_on_new_and_legacy_endpoints(role, allowed):
    client = login(role + "@example.com", role)
    for path in ["roles", "backups", "history", "guides", "stats", "alerts"]:
        expected = 200 if path in allowed else 403
        assert client.get("/api/admin/manage/" + path).status_code == expected, path
    assert client.post("/api/admin/settings", json={"visibility": False}).status_code == 403
    assert client.post("/api/admin/manage/roles", json={"email": "me@example.com", "role": "owner"}).status_code == 403
    assert save(client, "batch", {"max_links": 20}).status_code == 403
    notice = {"enabled": True, "text": "New announcement", "pause_downloads": False, "start": 0, "end": 0}
    assert save(client, "announcement", notice).status_code == (200 if role == "editor" else 403)


def test_roles_are_revoked_immediately_and_bootstrap_owner_protected():
    owner, support = login(), login("support@example.com", "support")
    assert owner.post("/api/admin/manage/roles", json={"email": "owner@example.com", "role": "remove"}).status_code == 400
    assert owner.post("/api/admin/manage/roles", json={"email": "support@example.com", "role": "remove"}).status_code == 200
    assert support.get("/api/admin/state").status_code == 401
    assert owner.get("/api/admin/state").json()["role"] == "owner"


def test_tool_switch_controls_public_pages_sitemap_and_backend():
    owner = login()
    assert save(owner, "tools", {"batch": False, "mp3": False}).status_code == 200
    public = TestClient(backend.app)
    assert public.get("/batch-downloads").status_code == 503
    assert public.get("/api/mp3", params={"url": "https://instagram.com/reel/abc123/"}).status_code == 503
    assert public.post("/api/batches", json={"urls": ["https://instagram.com/reel/abc123/"]}).status_code == 503
    assert "batch-downloads" not in public.get("/sitemap.xml").text
    assert save(owner, "tools", {"batch": True, "mp3": True}).status_code == 200
    assert public.get("/batch-downloads").status_code == 200
    assert save(owner, "tools", {"does-not-exist": False}).status_code == 400


def test_disabled_video_cannot_be_downloaded_through_api(monkeypatch):
    owner = login(); assert save(owner, "tools", {"video": False}).status_code == 200
    monkeypatch.setattr(backend, "media", lambda *a, **kw: ("https://media.example/a.mp4", {}, True, {"title": "sample"}))
    response = owner.get("/api/download", params={"url": "https://instagram.com/p/abc123/"})
    assert response.status_code == 503


def test_batch_limits_and_leases_enforced_and_released(monkeypatch):
    owner = login()
    assert save(owner, "batch", {"max_links": 1, "concurrency": 1, "cooldown": 0, "requests_per_minute": 120}).status_code == 200
    url = "https://instagram.com/reel/abc123/"
    assert owner.post("/api/batches", json={"urls": [url, url]}).status_code == 400
    assert owner.post("/api/batches", json={"urls": ["https://example.com/private"]}).status_code == 400
    batch = owner.post("/api/batches", json={"urls": [url]}).json()
    token = batch["token"]
    assert token
    import hashlib
    ident = hashlib.sha256(b"testclient").hexdigest()[:24]
    key = STORE.k("batch_slot:" + ident + ":0")
    STORE.cmd("SET", key, "occupied", "NX", "EX", 60)
    headers = {"X-WaveRise-Batch": token}
    assert owner.get("/api/download", params={"url": url}, headers=headers).status_code == 429
    STORE.cmd("DEL", key)
    monkeypatch.setattr(backend, "media", lambda *a, **kw: ("media", {}, True, {"title": "sample"}))
    monkeypatch.setattr(backend, "stream", lambda *a, **kw: Response(b"video", media_type="video/mp4"))
    assert owner.get("/api/download", params={"url": url}, headers=headers).status_code == 200
    assert STORE.cmd("GET", key) is None
    assert owner.get("/api/download", params={"url": url.replace("abc123", "other1")}, headers=headers).status_code == 403
    assert owner.get("/api/download", params={"url": url}, headers={"X-WaveRise-Batch": "invalid"}).status_code == 403


def test_batch_cooldown_and_validation():
    owner = login()
    assert save(owner, "batch", {"cooldown": 3600}).status_code == 200
    data = {"urls": ["https://instagram.com/reel/abc123/"]}
    assert owner.post("/api/batches", json=data).status_code == 200
    assert owner.post("/api/batches", json=data).status_code == 429
    assert save(owner, "batch", {"max_links": -1}).status_code == 400
    assert save(owner, "batch", {"concurrency": 4}).status_code == 400


def test_scheduled_announcement_and_maintenance(monkeypatch):
    owner = login()
    now = int(M.time.time())
    notice = {"enabled": True, "text": "<script>alert('notice')</script>", "start": now + 100, "end": now + 200, "pause_downloads": True}
    assert save(owner, "announcement", notice).status_code == 200
    assert M.active_announcement() is None
    monkeypatch.setattr(M.time, "time", lambda: now + 101)
    assert M.active_announcement()["text"] == notice["text"]
    page = owner.get("/")
    assert notice["text"] not in page.text
    assert "\\u003cscript" in page.text
    assert owner.post("/api/info", json={"url": "https://instagram.com/reel/abc123/"}).status_code == 503
    monkeypatch.setattr(M.time, "time", lambda: now + 201)
    assert M.active_announcement() is None


GUIDE = {"slug": "test-new-guide", "title": "An original guide", "desc": "A useful new guide description.",
         "lead": "Learn how to use this tool with a few clear steps.", "cat": "photos", "tool": "video",
         "sections": [{"heading": "First step", "body": "<img src=x onerror=alert(1)>\nFollow these steps."}]}


def test_editor_draft_publish_update_and_unpublish():
    editor = login("editor@example.com", "editor")
    assert editor.post("/api/admin/manage/guides", json={"action": "draft", "guide": GUIDE}).status_code == 200
    assert editor.get("/guides/test-new-guide").status_code == 404
    assert editor.post("/api/admin/manage/guides", json={"action": "publish", "guide": GUIDE}).status_code == 200
    page = editor.get("/guides/test-new-guide")
    assert page.status_code == 200
    assert "&lt;img src=x onerror=alert(1)&gt;" in page.text
    assert "test-new-guide" in editor.get("/sitemap.xml").text
    changed = {**GUIDE, "title": "A changed draft title"}
    assert editor.post("/api/admin/manage/guides", json={"action": "draft", "guide": changed}).status_code == 200
    assert "A changed draft title" not in editor.get("/guides/test-new-guide").text
    assert editor.post("/api/admin/manage/guides", json={"action": "unpublish", "guide": changed}).status_code == 200
    assert editor.get("/guides/test-new-guide").status_code == 404
    assert "test-new-guide" not in editor.get("/sitemap.xml").text


def test_builtin_guide_unpublish_updates_home_and_popular_lists():
    editor = login("editor@example.com", "editor")
    slug = "resize-photo-20-50-kb-exam-forms"
    doc = editor.get("/api/admin/manage/guides").json()["rows"][slug]["draft"]
    assert editor.post("/api/admin/manage/guides", json={"action": "unpublish", "guide": doc}).status_code == 200
    page = editor.get("/").text
    assert 'href="/guides/' + slug + '"' not in page
    assert editor.get("/guides/" + slug).status_code == 404


def test_signed_backup_roundtrip_excludes_secrets_and_keeps_roles():
    owner = login()
    STORE.set_json("google", {"rt": "private-oauth-token"})
    STORE.set_json("settings", {"visibility": True, "accidental_secret": "never-export"})
    STORE.hset("admin_roles", "editor@example.com", "editor")
    backup = owner.get("/api/admin/manage/backups").json()
    assert "private-oauth-token" not in json.dumps(backup)
    assert "never-export" not in json.dumps(backup)
    assert "admin_roles" not in backup["payload"]
    assert save(owner, "tools", {"batch": False}).status_code == 200
    modified = copy.deepcopy(backup); modified["payload"]["management"]["tools"]["batch"] = False
    assert owner.post("/api/admin/manage/backups/restore", json={"backup": modified}).status_code == 400
    assert owner.post("/api/admin/manage/backups/restore", json={"backup": backup}).status_code == 200
    assert M.config()["tools"].get("batch", True)
    assert STORE.hall("admin_roles")["editor@example.com"] == "editor"
    assert STORE.get_json("google")["rt"] == "private-oauth-token"
    assert owner.get("/api/admin/manage/history").json()["rows"]


def test_restore_history_and_checkpoint():
    owner = login()
    assert owner.post("/api/admin/manage/history", json={}).status_code == 200
    ident = owner.get("/api/admin/manage/history").json()["rows"][0]["id"]
    assert save(owner, "tools", {"batch": False}).status_code == 200
    assert owner.post("/api/admin/manage/backups/restore", json={"id": ident}).status_code == 200
    assert M.config()["tools"].get("batch", True)


def test_statistics_and_alert_acknowledgement():
    owner = login()
    assert save(owner, "alerts", {"enabled": True, "window_minutes": 15, "min_requests": 3, "failure_percent": 50}).status_code == 200
    M.record("instagram", "download", 502, 1.2, 0)
    M.record("instagram", "download", 502, 1.5, 0)
    M.record("instagram", "download", 200, 2, 1234)
    stats = owner.get("/api/admin/manage/stats?days=7").json()
    assert stats["platforms"]["instagram"]["success"] == 1
    assert stats["platforms"]["instagram"]["failure"] == 2
    alert = owner.get("/api/admin/manage/alerts").json()["rows"][0]
    support = login("support@example.com", "support")
    assert support.post("/api/admin/manage/alerts/ack", json={"id": alert["id"]}).status_code == 200
    assert owner.get("/api/admin/manage/alerts").json()["rows"][0]["acknowledged"]["by"] == "support@example.com"


def test_public_requests_feed_statistics(monkeypatch):
    owner = login()
    monkeypatch.setattr(backend, "fetch_info", lambda url: {"url": "https://media.example/a.jpg", "description": "caption"})
    assert owner.post("/api/info", json={"url": "https://instagram.com/p/abc123/"}).status_code == 200
    stats = owner.get("/api/admin/manage/stats").json()["platforms"]["instagram"]
    assert stats["success"] == 1


def test_ad_controls_validate_ids_and_change_page_configuration():
    owner = login()
    ads = {"enabled": True, "client": "ca-pub-1234567890123456", "slot": "1234567890", "pages": ["home"], "placements": ["hub"]}
    assert save(owner, "ads", ads).status_code == 200
    assert wr_seo.adsense() == (ads["client"], ads["slot"])
    home = owner.get("/").text
    assert '"pages": ["home"]' in home
    assert '"placements": ["hub"]' in home
    assert save(owner, "ads", {**ads, "client": '<script>alert(1)</script>'}).status_code == 400
    assert save(owner, "ads", {**ads, "enabled": False}).status_code == 200
    assert wr_seo.adsense() == ("", "")


def test_independent_config_sections_and_empty_ad_choices_survive_restore():
    owner = login()
    assert save(owner, "tools", {"batch": False}).status_code == 200
    ads = {"enabled": True, "client": "", "slot": "", "pages": [], "placements": []}
    assert save(owner, "ads", ads).status_code == 200
    assert M.config()["tools"]["batch"] is False
    assert M.config()["ads"]["pages"] == []
    backup = owner.get("/api/admin/manage/backups").json()
    assert save(owner, "tools", {"batch": True}).status_code == 200
    assert owner.post("/api/admin/manage/backups/restore", json={"backup": backup}).status_code == 200
    assert M.config()["ads"]["placements"] == []
    assert M.config()["tools"]["batch"] is False


def test_admin_assets_are_protected_and_present():
    public = TestClient(backend.app)
    assert public.get("/admin/manage.js").status_code == 401
    owner = login()
    page = owner.get("/admin")
    assert '<script src="/admin/manage.js">' in page.text
    asset = owner.get("/admin/manage.js")
    assert asset.status_code == 200
    assert "SECTIONS.backups" in asset.text


def test_threads_addition_preserved_and_status_switch_enforced(monkeypatch):
    owner = login()
    assert owner.get("/threads-video-downloader").status_code == 200
    assert "threads" in owner.get("/api/admin/manage/config").json()["tools"]
    assert save(owner, "tools", {"threads": False, "status": False}).status_code == 200
    assert owner.get("/threads-video-downloader").status_code == 503
    assert owner.get("/api/status", params={"url": "https://instagram.com/reel/abc123/"}).status_code == 503
    monkeypatch.setattr(backend, "media", lambda *a, **kw: ("media", {}, True, {"title": "sample"}))
    assert owner.get("/api/download", params={"url": "https://www.threads.com/@author/post/abc123"}).status_code == 503


def test_redis_pipeline_wire_format_and_error_handling():
    import httpx
    from wr_store import _Redis, StoreError
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[{"result": 1}, {"result": 1}])
    store = _Redis("https://redis.example", "test-only-token")
    store.c = httpx.Client(transport=httpx.MockTransport(handler))
    result = store.batch([("HINCRBY", "wr:metric", "requests", 1), ("EXPIRE", "wr:metric", 90)])
    assert result == [1, 1]
    assert str(seen[0].url) == "https://redis.example/pipeline"
    assert json.loads(seen[0].content)[0] == ["HINCRBY", "wr:metric", "requests", "1"]
    store.c.close()
    store.c = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=[{"error": "unavailable"}])))
    with pytest.raises(StoreError): store.batch([("PING",)])
    store.c.close()
