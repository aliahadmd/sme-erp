# Plan 7 — Inventory: Stock Moves, Receipts/Deliveries, Costing

- **Depends on:** plan-6
- **Goal:** truth about stock. Every quantity change is an immutable move; current
  stock and moving-average cost are derived transactionally; deliveries from sales
  orders and receipts from purchase orders become one-click operations.

## 1. Data model (schema `inventory`)

- [x] `warehouses` — org, branch, code, name, is_default flag. One seeded.
- [x] `stock_moves` — org, product_id, warehouse_id, qty **signed** NUMERIC(18,4)
      (+in / −out), move_type (`receipt|delivery|adjustment|transfer_in|transfer_out`),
      ref_type + ref_id (source SO/PO/adjustment), unit_cost NUMERIC(18,6),
      cogs NUMERIC(18,2) (populated on outbound moves), moved_at, created_by,
      voided_by_move_id (reversal pattern). Append-only — corrections are reversing
      moves, never edits.
- [x] `stock` — current state per (product, warehouse): qty_on_hand, avg_cost
      NUMERIC(18,6), last_move_at. Updated **in the same transaction** as its moves.

## 2. Services & rules

- [x] `post_move()` single entry point: validates (no negative stock unless
      `inventory.allow_negative` setting — default false → 409), computes moving
      average on inbound (`new_avg = (q_on_hand·avg + q_in·cost) / (q_on_hand + q_in)`),
      stamps COGS = qty × avg_cost on outbound, upserts `stock`. All in one tx.
- [x] Listens to events from plan-6: `purchase_order.confirmed` → draft receipt
      available; `sales_order.confirmed` → draft delivery available. Generating the
      receipt/delivery document is explicit (one click / one API call), posting it is
      what moves stock.
- [x] `receipts` / `deliveries` modeled as thin headers over stock_moves
      (ref grouping) with their own status (`draft → posted`, `void` via reversal) and
      permissions (`inventory.receipt.post`, `inventory.delivery.post`, …).
- [x] Standalone adjustments: reason required (`stock_correction|damage|count|other`),
      permission `inventory.adjust`.
- [x] Warehouse transfers: paired transfer_out/transfer_in moves in one tx (cost
      carried, avg per warehouse).
- [x] Services cannot be received/delivered — guarded.

## 3. Frontend

- [x] **Inventory** section: Stock on Hand page (per warehouse filter, product search,
      qty + avg cost + value columns, low-stock highlight vs min_stock), product
      stock-history drawer (its moves with refs), Receipts / Deliveries / Adjustments
      list+create pages, warehouse admin under Settings.
- [x] From a confirmed PO: "Receive" action → receipt editor (default ordered qtys,
      editable) → post. Same for SO → "Deliver" (blocked inline if insufficient stock,
      with remaining-qty hint). Partial receipts/deliveries allowed; order closes when
      fully processed (event back to sales/purchasing → status `closed`).
- [x] Dashboard widget: low stock count (stretch).

## Acceptance

- [x] PO → receipt(posted): stock up, avg cost correct (multi-price test:
      buy 10 @ 10.00 then 10 @ 14.00 → avg 12.00).
- [x] SO → delivery(posted): stock down, COGS = qty × avg at move time; insufficient
      stock blocked with clear error when guard on.
- [x] Void a posted receipt → reversing move, stock/avg restored (avg restoration
      approximates by re-weighting — document the exact behavior chosen in code).
- [x] Transfer moves qty between warehouses with totals conserved.
- [x] Audit entries for every post/void; `make verify` green.
