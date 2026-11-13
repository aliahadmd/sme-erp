CREATE INDEX hr_leave_employee_status_dates
 ON hr_leave_requests(org_id,employee_id,status,date_from,date_to);
CREATE INDEX hr_leave_employee_type_status
 ON hr_leave_requests(org_id,employee_id,type_id,status);
CREATE TRIGGER hr_leave_valid_insert BEFORE INSERT ON hr_leave_requests
WHEN NEW.days<=0 OR NEW.date_to<NEW.date_from OR NEW.status!='pending'
BEGIN SELECT RAISE(ABORT,'Invalid leave request'); END;
CREATE TRIGGER hr_leave_valid_update BEFORE UPDATE ON hr_leave_requests
WHEN OLD.status!='pending' OR NEW.status NOT IN ('approved','rejected','cancelled')
 OR NEW.id IS NOT OLD.id OR NEW.org_id IS NOT OLD.org_id
 OR NEW.employee_id IS NOT OLD.employee_id OR NEW.type_id IS NOT OLD.type_id
 OR NEW.date_from IS NOT OLD.date_from OR NEW.date_to IS NOT OLD.date_to
 OR NEW.days IS NOT OLD.days OR NEW.reason IS NOT OLD.reason
 OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'Leave request content and decisions are immutable'); END;
CREATE TRIGGER hr_leave_no_delete BEFORE DELETE ON hr_leave_requests
BEGIN SELECT RAISE(ABORT,'Leave request history is immutable'); END;
