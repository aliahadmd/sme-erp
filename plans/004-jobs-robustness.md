# Plan 004: Background jobs robustness

> **Executor instructions**: Step-by-step; run every verification command;
> STOP and report on mismatches.
>
> **Drift check (run first)**: `git diff --stat 8d1a5fd..HEAD -- api/app/jobs api/app/modules/core/router.py api/app/modules/sales/quote_router.py`
> — compare excerpts; mismatch = STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW–MED
- **Depends on**: none (plan-1 infrastructure already exists)
- **Category**: bug / tech-debt / perf
- **Planned at**: commit `8d1a5fd`, 2026-09-27

## Why this matters

Four verified defects in the job layer: an inverted TTL test makes the login-counter purge a no-op; blocking synchronous I/O (pg_dump, SMTP) freezes the
worker's event loop for everything queued behind it; one failing org aborts
the overdue scan for all orgs; and the quotation-expiry pass still lives as a
write inside the quotes GET handler instead of the scheduled job. Together
they mean the job system's guarantees are softer than its docstrings.

## Current state

- `api/app/jobs/maintenance.py:45-48` — `if not await redis.ttl(key): await
  redis.delete(key)` — Redis returns `-1` for keys with no expiry; `not -1`
  is `False`, so keys that lost their TTL are never deleted (inverted test).
- `api/app/jobs/backups.py:61-65` — `subprocess.run([...], capture_output=True,
  text=True)` with no timeout → a hung `pg_dump` blocks the worker loop
  forever (sync call inside async `backup_database`).
- `api/app/jobs/email.py:26-30` — `smtplib.SMTP(..., timeout=20)` + `starttls`
  + `sendmail` are synchronous inside `send_email`; a slow SMTP server stalls
  all jobs behind it.
- `api/app/jobs/maintenance.py:25-29` — `check_overdue_invoices` loops all
  orgs in one session and commits once at the end.
- `api/app/modules/sales/quote_router.py::list/get` — expiry still mutates and
  commits inside GET handlers (the cron from the phase-2 plan-1 batch exists
  at `api/app/jobs/__init__.py`, `expire_quotations` at 00:05 daily).
- `api/app/modules/core/router.py:663` — every unread-count poll still calls
  the lazy overdue scan (`notify_overdue_invoices`) and commits.

## Commands

| Purpose | Command (repo root) | Expected |
|---|---|---|
| Tests | `docker compose exec -T api uv run pytest tests/test_jobs.py -q` | all pass |
| Lint/format | `docker compose exec -T api uv run ruff check --fix app tests && docker compose exec -T api uv run ruff format app tests` | clean |
| Full gate | `make verify` | exit 0 |

## Scope

**In scope**: `api/app/jobs/maintenance.py`, `api/app/jobs/email.py`,
`api/app/jobs/backups.py`, `api/app/jobs/__init__.py` (cron + registration),
`api/app/modules/core/router.py` (remove the lazy overdue call from
unread-count), `api/app/modules/sales/quote_router.py` (remove the lazy
expiry from list/get), `api/tests/test_jobs.py`.

**Out of scope**: dead-letter tables/notification systems (OPS-02 beyond the
inline-mode catch — document if discovered); moving event handlers to the
worker; changing `JOBS_MODE` defaults.

## Steps

1. **Fix the inverted TTL purge** — `maintenance.py::purge_login_counters`:
   delete when `ttl < 0` (no expiry), i.e.
   `if await redis.ttl(key) < 0: await redis.delete(key)`.
   **Verify**: unit test — set a key with no TTL, run the job, key gone; a
   keyed-with-TTL key survives.
2. **Unblock the worker loop** — wrap the synchronous sections:
   `backups.py`: `subprocess.run(...)` →
   `await anyio.to_thread.run_sync(lambda: subprocess.run(..., timeout=1800))`
   (add `timeout=1800` as a belt-and-braces bound); `email.py::_send_smtp` →
   call via `anyio.to_thread.run_sync` from `send_email`. Add `anyio` usage
   matching `app/main.py::_s3_ok` (already in the codebase as the exemplar).
   **Verify**: `pytest tests/test_jobs.py tests/test_ai.py -q` green; add a
   test that a slow email (monkeypatched sleep) does not block a concurrent
   second enqueue.
3. **Per-org isolation in the overdue scan** —
   `maintenance.py::check_overdue_invoices`: commit after each org; wrap each
   org's `notify_overdue_invoices` call in try/except that logs
   `overdue_check_org_failed` and continues.
   **Verify**: test with one poisoned org (monkeypatch to raise for a given
   id) → other orgs still receive notifications.
4. **Quotation expiry: cron owns it, GET stops writing** —
   `expire_quotations` already exists (`maintenance.py`, cron 00:05 daily);
   remove the mutate-and-commit expiry pass from
   `sales/quote_router.py::list_quotations` (the `changed = True` /
   `await session.commit()` block) and from `get_quotation`
   (`check_expiry` + commit). Keep `check_expiry` inside the
   send/accept/convert transitions (still correct there — a sent quote past
   validity must not be accepted).
   **Verify**: `pytest tests/test_quotations.py -q` green, including the
   expiry test (which now calls the job inline).
5. **Remove the redundant lazy overdue poll** — `core/router.py`
   `unread_count`: delete the `notify_overdue_invoices` import + call + the
   `await session.commit()` it required. The daily cron (plan-1) owns the
   scan now; the notifications-list endpoint is untouched.
   **Verify**: `pytest tests/test_notifications.py tests/test_jobs.py -q`
   green; `/api/core/notifications/unread-count` still returns counts.
6. Fix the stale docstring in `notification_events.py` (it still says
   "phase 1 has no scheduler") and the module docstring's mention of the lazy
   fallback.
   **Verify**: `make verify` → exit 0; all tests green.

## Test plan

New tests in `tests/test_jobs.py`: TTL-purge correctness (step 1), per-org
isolation (step 3), expiry job behavior (step 4 — extend
`tests/test_quotations.py::test_expired_quote_cannot_be_accepted` to run the
job inline before asserting, replacing the lazy GET trigger).

## Maintenance

The overdue/heartbeat/expiry crons are the contract — any new scheduled job
must register in `WorkerSettings.cron_jobs` AND in
`tests/test_jobs.py::test_worker_settings_wiring`.

## STOP conditions

- If removing the lazy overdue poll breaks the notification acceptance test
  in a way the cron cannot cover (no worker in some environment), STOP and
  report the environment matrix — the fallback policy needs a decision.
- If `pg_dump` is genuinely absent from the prod image after the Dockerfile
  change (plan-2 added PGDG client — verify), STOP; the backup story needs
  rework, not a skip path.
