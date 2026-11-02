import os

import pytest

for k in ("NO_PROXY", "no_proxy"):          # the fakes run on 127.0.0.1; never send them through a proxy
    os.environ[k] = ",".join(x for x in (os.environ.get(k, ""), "127.0.0.1", "localhost") if x)

TEST_DB = os.getenv("TEST_DATABASE_URL", "")
needs_db = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL is not set (CI sets it; locally point it at an empty test database)")


@pytest.fixture
def pg():
    """A clean bug fixer schema in the test database."""
    from bugfixer.db import DB
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL is not set")
    db = DB(TEST_DB)
    tables = [r["tablename"] for r in db.all("select tablename from pg_tables where schemaname = 'public' and tablename like 'bf\\_%'")]
    if tables:
        db.exec("drop table if exists " + ", ".join(tables) + " cascade")
    db.migrate()
    yield db
    db.close()


@pytest.fixture
def world(tmp_path):
    from tests.integration.fakes import World
    return World(tmp_path)


@pytest.fixture
def server(world):
    from tests.integration.fakes import Server
    with Server(world) as s:
        yield s


@pytest.fixture
def cfg(server, world, tmp_path):
    from bugfixer.config import Config
    from tests.integration.fakes import REPO
    return Config(database_url=TEST_DB, anthropic_api_key="test-anthropic-key", anthropic_base_url=server.url + "/claude", model="claude-opus-5-5",
                  max_ai_steps=6, github_token="test-gh-token", github_repo=REPO, github_api=server.url + "/gh", base_branch="main",
                  git_url=world.git_url, git_auth="none", sentry_token="test-sentry-token", sentry_org="wr", sentry_project="site",
                  sentry_api=server.url + "/sentry", vercel_token="test-vercel-token", vercel_team_id="", vercel_api=server.url + "/vercel",
                  vercel_bypass="", vercel_status_context="Vercel", site_url=server.url + "/site/live",
                  required_checks=["lint", "unit", "integration", "regression", "security"], checks_timeout_min=45, deploy_timeout_min=20,
                  max_patch_files=5, max_patch_lines=120, max_repair_attempts=2, max_cycles_per_bug=3, client_min_sessions=2,
                  probe_paths=["/health", "/"], worker_id="test-worker", cache_dir=str(tmp_path / "cache"))


@pytest.fixture
def ctx(pg, cfg):
    from bugfixer import jobs
    from bugfixer.context import Ctx
    jobs._probe_streak.clear()
    return Ctx(cfg, pg)
