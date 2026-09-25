# Phase 1 — ERP Foundation & Core Business Flow

- **Status:** 📝 Draft — awaiting approval (no code until these plans are approved)
- **Created:** 2026-09-26
- **Product:** General-purpose SME ERP, self-hosted, single application, single database

---

## 1. Goal

Deliver a working vertical-slice ERP: an operator can log in, manage master data
(customers, suppliers, products), run the two money cycles end-to-end
(**Order → Delivery/Receipt → Invoice → Payment**) with inventory and basic
accounting kept automatically consistent, and see reports + audit logs. All of it
runs in Docker with one command.

## 2. Architecture (modular monolith)

```
                 ┌────────────────────────────┐
                 │       ERP APPLICATION      │
                 │  FastAPI (api)  ·  React SPA (web)
                 └─────────────┬──────────────┘
        ┌──────────┬───────────┼───────────┬──────────┐
        │          │           │           │          │
      CRM        Sales     Purchasing  Inventory  Invoicing ── Accounting
        └──────────┴───────────┴───────────┴──────────┘
                                │
                    PostgreSQL 17 (pgvector image, schemas per module)
                    Redis 7 (cache/sessions) · SeaweedFS (S3 files)
```

**ERP Core** (shared kernel every module builds on):
Organizations · Branches · Users · Roles & Permissions · Auth · Settings ·
Audit Logs · Notifications · Document Numbering · shared primitives (money, pagination, events).

**Module rules (enforced by convention, checked in review):**
- Each module owns its tables in its own PostgreSQL schema
  (`core`, `crm`, `catalog`, `sales`, `purchasing`, `inventory`, `invoicing`, `accounting`).
- Cross-module reads/writes go through the owning module's **service functions**, never
  direct foreign-key joins into another module's tables.
- Cross-module reactions (audit log, notifications, accounting postings) ride an
  **in-process event bus** — publishers don't import subscribers.
- One FastAPI app, one Alembic migration history, one deployment unit.

## 3. Repository layout (best practice proposal)

```
ERP/
├── api/                     # FastAPI backend — uv project
│   ├── app/
│   │   ├── main.py          # create_app(), router mounting, lifespan
│   │   ├── core/            # config, db, security, errors, deps, ai client, events
│   │   ├── shared/          # base models, pagination, money, numbering
│   │   └── modules/         # crm/ catalog/ sales/ purchasing/ inventory/ invoicing/ accounting/ reporting/
│   │       └── <module>/    # models.py · schemas.py · service.py · router.py · events.py · tests/
│   ├── alembic/             # single migration history
│   └── tests/
├── web/                     # Vite + React + TS SPA — pnpm
│   └── src/
│       ├── app/             # router, providers, guards, layouts
│       ├── features/        # feature-sliced: auth/ contacts/ products/ sales/ ... (api·components·pages)
│       ├── components/      # ui/ (shadcn) + shared app components
│       └── lib/             # api client, query client, utils
├── docker/                  # dev Dockerfiles + init scripts (api.Dockerfile, web.Dockerfile, seaweedfs/)
├── docker-compose.yml       # THE dev environment (all six services)
├── .env.example             # every variable the system reads
├── Makefile                 # up / down / logs / migrate / seed / verify / reset
├── README.md
└── plans/                   # this folder
```

## 4. Stack (your picks + proposed additions marked ➕)

| Layer | Choice |
|---|---|
| Frontend | Vite · React 19 · TypeScript · pnpm · React Router 7 · shadcn/ui (`--preset b0`) |
| ➕ Frontend | **TanStack Query** (server state), **react-hook-form + zod** (forms), **openapi-typescript** (generate TS types from FastAPI's OpenAPI schema — typed end-to-end for free), **recharts** (dashboards) |
| Backend | Python 3.12 · FastAPI · uv |
| ➕ Backend | **SQLAlchemy 2 (async) + asyncpg**, **Alembic** (migrations from day one), **pydantic-settings** (env config), **argon2-cffi + PyJWT** (auth), **structlog**, **ruff** (lint+format), **pytest + pytest-asyncio** |
| AI | `ai` SDK for python (`uv add ai`) + **OpenRouter** (OpenAI-compatible); key optional in dev |
| Infra | Docker Compose · `pgvector/pgvector:pg17` · `redis:7-alpine` · `chrislusf/seaweedfs` (S3 API) |

## 5. Key design decisions (my interventions on your brief)

1. **Single org per deployment (phase 1).** Self-hosted SME software ships one company per
   install. `organizations` and `branches` are fully modeled so multi-org is a later
   schema-level change, not a rewrite — but phase 1 does not pay the multi-tenant tax
   everywhere.
2. **Unified contacts.** Customers and Suppliers are one `crm.contacts` table with
   `is_customer` / `is_supplier` flags (the Odoo `res.partner` pattern) — the same company
   is frequently both, and this avoids duplicate-party drift.
3. **Money & IDs.** PostgreSQL `NUMERIC` only (never float): amounts `NUMERIC(18,2)`,
   quantities `NUMERIC(18,4)`, unit prices `NUMERIC(18,6)`. Primary keys are **UUIDv7**
   (time-ordered, index-friendly).
4. **Single currency in phase 1** (org base currency). `currency` columns exist on documents
   so multi-currency can land later without schema surgery.
5. **Quotes deferred.** Phase 1 documents: Sales Orders, Purchase Orders, AR/AP invoices,
   payments, receipts/deliveries/adjustments. Quotations → phase 2.
6. **Costing = moving average**, stock never negative (configurable guard), every stock
   change is an immutable `stock_moves` row; `stock` is the derived current state.
7. **Accounting is derived, not hand-fed.** Posting an invoice/payment/delivery emits events;
   the accounting module writes balanced journal entries automatically. Trial balance must
   always sum to zero.
8. **Auth:** argon2 password hashing, short-lived JWT access token + refresh token in an
   httpOnly cookie. No external IdP in phase 1.
9. **RBAC as strings:** permission codes like `sales.order.confirm`, enforced by one FastAPI
   dependency; superuser bypass; seeded system roles (admin, accountant, sales, purchasing, warehouse, viewer).
10. **Documents are immutable once posted/confirmed** — corrections happen by reversal, not edit.
11. **SeaweedFS via its S3 API** (boto3-style client). Phase 1 proves the plumbing in health
   checks + config; file attachments/product images are a phase-2 feature.
12. **AI in phase 1 = plumbing only** (OpenRouter client + graceful "no key" behavior). First
   user-facing AI feature (e.g. product description / report summarizer) lands in phase 2 or as
   plan-10 stretch.
13. **Dev parity with Dokploy:** every service is configured purely by env vars
   (`DATABASE_URL`, `REDIS_URL`, `S3_*`, `JWT_SECRET`, `OPENROUTER_API_KEY`). Moving DB/Redis/S3
   to external managed credentials later is a `.env` swap — zero code change. Production
   Dockerfiles/deployment are explicitly out of scope now.

## 6. Out of scope (deferred to phase 2+)

HR · workflow engine · email/SMS notifications (in-app only now) · quotations · multi-currency
FX · bank reconciliation · e-invoicing · i18n · file attachments UI · mobile · production
Dockerfiles/Dokploy config · backup tooling.

## 7. Plans & execution order

Execute in numeric order. **One plan = one commit.** Tick checkboxes in the plan files and
update the Status column here as work completes.

| # | Plan file | Delivers | Depends on | Status |
|---|---|---|---|---|
| 1 | [plan-1.md](plan-1.md) | Repo skeleton + full Docker dev environment (`make up` works) | — | ✅ done |
| 2 | [plan-2.md](plan-2.md) | FastAPI foundation: config, DB, Alembic, auth plumbing, AI client, tests | 1 | ✅ done |
| 3 | [plan-3.md](plan-3.md) | Frontend foundation: Vite+shadcn shell, router, typed API client, login UI | 1 (2 for live login) | ✅ done |
| 4 | [plan-4.md](plan-4.md) | ERP Core: org, branches, users, RBAC, real auth, settings, audit, notifications | 2+3 | ✅ done |
| 5 | [plan-5.md](plan-5.md) | Master data: contacts (customers/suppliers), products, categories, UoM, taxes | 4 | ✅ done |
| 6 | [plan-6.md](plan-6.md) | Sales & Purchasing: SO/PO with numbering, state machines, line editors | 5 | ✅ done |
| 7 | [plan-7.md](plan-7.md) | Inventory: warehouses, stock moves, receipts/deliveries, avg costing | 6 | ✅ done |
| 8 | [plan-8.md](plan-8.md) | Invoicing & payments: AR/AP invoices, payments, allocations, statements | 7 | ☐ todo |
| 9 | [plan-9.md](plan-9.md) | Basic accounting: CoA, auto-posted journals, ledger, trial balance | 8 | ☐ todo |
| 10 | [plan-10.md](plan-10.md) | Dashboard, reports, audit UI, demo seed, end-to-end acceptance | 4–9 | ☐ todo |

## 8. Definition of done (phase-level acceptance walkthrough)

With a fresh clone, on a machine with only Docker + uv + pnpm installed:

1. `cp .env.example .env && make up` — all six services healthy.
2. Log in as the seeded admin at `localhost:5173`.
3. Create a customer, a supplier, and three products (one service-type).
4. Create + confirm a **PO** for the supplier; post the **receipt** → stock up, avg cost set.
5. Create + confirm an **SO** for the customer; post the **delivery** → stock down, COGS captured.
6. Generate the **AR invoice** from the SO, post it → balanced journal entry exists.
7. Record a **partial payment**, then the remainder → invoice closes, cash/AR entries correct.
8. Same cycle on the buy side (bill + supplier payment).
9. Dashboard shows this month's sales, open AR/AP; **stock valuation** and **trial balance**
   reports are consistent with the moves above (trial balance sums to zero).
10. Audit log shows every create/confirm/post action with actor + before/after.
11. `make verify` (ruff + pytest + web typecheck/lint/build) is green.
