-- ISOLATED STAGING ONLY. Not an application migration or part of the bundle.
-- Apply through the D1 query API, run verify_accounting_remote.py --failure-probe,
-- then remove with: DROP TRIGGER cf_accounting_rollback_probe;
CREATE TRIGGER cf_accounting_rollback_probe
AFTER INSERT ON accounting_journal_lines
WHEN EXISTS (
  SELECT 1 FROM accounting_journal_entries e
  WHERE e.id=NEW.entry_id AND e.memo='Cloudflare accounting atomic rollback probe'
)
BEGIN
  INSERT INTO cf_assertions(command_id,valid) VALUES ('remote-failure-proof',0);
END;
