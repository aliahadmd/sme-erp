# Plan 1 — Repository Skeleton & Docker Dev Environment

- **Depends on:** nothing (first plan)
- **Goal:** an empty repo becomes an environment where `make up` starts the entire ERP
  stack — postgres, redis, seaweedfs, api, web — with healthchecks, env-driven config,
  and a Makefile as the single entry point.

## 1. Tasks

### Repo skeleton
- [ ] `git init`, root `.gitignore` (node, python, .env, docker volumes), `README.md`,
      `.env.example`, `Makefile`, `docker/`, `api/`, `web/` (placeholder dirs; real
      scaffolds land in plans 2–3).
- [ ] `README.md`: 3-command quickstart, architecture diagram (copy from
      `plans/phase1/index.md`), env var table, Makefile target list.

### docker-compose.yml (dev)
Target shape (tune details at implementation):

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg17
    environment: { POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB }   # from .env
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck: pg_isready
  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    ports: ["6379:6379"]
    volumes: [redisdata:/data]
    healthcheck: redis-cli ping
  seaweedfs:
    image: chrislusf/seaweedfs
    command: server -dir=/data -ip=seaweedfs -master.volumeSizeLimitMB=1024 -volume.max=0 -filer -s3 -s3.port=8333
    ports: ["8333:8333", "9333:9333"]
    volumes: [s3data:/data]
    healthcheck: curl master /cluster/status
  seaweed-init:            # one-shot: create bucket erp-dev via awscli against S3 API
    ...depends_on: [seaweedfs]
  api:
    build: { context: ., dockerfile: docker/api.Dockerfile }
    command: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000   # reload via bind mount
    env_file: .env
    volumes: [./api:/app]
    ports: ["8000:8000"]
    depends_on: { postgres: { condition: service_healthy }, redis: …, seaweedfs: … }
  web:
    build: { context: ., dockerfile: docker/web.Dockerfile }
    command: pnpm dev --host 0.0.0.0            # Vite dev server + HMR, CHOKIDAR_USEPOLLING
    volumes: [./web:/app, web_node_modules:/app/node_modules]
    ports: ["5173:5173"]
volumes: { pgdata: {}, redisdata: {}, s3data: {}, web_node_modules: {} }
```

- [ ] `docker/api.Dockerfile`: `python:3.12-slim` + uv (copied from ghcr image or installer),
      installs `api/` deps. Placeholder app for now (returns `{"status":"ok"}`) so the
      stack is up before plan-2.
- [ ] `docker/web.Dockerfile`: `node:22-alpine` + corepack pnpm. Placeholder page for now.
- [ ] `.env.example` — single source of all config:
      `POSTGRES_USER/PASSWORD/DB`, `DATABASE_URL`, `REDIS_URL`,
      `S3_ENDPOINT/S3_ACCESS_KEY/S3_SECRET_KEY/S3_BUCKET`,
      `JWT_SECRET`, `OPENROUTER_API_KEY=` (may stay empty), `OPENROUTER_MODEL`,
      `ADMIN_EMAIL/ADMIN_PASSWORD` (used by `make seed` in plan-4), `VITE_API_URL=http://localhost:8000`.
- [ ] Apps read **only** env vars — no hostnames like `postgres:` hardcoded outside compose
      (that is what keeps the Dokploy external-credentials swap trivial later).

### Makefile
- [ ] `up` (compose up -d --build), `down`, `logs` (follow), `ps`, `restart`,
      `reset` (**down -v** — destructive, documented as such),
      `migrate` (runs in api container; no-op until plan-2), `seed` (plan-4),
      `shell-api`, `shell-web`, `verify` (lint/typecheck/test; fills in as plans land).

## 2. Acceptance

- [ ] Fresh clone: `cp .env.example .env && make up` → all services healthy;
      `make ps` shows healthy states.
- [ ] Postgres accepts `CREATE EXTENSION vector;` (proves pgvector works).
- [ ] `redis-cli ping` → PONG; a test object PUT/GET against SeaweedFS S3 API on :8333
      into bucket `erp-dev` succeeds.
- [ ] `http://localhost:8000/healthz` → ok (placeholder); `http://localhost:5173` → placeholder page.
- [ ] `make down && make up` is idempotent; `make reset` wipes volumes cleanly.
