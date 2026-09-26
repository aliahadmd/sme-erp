# Plan 6 — Credit Notes & Refunds

- **Depends on:** plan-4 (document patterns); pairs with plan-7 (FX-safe if
  landed after multi-currency, but not required)
- **Goal:** correct mistakes and returns the ERP way — reversing documents that
  keep the books balanced and the audit trail intact.

## 1. Data model & backend (schema `invoicing`)

- [x] Reuse `invoices` with `invoice_type` extended to
      `ar | ap | ar_credit | ap_credit` and a nullable `original_invoice_id`
      (linked credit). Credit notes get their own numbering entities
      (`ar_credit` → `CRN-`, `ap_credit` → `SCN-`).
- [x] Posting a credit note emits `credit_note.posted`; the accounting
      subscriber posts the exact mirror of the original invoice's entry
      (CR AR / DR Revenue / DR Tax Payable for AR — keeps trial balance zero).
- [x] Applying a credit note to open invoices: reuse the existing
      `payment_allocations` mechanism with a "credit" payment source — a credit
      reduces open balances exactly like money, without touching cash accounts.
- [x] Guards: credit total ≤ original invoice total (per original, summed);
      credits cannot be edited after post; void reverses the journal entry and
      releases allocations (same rules as invoices).
- [x] Refund recording: a negative-direction payment
      (`direction=out` for AR refunds via method `bank`) linked to the credit
      note, producing the cash-side journal entry.

## 2. Frontend

- [x] Invoicing area: "New credit note" action on posted invoices (pre-filled
      header, lines editable, reason required); credit notes appear in the same
      lists with `CRN/SCN` prefixes and a credit badge.
- [x] Record-refund dialog on credit notes (method + reference).
- [x] Party statement extended: credit notes and refunds shown; balance math
      unchanged (credits reduce open balances through allocations).

## Acceptance

- [x] Post invoice → credit one line → journal mirror exists, trial balance
      zero, invoice balance reduced via allocation.
- [x] Cash refund posts the cash-side entry; statement shows net effect.
- [x] Over-credit rejected; voids reverse cleanly; audit + notifications fire.
- [x] Tests for the full cycle, guards, and journal balance; `make verify` green.
