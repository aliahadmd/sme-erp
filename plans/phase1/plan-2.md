# Plan 2 — Backend Foundation (FastAPI + uv)

- **Depends on:** plan-1
- **Goal:** the `api/` project is real: configurable FastAPI app, async SQLAlchemy +
  Alembic, structured errors, auth primitives, AI/OpenRouter plumbing, test + lint
  gates. No business modules yet.

## 1. Project setup

- [x] `uv init` inside `api/`; deps:
      `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`,
      `pydantic-settings`, `argon2-cffi`, `pyjwt`, `structlog`, `httpx`, `ai` (AI SDK, ai-python.dev)
      · dev: `pytest`, `pytest-asyncio`, `ruff`.
- [x] `ruff` config in `pyproject.toml` (line length 100, isort profile);
      format + lint rules agreed once, used everywhere.
- [x] Replace the plan-1 placeholder app with the real one.

## 2. App skeleton

```
api/app/
├── main.py            # create_app(): routers, exception handlers, lifespan (ping db/redis/s3)
├── core/
│   ├── config.py      # pydantic-settings BaseSettings — every env var, one Settings object
│   ├── db.py          # create_async_engine + async_sessionmaker + get_session dependency
│   ├── security.py    # argon2 hash/verify · JWT encode/decode (access 15m / refresh 7d)
│   ├── errors.py      # DomainError hierarchy + global handlers → uniform {code, detail, errors?}
│   ├── logging.py     # structlog JSON config, request-id middleware
│   ├── ai.py          # OpenRouter client factory via `ai` SDK; model from env; None if no key
│   └── deps.py        # reusable FastAPI dependencies (auth user added in plan-4)
├── shared/
│   ├── models.py      # Base: UUIDv7 pk (uuid7 lib or db default), TimestampMixin
│   ├── pagination.py  # limit/offset params + Page[T] response schema
│   ├── money.py       # NUMERIC type aliases + Decimal rounding helpers (banker's rounding)
│   └── events.py      # in-process async event bus: publish/subscribe, consumed post-commit
└── modules/           # empty; populated from plan-4
```

## 3. Database & migrations

- [x] Async engine from `DATABASE_URL`; `pool_pre_ping=True`.
- [x] Alembic async template; initial migration enables `CREATE EXTENSION IF NOT EXISTS vector`
      (future AI/embedding features get storage for free).
- [x] `make migrate` → `alembic upgrade head` inside the api container.

## 4. Plumbing

- [x] `GET /healthz` — checks postgres (`SELECT 1`), redis (`PING`), S3 (HEAD bucket);
      returns per-dependency status. `GET /readyz` alias.
- [x] `GET /api/meta` — app version, modules registered (used by web later).
- [x] Global exception handlers: `DomainError` → 4xx with stable `code`; `RequestValidationError`
      → 422 clean body; unexpected → 500 + structlog error (no stack trace leakage).
- [x] CORS middleware allowing `VITE_API_URL` origin.
- [x] `core/ai.py`: thin wrapper around `ai` SDK pointed at OpenRouter
      (`https://openrouter.ai/api/v1`, model from `OPENROUTER_MODEL`, e.g. a default like
      `openai/gpt-4o-mini` — final default decided at implementation); must degrade
      gracefully (`ai_enabled=False`) when `OPENROUTER_API_KEY` is empty. Smoke endpoint
      `POST /api/ai/echo` (test prompt) guarded to superuser — proves the pipe end-to-end.

## 5. Quality gates

- [x] pytest + pytest-asyncio: test database created/dropped per session from
      `DATABASE_URL` + `_test` suffix; `httpx.AsyncClient` against the app; first tests:
      healthz ok, migration head applied, 404 handler shape.
- [x] `make verify` runs: `ruff check` + `ruff format --check` + `pytest` (api side;
      web joined in plan-3).
- [x] README updated: how to run backend tests, add a migration.

## Acceptance

- [x] `make migrate` applies initial migration to a clean database (vector extension on).
- [x] `/healthz` reports postgres/redis/s3 all ok.
- [x] `make verify` green; `uv run pytest` green.
- [x] `POST /api/ai/echo` works with a key set and returns a clean "disabled" response without one.
