# Plan 4 — ERP Core: Organization, Users, RBAC, Auth, Settings, Audit

- **Depends on:** plans 2 + 3
- **Goal:** the shared kernel every module builds on — and the first real
  end-to-end feature: login.

## 1. Data model (PostgreSQL schema `core`)

- [ ] `organizations` — name, legal_name, tax_id, base_currency (ISO 4217, default from
      env), address fields, logo_key (S3, later). Phase 1: exactly one row.
- [ ] `branches` — org FK, code (unique), name, address, is_active. Seed one branch.
- [ ] `users` — email (citext-unique), hashed_password (argon2), full_name,
      is_active, is_superuser, default_branch_id, last_login_at. Soft-disable only
      (no delete — audit integrity).
- [ ] `roles` — code, name, is_system (system roles seeded: admin, accountant, sales,
      purchasing, warehouse, viewer), description.
- [ ] `permissions` — seeded catalog of strings, grouped `module.resource.action`
      (`core.user.create`, `crm.contact.read`, `sales.order.confirm`, …).
- [ ] `role_permissions` · `user_roles` (M2M; org-wide in phase 1, branch-scoping later).
- [ ] `settings` — org FK, key, value JSONB (typed at service layer: company info,
      invoice defaults, numbering prefixes, negative-stock guard…).
- [ ] `audit_logs` — org, user, action (`create|update|delete|confirm|post|void|login`),
      entity_type, entity_id, before JSONB, after JSONB, ip, created_at. Append-only.
- [ ] `notifications` — user, type, title, body, link, payload JSONB, read_at.

## 2. Backend (module `core`)

- [ ] Auth endpoints: `POST /api/auth/login` (→ access token in body + refresh in
      httpOnly SameSite=Lax cookie), `POST /api/auth/refresh`, `POST /api/auth/logout`
      (cookie clear; token denylist via redis), `GET /api/auth/me` → user + roles +
      flattened permission list.
- [ ] RBAC: `require("code")` dependency → 401/403; `is_superuser` bypasses.
      Route-level on every business endpoint from here on.
- [ ] UserService + user endpoints (admin CRUD, activate/deactivate, reset password,
      assign roles) under `core.user.*`.
- [ ] Role/permission endpoints: list roles, create/update **custom** roles and their
      permission sets (`core.role.*`).
- [ ] SettingsService (get/set with JSON schema per key) + endpoint.
- [ ] AuditService: `audit(session, actor, action, entity, before, after)` called
      explicitly in every mutating service; `GET /api/audit-logs` (filter by
      user/entity/date) under `core.audit.read`.
- [ ] NotificationService: create/list/mark-read endpoints (in-app only).
- [ ] Bootstrap seed (idempotent, `make seed`): org, default branch, admin user from
      `ADMIN_EMAIL/ADMIN_PASSWORD`, system roles, permission catalog, default settings.
- [ ] Tests: login/refresh/logout roundtrip, RBAC allow/deny, audit row written on a
      mutating call, seed idempotency.

## 3. Frontend (feature `auth` + `settings` start)

- [ ] Wire login page to real endpoints; on success fetch `/me`, populate auth context;
      silent refresh on 401 via interceptor; logout button.
- [ ] User menu shows name/roles; route guard now real.
- [ ] Settings area pages: **Users** (list, create/edit form, activate/deactivate, role
      assignment), **Roles** (list system roles read-only, manage custom roles with
      permission checkboxes grouped by module), **Organization** (edit profile fields),
      **Branches** (CRUD), **Audit log** viewer (table + filters; read-only).

## Acceptance

- [ ] Seeded admin logs in; wrong password → clean 401; refresh keeps session across
      access-token expiry; logout kills the refresh cookie.
- [ ] A user without `core.user.create` gets 403 on user creation (verified by test).
- [ ] Every admin action above appears in the audit log with before/after.
- [ ] `make verify` green (api + web).
