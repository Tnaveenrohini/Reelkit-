-- WaveRise AI Bug Fixer schema (PostgreSQL 13+ / Supabase). Safe to run many times.
create table if not exists bf_meta (key text primary key, value text);

create table if not exists bf_bugs (
  id               bigserial primary key,
  fingerprint      text not null unique,
  title            text not null,
  kind             text not null,              -- frontend | backend | api | database | upload | download | probe
  source           text not null,              -- client | server | sentry | probe
  error_type       text,
  route            text,
  status_code      int,
  first_seen       timestamptz not null default now(),
  last_seen        timestamptz not null default now(),
  count            bigint not null default 0,
  count_1h         int not null default 0,     -- refreshed by the worker
  count_24h        int not null default 0,
  sessions_24h     int not null default 0,
  priority         text not null default 'low',
  priority_score   int not null default 0,
  priority_reason  text,
  priority_manual  boolean not null default false,
  status           text not null default 'new',
  muted            boolean not null default false,
  sentry_issue_id  text,
  cycles           int not null default 0,     -- full repair cycles started
  attempts         int not null default 0,     -- failed step attempts in the current state
  next_action_at   timestamptz not null default now(),
  lease_owner      text,
  lease_until      timestamptz,
  last_error       text,
  fixed_at         timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index if not exists bf_bugs_queue on bf_bugs (status, next_action_at);
create index if not exists bf_bugs_seen on bf_bugs (last_seen desc);

create table if not exists bf_events (
  id           bigserial primary key,
  bug_id       bigint references bf_bugs(id) on delete cascade,
  received_at  timestamptz not null default now(),
  source       text not null,
  kind         text not null,
  error_type   text,
  message      text,
  url          text,
  route        text,
  status_code  int,
  stack        text,
  frames       jsonb,
  context      jsonb,
  session_hash text,
  release      text
);
create index if not exists bf_events_bug on bf_events (bug_id, received_at desc);
create index if not exists bf_events_time on bf_events (received_at desc);

create table if not exists bf_bug_sessions (
  bug_id       bigint references bf_bugs(id) on delete cascade,
  session_hash text not null,
  seen_at      timestamptz not null default now(),
  primary key (bug_id, session_hash)
);

create table if not exists bf_analyses (
  id               bigserial primary key,
  bug_id           bigint references bf_bugs(id) on delete cascade,
  created_at       timestamptz not null default now(),
  model            text,
  base_sha         text,
  category         text,
  root_cause       text,
  summary          text,
  affected_files   jsonb,
  dependencies     jsonb,
  fixable          boolean,
  confidence       real,
  risk             text,
  suggested_priority text,
  fix_plan         text,
  verification     jsonb,
  raw              jsonb,
  tokens_in        int,
  tokens_out       int
);

create table if not exists bf_repairs (
  id            bigserial primary key,
  bug_id        bigint references bf_bugs(id) on delete cascade,
  analysis_id   bigint references bf_analyses(id) on delete set null,
  attempt       int not null default 1,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  status        text not null,   -- proposed | rejected_policy | committed | tests_passed | tests_failed | staging_ok | staging_failed | approved | rejected | merged | verified | rolled_back | closed
  branch        text,
  base_sha      text,
  head_sha      text,
  pr_number     int,
  pr_url        text,
  summary       text,
  edits         jsonb,
  diff          text,
  lines_changed int,
  files         jsonb,
  risk          text,
  policy        jsonb,
  needs_approval boolean not null default false,
  merge_sha     text,
  tokens_in     int,
  tokens_out    int
);
create index if not exists bf_repairs_bug on bf_repairs (bug_id, id desc);

create table if not exists bf_test_runs (
  id           bigserial primary key,
  repair_id    bigint references bf_repairs(id) on delete cascade,
  sha          text,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  status       text not null,    -- running | passed | failed | timed_out
  checks       jsonb,
  failure_log  text
);

create table if not exists bf_deployments (
  id            bigserial primary key,
  repair_id     bigint references bf_repairs(id) on delete cascade,
  environment   text not null,   -- staging | production
  sha           text,
  previous_sha  text,
  deployment_id text,
  url           text,
  status        text not null,   -- waiting | ready | healthy | unhealthy | failed | rolled_back
  health        jsonb,
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  approved_by   text
);

create table if not exists bf_rollbacks (
  id             bigserial primary key,
  deployment_id  bigint references bf_deployments(id) on delete set null,
  repair_id      bigint references bf_repairs(id) on delete set null,
  bug_id         bigint references bf_bugs(id) on delete set null,
  created_at     timestamptz not null default now(),
  finished_at    timestamptz,
  reason         text,
  triggered_by   text,           -- worker | admin email
  from_sha       text,
  to_sha         text,
  revert_pr      int,
  status         text not null   -- requested | running | done | failed | needs_human
);

create table if not exists bf_approvals (
  id           bigserial primary key,
  repair_id    bigint references bf_repairs(id) on delete cascade,
  kind         text not null,     -- production_deploy | rollback
  reasons      jsonb,
  requested_at timestamptz not null default now(),
  decided_at   timestamptz,
  decided_by   text,
  decision     text               -- approve | reject
);

create table if not exists bf_log (
  id         bigserial primary key,
  at         timestamptz not null default now(),
  bug_id     bigint,
  repair_id  bigint,
  level      text not null default 'info',
  actor      text not null default 'worker',
  step       text,
  message    text not null,
  data       jsonb
);
create index if not exists bf_log_time on bf_log (at desc);
create index if not exists bf_log_bug on bf_log (bug_id, at desc);

create table if not exists bf_settings (key text primary key, value jsonb not null, updated_at timestamptz not null default now(), updated_by text);

create table if not exists bf_workers (id text primary key, heartbeat timestamptz not null default now(), started_at timestamptz not null default now(),
                                        version text, current text, info jsonb);

create table if not exists bf_rate (bucket text primary key, n int not null, reset_at timestamptz not null);
