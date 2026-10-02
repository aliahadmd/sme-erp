# Plan 001: Security hardening sweep

> **Executor instructions**: Follow step by step; run every verification
> command and compare with the expected result before moving on. On any STOP
> condition, report back — do not improvise.
>
> **Drift check (run first)**: `git diff --stat 8d1a5fd..HEAD -- api/ docker/`
> — if any file below changed since `8d1a5fd`, compare the "Current state"
> excerpts against the live code before proceeding; mismatch = STOP.

## Status

- **Priority**: P1
- **Effort**: M (13 small fixes)
- **Risk**: LOW–MED (user-admin flow changes need care)
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `8d1a5fd`, 2026-09-27

## Why this matters

Thirteen verified security gaps: a privilege-escalation path through user
updates, an unauthenticated TLS channel for SMTP credentials, email header
injection from CRM data, a spoofable brute-force limiter key, the database
password on the pg_dump argv, world-readable dumps, a known default superuser
password outside dev, missing browser hardening headers, public API docs,
self-approval of leave, a latent arbitrary-attribute write, an external-send
gated on a read permission, and an unauthenticated redis holding auth state.
Each is small; together they are the difference between a demo and a system
that holds financial data.

## Current state

- `api/app/modules/core/router.py` — `update_user_endpoint` forwards
  `password`/`role_codes` for any target with only `core.user.update`
  required; the only guards are self-deactivation and last-active-superuser
  deactivation. Create-side has an escalation guard; update-side does not.
- `api/app/jobs/email.py:27` — `smtp.starttls()` called with no SSL context
  (smtplib defaults to an unverified context); `:37` logs `body[:200]`.
- `api/app/modules/core/notification_events.py` — party email pulled from
  `contact.emails[0]["value"]` with no format validation; enqueued as-is.
- `api/app/modules/core/deps.py:77-81` — `client_ip` takes the FIRST entry of
  `X-Forwarded-For`; `docker/nginx.conf` uses `$proxy_add_x_forwarded_for`
  (appends), so the leftmost value is client-controlled. The login limiter
  (`core/router.py`, `login:fail:{ip}:{email}`) keys on it.
- `api/app/jobs/backups.py:56-59` — `pg_dump --dbname <url-with-password>` on
  argv; dump file written with default umask (0644).
- `api/app/core/config.py` — `_production_safety` validator covers JWT_SECRET
  only; `admin_password` default `admin123` unguarded.
- `docker/nginx.conf` — no CSP / X-Frame-Options / nosniff / Referrer-Policy.
- `api/app/main.py` — `docs_url="/api/docs"`, `openapi_url="/openapi.json"`
  unconditional.
- `api/app/modules/hr/router.py::approve_leave_request` — no self-approval
  check (approver's own employee record).
- `api/app/modules/ai/router.py::accept_draft` — `setattr(product,
  draft.field or "description", ...)` with `field` unchecked.
- `api/app/modules/invoicing/router.py::email_invoice` — requires
  `invoicing.invoice.read` (a read permission) for an external side effect.
- `docker-compose.prod.yml` redis service — no `--requirepass`.
- `api/app/core/config.py` — `cors_origins` normalized from comma-separated
  strings by `_split_cors_origins` — reuse this pattern for any new list env.

Repo conventions: errors via `app/core/errors.py` (`DomainError` +
`ValidationError`/`PermissionDeniedError`); routes require permission codes
via `require("module.resource.action")`; tests in `api/tests/test_<module>.py`
using the `_admin`/`_auth` helpers; all state-changing endpoints write audit
rows via `write_audit`.

## Commands

| Purpose | Command (from repo root) | Expected |
|---|---|---|
| Lint/format | `docker compose exec -T api uv run ruff check --fix app tests && docker compose exec -T api uv run ruff format app tests` | "All checks passed!" |
| Tests | `docker compose exec -T api uv run pytest -q` | all pass |
| Full gate | `make verify` | exit 0 |

## Scope

**In scope**: `api/app/modules/core/router.py`, `api/app/core/config.py`,
`api/app/jobs/email.py`, `api/app/jobs/backups.py`,
`api/app/modules/hr/router.py`, `api/app/modules/ai/router.py`,
`api/app/modules/invoicing/router.py` (email_invoice permission only),
`api/app/modules/core/schemas.py` (if an EmailStr schema is added),
`api/tests/test_security.py`, `api/tests/test_core.py`, `api/tests/test_hr.py`,
`docker/nginx.conf`, `docker-compose.prod.yml`, `.env.example`.

**Out of scope**: any change to the refresh-cookie design, the event bus, or
the schema-per-module layout; adding new permissions beyond the optional
`invoicing.invoice.send`; touching `plans/` files other than the status table.

## Steps

1. **Update-side privilege guard** — in `update_user_endpoint`
   (`core/router.py`): when `target.is_superuser` is true and the actor is not
   a superuser, reject password, `is_active=False`, and role changes with
   `PermissionDeniedError`. Keep the existing last-active-superuser and
   self-deactivation guards.
   **Verify**: extend `tests/test_core.py::test_superuser_escalation_blocked`
   with a PATCH-based takeover attempt → 403.
2. **Verified STARTTLS** — `jobs/email.py`: build
   `ssl_ctx = ssl.create_default_context()` and pass it:
   `smtp.starttls(context=ssl_ctx)`. Import `ssl` at module top.
   **Verify**: `uv run python -c "import ssl; print(ssl.create_default_context().verify_mode)"` → `VerifyMode.CERT_REQUIRED`.
3. **Header-injection guard** — in `jobs/email.py::_send_smtp` (and the
   `send_email` job entry): reject `to` or `subject` containing `\r` or `\n`
   with `ValueError`; add a test posting a contact whose email contains
   `%0A` and asserting the send job raises/logs rather than sending.
   **Verify**: new test in `tests/test_jobs.py` → passes.
4. **Trusted client IP** — `core/deps.py::client_ip`: take the LAST entry of
   `X-Forwarded-For` (rightmost hop added by our own proxy) instead of the
   first; keep `request.client.host` fallback. Update the login-limiter test
   to cover a spoofed XFF header being ignored.
   **Verify**: `pytest tests/test_core.py -q` → all pass.
5. **Dump file permissions** — `jobs/backups.py`: after a successful
   `pg_dump`, `os.chmod(target, 0o600)`. Add an assertion-style comment.
   **Verify**: run the backup job in dev via `make backup` (postgres container
   path) or unit-check the chmod call exists.
6. **Admin password fail-fast** — `core/config.py::_production_safety`: when
   `environment != "dev"`, also reject `admin_password == "admin123"` or
   shorter than 12 chars, mirroring the JWT check.
   **Verify**: `pytest tests/test_security.py -q` → add a mirror test of the
   JWT one for `admin_password`.
7. **nginx hardening headers** — `docker/nginx.conf` server block: add
   `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
   `Referrer-Policy: strict-origin-when-cross-origin`, and a CSP starter
   (`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
   img-src 'self' data:; connect-src 'self'`). Rebuild web and confirm the SPA
   still loads (inline module scripts may need `script-src 'self'` iteration).
   **Verify**: `curl -I http://localhost:8080/` shows the headers (prod
   compose), SPA loads.
8. **Gate docs/OpenAPI in prod** — `app/main.py::create_app`: set
   `docs_url=None, openapi_url=None, redoc_url=None` when
   `settings.environment != "dev"`. Keep them in dev (the web `gen:api`
   script depends on the dev server).
   **Verify**: with `ENVIRONMENT=prod` the `/openapi.json` request 404s; in
   dev it still returns the schema.
9. **Self-approval guard (HR)** — `hr/router.py::approve_leave_request`:
   resolve the target request's `employee.user_id`; if it equals `user.id`,
   raise `PermissionDeniedError("You cannot approve your own leave request")`.
   **Verify**: test in `tests/test_hr.py` where the approver's own linked
   employee requests leave → approve → 403.
10. **Draft field whitelist** — `ai/router.py::accept_draft`: before
    `setattr`, assert `draft.field in {"description"}` else raise
    `ValidationError`. **Verify**: unit-test with a tampered draft field.
11. **send-email permission** — `invoicing/router.py::email_invoice`: change
    the dependency to `require("invoicing.invoice.update")`; add
    `invoicing.invoice.update` to the accountant role if absent (it is —
    verify in `core/permissions.py`).
    **Verify**: viewer-role user → 403 (extend `tests/test_invoicing.py`).
12. **Redis auth (prod)** — `docker-compose.prod.yml`: add
    `--requirepass ${REDIS_PASSWORD:?}` to the redis command and append
    `:REDIS_URL` password segment handling (set
    `REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0`); add
    `REDIS_PASSWORD` to `.env.example`. **Verify**: prod compose boots, api
    healthz redis ok.
13. Run `make verify` → exit 0. Commit: `security: hardening sweep (audit 001)`.

## Test plan

New tests: update-side takeover 403; CRLF email rejection; spoofed-XFF ignored
by the limiter; prod-mode docs 404; self-approval 403; draft-field whitelist
422; send-email 403 for viewer. Each in the module's existing test file
following its helpers.

## Maintenance

Future routes must keep using `require(...)` and the trusted-IP helper; the
CSP will need loosening only if an external script origin is ever added —
record that as an ADR if it happens.

## STOP conditions

- If tightening the IP parsing breaks the dev login flow (nginx not in the
  path), STOP and report the observed headers instead of loosening security.
- If a test asserts behavior that conflicts with an existing product flow
  (e.g. an automated integration depends on open docs), STOP and report.
