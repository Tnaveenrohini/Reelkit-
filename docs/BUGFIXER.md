# WaveRise AI Autonomous Bug Fixer — production deployment guide

The bug fixer finds real errors on www.waverise.in, works out why they happen with Claude, writes a small fix on a separate
branch, tests it, checks it on a staging copy of the site, puts it live (with your approval by default), and checks the live
site afterwards. If the live site gets worse, it reverts the change by itself.

It has two parts:

| Part | Where it runs | What it does |
|---|---|---|
| **Website side** (`wr_bugs.py`, the reporter script in `templates/index.html`, the admin dashboard) | Vercel, with the site | Records errors and serves the private admin dashboard and API. **Without `DATABASE_URL` it does nothing and the site works as before.** |
| **Worker** (`bugfixer/`) | An always-on machine (Docker) | Runs all the time: imports Sentry issues, checks the live site, analyses, repairs, waits for tests, deploys, verifies, rolls back. |

Both use one PostgreSQL database (Supabase).

## The six modules

| Module | Code | How it works |
|---|---|---|
| 1. Bug detection | `bugfixer/detection.py`, `bugfixer/jobs.py`, `wr_bugs.py`, reporter script in `templates/index.html` | Browser errors (JavaScript errors, failed API calls, failed uploads and downloads, error messages shown to visitors) are sent to `/api/errors`. Unhandled backend exceptions and 5xx answers are recorded by middleware. Database errors from the store are reported. Sentry issues are imported every 5 minutes, or at once when Sentry's webhook calls. The worker checks the live site every 5 minutes. Each error gets a fingerprint (type, cleaned message, top frame of our own code), so repeats count towards one bug instead of creating duplicates. Expected answers such as a private post, a rate limit or "coming soon" are not bugs. |
| 2. AI bug analysis | `bugfixer/analysis.py`, `bugfixer/claude.py` | Claude reads the error events and explores a read-only copy of the code at the current `main` commit with `list_files`, `read_file` and `search_code`. It returns the category, root cause, affected files and lines, dependencies, whether code can fix it, its confidence and the risk. Error text from the internet is wrapped in `<untrusted_…>` tags and treated as data. |
| 3. Automatic code repair | `bugfixer/repair.py`, `bugfixer/patching.py`, `bugfixer/policy.py`, `bugfixer/github.py` | Claude writes exact text edits, and may add a regression test under `tests/regression_fixes/`. The worker checks four things before anything is committed: (1) each edit matches exactly once; (2) Python compiles; (3) page scripts and JSON parse, without running them; (4) the safety policy passes. The policy has file and line limits, forbidden paths, blocked dangerous code, and approval for sensitive files, new domains or large deletions. The fix is committed to a new `autofix/bug-…` branch and a pull request is opened. `main` is never written directly. |
| 4. Automatic testing | `.github/workflows/autofix-ci.yml`, `bugfixer/testing.py` | GitHub Actions runs `lint`, `unit`, `integration`, `regression` and `security` on a fresh, isolated machine for every pull request. The worker reads the results. If any required check fails, the PR is closed and the failure log goes back to Claude for one more attempt. After that the bug is marked "tests failed". |
| 5. Automatic deployment | `bugfixer/deploy.py` | **Staging** is the PR's Vercel preview deployment. Its health is checked, including the route that was failing. **Production:** the PR is squash-merged only if its head is still the commit that was tested. Vercel then deploys `main`, and the worker checks the live site and the new deployment 3 times and watches for the bug coming back. If anything is wrong, it **rolls back automatically** with a revert PR (history is kept and nothing is force-pushed). Only one production change runs at a time. |
| 6. Bug priority | `bugfixer/priority.py` | **Critical, High, Medium or Low**, set by what failed, where, how often and for how many visitors. Claude may move it one level. You can set it by hand. The worker always takes Critical first, then High, Medium and Low. |

Every step is written to the database: analyses, repairs and diffs, test runs, deployments, approvals, rollbacks and a full log. The admin dashboard shows all of it.

### Bug states

```
new / regressed → queued → analyzed → testing → staging → (awaiting_approval) → deploying → verifying → verified
                                   ↘ needs_human · tests_failed · staging_failed · rejected · failed
                                                                    verifying → rolling_back → rolled_back
```

The worker leases one bug at a time and renews the lease while it works. If the worker stops or crashes, the lease runs out
after 10 minutes and the bug continues from its saved state. Each step first checks what was already done (branch, pull
request, merge), so nothing is done twice. A failing step is retried with growing waits (2, 4, 8, 16 minutes). After 5 failures
the bug is marked `failed`.

## What you need (all have free plans)

- **Supabase** project (PostgreSQL).
- **Anthropic API key** for Claude. API usage is paid per token; each bug uses about two short Claude sessions.
- **GitHub** fine-grained token for `Tnaveenrohini/Reelkit-`.
- **Vercel** access token (the site is already on Vercel).
- **Sentry** project (optional but recommended).
- An **always-on machine** for the worker that can run Docker or Python 3.12 with `git` and `node`, such as a free-tier cloud VM, a home server or a Raspberry Pi. Free-tier terms change, so check the provider's current limits. The worker needs about 256 MB of RAM.

## Step 1 — Database (Supabase)

1. Create a project at supabase.com.
2. Click **Connect** and copy two connection strings:
   - **Transaction pooler** (port 6543), for Vercel's serverless functions.
   - **Session pooler** or **direct** connection (port 5432), for the worker.

   Prepared statements are switched off in the code, so the pooler works.
3. The tables are created automatically when the worker starts. To create them by hand, run `python -m bugfixer.migrate` with `DATABASE_URL` set.

## Step 2 — GitHub

1. Create a **fine-grained personal access token**:
   - Path: GitHub → Settings → Developer settings → Fine-grained tokens.
   - Repository access: **only** `Tnaveenrohini/Reelkit-`.
   - Permissions:

     | Permission | Access |
     |---|---|
     | Contents | Read and write |
     | Pull requests | Read and write |
     | Actions | Read |
     | Checks | Read |
     | Commit statuses | Read |
     | Metadata | Read |

   Do **not** grant "Workflows". The token then can't change `.github/workflows` even if everything else failed.
2. Protect `main` under Settings → Branches (or Rules):
   - Require a pull request before merging.
   - Require status checks `lint`, `unit`, `integration`, `regression` and `security`.
   - Block force pushes and deletions.

   Owner approval happens in the WaveRise dashboard. If you also require GitHub review approvals, merges by the bug fixer are refused and the bug goes to "needs human".
3. GitHub Actions minutes are free for public repositories. Private repositories get a monthly free allowance; each run of the CI takes about 5 minutes.

## Step 3 — Vercel

1. Create an access token under Account Settings → Tokens, scoped to the team that owns the project. This is `VERCEL_TOKEN`.
2. If the project is under a team, copy the team ID from Team Settings. This is `VERCEL_TEAM_ID`.
3. If preview deployments are protected (Deployment Protection), create a **Protection Bypass for Automation** secret. This is `VERCEL_PROTECTION_BYPASS`. Without it, the staging health check sees the login wall.
4. Keep the Vercel GitHub integration connected. The worker finds deployments through the "Vercel" commit status that the integration writes on each commit.

## Step 4 — Sentry (error monitoring)

1. Create a project with the **FastAPI** platform and copy the **DSN**. This is `SENTRY_DSN` (Vercel).
2. Create an **Internal Integration** under Settings → Developer Settings, with permissions **Issue & Event: Read & Write** and **Project: Read**. Copy its token. This is `SENTRY_AUTH_TOKEN` (worker). Write access is needed so that verified fixes can mark the Sentry issue as resolved.
3. Optional: an instant import webhook. In the same integration:
   - Set the webhook URL to `https://www.waverise.in/api/bugfixer/sentry-webhook` and tick **issue**.
   - Copy the **Client Secret**. This is `SENTRY_WEBHOOK_SECRET` (Vercel).

   Without the webhook the worker imports issues every 5 minutes anyway.

## Step 5 — Environment variables

**Vercel** (Project → Settings → Environment Variables, Production and Preview), then redeploy:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase transaction-pooler URL (port 6543) |
| `SENTRY_DSN` | Sentry DSN (optional) |
| `SENTRY_WEBHOOK_SECRET` | Sentry integration client secret (optional) |

The admin variables you already have (`SESSION_SECRET`, `ADMIN_EMAILS`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`) protect the dashboard.

**Worker** (a `worker.env` file on the worker machine; never commit it):

| Variable | Required | Value / default |
|---|---|---|
| `DATABASE_URL` | yes | Supabase session or direct URL (port 5432) |
| `ANTHROPIC_API_KEY` | yes | Claude API key |
| `GITHUB_TOKEN` | yes | the fine-grained token |
| `GITHUB_REPO` | yes | `Tnaveenrohini/Reelkit-` (default) |
| `VERCEL_TOKEN` | yes | Vercel token |
| `VERCEL_TEAM_ID` | if team | team ID |
| `VERCEL_PROTECTION_BYPASS` | if previews are protected | bypass secret |
| `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT` | recommended | Sentry token, organisation slug and project slug |
| `SESSION_SECRET` | recommended | same value as on Vercel (salts the anonymous visitor IDs and helps redact secrets from logs) |
| `SITE_URL` | no | `https://www.waverise.in` |
| `BUGFIXER_MODEL` | no | `claude-opus-5-5` |
| `GITHUB_BASE_BRANCH` | no | `main` |
| `BUGFIXER_REQUIRED_CHECKS` | no | `lint,unit,integration,regression,security` |
| `BUGFIXER_PROBE_PATHS` | no | `/health,/,/instagram-video-downloader,/photo-mb-to-kb,/guides` |
| `BUGFIXER_MAX_PATCH_FILES` / `BUGFIXER_MAX_PATCH_LINES` | no | `5` / `120` |
| `BUGFIXER_MAX_REPAIR_ATTEMPTS` / `BUGFIXER_MAX_CYCLES_PER_BUG` | no | `2` / `3` |
| `BUGFIXER_CHECKS_TIMEOUT_MIN` / `BUGFIXER_DEPLOY_TIMEOUT_MIN` | no | `45` / `20` |
| `BUGFIXER_CLIENT_MIN_SESSIONS` | no | `2` (different visitors needed before a browser-only error goes to the AI) |

Secrets are only read from the environment. They are never written to the database, the logs, a pull request or the dashboard; the dashboard shows only "configured: yes/no".

## Step 6 — Run the worker

**Docker** (recommended):

```bash
git clone https://github.com/Tnaveenrohini/Reelkit-.git && cd Reelkit-
docker build -f Dockerfile.worker -t waverise-bugfixer .
docker run --rm --env-file worker.env waverise-bugfixer python -m bugfixer.selftest --write   # check every connection first
docker run -d --name waverise-bugfixer --restart unless-stopped --env-file worker.env waverise-bugfixer
docker logs -f waverise-bugfixer
```

**Without Docker** (systemd), with Python 3.12, `git` and `node` installed:

```bash
python3 -m venv /opt/bugfixer/venv && /opt/bugfixer/venv/bin/pip install -r requirements-worker.txt
```

`/etc/systemd/system/waverise-bugfixer.service`:

```ini
[Unit]
Description=WaveRise AI Bug Fixer
After=network-online.target

[Service]
WorkingDirectory=/opt/bugfixer/Reelkit-
EnvironmentFile=/opt/bugfixer/worker.env
ExecStart=/opt/bugfixer/venv/bin/python -m bugfixer.worker
Restart=always
RestartSec=10
User=bugfixer
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Then run `sudo systemctl enable --now waverise-bugfixer`. To update the worker, pull the repository and restart it.

### Self-test

`python -m bugfixer.selftest` checks each service and prints ✓ or ✗:

- the database: connection and tables;
- Claude: a real one-tool call;
- GitHub: repository, `main` and check runs; with `--write` it also creates and deletes a throwaway `autofix/selftest-…` branch;
- Sentry: issues are readable;
- Vercel: the production deployment is found through the commit status;
- the live site's health.

Use `--only db,claude` to run some of the checks.

## Step 7 — Use the dashboard

Sign in at `https://www.waverise.in/admin` and open **AI Bug Fixer**:

| Page | What it shows / lets you do |
|---|---|
| **Live Bug Monitoring** | Live error stream, open bugs, the worker's heartbeat and which services are configured. |
| **Bug Priority** | Bugs by level. Change a level (it then stays as you set it). |
| **AI Analysis** | Root cause, affected files and dependencies, confidence and risk. |
| **Automatic Code Repair** | Patches with their diffs and pull requests. Switches for automatic analysis and repair, the minimum priority, and pause. |
| **Test Results** | Every CI check per patch, with failure logs. |
| **Deployment Status** | Staging and production deployments. Set the **deploy permission** here and approve or reject waiting deploys. |
| **Rollback History** | Every rollback with its reason. |
| **Repair Logs** | The full history. |

Opening a bug shows everything about it. From there you can approve, reject, retry, queue, ignore, change the priority or roll back.

### Deploy permissions

| Setting | Production deploys |
|---|---|
| `approval` (default) | You approve every deploy. Type `DEPLOY <bug id>` to confirm. |
| `low_risk` | Automatic for low-risk patches; the rest wait for you. |
| `auto` | Automatic unless the patch is high risk or destructive. |

Some changes **always** need your approval, whatever the setting:

- sensitive files: `wr_admin.py`, admin templates, `requirements.txt`, `sw.js`, `manifest.json`, `wr_store.py`, SQL;
- a new external domain;
- removing 40 or more lines, or emptying a file;
- anything Claude marks as touching security or adding dependencies.

Some changes are never allowed:

- `bugfixer/`, `.github/`, `vercel.json`, Dockerfiles, the worker requirements, `wr_bugs.py`, `.env` files or keys;
- `eval`/`exec`, shell commands, or hard-coded credentials.

A manual rollback asks you to type `ROLLBACK <bug id>`.

## Security summary

- **No exposed credentials.** API keys live only in the worker's environment. The browser reporter sends no secrets, and errors are scrubbed of tokens, emails, IPs and phone numbers before they are stored. Visitor IDs are salted daily hashes.
- **Production code is never overwritten directly.** Changes go only through `autofix/` branches, pull requests and a checked squash merge. There are no force-pushes, and rollbacks are revert commits.
- **Isolated tests.** CI runs on fresh GitHub-hosted machines. The worker itself never imports or runs the patched code; it only compiles and parses it.
- **No untrusted code is executed.** Error reports are data. Claude's tools are read-only. Patches are text edits that pass the policy before they are committed.
- **Owner approval** is required for destructive and high-risk deploys (above) and is the default for all deploys.
- **Backups and rollback.** Git history keeps every version, and every production deploy stores the previous commit. The database keeps the complete repair history. Supabase has its own backups.
- **Abuse protection.** `/api/errors` limits body size, checks the origin, and rate-limits per visitor and overall. Errors that only come from browsers need reports from at least 2 different visitors before the AI looks at them, so one person can't steer it.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard says the database isn't connected | Add `DATABASE_URL` in Vercel and redeploy. |
| Dashboard says the tables don't exist | Start the worker once, or run `python -m bugfixer.migrate`. |
| "Worker not running" | The worker is stopped. Check `docker logs waverise-bugfixer`. |
| Bugs stay "new" | Low priority (below the minimum), a browser-only error with too few visitors, or automatic analysis is switched off. Use **Repair now** on the bug. |
| Stuck in "testing" | Check that the CI workflow runs on pull requests and that the job names match `BUGFIXER_REQUIRED_CHECKS`. |
| Staging always fails | Set `VERCEL_PROTECTION_BYPASS`, and check that the Vercel GitHub integration writes the "Vercel" status. |
| "Merge refused" | Branch rules require reviews, or the PR changed after testing. See the PR on GitHub. |

## Running the tests locally

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/unit tests/regression_fixes
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/bugfixer_test pytest tests/integration   # an empty database
python -m playwright install chromium && pytest tests/regression
```

The integration tests run the whole worker against a real PostgreSQL database and a real git repository. Every outside service (GitHub's REST API, the Claude Messages API, Vercel, Sentry and the website) is replaced by an HTTP fake that follows that service's documented request and response format. The tests cover:

- the full fix-to-verified path;
- failed CI followed by a retry, then owner approval through the real admin API;
- an automatic production rollback;
- a failed staging deployment;
- unsafe patches being blocked;
- prompt injection in error reports;
- live-site outage detection;
- lease recovery.
