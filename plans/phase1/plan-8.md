# Plan 8 — Invoicing & Payments (AR/AP)

- **Depends on:** plan-7
- **Goal:** customer invoices (AR) and supplier bills (AP) — created standalone or
  generated from orders — plus payments that allocate across invoices. After this
  plan the money cycles are closed; plan-9 makes the books react.

## 1. Data model (schema `invoicing`)

- [x] `invoices` — invoice_type (`ar|ap`), number (`INV-2026-0001` / `BILL-…`),
      party (customer/supplier), source doc (SO/PO, nullable), invoice_date, due_date
      (party terms default), currency, status
      `draft → posted → partial → paid` (+ `void`), subtotal/discount/tax/total,
      amount_paid, notes, posted_at/by. Posted invoices are immutable; `void` writes
      reversal entries (plan-9) and never deletes.
- [x] `invoice_lines` — same snapshot shape as order lines (+ account hint for
      free-text revenue/expense lines, default from settings).
- [x] `payments` — number (`PAY-…` customer / `SPAY-…` supplier), party, payment_date,
      amount, method (`cash|bank|card|transfer|other`), reference, notes, created_by;
      permissions `invoicing.payment.*`.
- [x] `payment_allocations` — payment_id, invoice_id, amount. One payment can settle
      several invoices; an invoice can take several payments. Sum(allocations) ≤
      min(payment.amount, invoice.due).

## 2. Services & rules

- [x] Generate AR invoice from a `delivered`/`confirmed` SO (and AP bill from PO) —
      copies snapshot lines; order moves to `invoiced` and closes when fully invoiced.
- [x] `post_invoice()` — validates totals, locks the document, emits
      `invoice.posted` (accounting listens in plan-9). Numbering assigned at post time
      (drafts unnumbered — avoids gaps from abandoned drafts).
- [x] Payment recording flow: pick party → see open invoices with due amounts →
      allocate (auto-allocate FIFO default, manual override) → save. Invoice status
      flips posted → partial → paid automatically.
- [x] Void rules: draft → delete freely; posted → void (superuser/accountant perm)
      → reversal journal + allocations released.
- [x] Party statement endpoint: open invoices + due amounts + payments per party.
- [x] Overpayment handling: rejected in phase 1 (credit notes deferred; documented).

## 3. Frontend

- [x] **Invoicing** section: tabs Customers (AR) / Suppliers (AP) — invoice lists
      with status/due-date filters and aging hint column (days overdue), invoice
      editor (from order: prefilled; standalone: `<PartyPicker>` + lines),
      Post / Void / Record Payment actions.
- [x] Payment dialog: amount, method, allocation table with auto-FIFO button.
- [x] Party statement drawer on contact detail (open invoices, payments, balance).
- [x] Sidebar: Invoicing → Invoices, Payments.

## Acceptance

- [x] SO → invoice → 40% payment (status `partial`, amount_paid right) → 60% payment
      (status `paid`); allocations inspectable; SO auto-closed.
- [x] Same cycle AP-side with BILL numbering sequence separate from INV.
- [x] Posting is idempotent-safe (double POST → 409), posted invoice not editable,
      void produces correct reverse effect on status/payments.
- [x] `invoice.posted` event visible on the bus (test listener) — ready for plan-9.
- [x] `make verify` green.
