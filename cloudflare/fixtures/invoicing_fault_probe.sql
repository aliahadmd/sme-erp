-- ISOLATED STAGING ONLY. Run verify_invoicing_remote.py --failure-probe, then:
-- DROP TRIGGER cf_invoicing_rollback_probe;
-- Fail after invoice/payment, settlement, source progress and journal writes.
CREATE TRIGGER cf_invoicing_rollback_probe AFTER INSERT ON accounting_journal_lines
WHEN EXISTS (
 SELECT 1 FROM accounting_journal_entries e
 LEFT JOIN invoicing_invoices i ON i.id=e.source_id
 LEFT JOIN invoicing_payments p ON p.id=e.source_id
 WHERE e.id=NEW.entry_id AND (
  i.notes='Cloudflare invoicing atomic rollback probe' OR
  p.reference='Cloudflare invoicing atomic rollback probe'
 )
)
BEGIN INSERT INTO cf_assertions(command_id,valid) VALUES ('invoicing-failure-proof',0); END;
