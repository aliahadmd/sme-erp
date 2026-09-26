# Plan 5 — Fulfillment UX: Partial Delivery & Billing Made Visible

- **Depends on:** nothing (builds directly on phase-1 audit-fix quantity tracking)
- **Goal:** the backend tracks `qty_delivered/received/invoiced` per order line;
  this plan surfaces it so operators can see outstanding quantities, deliver or
  bill in parts deliberately, and understand why an order is (or isn't) closed.

## 1. Backend

- [x] Order line responses already expose progress fields — extend them with
      `qty_remaining` (server-computed) for sales (`qty - qty_delivered`,
      `qty - qty_invoiced`) and purchasing mirrors.
- [x] List endpoints accept `fulfillment=complete|outstanding` filters.
- [x] Backorder helper: when a delivery is blocked by stock, the UI can save it
      as `draft` — this plan adds `GET /orders/{id}/outstanding` returning the
      remaining lines (used by the inventory "Deliver" flow).
- [x] Tests: remaining math with multi-line same-product orders; filters;
      outstanding endpoint.

## 2. Frontend

- [x] Order editor lines: progress column per line ("3 of 5 delivered") with a
      progress bar; header badge "Partially delivered / Partially invoiced".
- [x] Orders list: fulfillment column (progress fraction), filter chips
      (All open / Outstanding / Complete).
- [x] Inventory Deliver/Receive dialogs: show ordered vs remaining vs
      already-processed; warn (non-blocking) when posting more than remaining.
- [x] Auto-close transparency: order status timeline in the editor header
      (draft → confirmed → delivered → invoiced → closed with timestamps where
      available).
- [x] Permission-filtered nav carries over; no new permissions (reuses
      `sales.order.*` / `purchasing.order.*` / `inventory.*`).

## Acceptance

- [x] A 5-unit order delivered 2 then 3 shows partial → complete states and
      auto-closes once invoiced 5 (existing backend behavior, now visible).
- [x] Over-delivery attempts surface the remaining quantity in the UI error.
- [x] Filters and outstanding endpoint covered by tests; `make verify` green.
