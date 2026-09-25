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
```

| Service | URL |
|---|---|
| Web (Vite dev server) | http://localhost:5173 |
| API | http://localhost:8000 |
| API health | http://localhost:8000/healthz |
| Postgres | localhost:5432 |
| Redis | localhost:6379 |
| SeaweedFS S3 | localhost:8333 (master console: http://localhost:9333) |

## Make targets

| Target | What it does |
|---|---|
| `make up` | Build & start everything |
| `make down` | Stop everything |
| `make logs` | Follow logs |
| `make ps` | Service status |
| `make reset` | **Destructive:** stop and wipe all volumes |
| `make migrate` | Apply database migrations |
| `make seed` | Seed bootstrap data (admin user, roles, permissions) |
| `make shell-api` / `make shell-web` | Shell into a container |
| `make verify` | Quality gates: lint, format, tests |

## Repository layout

```
api/       FastAPI backend (uv project) — app/core, app/shared, app/modules/<module>
web/       Vite + React + TypeScript SPA (pnpm) — feature-sliced
docker/    dev Dockerfiles + service init configs
plans/     phased implementation plans (plans/phase1 first)
```

## Configuration

Everything is configured via environment variables (see `.env.example`).
In development the database, Redis, and S3 run as local containers; for
deployment their host/credential variables can simply be pointed at external
services — no code changes.

## Implementation plans

See [`plans/phase1/index.md`](plans/phase1/index.md) for the phase-1 plan set,
execution order, and status.
