# Plan 2 — Production Build & Dokploy Deployment

- **Depends on:** nothing (parallel with plan-1)
- **Goal:** the exact repo deploys to a Dokploy VPS with HTTPS, non-root
  containers, static web served by a reverse proxy, and zero dev conveniences
  (no bind mounts, no reload, no root).

## 1. Tasks

### Production images
- [x] `docker/api.prod.Dockerfile`: multi-stage — deps stage (`uv sync --frozen
      --no-dev`), runtime stage on `python:3.12-slim`, non-root `appuser`,
      copied `app/` + `alembic/`, `collect`-style static nothing, `CMD uvicorn
      app.main:app --host 0.0.0.0 --port 8000 --workers 2` (no reload).
- [x] `docker/web.prod.Dockerfile`: multi-stage — build stage (`pnpm install
      --ignore-scripts && pnpm build`), runtime on `nginx:alpine` (non-root port
      8080), serving `dist/` and **proxying `/api` → api:8000** so production has
      a single origin; long-cache `/assets/`, no-cache `index.html`.
- [x] Healthchecks in both Dockerfiles (api python-urllib `/healthz`, web wget).

### Deployment compose
- [x] `docker-compose.prod.yml` (Dokploy "Compose" deployment type): api, worker
      (plan-1), web, postgres, redis, seaweedfs — images built via `build:`,
      volumes for pgdata/s3data, **no source mounts**.
- [x] Required-env contract: `ENVIRONMENT=prod`, `JWT_SECRET` (≥32),
      `POSTGRES_*`, `S3_*`, `ADMIN_EMAIL/PASSWORD`, `CORS_ORIGINS`,
      `VITE_API_URL` baked as a build arg into the web image.
      Config validators from the phase-1 audit fix make misconfigurations boot-fail.
- [x] `make deploy-check`: boots the prod compose locally on free ports and runs
      a smoke script (healthz → login → meta) so deployment problems surface
      before Dokploy.
- [x] Dokploy notes in `README.md`: project creation, domain + HTTPS (port 8080
      web), env paste, first-boot `make seed` equivalent (`docker compose ... exec
      api python -m app.core.seed`).

## 2. Acceptance

- [x] `make deploy-check` passes locally; the SPA and API share one origin and
      cookies work without CORS special-casing.
- [x] Images run as non-root (`docker exec ... id` ≠ root); containers healthy.
- [x] A deployment to a Dokploy VPS with HTTPS serves the app; login works;
      audit log records the first login (checked once during this plan).
