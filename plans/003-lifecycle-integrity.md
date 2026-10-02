# Plan 003: Document lifecycle integrity

> **Executor instructions**: Step-by-step; run every verification command;
> STOP and report on mismatches with the "Current state" excerpts.
>
> **Drift check (run first)**: `git diff --stat 8d1a5fd..HEAD -- api/app/modules/invoicing api/app/modules/hr api/app/modules/sales api/app/modules/documents api/app/shared`
> — compare excerpts; mismatch = STOP.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (void/conversion paths)
- **Depends on**: none (sequence after 002 if both touch invoicing)
- **Category**: bug
- **Planned at**: commit `8d1a5fd`, 2026-09-27

## Why this matters

Four lifecycle defects let the system enter states the business rules say are
impossible: voiding an order-derived invoice permanently strands the order's
quantities as "invoiced"; a payment can be allocated to another customer's
invoice; two concurrent conversions of one quotation produce two sales orders;
and a manager can approve their own leave. Each undermines the document state
machines that the rest of the ERP trusts.

## Current state

- `api/app/modules/invoicing/router.py::void_invoice` — sets status `void` and
  publishes `invoice.voided`; never touches the source order's `qty_invoiced`
  progress or status. `shared/order_progress.py::apply_progress` only moves
  forward.
- `api/app/modules/invoicing/service.py::record_payment` — validates
  allocation type/status/balance but not that `invoice.party_id == party_id`.
- `api/app/modules/invoicing/router.py::create_payment` — probes allocation
  invoice currencies with `session.get(Invoice, a.invoice_id)` without an
  org/None check before dereferencing `.currency`.
- `api/app/modules/sales/quote_router.py::convert_quotation` — checks and sets
  `quotation.status` without locking the row (`SELECT … FOR UPDATE`), so two
  concurrent converts can both pass `ensure_transition`.
- Conventions: transitions via `shared/order_engine.py::ensure_transition`;
  row locking pattern `select(...).with_for_update()` (see
  `invoicing/service.py` allocation lock); tests use the `_admin`/`_auth`
  helpers and `tests/test_invoicing.py` fixtures.

## Commands

| Purpose | Command (repo root) | Expected |
|---|---|---|
| Tests | `docker compose exec -T api uv run pytest -q` | all pass |
| Lint/format | `docker compose exec -T api uv run ruff check --fix app tests && docker compose exec -T api uv run ruff format app tests` | clean |
| Full gate | `make verify` | exit 0 |

## Scope

**In scope**: `api/app/modules/invoicing/router.py`,
`api/app/modules/invoicing/service.py`,
`api/app/modules/sales/quote_router.py`,
`api/app/modules/sales/service.py`,
`api/app/modules/purchasing/service.py`,
`api/tests/test_invoicing.py`, `api/tests/test_quotations.py`.

**Out of scope**: re-opening posted documents; changing `qty_delivered`
handling; adding new endpoints; modifying the quotations list UI.

## Steps

1. **Void releases invoiced quantities** — in `void_invoice`, when
   `invoice.source_id` is set: fetch the source order, and for each
   `invoice.lines[i]` reduce the matching order line's `qty_invoiced`
   (match by `product_id`, FIFO by position — reuse
   `shared/order_progress.py::apply_progress` with negated amounts). If the
   order's remaining `qty_invoiced` across lines drops to zero, downgrade the
   order status `invoiced → delivered` (sales) / `invoiced → received`
   (purchasing) using the same `mark_status` services with a new
   `ALLOWED_REMOTE` entry; otherwise leave the status.
   **Verify**: new test — order → invoice → void → order is back to
   `delivered` (or `confirmed` if it was never delivered) and
   `remaining_by_product` shows the quantity outstanding again.
2. **Allocation party + currency validation** — in
   `invoicing/service.py::record_payment`'s allocation loop (rows are already
   locked `with_for_update`): add
   `if invoice.party_id != party_id: raise ValidationError(...)`. In
   `create_payment`'s currency probe
   (`invoicing/router.py`, the `currencies = {...}` block), null-check each
   fetched invoice and raise `ValidationError("Unknown invoice in
   allocations")` instead of an AttributeError.
   **Verify**: test — payment for customer A allocated to customer B's
   invoice → 422 with a clear message (extend `test_invoicing.py`).
3. **Serialize quotation conversion** — in
   `sales/quote_router.py::convert_quotation`, immediately after
   `_quote_or_404` re-fetch the quotation with
   `select(Quotation).where(Quotation.id == quote_id).with_for_update()`
   before `ensure_transition`. Do the same in the orders `confirm` flow
   (`documents/router_factory.py`) — re-fetch the order `with_for_update`
   before the transition check so concurrent confirms/cancels serialize.
   **Verify**: concurrent-convert test (pattern:
   `tests/test_numbering.py::test_numbering_concurrent_sessions_never_collide`)
   → exactly one order created per quotation.
4. **Self-approval guard (HR)** — `hr/router.py::approve_leave_request`:
   resolve `request.employee.user_id`; when it equals `user.id`, raise
   `PermissionDeniedError("You cannot approve your own leave request")`.
   **Verify**: test — HR-linked user requests leave → own approve → 403.
5. `make verify` → exit 0. Commit: `fix: document lifecycle integrity (audit 003)`.

## Test plan

Three new tests as called out above, in `tests/test_invoicing.py` and
`tests/test_quotations.py`, following the file's existing helpers.

## Maintenance

`apply_progress` with negative amounts is the sanctioned reversal mechanism —
future "reopen" features must use it, not direct column writes.

## STOP conditions

- If status downgrade after void would orphan notifications or downstream
  documents (e.g. a delivery exists), STOP and report the document graph —
  the downgrade policy needs a human decision.
- If locking changes deadlock under the concurrency test, report the
  deadlock graph instead of removing locks.
