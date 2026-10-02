# plans/ — Implementation & Improvement Plans

Master index for everything planned in this repo. New work enters through the
numbered improve plans below (`001`–`006`); phase directories record the
original builds.

## Improve round — 2026-09-27 (planned at commit `8d1a5fd`)

Findings from the post-phase-2 full audit (4 parallel audits: correctness,
security, performance/architecture, tests/DX/docs/direction). Vetted; the
rejected-by-design list lives at the bottom of each plan. Execute in ID order
unless a dependency says otherwise.

| ID | Plan | Priority | Effort | Status |
|---|---|---|---|---|
| 001 | [Security hardening sweep](001-security-hardening.md) | P1 | M | ☐ todo |
| 002 | [FX & money correctness on writes, voids, and reports](002-fx-money-correctness.md) | P1 | S–M | ☐ todo |
| 003 | [Document lifecycle integrity](003-lifecycle-integrity.md) | P2 | M | ☐ todo |
| 004 | [Background jobs robustness](004-jobs-robustness.md) | P2 | M | ☐ todo |
| 005 | [Database indexes for hot queries](005-db-indexes.md) | P2 | S–M | ☐ todo |
| 006 | [Test coverage, DX, and docs](006-tests-dx-docs.md) | P3 | M | ☐ todo |

Dependency notes: 002 and 003 are independent of each other but both touch
`invoicing/router.py` — sequence them to avoid rebasing. 006's reporting tests
should land after 002 (report totals change to base currency).

## Phase directories (historical record)

- [`phase1/`](phase1/index.md) — initial build, all 10 plans ✅
- [`phase2/`](phase2/index.md) — jobs, prod deploy, CI/backups, quotations,
  fulfillment UX, credit notes, multi-currency, email, AI, HR — all 10 plans ✅
- [`audit-fix-log.md`](audit-fix-log.md) — findings and fixes from the first
  audit (2026-09-26) and its post-phase-2 follow-up (2026-09-27)

## Conventions

- One plan = one commit; tick checkboxes and update status tables as you go.
- Verification gate for everything: `make verify` (ruff + pytest; web
  typecheck/lint/vitest/build). Must be green before a plan is called done.
- Backend tests live in `api/tests/test_<module>.py`; match the existing
  fixtures (`_admin`, `_auth`) and the shared seeded `erp_test` database.
