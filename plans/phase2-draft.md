# Phase 2 — Draft Backlog (SUPERSEDED)

> **Superseded by [`plans/phase2/index.md`](phase2/index.md)** — these raw notes
> were expanded into the 10 executable phase-2 plans. Kept for reference.

Deferred from phase 1 (see `phase1/index.md §6`). Rough priority order; refine
into `phase2/plan-*.md` files before starting.

## Product
- [ ] Quotations (quote → order conversion; reuses the order engine)
- [ ] File attachments (product images, invoice PDFs) — SeaweedFS plumbing already live
- [ ] Partial deliveries/billing (qty tracking per order line)
- [ ] Credit notes & refunds
- [ ] Multi-currency with FX rates (currency columns already exist)
- [ ] Email notifications (order confirmations, invoice overdue)

## Platform
- [ ] Production Dockerfiles (multi-stage, non-reload) + Dokploy deployment
- [ ] Backups: pg_dump cron + S3 offsite, restore runbook
- [ ] Background jobs (email, report exports) — arq/celery on existing Redis
- [ ] GitHub Actions CI: `make verify` equivalent + image builds
- [ ] Rate limiting on auth endpoints; audit-log retention policy

## Modules
- [ ] HR: employees, departments, leave (module #7 in the architecture)
- [ ] Workflow engine: approval rules on POs above thresholds
- [ ] Bank feeds / reconciliation
- [ ] E-invoicing integrations (per-market)
- [ ] AI features (OpenRouter plumbing is live): product description generator,
      report summarizer, natural-language report queries
- [ ] i18n (UI strings extraction; RTL already supported by shadcn preset)
- [ ]Embeddings/pgvector semantic search over documents (extension already enabled)

## Tech debt noticed during phase 1
- [ ] `pnpm approve-builds` dance: pin pnpm version per-project; consider corepack defaults
- [ ] Numbering: expose prefix/next-number admin UI (settings keys exist)
- [ ] Reports module uses raw SQL for cross-schema aggregates — consider views
- [ ] Consider per-module alembic branches if migrations start colliding
