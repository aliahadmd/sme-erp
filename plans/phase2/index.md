# Phase 2 — Production Readiness & the Complete Commercial Loop

- **Status:** 📝 Draft — awaiting approval (no code until these plans are approved)
- **Created:** 2026-09-27
- **Predecessor:** phase 1 — complete (all 10 plans, see `phase1/index.md`)
- **Supersedes:** `plans/phase2-draft.md` (kept as raw backlog notes)

## 1. Goal

Phase 1 proved the product. Phase 2 makes it **deployable and operable**, then
**completes the commercial loop** that real SMEs need on day one:

1. Run it on a real server (Dokploy, HTTPS, backups, CI) — production readiness.
2. Close the remaining document gaps — quotations, credit notes, partial
   fulfillment surfaced in the UI.
3. Add the first "growth" features — background jobs, email, multi-currency,
   AI assistance, and the HR module.

Explicit non-goals (deferred to phase 3+): i18n, bank feeds/reconciliation,
e-invoicing integrations, payroll, multi-org tenancy, mobile apps.

## 2. Recurring principles (inherited from phase 1, still binding)

- One plan = one commit; tick checkboxes in plan files, update the status
  table here as work completes. `make verify` must be green at every commit.
- Schema-per-module, service-function + event-bus boundaries, UUIDv7 PKs,
  NUMERIC money with Decimal, immutable posted documents (reversals only).
- Everything env-configured — the Dokploy deployment is the same images with
  different env, zero code changes.
- Every plan ships tests and, where user-visible, browser verification.

## 3. Key design decisions for this phase

1. **Background jobs = arq** (async worker on the Redis we already run) — not
   Celery: same async stack, fewer moving parts, no new broker.
2. **Production web = static build behind a reverse proxy.** `web` builds to
   static files (nginx/Caddy image) that also proxy `/api` to the api service —
   the SPA never talks to two origins in production.
3. **Multi-stage, non-root Dockerfiles** for api and web; dev keeps bind mounts
   and root (documented dev-only convenience).
4. **Multi-currency stays ledger-simple:** documents store currency + rate at
   document date; the ledger (phase-1 accounting) is always posted in the
   company base currency; realized FX gain/loss posts on settlement.
5. **Credit notes are first-class documents** (`ar_credit` / `ap_credit`) that
   reverse the original invoice's journal entry and can absorb payments —
   not "negative invoices".
6. **HR starts small:** employees, departments, leave requests with an
   approval flow that doubles as the first workflow-engine use case.
7. **AI is assistive only:** every AI feature writes nothing without explicit
   user acceptance of the generated content; all calls degrade gracefully
   without `OPENROUTER_API_KEY`.
8. **Audit-log retention** moves to a scheduled job (default 365 days) instead
   of unbounded growth.
9. **Auth rate limiting** and `JWT_SECRET` fail-fast already landed in phase 1's
   audit fixes — phase 2 extends rate limiting to all write endpoints via
   middleware.

## 4. Plans & execution order

| # | Plan file | Delivers | Depends on | Status |
|---|---|---|---|---|
| 1 | [plan-1.md](plan-1.md) | Background jobs infrastructure (arq worker, scheduling, retries) | — | ✅ done |
| 2 | [plan-2.md](plan-2.md) | Production build & Dokploy deployment (multi-stage images, HTTPS, non-root) | — | ✅ done |
| 3 | [plan-3.md](plan-3.md) | CI pipeline + backups + audit-log retention | 1, 2 | ✅ done |
| 4 | [plan-4.md](plan-4.md) | Quotations (quote documents, conversion to orders) + numbering admin UI | — | ☐ todo |
| 5 | [plan-5.md](plan-5.md) | Fulfillment UX: partial delivery/billing states in the UI, backorder visibility | — | ☐ todo |
| 6 | [plan-6.md](plan-6.md) | Credit notes & refunds | 4 | ✅ done |
| 7 | [plan-7.md](plan-7.md) | Multi-currency with FX rates | 6 | ✅ done |
| 8 | [plan-8.md](plan-8.md) | Email notifications + preferences | 1, 6 | ☐ todo |
| 9 | [plan-9.md](plan-9.md) | AI: bulk descriptions, report summarizer, semantic document search (pgvector) | 1 | ☐ todo |
| 10 | [plan-10.md](plan-10.md) | HR module: employees, departments, leave with approvals | 1 | ☐ todo |

Plans 1–3 are the platform track; 4–8 the commercial track; 9–10 growth.
Plans from different tracks can proceed in parallel once their dependencies exist.

## 5. Definition of done (phase-level acceptance)

1. A fresh VPS running Dokploy deploys the app from the repo with HTTPS and
   environment-provided secrets; `JWT_SECRET` fails fast if unset (phase-1 fix).
2. Nightly Postgres backups land in S3; a documented restore has been rehearsed
   once into a scratch database.
3. CI runs `make verify` on every push and builds/publishes images on main.
4. Business flow: quote → convert to SO → partial delivery → partial invoice →
   remainder → **credit note on one line** → all documents numbered, journal-
   balanced, and visible in reports.
5. A customer invoice past due date triggers an emailed reminder (background
   job) plus the in-app notification — no scheduler code in the API process.
6. Working in a second currency produces correct base-currency journals and a
   realized-FX entry on settlement.
7. HR: employees exist with a department, a leave request can be submitted and
   approved, and approvals appear in the audit log and notifications.
8. `make verify` green; demo seed extended to exercise every new feature.
