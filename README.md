# ERP — Self-Hosted SME ERP

A general-purpose ERP for small and medium-sized enterprises, built as a
**modular monolith**: one application, one database, clear internal modules.

```
                  ERP APPLICATION  (FastAPI api/ + React web/)
                                │
     ┌──────────┬───────────────┼───────────────┬─────────────┐
     CRM        Sales        Purchasing      Inventory     Invoicing ── Accounting
     └──────────┴───────────────┴───────────────┴─────────────┘
                                │
              PostgreSQL 17 (pgvector) · Redis 7 · SeaweedFS (S3)
```

Modules build on a shared **ERP Core**: organizations, branches, users, roles &
permissions, auth, settings, audit logs, notifications, document numbering.

## Quickstart

Requires: Docker, and (for host-side tooling) [uv](https://docs.astral.sh/uv/) + pnpm.

```bash
cp .env.example .env
make up        # builds & starts postgres, redis, seaweedfs, api, web
make migrate   # apply migrations (runs on the host via uv)
make seed      # admin user, roles, permissions
```

**No bind mounts.** Docker Desktop on the dev machine cannot share the SSD
volume this repo lives on, so compose never mounts host paths: source code is
baked into the dev images (`make up` rebuilds after changes) and data lives in
named volumes. For live reload, run only the infrastructure in Docker and the
apps on the host:

```bash
make infra     # postgres, redis, seaweedfs
make dev-api   # uvicorn --reload on :8000 (host)
make dev-web   # Vite HMR on :5173 (host)
make dev-worker  # optional: arq worker (set JOBS_MODE=redis in .env)
```

Host-side targets read `.env` and point DB/Redis/S3 at the published
localhost ports automatically.

| Service | URL |
|---|---|
| Web (Vite dev server) | http://localhost:5173 |
| API | http://localhost:8000 |
| API health | http://localhost:8000/healthz |
| Postgres | localhost:55432 (host) — `postgres:5432` inside the docker network |
| Redis | localhost:56379 (host) — `redis:6379` inside the docker network |
| SeaweedFS S3 | localhost:18333 (host) — `seaweedfs:8333` inside the network; master console: http://localhost:19333 |

Host ports are chosen to avoid clashes with other local Docker projects.

## Make targets

| Target | What it does |
|---|---|
| `make up` | Build & start everything (source baked into images) |
| `make infra` | Start only postgres, redis, seaweedfs |
| `make dev-api` / `make dev-web` / `make dev-worker` | Run the apps on the host with live reload |
| `make down` | Stop everything |
| `make logs` | Follow logs |
| `make ps` | Service status |
| `make reset` | **Destructive:** stop and wipe all volumes |
| `make migrate` | Apply database migrations (host; needs infra) |
| `make seed` | Seed bootstrap data (admin user, roles, permissions) |
| `make shell-api` / `make shell-web` | Shell into a container |
| `make verify` | Quality gates on the host: lint, format, tests, build (needs infra) |

## Repository layout

```
api/       FastAPI backend (uv project) — app/core, app/shared, app/modules/<module>
web/       Vite + React + TypeScript SPA (pnpm) — feature-sliced
docker/    Dockerfiles (dev + prod), nginx config, deploy smoke script
plans/     phased implementation plans (plans/phase1 first)
```

## Configuration

Everything is configured via environment variables (see `.env.example`).
In development the database, Redis, and S3 run as local containers; for
deployment their host/credential variables can simply be pointed at external
services — no code changes.

## CI & backups

- `.github/workflows/ci.yml` — ruff + pytest (with pgvector/redis services) and
  web typecheck/lint/vitest/build on every push.
- Backups: prod worker cron `backup_database` (02:30, GFS retention) and
  `prune_audit_logs` (03:15, `AUDIT_RETENTION_DAYS`); dev: `make backup`.

## Production deployment (single demo server + Cloudflare Tunnel)

Everything runs in Docker; **the only published port is the web container on
`127.0.0.1:$PROXY_PORT`** (loopback — never `0.0.0.0`, Docker bypasses host
firewalls). postgres/redis/seaweedfs/api/worker stay on the project's internal
network. A host `cloudflared` tunnel routes a public hostname to that port.

```bash
# one-time on the server
git clone https://github.com/aliahadmd/sme-erp.git /opt/apps/sme-erp
/opt/apps/sme-erp/deploy/deploy.sh          # generates .env, builds, starts, seeds demo
( crontab -l; echo '* * * * * /opt/apps/sme-erp/deploy/deploy.sh >> /var/log/sme-erp-deploy.log 2>&1' ) | crontab -
# Cloudflare dashboard → tunnel → public hostname → http://localhost:30001
```

**Continuous deployment is pull-based:** cron runs `deploy/deploy.sh` every
minute; it redeploys only when `origin/main` moved (`git reset --hard` +
`docker compose up -d --build`). No SSH keys in GitHub, no inbound access.
Server secrets live in the generated, git-ignored `.env` (`chmod 600`);
deploys never rewrite it. Force a redeploy with `deploy/deploy.sh --force`.

`docker-compose.prod.yml` runs: api (migrations + idempotent seed + uvicorn,
non-root), worker (arq), web (nginx serving the built SPA + proxying `/api`),
postgres, redis, seaweedfs. One origin — no CORS setup needed. Local smoke test
of the same stack: `PROXY_PORT=8080 ADMIN_EMAIL=... ADMIN_PASSWORD=... make deploy-check`.

## Implementation plans

See [`plans/phase1/index.md`](plans/phase1/index.md) for the phase-1 plan set,
execution order, and status; [`plans/phase2-draft.md`](plans/phase2-draft.md)
for the deferred backlog.

## Test users (dev only)

```bash
make seed-users   # one account per role + a self-service employee + a disabled user
```

Accounts, roles and the shared dev password are listed in
[`api/app/core/seed_test_users.py`](api/app/core/seed_test_users.py); the
superuser is `ADMIN_EMAIL` / `ADMIN_PASSWORD` from `.env`. The script refuses
to run unless `ENVIRONMENT=dev`.

## Demo data

```bash
make seed-demo   # contacts, products, full buy/sell cycles with payments
```

## Adding a module (the architecture contract)

1. `api/app/modules/<name>/` with `models.py` (own PostgreSQL schema),
   `schemas.py`, `service.py`, `router.py`; import models in `alembic/env.py`.
2. Cross-module reads go through the owning module's service functions;
   reactions (audit, notifications, postings) subscribe to events
   (see `app/modules/accounting/postings.py` for the pattern).
3. Frontend: `web/src/features/<name>/` with `api.ts` + pages; add the nav
   entry in `src/app/nav-items.ts` and routes in `src/app/router.tsx`.
4. Add tests under `api/tests/`, regenerate web types with `pnpm gen:api`,
   and run `make verify`.

