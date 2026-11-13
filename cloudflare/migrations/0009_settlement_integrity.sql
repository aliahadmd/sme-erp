-- Base amounts cleared by each immutable allocation / credit application.
CREATE TABLE cf_settlement_effects (
 id TEXT PRIMARY KEY NOT NULL,
 org_id TEXT NOT NULL,
 invoice_id TEXT NOT NULL REFERENCES invoicing_invoices(id),
 source_kind TEXT NOT NULL CHECK(source_kind IN ('payment','credit')),
 source_id TEXT NOT NULL,
 purpose TEXT NOT NULL CHECK(purpose IN ('allocation','refund','credit_applied','credit_used')),
 amount INTEGER NOT NULL CHECK(amount>0 AND amount<1000000000000000000),
 amount_base INTEGER NOT NULL CHECK(amount_base>=0 AND amount_base<1000000000000000000)
) STRICT;
CREATE INDEX cf_settlement_invoice ON cf_settlement_effects(org_id,invoice_id);
CREATE INDEX cf_settlement_source ON cf_settlement_effects(org_id,source_kind,source_id);
CREATE TRIGGER cf_settlement_no_update BEFORE UPDATE ON cf_settlement_effects
BEGIN SELECT RAISE(ABORT,'Settlement effects are immutable'); END;
CREATE TRIGGER cf_settlement_no_delete BEFORE DELETE ON cf_settlement_effects
BEGIN SELECT RAISE(ABORT,'Settlement effects are immutable'); END;
ALTER TABLE invoicing_invoices ADD COLUMN _number_fold TEXT NOT NULL DEFAULT '';
ALTER TABLE invoicing_invoices ADD COLUMN _party_fold TEXT NOT NULL DEFAULT '';
CREATE TRIGGER invoicing_balances_insert BEFORE INSERT ON invoicing_invoices
WHEN NEW.amount_paid<0 OR NEW.applied_credits<0 OR NEW.amount_paid>NEW.total OR NEW.applied_credits>NEW.total-NEW.amount_paid
BEGIN SELECT RAISE(ABORT,'Invalid invoice settlement balance'); END;
CREATE TRIGGER invoicing_balances_update BEFORE UPDATE ON invoicing_invoices
WHEN NEW.amount_paid<0 OR NEW.applied_credits<0 OR NEW.amount_paid>NEW.total OR NEW.applied_credits>NEW.total-NEW.amount_paid
BEGIN SELECT RAISE(ABORT,'Invalid invoice settlement balance'); END;
CREATE TRIGGER invoicing_invoice_no_delete BEFORE DELETE ON invoicing_invoices WHEN OLD.status!='draft'
BEGIN SELECT RAISE(ABORT,'Posted invoices are immutable'); END;
CREATE TRIGGER invoicing_allocations_no_update BEFORE UPDATE ON invoicing_payment_allocations
BEGIN SELECT RAISE(ABORT,'Payment allocations are immutable'); END;
CREATE TRIGGER invoicing_allocations_no_delete BEFORE DELETE ON invoicing_payment_allocations
BEGIN SELECT RAISE(ABORT,'Payment allocations are immutable'); END;
CREATE TRIGGER invoicing_allocations_insert BEFORE INSERT ON invoicing_payment_allocations
WHEN NEW.amount<=0 OR (SELECT status FROM invoicing_payments WHERE id=NEW.payment_id)!='recorded'
BEGIN SELECT RAISE(ABORT,'Invalid payment allocation'); END;

CREATE TRIGGER invoicing_invoices_immutable BEFORE UPDATE ON invoicing_invoices WHEN OLD.status!='draft' AND (OLD.status='void' OR NEW.status NOT IN ('posted','partial','paid','void') OR NEW."org_id" IS NOT OLD."org_id" OR NEW."invoice_type" IS NOT OLD."invoice_type" OR NEW."original_invoice_id" IS NOT OLD."original_invoice_id" OR NEW."number" IS NOT OLD."number" OR NEW."party_id" IS NOT OLD."party_id" OR NEW."party_name" IS NOT OLD."party_name" OR NEW."source_type" IS NOT OLD."source_type" OR NEW."source_id" IS NOT OLD."source_id" OR NEW."source_number" IS NOT OLD."source_number" OR NEW."invoice_date" IS NOT OLD."invoice_date" OR NEW."due_date" IS NOT OLD."due_date" OR NEW."currency" IS NOT OLD."currency" OR NEW."subtotal" IS NOT OLD."subtotal" OR NEW."discount_total" IS NOT OLD."discount_total" OR NEW."tax_total" IS NOT OLD."tax_total" OR NEW."total" IS NOT OLD."total" OR NEW."fx_rate" IS NOT OLD."fx_rate" OR NEW."total_base" IS NOT OLD."total_base" OR NEW."notes" IS NOT OLD."notes" OR NEW."posted_at" IS NOT OLD."posted_at" OR NEW."posted_by" IS NOT OLD."posted_by" OR NEW."created_at" IS NOT OLD."created_at" OR NEW."id" IS NOT OLD."id")
BEGIN SELECT RAISE(ABORT,'Posted financial content is immutable'); END;

CREATE TRIGGER invoicing_payments_immutable BEFORE UPDATE ON invoicing_payments WHEN OLD.status='void' OR NEW.status NOT IN ('recorded','void') OR NEW."org_id" IS NOT OLD."org_id" OR NEW."number" IS NOT OLD."number" OR NEW."direction" IS NOT OLD."direction" OR NEW."party_id" IS NOT OLD."party_id" OR NEW."party_name" IS NOT OLD."party_name" OR NEW."payment_date" IS NOT OLD."payment_date" OR NEW."currency" IS NOT OLD."currency" OR NEW."fx_rate" IS NOT OLD."fx_rate" OR NEW."amount" IS NOT OLD."amount" OR NEW."amount_base" IS NOT OLD."amount_base" OR NEW."method" IS NOT OLD."method" OR NEW."reference" IS NOT OLD."reference" OR NEW."notes" IS NOT OLD."notes" OR NEW."credit_note_id" IS NOT OLD."credit_note_id" OR NEW."created_by" IS NOT OLD."created_by" OR NEW."created_at" IS NOT OLD."created_at" OR NEW."id" IS NOT OLD."id"
BEGIN SELECT RAISE(ABORT,'Posted financial content is immutable'); END;

CREATE TRIGGER invoicing_payment_no_delete BEFORE DELETE ON invoicing_payments
BEGIN SELECT RAISE(ABORT,'Recorded payments are immutable'); END;

CREATE TRIGGER invoicing_lines_insert BEFORE INSERT ON invoicing_invoice_lines WHEN (SELECT status FROM invoicing_invoices WHERE id=NEW.invoice_id)!='draft'
BEGIN SELECT RAISE(ABORT,'Posted invoice lines are immutable'); END;

CREATE TRIGGER invoicing_lines_update BEFORE UPDATE ON invoicing_invoice_lines WHEN (SELECT status FROM invoicing_invoices WHERE id=OLD.invoice_id)!='draft' OR (SELECT status FROM invoicing_invoices WHERE id=NEW.invoice_id)!='draft'
BEGIN SELECT RAISE(ABORT,'Posted invoice lines are immutable'); END;

CREATE TRIGGER invoicing_lines_delete BEFORE DELETE ON invoicing_invoice_lines WHEN (SELECT status FROM invoicing_invoices WHERE id=OLD.invoice_id)!='draft'
BEGIN SELECT RAISE(ABORT,'Posted invoice lines are immutable'); END;
