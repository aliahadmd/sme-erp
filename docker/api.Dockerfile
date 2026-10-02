# Dev image: uv-managed Python 3.12 with dependencies AND source baked in.
# No bind mounts (Docker Desktop cannot share the SSD volume this repo lives
# on) — `make up` rebuilds the image; for live reload run `make dev-api` on
# the host instead.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first so this layer is cached between source edits.
COPY api/pyproject.toml api/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project

COPY api/ .

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
