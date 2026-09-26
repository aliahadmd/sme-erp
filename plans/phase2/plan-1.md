# Plan 1 — Background Jobs Infrastructure (arq)

- **Depends on:** nothing (platform foundation for phase 2)
- **Goal:** a real job queue on the Redis we already run, so email, overdue
  checks, retention, exports, and AI calls stop living inside the request cycle.

## 1. Tasks

- [ ] Add `arq` worker as a second process of the api image:
      `arq app.jobs.WorkerSettings` — same codebase, same env, no new image.
- [ ] `app/jobs/__init__.py` with `WorkerSettings` (redis from `REDIS_URL`,
      max_jobs, retry policy: 3 attempts, exponential backoff), health job,
      and a `jobs/` package for task modules (`jobs/email.py`, `jobs/maintenance.py`).
- [ ] `app/jobs/queue.py`: typed `enqueue(name, **kwargs)` helper (no-op fallback
      to inline execution when `JOBS_MODE=inline`, so tests and dev-without-worker work).
- [ ] Scheduled jobs via arq cron:
      - `maintenance.check_overdue_invoices` — replaces the lazy phase-1 check
        (keep the lazy path as fallback when no worker runs);
      - `maintenance.purge_expired_login_counters`.
- [ ] Compose: `worker` service (same image/command as api, arq entrypoint),
      healthcheck via arq's Redis heartbeat; `make jobs-logs` target.
- [ ] Migrate phase-1 lazy pieces that belong in jobs:
      overdue-invoice notifications and login-counter cleanup.
- [ ] Observability: structlog job start/success/failure with duration; failed-job
      counter exposed on `/healthz` (`jobs: ok|stalled`).
- [ ] Tests: enqueue→run in eager/inline mode, retry path (failing job retried
      then dead-lettered to the log), cron registration, overdue job produces the
      same notifications as the lazy path.

## 2. Acceptance

- [ ] `docker compose ps` shows a healthy worker; a test enqueued from the API
      executes in the worker with structured logs.
- [ ] With `JOBS_MODE=inline` (or no worker), everything still works synchronously.
- [ ] `make verify` green including new tests.
