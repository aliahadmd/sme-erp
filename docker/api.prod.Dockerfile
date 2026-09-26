# Production API image — multi-stage, non-root, no reload.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS deps
ENV UV_LINK_MODE=copy
WORKDIR /app
COPY api/pyproject.toml api/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS runtime
# pg_dump (PGDG, server-matching v17) for the backup job.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
 && curl https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update && apt-get install -y --no-install-recommends postgresql-client-17 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -r app && useradd -r -g app --home-dir /app appuser

WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY api/alembic.ini api/pyproject.toml api/uv.lock ./
COPY api/app ./app
COPY api/alembic ./alembic
# Backup job writes dumps here — must exist and be writable by appuser.
RUN install -d -o appuser -g app /app/dumps

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"

# Migrations run before the server starts (single-instance rollout in phase 2).
ENTRYPOINT ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]
