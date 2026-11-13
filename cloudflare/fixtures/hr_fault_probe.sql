-- ISOLATED STAGING ONLY. Run verify_hr_remote.py --failure-probe, then:
-- DROP TRIGGER cf_hr_rollback_probe;
CREATE TRIGGER cf_hr_rollback_probe AFTER INSERT ON core_notifications
WHEN EXISTS (
 SELECT 1 FROM hr_leave_requests r WHERE r.id=json_extract(NEW.payload,'$.request_id')
 AND r.reason='Cloudflare HR atomic rollback probe'
)
BEGIN INSERT INTO cf_assertions(command_id,valid) VALUES ('hr-failure-proof',0); END;
