# Plan 3 — CI Pipeline, Backups, Audit Retention

- **Depends on:** plan-1 (worker), plan-2 (prod images)
- **Goal:** every push is verified by CI, images are published from main, and
  database objects survive disk loss.

## 1. Tasks

### CI (GitHub Actions)
- [x] `.github/workflows/ci.yml`: on PR/push — api job (uv sync, ruff, pytest;
      services: postgres:pg17 + redis:7 as GH services, env-driven URLs),
      web job (pnpm install, typecheck, lint, vitest, build).
- [x] `.github/workflows/images.yml`: on main — build/push `ghcr.io` images for
      api + web using the plan-2 Dockerfiles (buildx, cache from previous run).
- [x] Branch protection note in README: CI must pass before merge.

### Backups
- [x] `docker/backup/backup.sh`: `pg_dump` (custom format) → timestamped file →
      upload to the configured S3 bucket (awscli container or boto3 script),
      retention pruning (keep 14 daily, 8 weekly, 6 monthly).
- [x] Scheduled by the arq worker cron (reuse plan-1) OR host cron — implement
      as an arq job `maintenance.backup_database` calling the same script logic,
      so backups exist wherever the app runs.
- [x] `make restore DB= dumps/xxx.dump` target + documented rehearsal procedure
      (restore into a scratch database, run migrations, `SELECT` spot checks).
- [x] SeaweedFS volume directory included in the retention plan (documented
      volume-level copy; file-store backups to a second bucket).

### Audit-log retention
- [x] arq cron `maintenance.purge_audit_logs`: delete rows older than
      `AUDIT_RETENTION_DAYS` (default 365, env-tunable); count logged.
- [x] Tests: backup script dry-run, retention purge (old rows deleted, new kept).

## 2. Acceptance

- [x] CI green on a real push (both jobs); images published on main.
- [x] A backup file exists in S3 with correct naming; the rehearsal restore
      into a scratch DB passes the spot checks.
- [x] Audit rows older than the retention window are purged by the cron; newer
      rows untouched; `make verify` green.
