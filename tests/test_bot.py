"""The assistant: error notebook, owner chat, reply drafts and visitor chat (Gemini is faked)."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import app as backend
import wr_admin as A
import wr_bot as B
from wr_store import STORE, _Memory
from test_admin_manage import login


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(STORE, "r", _Memory())
    monkeypatch.setattr(STORE, "kind", "memory")
    monkeypatch.setattr(STORE, "_cache", {})
    monkeypatch.setenv("SESSION_SECRET", "test-only-session-secret-" * 3)
    monkeypatch.setenv("ADMIN_EMAILS", "owner@example.com")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    A._mem_rl.clear()
    backend._help_hits.clear()


class FakeGemini:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "body": json})
        code, text = self.answers.pop(0) if self.answers else (200, "ok")
        req = httpx.Request("POST", url)
        if code != 200:
            return httpx.Response(code, text=text, request=req)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]}, request=req)


def test_notebook_groups_the_same_problem():
    B.note("server", "/api/info", "Boom 123 at https://x.com/a")
    B.note("server", "/api/info", "Boom 456 at https://x.com/b")
    B.note("browser", "/", "TypeError: x is undefined")
    rows = B.errors()
    assert len(rows) == 2
    boom = next(r for r in rows if r["source"] == "server")
    assert boom["count"] == 2
    assert B.day_counts(1)[0]["server"] == 2


def test_browser_errors_are_noted_but_noise_is_ignored():
    c = TestClient(backend.app, base_url="https://testserver")
    h = {"Origin": "https://testserver"}
    assert c.post("/api/err", json={"msg": "TypeError: a.b is null", "src": "/x.js:1:2", "page": "https://testserver/photo-mb-to-kb"}, headers=h).status_code == 200
    c.post("/api/err", json={"msg": "ResizeObserver loop limit exceeded"}, headers=h)
    c.post("/api/err", json={"msg": "from another site"}, headers={"Origin": "https://evil.example"})
    rows = B.errors()
    assert [r["msg"] for r in rows] == ["TypeError: a.b is null"]
    assert rows[0]["where"] == "/photo-mb-to-kb"


def test_server_crash_and_failed_download_are_noted(monkeypatch):
    def crash(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(backend, "help_status", crash)
    c = TestClient(backend.app, base_url="https://testserver", raise_server_exceptions=False)
    # a failing download with the reason visitors saw
    c.post("/api/info", json={"url": "not a link"})
    rows = B.errors()
    assert any(r["source"] == "visitor" and r["where"].startswith("/api/info") for r in rows), rows


def test_owner_assistant_and_admin_pages(monkeypatch):
    owner = login()
    assert owner.get("/api/admin/bot").json()["engine"] == "own"
    B.note("browser", "/photo-mb-to-kb", "TypeError: x is null")
    own = owner.post("/api/admin/bot", json={"q": "eeroju site ela undi?"})
    assert own.status_code == 200 and "real bug" in own.json()["answer"] and "/photo-mb-to-kb" in own.json()["answer"]
    owner.post("/api/admin/errors", json={"action": "clear"})
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    B.note("server", "/api/download", "KeyError: 'url'")
    fake = FakeGemini([(404, "gone"), (200, "Site bagundi, oka bug undi.")])
    monkeypatch.setattr(B.httpx, "post", fake)
    r = owner.post("/api/admin/bot", json={"q": "ela undi?", "hist": [{"me": 1, "t": "hello"}, {"t": "hi"}]})
    assert r.status_code == 200 and r.json()["answer"] == "Site bagundi, oka bug undi."
    system = fake.calls[-1]["body"]["systemInstruction"]["parts"][0]["text"]
    assert "KeyError" in system and "SITE DATA" in system
    assert "x-goog-api-key" not in json.dumps(fake.calls[-1]["body"])
    # error log actions
    rid = B.errors()[0]["id"]
    assert owner.post("/api/admin/errors", json={"action": "fixed", "ids": [rid]}).status_code == 200
    assert owner.get("/api/admin/errors").json()["rows"][0]["fixed"]
    B.note("server", "/api/download", "KeyError: 'url'")               # it happened again
    assert not owner.get("/api/admin/errors").json()["rows"][0]["fixed"]
    assert owner.post("/api/admin/errors", json={"action": "clear"}).status_code == 200
    assert B.errors() == []


def test_reply_draft_for_contact_message(monkeypatch):
    c = TestClient(backend.app, base_url="https://testserver")
    assert c.post("/api/contact", json={"message": "Photo resize not working on my phone", "reply": "9876543210", "topic": "Bug report"},
                  headers={"Origin": "https://testserver"}).status_code == 200
    owner = login()
    mid = owner.get("/api/admin/messages").json()["rows"][0]["id"]
    own = owner.post(f"/api/admin/messages/{mid}/draft", json={}).json()["draft"]
    assert own.startswith("Hi there,") and "Naveen, WaveRise" in own and "photo-mb-to-kb" in own
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(B.httpx, "post", FakeGemini([(200, "Hi! Thanks for telling us. — Naveen, WaveRise")]))
    r = owner.post(f"/api/admin/messages/{mid}/draft", json={})
    assert r.status_code == 200 and "Naveen" in r.json()["draft"]
    assert owner.post(f"/api/admin/messages/{mid}/reply", json={"draft": "edited", "sent": True}).status_code == 200
    row = owner.get("/api/admin/messages").json()["rows"][0]
    assert row["draft"] == "edited" and row["replied"] and row["read"]


def test_visitor_chat_talks_like_a_person(monkeypatch):
    c = TestClient(backend.app, base_url="https://testserver")
    own = c.post("/api/help", json={"q": "photo size ela taggali", "lang": "en"}).json()
    assert "/photo-mb-to-kb" in own["answer"] and "andi" in own["answer"] and own["go"] == ["reduce"]
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    fake = FakeGemini([(200, "Sure! Open [Photo MB to KB](/photo-mb-to-kb) and pick 50 KB.")])
    monkeypatch.setattr(B.httpx, "post", fake)
    j = c.post("/api/help", json={"q": "photo size ela taggali", "lang": "te", "hist": []}).json()
    assert j["answer"].startswith("Sure!")
    sysmsg = fake.calls[0]["body"]["systemInstruction"]["parts"][0]["text"]
    assert "English letters" in sysmsg and "Photo Editor" in sysmsg
    # quota used up on every model: the visitor gets the normal fallback, and the problem is noted
    monkeypatch.setattr(B.httpx, "post", FakeGemini([(429, "quota")] * 10))
    fallback = c.post("/api/help", json={"q": "is it free?"}).json()
    assert "free" in fallback["answer"].lower()                        # our own brain answered instead
    assert any(r["source"] == "ai" for r in B.errors())


def test_support_role_sees_errors_but_not_owner_assistant():
    support = login("support@example.com", "support")
    assert support.get("/api/admin/errors").status_code == 200
    assert support.get("/api/admin/bot").status_code == 403
