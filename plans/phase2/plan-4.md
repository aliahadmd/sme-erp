# Plan 4 — Quotations & Numbering Admin UI

- **Depends on:** nothing (commercial track start)
- **Goal:** send prices before committing stock or books. Quotations reuse the
  order engine; conversion to SO is one click. Also: the numbering-prefix
  admin UI that phase 1 left as settings-keys only.

## 1. Data model & engine (schema `sales`)

- [x] `quotations` + `quotation_lines` — mirror of sales orders with
      `valid_until` date and status `draft → sent → accepted → converted`
      (+ `cancelled`, + `expired` set lazily by job/middleware when
      `valid_until` passes).
- [x] Reuse `shared/order_engine` via the documents factory (third config:
      prefix `QT`, entity `quotation`, perm `sales.quote.*`). Totals, line
      snapshots, state machine, audit — all inherited.
- [x] Numbering admin UI (Settings → Numbering): edit the
      `numbering.prefixes` setting per entity (contact, quotation, sales_order,
      purchase_order, receipt, delivery, adjustment, ar_invoice, ap_invoice,
      payments, journal_entry) and show the next number per entity. Resetting
      counters is superuser-only and audited.

## 2. Backend

- [x] Router via documents factory: CRUD + `send` (draft→sent, audit),
      `accept` (sent→accepted), `convert` (accepted→converted, creates a draft
      SO copying lines/notes, links `quotation_id` on the SO), `cancel`.
      Conversion is idempotent-safe: an accepted quote converts exactly once
      (transition guard).
- [x] Expiry: lazy check on quote read/list (like overdue invoices) + optional
      arq cron from plan-1.
- [x] Permissions `sales.quote.*` added to the catalog and to the `sales`
      system role; conversion requires `sales.order.create`.
- [x] Tests: totals reuse, send/accept/convert lifecycle, double-convert 409,
      expiry lazily marks `expired`, RBAC (purchasing user can't read quotes? —
      decidable default: quotes are `sales.quote.*` only).

## 3. Frontend

- [x] Sales area gains a `Quotations` tab: list (status tabs, valid-until
      badge, expired highlighting), quote editor (same document editor as
      orders, header adds valid_until), actions Send / Accept / Convert / Cancel.
- [x] Converted quotes deep-link to the created SO; SO shows "From QT-…".
- [x] Settings → Numbering page (table of entities, prefix inputs, next-number
      preview).

## Acceptance

- [x] Quote → accept → convert → SO → delivery → invoice flow works
      end-to-end with correct numbering at every step.
- [x] Expired quotes are marked and cannot be accepted; double conversion 409s.
- [x] Prefixes changed in the admin UI affect the next document number; audit
      entries exist for prefix changes.
