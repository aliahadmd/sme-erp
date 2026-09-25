# Plan 9 — Basic Accounting

- **Depends on:** plan-8
- **Goal:** double-entry bookkeeping that runs itself. Business events post balanced
  journal entries automatically; humans get a chart of accounts, manual entries,
  ledger, and a trial balance that always sums to zero.

## 1. Data model (schema `accounting`)

- [ ] `accounts` — code (e.g. `1000`), name, type (`asset|liability|equity|income|expense`),
      parent_id, is_system, is_active, currency (= base). **Seeded minimal CoA:**
      Cash, Bank, Accounts Receivable, Accounts Payable, Inventory, Tax Payable,
      Sales Revenue, Sales Tax, COGS, Purchases Expense, Stock Correction,
      Rounding — mapped to business events via a settings-backed mapping table
      (`event → account`), editable by admin.
- [ ] `journal_entries` — number (`JE-2026-0001`), entry_date, memo, source_type
      (`ar_invoice|ap_bill|payment|delivery|receipt|adjustment|manual`), source_id,
      status (`posted` only in phase 1; void = reversing entry), created_by.
- [ ] `journal_lines` — entry_id, account_id, debit, credit NUMERIC(18,2).
      Constraint: entry sums must balance (service-enforced + DB trigger as backstop).

## 2. Auto-posting (event subscribers — the payoff of the event bus)

- [ ] `invoice.posted` (AR): DR Accounts Receivable / CR Sales Revenue / CR Tax Payable
      (per tax line). AP: mirror (DR expense-or-inventory-hint per line / CR AP).
- [ ] `payment.recorded`: AR payment → DR Cash/Bank (by method mapping) / CR AR.
      AP payment mirror.
- [ ] `delivery.posted`: DR COGS / CR Inventory (qty × avg cost from plan-7 move).
      `adjustment.posted`: DR/CR Stock Correction ↔ Inventory.
- [ ] Invoice void / payment void / delivery void → automatically posted **reversing
      entries** referencing the originals.
- [ ] Every posting is one transaction with the business action; failure aborts the
      business action too (no unbacked documents).

## 3. Endpoints & frontend

- [ ] Endpoints: CoA CRUD (system accounts locked), manual journal entry
      (`accounting.entry.create` — balanced or rejected), general ledger per account
      (period filter, running balance), trial balance (per account: opening is
      out of scope — period debit/credit/totals), journal browser.
- [ ] Frontend **Accounting** section: Chart of Accounts page, Journal Entries list +
      manual entry form (draggable lines, live balance indicator), General Ledger
      report page, Trial Balance page (totals row must read 0.00 — a visible integrity
      promise).
- [ ] Settings: account mapping editor (which account receives revenue, COGS, etc.).

## Acceptance

- [ ] Full demo cycle produces balanced entries: invoice posts revenue+AR+tax;
      payments clear AR into cash; delivery posts COGS from avg cost.
- [ ] Trial balance sums to exactly zero after every scenario (test asserts this on a
      random operation sequence — property test).
- [ ] Voiding a posted invoice reverses it 1:1; trial balance still zero.
- [ ] Manual unbalanced entry rejected with a helpful error.
- [ ] `make verify` green.
