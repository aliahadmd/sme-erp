# Plan 7 — Multi-Currency with FX Rates

- **Depends on:** plan-6 (land after credit notes so credits are FX-aware too)
- **Goal:** buy and sell in foreign currencies while the books stay in the
  company base currency — the phase-1 `currency` columns finally go to work.

## 1. Data model & backend

- [ ] `currencies` (code, name, symbol) and `fx_rates` (currency, rate_date,
      rate_to_base NUMERIC(18,8); unique per (currency, date)) in schema `core`.
- [ ] Rate resolution service: exact date → latest earlier date → error.
      Manual rate entry UI (admin) + optional scheduled importer stub (manual
      rates only in phase 2).
- [ ] Documents keep their `currency` + gain a `fx_rate` snapshot
      (rate to base at document date). Line math unchanged (document currency);
      header stores `total_base` alongside `total`.
- [ ] Accounting postings convert with the snapshot rate: journal entries stay
      100% base currency; AR/AP sub-ledger tracks the document-currency balance
      on the invoice (already has currency).
- [ ] **Realized FX on settlement:** when a payment settles a document whose
      payment-date rate differs from the invoice's snapshot rate, post the
      difference to `FX Gain/Loss` (two accounts, seeded; mapping in
      `accounting.mapping`). Unrealized revaluation stays out of scope.
- [ ] Settings: base currency becomes truly changeable while org has no
      documents (guard); per-party currency defaults validated against it.

## 2. Frontend

- [ ] Currency admin (Settings → Currencies): list, rate entry grid (per
      currency per date), "latest rates" view.
- [ ] Documents: currency picker on orders/invoices/payments (defaults from
      party); unit prices entered in document currency; totals panel shows
      document total + base equivalent at the snapshot rate.
- [ ] Reports: sales/purchases/valuation/agings converted to base (rates at
      document date) with a per-currency breakdown toggle.

## Acceptance

- [ ] Sell in EUR while base is USD: invoice → payment at a different rate →
      journals in base, FX gain/loss entry correct (property test with random
      rates), trial balance zero.
- [ ] Changing rates after documents exist never rewrites history (snapshots).
- [ ] Reports reconcile with the ledger in base currency; `make verify` green.
