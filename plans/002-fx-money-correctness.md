# Plan 002: FX & money correctness on writes, voids, and reports

> **Executor instructions**: Step-by-step; run every verification command;
> STOP and report on any mismatch with the "Current state" excerpts.
>
> **Drift check (run first)**: `git diff --stat 8d1a5fd..HEAD -- api/app/modules/invoicing api/app/modules/reporting api/app/modules/documents api/app/modules/sales api/app/jobs`
> — compare excerpts below against live code; mismatch = STOP.

## Status

- **Priority**: P1
- **Effort**: S–M
- **Risk**: MED (touches the money path — characterization first)
- **Depends on**: none (but sequence after 001 to avoid rebasing both on
  `core/router.py`… this plan does not touch that file)
- **Category**: bug (financial correctness)
- **Planned at**: commit `8d1a5fd`, 2026-09-27

## Why this matters

Five verified defects corrupt base-currency numbers or silently drop
bookkeeping: voided-payment reversals post the wrong amount, FX rounding can
straddle a cent and abort the journal, invoice edits leave stale FX snapshots,
reports sum document-currency totals across currencies and ignore credit
notes, and AP credit lines inherit sale-side tax defaults. In an ERP these are
not edge cases — they are the monthly close.

## Current state

- `api/app/modules/invoicing/router.py` — `payment.voided` payload includes
  `amount` and `credit_note_id` but NOT `amount_base`
  (`accounting/postings.py::_on_payment_voided` falls back to
  `payload["amount"]`, posting the document amount as base).
- `api/app/modules/invoicing/router.py` (create + post payloads) — `net_base`
  and `tax_base` are raw `Decimal` divisions by `fx_rate`, unquantized;
  `accounting/service.py::post_entry` quantizes lines independently and
  raises when debit ≠ credit — a 1-cent straddle kills the whole event
  (swallowed by event isolation → invoice commits with NO journal entry).
- Same files — `update_invoice` (PATCH) overwrites `invoice.due_date =
  body.due_date` and `invoice.notes = body.notes` unconditionally, and never
  re-resolves `fx_rate` when `body.currency` changes.
- `api/app/modules/invoicing/router.py` credit branch —
  `build_lines_from_input(session, org.id, body.lines, False)` hardcodes the
  sale side for credit lines (`is_purchase=False` defaults unit price from
  `sale_price` and tax from `sale_tax_id`).
- `api/app/modules/reporting/router.py` — all six aggregates `SUM(total)` /
  `SUM(total - amount_paid)` in document currency (should aggregate the
  stored `total_base`) and filters `invoice_type = 'ar'`/`'ap'` that exclude
  credit notes.
- Repo conventions: money via `app/shared/money.py::money` (banker's
  rounding, NUMERIC); tests follow `tests/test_invoicing.py` helpers
  (`_admin`, `_auth`) and assert trial balance via
  `/api/accounting/trial-balance`.

## Commands

| Purpose | Command (repo root) | Expected |
|---|---|---|
| Lint/format | `docker compose exec -T api uv run ruff check --fix app tests && docker compose exec -T api uv run ruff format app tests` | clean |
| Tests | `docker compose exec -T api uv run pytest -q` | all pass |
| Full gate | `make verify` | exit 0 |

## Scope

**In scope**: `api/app/modules/invoicing/router.py`,
`api/app/modules/invoicing/service.py`, `api/app/modules/invoicing/schemas.py`,
`api/app/modules/reporting/router.py`,
`api/app/modules/documents/router_factory.py` (update-order fx recompute),
`api/app/modules/sales/quote_router.py` (quote PATCH fx recompute),
`api/tests/test_invoicing.py`, `api/tests/test_reporting.py` (create).

**Out of scope**: changing the FX snapshot model (rates stay date-resolved and
frozen at creation for posted documents); realized-FX calculation design;
`plans/phase2-draft.md` items.

## Steps

1. **Characterization first (money path)** — before changing behavior, add
   `tests/test_fx_characterization.py`: create a EUR invoice (rate 0.80) with
   a line set chosen so net/tax straddle a cent (e.g. qty 3 × 16.67 at 7%
   tax), post it, and assert the current journal payload amounts. This test
   pins today's behavior so the quantize fix in step 2 is provably a fix, not
   a drift.
   **Verify**: test passes before the payload changes; keep it green after.
2. **Quantize base amounts at the payload boundary** — in the `invoice.posted`
   and `invoice.voided` payload construction, pass `net_base` and `tax_base`
   through `app/shared/money.py::money`, and derive
   `total_check = net_base + tax_base`; when `total_check != total_base`,
    adjust `net_base` by the residual so `net + tax == total` exactly
    (standard ERP rounding-residual handling — the residual goes to revenue,
    not to a rounding account, for invoices).
   **Verify**: the characterization test still passes; add a straddle case
   where `round(net)+round(tax) != round(total)` and assert balanced lines.
3. **Voided payment payload completeness** — in `void_payment`, add
   `amount_base` (from `payment.amount_base`) and `credit_note_id` to the
   `payment.voided` payload, mirroring `payment.recorded`. The subscriber
   already branches on both.
   **Verify**: extend the refund/void tests to assert the reversing entry
   hits the refunds account and uses the base amount.
4. **PATCH no longer clears optional fields + re-resolves FX on currency
   change** — in `update_invoice`: only overwrite `due_date`/`notes` when
   present in the payload; when `currency` changes, re-resolve
   `resolve_rate(session, org.id, new_currency, invoice.invoice_date)`,
   update `invoice.fx_rate`, and recompute `total_base` after
   `recompute_header`. Add tests: PATCH without `currency` keeps the old
   rate; PATCH with a new currency updates it.
   **Verify**: two new tests in `tests/test_invoicing.py` pass.
5. **Reporting in base currency, credits included** — `reporting/router.py`:
   switch `SUM(total)` → `SUM(total_base)`,
   `SUM(total - amount_paid)` → `SUM(total_base - amount_paid)`; change the
   invoice-type filters so `ar` buckets include `ar` sales minus `ar_credit`
   totals (negated) and `ap` mirrors `ap_credit`; keep the per-currency
   breakdown OUT of scope (phase 3).
   **Verify**: extend `tests/test_reporting.py` (new file): seed an invoice +
   credit via the API and assert dashboard/sales/tax numbers net correctly;
   assert the aging report ignores credit rows' positive totals.
6. `make verify` → exit 0. Commit: `fix: FX/base correctness on updates, voids, and reports (audit 002)`.

## Test plan

- `tests/test_fx_characterization.py` (new) — pins payload behavior.
- `tests/test_reporting.py` (new) — dashboard, sales-by-customer with credit,
  tax summary.
- Extensions in `tests/test_invoicing.py` for PATCH semantics.

## Maintenance

After this lands, `total_base` is authoritative for every report; any new
report must aggregate `*_base` columns. The characterization test is the
guard for future rounding changes.

## STOP conditions

- If making `net_base + tax_base == total_base` requires changing the
  journal shape (e.g. a separate rounding account), STOP and report the
  numbers — that is a design decision.
- If any existing test other than the characterization ones changes meaning,
  STOP and report.
