-- ISOLATED STAGING ONLY. Run verify_inventory_remote.py --failure-probe, then:
-- DROP TRIGGER cf_inventory_rollback_probe;
-- The fault occurs after stock, progress and journal header writes are planned.
CREATE TRIGGER cf_inventory_rollback_probe AFTER INSERT ON accounting_journal_lines
WHEN EXISTS (
  SELECT 1 FROM accounting_journal_entries e JOIN inventory_receipts r ON r.id=e.source_id
  WHERE e.id=NEW.entry_id AND r.notes='Cloudflare inventory atomic rollback probe'
)
BEGIN INSERT INTO cf_assertions(command_id,valid) VALUES ('inventory-failure-proof',0); END;
