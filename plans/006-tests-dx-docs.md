# Plan 006: Test coverage, DX, and docs

> **Executor instructions**: Step-by-step; run every verification command;
> STOP and report on mismatches.
>
> **Drift check (run first)**: `git diff --stat 8d1a5fd..HEAD -- api/tests api/app/modules/reporting .github web/package.json README.md plans/phase2/index.md`
> — compare excerpts; mismatch = STOP.

## Status

- **Priority**: P3 (coverage items themselves are P2-worthy — do them in ID order)
- **Effort**: M
- **Risk**: LOW (additive tests, docs, tooling)
- **Depends on**: 002 (reporting totals change shape — write the reporting
  tests after 002 lands)
- **Category**: tests / dx / docs
- **Planned at**: commit `8d1a5fd`, 2026-09-27

## Why this matters

The reporting module — six financial endpoints of hand-written cross-schema
SQL — has zero test coverage, the currencies admin is tested only incidentally,
the AI happy path is never exercised, and the frontend has no feature-flow
tests. Meanwhile the feedback loop for lint/test failures is a full
`make verify`, and the architecture contract that agents and humans both need
lives only in a README section. This plan closes those gaps cheaply.

## Current state

- Reporting endpoints (all untested): `api/app/modules/reporting/router.py`
  — `/api/reports/dashboard`, `/sales-by-customer`, `/purchases-by-supplier`,
  `/stock-valuation`, `/aging`, `/tax-summary`; hand-written `text()` SQL
  duplicating the status enum as literals.
- Currencies admin (untested paths): `api/app/modules/currencies/router.py`
  — duplicate 409 at `:66-70`, upsert update branch at `:129-131`, permission
  gates.
- AI happy path: `api/tests/test_ai.py` asserts only 503 disabled paths;
  nothing mocks the LLM call.
- `POST /api/invoicing/invoices/{id}/send-email`: untested; gated on
  `invoicing.invoice.read` while performing an external side effect.
- No `CLAUDE.md`/`AGENTS.md`; no `.pre-commit-config.yaml`; `make verify` is
  the only lint entry point; `pnpm gen:api` requires a running API server
  (undocumented prerequisite).
- Known good exemplars to follow: `api/tests/test_invoicing.py` (helpers,
  full-cycle patterns), `web/src/features/auth/login-page.test.tsx`.

## Commands

| Purpose | Command (repo root) | Expected |
|---|---|---|
| API tests | `docker compose exec -T api uv run pytest -q` | all pass |
| Web tests | `cd web && pnpm test` | all pass |
| Full gate | `make verify` | exit 0 |

## Scope

**In scope**: `api/tests/` (new files `test_reporting.py`,
`test_currencies.py`, `test_email_endpoint.py`; extensions to
`tests/test_ai.py`), `web/src/features/documents/order-editor-page.test.tsx`
(new), `web/src/features/invoicing/invoices-page.test.tsx` (new),
`.pre-commit-config.yaml` (new), `Makefile` (add `test`, `lint`, `format`
targets), `CLAUDE.md` (new), `README.md` (gen:api prerequisite note),
`plans/audit-fix-log.md` (split the two audits into dated sections).

**Out of scope**: changing any endpoint behavior; pytest-xdist
parallelization; the frontend permission-filtered nav rework; ADR authoring
(beyond stubbing `docs/adr/` with the four decisions named in DOC-02 —
that is allowed as docs, not code).

## Steps

1. **Reporting tests** — `api/tests/test_reporting.py` (new): seed via the
   API helpers (invoice posted/partial/paid + credit, receipt with cost,
   payment) and assert per endpoint: dashboard KPIs + weekly buckets,
   sales-by-customer totals, stock valuation (qty × avg cost), aging buckets
   (boundary dates), tax summary nets. Pattern:
   `tests/test_invoicing.py::test_full_ar_cycle_with_partial_payment`.
   **Verify**: `pytest tests/test_reporting.py -q` green.
2. **Currencies tests** — `api/tests/test_currencies.py` (new): duplicate 409,
   upsert insert-vs-update branches, permission denials for a viewer-role
   user. **Verify**: green.
3. **send-email endpoint tests** — draft (400), no party email (400), happy
   queue (200 + audit row), viewer-role 403. **Verify**: green.
4. **AI happy path with a mocked completion** — in `tests/test_ai.py`, add a
   test that monkeypatches `AIClient.complete` (or the underlying transport)
   to return a fixed text, then: `generate-missing` → an `AiDraft` row is
   created by the worker path; `accept` applies it to the product.
   **Verify**: green.
5. **Frontend money-flow test** — `web/src/features/documents/order-editor-page`:
   render the editor, fill customer + one line, submit, assert the API client
   was called with the computed payload (mock the transport like
   `login-page.test.tsx` does). One more for the invoice allocation dialog.
   **Verify**: `cd web && pnpm test` green.
6. **Pre-commit + granular targets** — `.pre-commit-config.yaml` with
   `ruff-check` + `ruff-format` hooks (repo root); Makefile additions:
   `test-api`, `test-web`, `lint`, `format` targets mirroring the verify
   steps; README documents them and the `gen:api` prerequisite
   (`make up` first).
   **Verify**: `pre-commit run --all-files` green (after `pre-commit install`);
   `make test-api` runs pytest.
7. **CLAUDE.md** — new file at repo root: point at README's "Adding a module"
   contract, `make verify`/`make up`, the plans/ one-plan-one-commit rule, and
   the frontend feature-slice conventions. Keep it under 40 lines.
   **Verify**: file exists; no drift with README (link, don't duplicate).
8. `make verify` → exit 0. Commit: `test/dx: reporting+currencies+email coverage, frontend money-flow tests, pre-commit, granular make targets, CLAUDE.md`.

## Test plan

This plan IS tests — each step's verification is its own gate. No behavior
changes to shipping code except the `send-email` permission (step 3 uses a
403 assertion to pin it: viewer-role → 403).

## Maintenance

New endpoints must land with their test file section the same day. If
pytest-xdist is adopted later, first fix the shared mutable `erp_test`
database (TEST-06 in the audit) — do not parallelize before that.

## STOP conditions

- If the mocked-AI test requires changing `AIClient`'s public shape, STOP and
  report — the seam should be the transport, not the client.
- If the frontend render test needs a router/state refactor to work, STOP and
  report the specific blocker instead of restructuring.
