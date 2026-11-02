"""PostgreSQL access (works with Supabase, including its pooler: prepared statements are switched off)."""
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCHEMA = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = "1"
DEFAULT_SETTINGS = {
    "paused": False,                 # stop all automatic work
    "auto_analyze": True,            # Module 2 runs on new bugs
    "auto_repair": True,             # Module 3 makes patches
    "auto_deploy": "approval",       # "approval" (owner approves every production deploy) | "low_risk" (auto for low risk) | "auto" (auto unless high risk/destructive)
    "min_priority": "medium",        # lowest priority the worker repairs automatically
}

_local = threading.local()


class DBError(Exception):
    pass


def connect(url, timeout=5):
    try:
        return psycopg.connect(url, autocommit=True, row_factory=dict_row, prepare_threshold=None, connect_timeout=timeout)
    except psycopg.Error as e:
        raise DBError(f"database unreachable: {e.__class__.__name__}") from None


class DB:
    def __init__(self, url, timeout=5):
        if not url:
            raise DBError("DATABASE_URL is not set")
        self.url, self.timeout, self._conn = url, timeout, None

    @property
    def conn(self):
        if self._conn is None or self._conn.closed or self._conn.broken:
            self._conn = connect(self.url, self.timeout)
        return self._conn

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except psycopg.Error:
                pass
            self._conn = None

    def _run(self, sql, params=None, fetch="all"):
        for attempt in (1, 2):
            try:
                with self.conn.cursor() as cur:
                    cur.execute(sql, params)
                    if fetch == "all":
                        return cur.fetchall() if cur.description else []
                    if fetch == "one":
                        return cur.fetchone() if cur.description else None
                    return cur.rowcount
            except psycopg.OperationalError as e:
                self.close()
                if attempt == 2:
                    raise DBError(f"database error: {e.__class__.__name__}") from None

    def all(self, sql, params=None):
        return self._run(sql, params, "all")

    def one(self, sql, params=None):
        return self._run(sql, params, "one")

    def exec(self, sql, params=None):
        return self._run(sql, params, "count")

    @contextmanager
    def tx(self):
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                yield cur

    # ---------- schema ----------
    def migrate(self):
        with self.tx() as cur:
            cur.execute("select pg_advisory_xact_lock(724001)")
            cur.execute(SCHEMA.read_text())
            cur.execute("insert into bf_meta(key, value) values ('schema_version', %s) on conflict (key) do update set value = excluded.value", (SCHEMA_VERSION,))
            for k, v in DEFAULT_SETTINGS.items():
                cur.execute("insert into bf_settings(key, value) values (%s, %s) on conflict (key) do nothing", (k, Jsonb(v)))

    def ready(self):
        try:
            return bool(self.one("select value from bf_meta where key = 'schema_version'"))
        except (DBError, psycopg.Error):
            return False

    # ---------- settings and log ----------
    def settings(self):
        out = dict(DEFAULT_SETTINGS)
        for r in self.all("select key, value from bf_settings"):
            out[r["key"]] = r["value"]
        return out

    def set_setting(self, key, value, who="system"):
        self.exec("insert into bf_settings(key, value, updated_by) values (%s, %s, %s) on conflict (key) do update "
                  "set value = excluded.value, updated_by = excluded.updated_by, updated_at = now()", (key, Jsonb(value), who))

    def log(self, message, bug_id=None, repair_id=None, level="info", step=None, actor="worker", data=None):
        try:
            self.exec("insert into bf_log(bug_id, repair_id, level, actor, step, message, data) values (%s,%s,%s,%s,%s,%s,%s)",
                      (bug_id, repair_id, level, actor, step, str(message)[:2000], Jsonb(data) if data is not None else None))
        except DBError:
            print("[bugfixer] could not write log:", message, flush=True)


def jsonb(v):
    return Jsonb(v)


def dumps(v):
    return json.dumps(v, default=str)
