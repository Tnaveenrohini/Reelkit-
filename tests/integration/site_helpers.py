"""A signed-in admin session for the real admin API (the same signed cookie wr_admin issues when no session store is set)."""
import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

ADMIN = "owner@waverise.in"
HOST = "testserver"


def admin_env(monkeypatch, db_url=""):
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    for k in ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN", "KV_REST_API_URL", "KV_REST_API_TOKEN", "SENTRY_DSN"):
        monkeypatch.delenv(k, raising=False)
    if db_url:
        monkeypatch.setenv("DATABASE_URL", db_url)
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    import wr_bugs
    wr_bugs._db, wr_bugs._down_until = None, 0.0


def bug_app():
    """A FastAPI app with the bug fixer's site side installed (as app.py does)."""
    import wr_bugs
    app = FastAPI()
    wr_bugs.install(app)
    return app


def signed_in(client: TestClient, email=ADMIN):
    import wr_admin
    csrf = "csrf-token-for-tests"
    data = {"email": email, "csrf": csrf, "created": int(time.time()), "exp": int(time.time()) + 3600, "ip": "testclient"}
    client.cookies.set(wr_admin.COOKIE, wr_admin.sign("s:" + wr_admin.b64u(json.dumps(data).encode())))
    return {"x-csrf-token": csrf, "origin": f"http://{HOST}"}
