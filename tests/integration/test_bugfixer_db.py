"""Detection, deduplication, priority ordering, leases and recovery on a real PostgreSQL database."""
from bugfixer import detection, worker
from bugfixer.config import Config
from bugfixer.context import Ctx
from tests.integration.conftest import needs_db

pytestmark = needs_db


def mk_ctx(pg, worker_id="w1"):
    return Ctx(Config(database_url=pg.url, worker_id=worker_id, client_min_sessions=2), pg)


def ev(**kw):
    base = {"source": "server", "kind": "backend", "error_type": "KeyError", "message": "'url'", "route": "/api/convert", "status_code": 500,
            "stack": 'Traceback (most recent call last):\n  File "/var/task/app.py", line 7, in convert\nKeyError: \'url\''}
    base.update(kw)
    return base


def test_migrate_is_repeatable_and_seeds_settings(pg):
    pg.migrate()
    pg.migrate()
    s = pg.settings()
    assert s["auto_deploy"] == "approval" and s["paused"] is False and s["min_priority"] == "medium"


def test_same_error_is_one_bug_and_comes_back_as_regressed(pg):
    a = detection.ingest(pg, ev(message="'url' at /tmp/a123"))
    b = detection.ingest(pg, ev(message="'url' at /tmp/b456", stack=ev()["stack"].replace("line 7", "line 9")))
    c = detection.ingest(pg, ev(error_type="ValueError"))
    assert a["new"] and not b["new"] and a["bug_id"] == b["bug_id"] != c["bug_id"]
    assert pg.one("select count from bf_bugs where id = %s", (a["bug_id"],))["count"] == 2
    pg.exec("update bf_bugs set status = 'verified', fixed_at = now() - interval '1 hour' where id = %s", (a["bug_id"],))
    again = detection.ingest(pg, ev(message="'url' at /tmp/c789"))
    assert again["bug_id"] == a["bug_id"] and again["status"] == "regressed"
    assert "came back" in pg.one("select message from bf_log where bug_id = %s order by id desc limit 1", (a["bug_id"],))["message"]


def test_expected_answers_are_not_stored_and_secrets_are_scrubbed(pg):
    assert detection.ingest(pg, {"source": "client", "kind": "api", "status_code": 422, "message": "This post is private"}) == {"ignored": "expected"}
    fake_token = "ghp_" + "a1b2c3d4e5" * 4                       # built at run time so the secret scanner doesn't flag the test
    r = detection.ingest(pg, ev(message=f"token={fake_token} for me@x.com", url="https://www.waverise.in/?u=secret"))
    e = pg.one("select * from bf_events where bug_id = %s", (r["bug_id"],))
    assert "ghp_" not in e["message"] and "me@x.com" not in e["message"] and e["url"] == "https://www.waverise.in/"


def test_public_page_errors_need_several_visitors_before_the_ai_sees_them(pg):
    ctx = mk_ctx(pg)
    js = {"source": "client", "kind": "frontend", "error_type": "TypeError", "message": "Cannot read properties of null (reading 'value')",
          "stack": "TypeError: x\n    at showDl (https://www.waverise.in/:2205:40)", "route": "/"}
    r = detection.ingest(pg, dict(js, session_hash="visitor-1"))
    detection.ingest(pg, dict(js, session_hash="visitor-1"))
    detection.refresh_stats(pg)
    worker.promote_new(ctx)
    assert pg.one("select status from bf_bugs where id = %s", (r["bug_id"],))["status"] == "new"
    detection.ingest(pg, dict(js, session_hash="visitor-2"))
    detection.refresh_stats(pg)
    worker.promote_new(ctx)
    assert pg.one("select status from bf_bugs where id = %s", (r["bug_id"],))["status"] == "queued"


def test_low_priority_bugs_wait_for_the_owner(pg):
    ctx = mk_ctx(pg)
    r = detection.ingest(pg, {"source": "client", "kind": "download", "status_code": 0, "message": "Failed to fetch", "route": "/api/download",
                              "session_hash": "v1"})
    detection.ingest(pg, {"source": "client", "kind": "download", "status_code": 0, "message": "Failed to fetch", "route": "/api/download",
                          "session_hash": "v2"})
    detection.refresh_stats(pg)
    worker.promote_new(ctx)
    b = pg.one("select status, priority from bf_bugs where id = %s", (r["bug_id"],))
    assert b["priority"] == "low" and b["status"] == "new"


def test_critical_bugs_are_processed_first(pg):
    ids = {}
    for p in ("low", "medium", "critical", "high"):
        ids[p] = detection.ingest(pg, ev(message=f"error {p}", error_type=f"E{p}"))["bug_id"]
        pg.exec("update bf_bugs set status = 'queued', priority = %s where id = %s", (p, ids[p]))
    ctx = mk_ctx(pg)
    order = []
    for _ in range(4):
        b = worker.claim(ctx)
        order.append(b["priority"])
    assert order == ["critical", "high", "medium", "low"]
    assert worker.claim(ctx) is None                                    # all leased


def test_a_crashed_worker_lease_expires_and_another_worker_resumes(pg):
    bid = detection.ingest(pg, ev())["bug_id"]
    pg.exec("update bf_bugs set status = 'testing' where id = %s", (bid,))
    a, b = mk_ctx(pg, "worker-a"), mk_ctx(pg, "worker-b")
    assert worker.claim(a)["id"] == bid
    assert worker.claim(b) is None                                      # leased by a
    pg.exec("update bf_bugs set lease_until = now() - interval '1 second' where id = %s", (bid,))   # a died
    got = worker.claim(b)
    assert got["id"] == bid and got["status"] == "testing" and got["lease_owner"] == "worker-b"


def test_only_one_production_change_at_a_time(pg):
    first = detection.ingest(pg, ev(error_type="A"))["bug_id"]
    second = detection.ingest(pg, ev(error_type="B"))["bug_id"]
    pg.exec("update bf_bugs set status = 'verifying' where id = %s", (first,))
    pg.exec("update bf_bugs set status = 'deploying', priority = 'critical' where id = %s", (second,))
    ctx = mk_ctx(pg)
    assert worker.claim(ctx)["id"] == first                            # the merged change continues …
    assert worker.claim(mk_ctx(pg, "w2")) is None                       # … and the critical merge waits for it
    worker.release(ctx, first)
    assert worker.claim(mk_ctx(pg, "w2"))["id"] == first               # still waiting while the first is unverified
    pg.exec("update bf_bugs set status = 'verified', lease_owner = null, lease_until = null where id = %s", (first,))
    assert worker.claim(ctx)["id"] == second


def test_step_failures_back_off_then_stop(pg, monkeypatch):
    bid = detection.ingest(pg, ev())["bug_id"]
    pg.exec("update bf_bugs set status = 'queued' where id = %s", (bid,))
    ctx = mk_ctx(pg)

    def boom(*a, **k):
        raise RuntimeError("GitHub is down")
    monkeypatch.setattr(worker, "step", boom)
    for i in range(worker.MAX_STEP_FAILURES):
        assert worker.run_once(ctx, {"stats": 9e18, "promote": 9e18, "probes": 9e18, "sentry": 9e18, "retention": 9e18})
        b = pg.one("select * from bf_bugs where id = %s", (bid,))
        if i < worker.MAX_STEP_FAILURES - 1:
            assert b["status"] == "queued" and b["attempts"] == i + 1 and b["lease_owner"] is None
            pg.exec("update bf_bugs set next_action_at = now() where id = %s", (bid,))
    assert b["status"] == "failed" and "GitHub is down" in b["last_error"]


def test_paused_worker_does_nothing(pg):
    bid = detection.ingest(pg, ev())["bug_id"]
    pg.exec("update bf_bugs set status = 'queued' where id = %s", (bid,))
    pg.set_setting("paused", True)
    assert worker.run_once(mk_ctx(pg), {"stats": 9e18, "promote": 9e18, "probes": 9e18, "sentry": 9e18, "retention": 9e18}) is False
    assert pg.one("select lease_owner from bf_bugs where id = %s", (bid,))["lease_owner"] is None
