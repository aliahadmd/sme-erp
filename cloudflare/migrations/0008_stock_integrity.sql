-- Exact stock-cost snapshots share the stock/document/journal D1 commit.
CREATE TABLE cf_stock_move_states (
  move_id TEXT PRIMARY KEY NOT NULL REFERENCES inventory_stock_moves(id),
  command_id TEXT NOT NULL REFERENCES cf_commands(id),
  before_json TEXT NOT NULL CHECK(json_valid(before_json)),
  after_json TEXT NOT NULL CHECK(json_valid(after_json))
) STRICT;
CREATE UNIQUE INDEX inventory_reversal_once
  ON inventory_stock_moves(org_id,reverses_move_id) WHERE reverses_move_id IS NOT NULL;
CREATE UNIQUE INDEX inventory_one_default_warehouse
  ON inventory_warehouses(org_id) WHERE is_default=1;
CREATE TRIGGER inventory_moves_no_update BEFORE UPDATE ON inventory_stock_moves
BEGIN SELECT RAISE(ABORT, 'Stock moves are immutable'); END;
CREATE TRIGGER inventory_moves_no_delete BEFORE DELETE ON inventory_stock_moves
BEGIN SELECT RAISE(ABORT, 'Stock moves are immutable'); END;
CREATE TRIGGER inventory_states_no_update BEFORE UPDATE ON cf_stock_move_states
BEGIN SELECT RAISE(ABORT, 'Stock cost snapshots are immutable'); END;
CREATE TRIGGER inventory_states_no_delete BEFORE DELETE ON cf_stock_move_states
BEGIN SELECT RAISE(ABORT, 'Stock cost snapshots are immutable'); END;

CREATE TRIGGER inventory_receipts_closed_update BEFORE UPDATE ON inventory_receipts
WHEN OLD.status!='draft' AND (OLD.status!='posted' OR NEW.status!='void' OR NEW."org_id" IS NOT OLD."org_id" OR NEW."number" IS NOT OLD."number" OR NEW."warehouse_id" IS NOT OLD."warehouse_id" OR NEW."source_type" IS NOT OLD."source_type" OR NEW."source_id" IS NOT OLD."source_id" OR NEW."source_number" IS NOT OLD."source_number" OR NEW."notes" IS NOT OLD."notes" OR NEW."posted_at" IS NOT OLD."posted_at" OR NEW."posted_by" IS NOT OLD."posted_by" OR NEW."created_at" IS NOT OLD."created_at" OR NEW."id" IS NOT OLD."id")
BEGIN SELECT RAISE(ABORT, 'Posted stock documents are immutable'); END;
CREATE TRIGGER inventory_receipts_closed_delete BEFORE DELETE ON inventory_receipts WHEN OLD.status!='draft'
BEGIN SELECT RAISE(ABORT, 'Posted stock documents are immutable'); END;
CREATE TRIGGER inventory_receipt_lines_closed_insert BEFORE INSERT ON inventory_receipt_lines
WHEN (SELECT status FROM inventory_receipts WHERE id=NEW.receipt_id)!='draft'
BEGIN SELECT RAISE(ABORT, 'Posted stock lines are immutable'); END;
CREATE TRIGGER inventory_receipt_lines_closed_update BEFORE UPDATE ON inventory_receipt_lines
WHEN (SELECT status FROM inventory_receipts WHERE id=OLD.receipt_id)!='draft' OR
     (SELECT status FROM inventory_receipts WHERE id=NEW.receipt_id)!='draft'
BEGIN SELECT RAISE(ABORT, 'Posted stock lines are immutable'); END;
CREATE TRIGGER inventory_receipt_lines_closed_delete BEFORE DELETE ON inventory_receipt_lines
WHEN (SELECT status FROM inventory_receipts WHERE id=OLD.receipt_id)!='draft'
BEGIN SELECT RAISE(ABORT, 'Posted stock lines are immutable'); END;

CREATE TRIGGER inventory_deliveries_closed_update BEFORE UPDATE ON inventory_deliveries
WHEN OLD.status!='draft' AND (OLD.status!='posted' OR NEW.status!='void' OR NEW."org_id" IS NOT OLD."org_id" OR NEW."number" IS NOT OLD."number" OR NEW."warehouse_id" IS NOT OLD."warehouse_id" OR NEW."source_type" IS NOT OLD."source_type" OR NEW."source_id" IS NOT OLD."source_id" OR NEW."source_number" IS NOT OLD."source_number" OR NEW."notes" IS NOT OLD."notes" OR NEW."posted_at" IS NOT OLD."posted_at" OR NEW."posted_by" IS NOT OLD."posted_by" OR NEW."created_at" IS NOT OLD."created_at" OR NEW."id" IS NOT OLD."id")
BEGIN SELECT RAISE(ABORT, 'Posted stock documents are immutable'); END;
CREATE TRIGGER inventory_deliveries_closed_delete BEFORE DELETE ON inventory_deliveries WHEN OLD.status!='draft'
BEGIN SELECT RAISE(ABORT, 'Posted stock documents are immutable'); END;
CREATE TRIGGER inventory_delivery_lines_closed_insert BEFORE INSERT ON inventory_delivery_lines
WHEN (SELECT status FROM inventory_deliveries WHERE id=NEW.delivery_id)!='draft'
BEGIN SELECT RAISE(ABORT, 'Posted stock lines are immutable'); END;
CREATE TRIGGER inventory_delivery_lines_closed_update BEFORE UPDATE ON inventory_delivery_lines
WHEN (SELECT status FROM inventory_deliveries WHERE id=OLD.delivery_id)!='draft' OR
     (SELECT status FROM inventory_deliveries WHERE id=NEW.delivery_id)!='draft'
BEGIN SELECT RAISE(ABORT, 'Posted stock lines are immutable'); END;
CREATE TRIGGER inventory_delivery_lines_closed_delete BEFORE DELETE ON inventory_delivery_lines
WHEN (SELECT status FROM inventory_deliveries WHERE id=OLD.delivery_id)!='draft'
BEGIN SELECT RAISE(ABORT, 'Posted stock lines are immutable'); END;

CREATE TRIGGER inventory_adjustments_closed_update BEFORE UPDATE ON inventory_adjustments
WHEN OLD.status!='draft' AND (OLD.status!='posted' OR NEW.status!='void' OR NEW."org_id" IS NOT OLD."org_id" OR NEW."number" IS NOT OLD."number" OR NEW."warehouse_id" IS NOT OLD."warehouse_id" OR NEW."reason" IS NOT OLD."reason" OR NEW."notes" IS NOT OLD."notes" OR NEW."posted_at" IS NOT OLD."posted_at" OR NEW."posted_by" IS NOT OLD."posted_by" OR NEW."created_at" IS NOT OLD."created_at" OR NEW."id" IS NOT OLD."id")
BEGIN SELECT RAISE(ABORT, 'Posted stock documents are immutable'); END;
CREATE TRIGGER inventory_adjustments_closed_delete BEFORE DELETE ON inventory_adjustments WHEN OLD.status!='draft'
BEGIN SELECT RAISE(ABORT, 'Posted stock documents are immutable'); END;
CREATE TRIGGER inventory_adjustment_lines_closed_insert BEFORE INSERT ON inventory_adjustment_lines
WHEN (SELECT status FROM inventory_adjustments WHERE id=NEW.adjustment_id)!='draft'
BEGIN SELECT RAISE(ABORT, 'Posted stock lines are immutable'); END;
CREATE TRIGGER inventory_adjustment_lines_closed_update BEFORE UPDATE ON inventory_adjustment_lines
WHEN (SELECT status FROM inventory_adjustments WHERE id=OLD.adjustment_id)!='draft' OR
     (SELECT status FROM inventory_adjustments WHERE id=NEW.adjustment_id)!='draft'
BEGIN SELECT RAISE(ABORT, 'Posted stock lines are immutable'); END;
CREATE TRIGGER inventory_adjustment_lines_closed_delete BEFORE DELETE ON inventory_adjustment_lines
WHEN (SELECT status FROM inventory_adjustments WHERE id=OLD.adjustment_id)!='draft'
BEGIN SELECT RAISE(ABORT, 'Posted stock lines are immutable'); END;
