"""Shared context for the worker steps: settings, database and lazily created API clients."""
from .claude import Claude
from .config import load
from .db import DB
from .github import GitHub
from .services import Sentry, Vercel


class Ctx:
    def __init__(self, cfg=None, db=None):
        self.cfg = cfg or load()
        self.db = db or DB(self.cfg.database_url)
        self._gh = self._ai = self._vercel = self._sentry = None

    @property
    def gh(self):
        if self._gh is None:
            self._gh = GitHub(self.cfg.github_token, self.cfg.github_repo, self.cfg.github_api, git_url=self.cfg.git_url,
                              git_auth=self.cfg.git_auth, tmp_root=self.cfg.cache_dir)
        return self._gh

    @property
    def ai(self):
        if self._ai is None:
            self._ai = Claude(self.cfg.anthropic_api_key, self.cfg.model, self.cfg.anthropic_base_url, self.cfg.max_ai_steps)
        return self._ai

    @property
    def vercel(self):
        if self._vercel is None:
            self._vercel = Vercel(self.cfg.vercel_token, self.cfg.vercel_team_id, self.cfg.vercel_api)
        return self._vercel

    @property
    def sentry(self):
        if self._sentry is None:
            self._sentry = Sentry(self.cfg.sentry_token, self.cfg.sentry_org, self.cfg.sentry_project, self.cfg.sentry_api)
        return self._sentry

    def log(self, bug, message, level="info", step=None, repair_id=None, data=None):
        bug_id = bug["id"] if isinstance(bug, dict) else bug
        print(f"[bugfixer] bug {bug_id} {step or ''}: {message}", flush=True)
        self.db.log(message, bug_id=bug_id, repair_id=repair_id, level=level, step=step, data=data)

    def set_bug(self, bug_id, **fields):
        fields.setdefault("updated_at", "now()")
        cols, vals = [], []
        for k, v in fields.items():
            if v == "now()":
                cols.append(f"{k} = now()")
            elif isinstance(v, tuple) and v and v[0] == "sql":
                cols.append(f"{k} = {v[1]}")
            else:
                cols.append(f"{k} = %s"); vals.append(v)
        self.db.exec(f"update bf_bugs set {', '.join(cols)} where id = %s", vals + [bug_id])
