"""All settings come from environment variables. Secrets are read here and never written to logs, the database or a page."""
import os
import socket
from dataclasses import dataclass, field


def _env(name, default=""):
    return (os.getenv(name) or default).strip()


def _int(name, default):
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _list(name, default):
    return [x.strip() for x in _env(name, default).split(",") if x.strip()]


@dataclass
class Config:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))
    # Claude API (the AI engine)
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_base_url: str = field(default_factory=lambda: _env("BUGFIXER_ANTHROPIC_BASE_URL"))   # only for tests / a proxy
    model: str = field(default_factory=lambda: _env("BUGFIXER_MODEL", "claude-opus-5-5"))
    max_ai_steps: int = field(default_factory=lambda: _int("BUGFIXER_MAX_AI_STEPS", 14))
    # GitHub (source control)
    github_token: str = field(default_factory=lambda: _env("GITHUB_TOKEN"))
    github_repo: str = field(default_factory=lambda: _env("GITHUB_REPO", "Tnaveenrohini/Reelkit-"))
    github_api: str = field(default_factory=lambda: _env("GITHUB_API", "https://api.github.com").rstrip("/"))
    base_branch: str = field(default_factory=lambda: _env("GITHUB_BASE_BRANCH", "main"))
    git_url: str = field(default_factory=lambda: _env("BUGFIXER_GIT_URL"))            # default https://github.com/<repo>.git
    git_auth: str = field(default_factory=lambda: _env("BUGFIXER_GIT_AUTH", "header"))  # "none" when git credentials are configured outside
    # Sentry (error monitoring)
    sentry_token: str = field(default_factory=lambda: _env("SENTRY_AUTH_TOKEN"))
    sentry_org: str = field(default_factory=lambda: _env("SENTRY_ORG"))
    sentry_project: str = field(default_factory=lambda: _env("SENTRY_PROJECT"))
    sentry_api: str = field(default_factory=lambda: _env("SENTRY_API", "https://sentry.io").rstrip("/"))
    # Vercel (hosting): staging = the pull request's preview deployment, production = the main branch
    vercel_token: str = field(default_factory=lambda: _env("VERCEL_TOKEN"))
    vercel_team_id: str = field(default_factory=lambda: _env("VERCEL_TEAM_ID"))
    vercel_api: str = field(default_factory=lambda: _env("VERCEL_API", "https://api.vercel.com").rstrip("/"))
    vercel_bypass: str = field(default_factory=lambda: _env("VERCEL_PROTECTION_BYPASS"))
    vercel_status_context: str = field(default_factory=lambda: _env("VERCEL_STATUS_CONTEXT", "Vercel"))
    site_url: str = field(default_factory=lambda: _env("SITE_URL", "https://www.waverise.in").rstrip("/"))
    # testing
    required_checks: list = field(default_factory=lambda: _list("BUGFIXER_REQUIRED_CHECKS", "lint,unit,integration,regression,security"))
    checks_timeout_min: int = field(default_factory=lambda: _int("BUGFIXER_CHECKS_TIMEOUT_MIN", 45))
    deploy_timeout_min: int = field(default_factory=lambda: _int("BUGFIXER_DEPLOY_TIMEOUT_MIN", 20))
    # repair limits
    max_patch_files: int = field(default_factory=lambda: _int("BUGFIXER_MAX_PATCH_FILES", 5))
    max_patch_lines: int = field(default_factory=lambda: _int("BUGFIXER_MAX_PATCH_LINES", 120))
    max_repair_attempts: int = field(default_factory=lambda: _int("BUGFIXER_MAX_REPAIR_ATTEMPTS", 2))
    max_cycles_per_bug: int = field(default_factory=lambda: _int("BUGFIXER_MAX_CYCLES_PER_BUG", 3))
    # detection
    client_min_sessions: int = field(default_factory=lambda: _int("BUGFIXER_CLIENT_MIN_SESSIONS", 2))
    probe_paths: list = field(default_factory=lambda: _list("BUGFIXER_PROBE_PATHS", "/health,/,/instagram-video-downloader,/photo-mb-to-kb,/guides"))
    worker_id: str = field(default_factory=lambda: _env("BUGFIXER_WORKER_ID", f"{socket.gethostname()}-{os.getpid()}"))
    cache_dir: str = field(default_factory=lambda: _env("BUGFIXER_CACHE_DIR", "/tmp/bugfixer-cache"))

    @property
    def secrets(self):
        return [s for s in (self.anthropic_api_key, self.github_token, self.sentry_token, self.vercel_token, self.vercel_bypass,
                            self.database_url, _env("SENTRY_WEBHOOK_SECRET"), _env("SESSION_SECRET"), _env("CRON_SECRET")) if s and len(s) >= 8]

    def configured(self):
        """What is set up, as yes/no only (shown in the dashboard, never the values)."""
        return {"database": bool(self.database_url), "claude": bool(self.anthropic_api_key), "github": bool(self.github_token and self.github_repo),
                "sentry_api": bool(self.sentry_token and self.sentry_org and self.sentry_project), "sentry_sdk": bool(_env("SENTRY_DSN")),
                "vercel": bool(self.vercel_token), "vercel_bypass": bool(self.vercel_bypass), "model": self.model, "repo": self.github_repo,
                "base_branch": self.base_branch, "site": self.site_url, "required_checks": self.required_checks}

    def missing_for_worker(self):
        need = {"DATABASE_URL": self.database_url, "ANTHROPIC_API_KEY": self.anthropic_api_key, "GITHUB_TOKEN": self.github_token,
                "GITHUB_REPO": self.github_repo, "VERCEL_TOKEN": self.vercel_token}
        return [k for k, v in need.items() if not v]


def load():
    return Config()
