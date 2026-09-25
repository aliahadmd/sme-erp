# Plan 6 — Sales & Purchasing Documents

- **Depends on:** plan-5
- **Goal:** the two order documents with real ERP semantics: sequential numbering,
  explicit state machines, line editing with live totals, RBAC on every transition.
  Stock and accounting reactions come later (plans 7–9) — confirmation here only
  validates and freezes the document.

## 1. Data model

- [x] Schema `sales`: `sales_orders` — org, branch, number (`SO-2026-0001`), customer_id,
      order_date, expected_date, currency (= org base in phase 1), status
      `draft → confirmed → delivered → invoiced → closed`, plus `cancelled`;
      subtotal, discount_total, tax_total, total (NUMERIC(18,2)); notes; created_by;
      confirmed_at/by. `sales_order_lines` — product_id (nullable for free-text lines),
      description, qty NUMERIC(18,4), uom snapshot, unit_price NUMERIC(18,6),
      tax_id, discount_pct, line_total (computed, stored).
- [x] Schema `purchasing`: `purchase_orders` / `purchase_order_lines` — mirror image
      with supplier_id and status `draft → confirmed → received → invoiced → closed`.
- [x] Lines keep **snapshots** (name, price, tax rate) — later edits to products never
      rewrite history.
- [x] Quotes: intentionally deferred (index decision 5); schema leaves room for a
      `doc_kind` column if quotes join the same tables in phase 2.

## 2. Services & rules

- [x] **Numbering:** per org + doc type + year via shared numbering service
      (gap-tolerant; prefix from settings).
- [x] **State machine** per document type as explicit transition table;
      illegal transition → 409 `DomainError`. Guards:
      confirm → requires ≥1 line, valid party, totals recompute; cancel → allowed from
      draft/confirmed (not delivered/invoiced); closed set automatically when fully
      invoiced (hooked by plan-8).
- [x] Totals engine in `shared/` (line total = qty × price × (1 − discount%) + tax;
      header totals = sums; Decimal end-to-end) — single implementation reused by
      plans 8–9.
- [x] Permissions per action: `sales.order.create|update|confirm|cancel|read`
      (mirror set for purchasing). Only draft is editable; confirmed documents are
      immutable.
- [x] Events published: `sales_order.confirmed`, `purchase_order.confirmed`, …
      (consumed by inventory plan-7, accounting plan-9 — no listeners yet).
- [x] Audit entries on create/update/confirm/cancel.

## 3. Frontend

- [x] `features/sales` + `features/purchasing` (mirrored structure):
      list pages (status filter tabs, date range, party + number search, totals footer),
      document editor page — header form + line-items table (add via
      `<ProductPicker>`, inline qty/price/discount, live header totals), notes,
      action buttons per status (Confirm / Cancel) with confirmation dialogs.
- [x] Draft autosave; leaving with unsaved changes prompts.
- [x] Sidebar links go live: Sales → Orders; Purchasing → Orders.
- [x] Dashboard counters (draft/confirmed counts) as stretch.

## Acceptance

- [x] Create → confirm SO/PO: number sequence correct, editing after confirm is blocked
      (API + UI), cancel from confirmed works and is audited.
- [x] Totals correct with mixed discounts and tax rates (property-ish test cases:
      qty 2.5 kg, 12.5% line discount, 18% tax).
- [x] RBAC: a `sales` user cannot confirm a PO; a viewer cannot create anything (tests).
- [x] Concurrent confirms cannot double-consume or corrupt numbering (test).
- [x] `make verify` green.
