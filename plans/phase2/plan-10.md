# Plan 10 — HR Module: Employees, Departments, Leave

- **Depends on:** plan-1 (worker for reminders); first consumer of the
      lightweight approval pattern
- **Goal:** the HR box from the architecture diagram, scoped to what an SME
      needs on day one — a directory and leave management with approvals.
      Payroll stays out (phase 3+).

## 1. Data model (schema `hr`)

- [x] `departments` — name, manager (employee FK), parent optional.
- [x] `employees` — org, employee number, full name, work email, user link
      (nullable — an employee may have no login), department, position,
      hired_at, terminated_at (soft exit), status.
- [x] `leave_types` — name (vacation/sick/unpaid), days_per_year,
      accrues (bool) — seeded basics.
- [x] `leave_requests` — employee, type, date_from, date_to, days (computed),
      reason, status `pending → approved | rejected | cancelled`, approver,
      decided_at. Approvals audited; notifications sent to approver and
      requester (reuses the phase-1 notification + plan-8 email plumbing).

## 2. Backend & rules

- [x] Permissions `hr.employee.*`, `hr.department.*`, `hr.leave.read |
      hr.leave.request | hr.leave.approve`; new `hr` system role seeded
      (employee: request+read own; manager: approve department).
- [x] Employees may read/update only their own leave requests (service-level
      ownership checks + `hr.leave.approve` for decisions).
- [x] Overlap guard: pending/approved requests may not overlap per employee.
      Balance check: requested days vs remaining allowance (warning at submit,
      blocking when exceeded and type is `accrues`).
- [x] Approvals set `decided_at/by`, notify, audit; approved leave appears on
      the employee's timeline. Optionally an approval threshold rule (e.g.
      > 10 days needs a second approver) — the seed of the future workflow
      engine, kept explicit and simple.

## 3. Frontend

- [x] New `HR` section (sidebar, permission `hr.employee.read`): Employees
      (list + form + department assignment), Departments (tree + manager),
      Leave (my requests + approvals inbox for managers, approve/reject with
      reason), Leave types admin under Settings → HR.
- [x] Leave balance widget on "my requests"; pending-approvals count in the
      notifications bell.

## Acceptance

- [x] Create departments/employees; submit overlapping leave → rejected;
      approve → notification + audit row; balance decrements correctly.
- [x] A non-HR user cannot see other employees' requests (403/UI guard).
- [x] Tests: CRUD, overlap guard, balance guard, approval flow, RBAC;
      `make verify` green.
