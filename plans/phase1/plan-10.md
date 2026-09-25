# Plan 10 — Dashboard, Reports, Audit UI, Demo Seed & Acceptance

- **Depends on:** plans 4–9
- **Goal:** phase-1 closure — the numbers become visible, a demo dataset makes the
  system self-explanatory, and the full acceptance walkthrough from
  `index.md §8` passes on a fresh clone.

## 1. Dashboard

- [ ] KPI cards: sales this month (posted AR invoices), open receivables, open
      payables, low-stock count. Click-through to the respective lists.
- [ ] Chart: last-12-weeks sales vs purchases (recharts; server aggregate endpoint).
- [ ] Recent activity feed (latest audit entries for the current user's permissions).

## 2. Reports (server-side aggregates, table UI + CSV export)

- [ ] Sales by customer / by product (date range, totals + %).
- [ ] Purchases by supplier / by product.
- [ ] Stock valuation (per warehouse: qty × avg cost; ties to inventory value account).
- [ ] AR / AP aging (current, 30/60/90+ buckets).
- [ ] Tax summary (by tax code, period — for filling returns).
- [ ] Trial balance (already in plan-9; linked here for one reports home).
- [ ] `reporting` module: read-only service aggregating across module schemas via
      their published service functions/reads (the one sanctioned cross-module reader),
      permission `reports.view`.

## 3. Polish & platform closure

- [ ] Audit log UI final pass: filters (user, action, entity, date), expandable
      before/after diff, CSV export (`core.audit.read`).
- [ ] Notifications bell: unread count, list, mark-read — wired to real events
      (e.g. low stock on delivery posting, invoice overdue).
- [ ] UX sweep: empty states with guidance per page, loading skeletons, consistent
      breadcrumbs, 403 page, sane default page sizes.

## 4. Demo seed (`make seed-demo`)

- [ ] Idempotent generator: org + 2 branches, admin + role-representative users,
      ~10 contacts, ~20 products across categories (incl. a service), one full buy
      cycle (PO → receipt → bill → payment) and two full sell cycles (SO → delivery →
      invoice → partial + final payment), a stock adjustment, one voided invoice —
      enough for every report to show meaningful numbers.

## 5. Phase acceptance

- [ ] Run `index.md §8` walkthrough end-to-end on a fresh clone; record results in
      this file's checklist.
- [ ] `make verify` green (ruff, pytest, web typecheck/lint/test/build).
- [ ] README final: screenshots skip, quickstart, env table, module map, "add a new
      module" guide (proves the architecture is teachable).
- [ ] Backlog file `plans/phase2-draft.md` seeded with deferred items: quotes, HR,
      multi-currency, attachments/uploads, email notifications, workflow engine,
      production Dockerfiles + Dokploy deployment, backups, AI features.

### Stretch (only if everything above is green)
- [ ] AI demo: "generate product description" button on the product form using the
      plan-2 OpenRouter plumbing; disabled state when no key.
