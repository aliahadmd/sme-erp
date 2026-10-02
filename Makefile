.DEFAULT_GOAL := help
COMPOSE := docker compose

# Host-side commands (dev servers, migrations, tests) read .env, then point
# DB/Redis/S3 at the ports published by the infra containers. No bind mounts:
# Docker Desktop cannot share the SSD volume this repo lives on.
HOST_ENV := set -a && . ./.env && set +a && \
	export DATABASE_URL="postgresql+asyncpg://$${POSTGRES_USER}:$${POSTGRES_PASSWORD}@localhost:55432/$${POSTGRES_DB}" \
	       REDIS_URL="redis://localhost:56379/0" \
	       S3_ENDPOINT="http://localhost:18333"

.PHONY: help up down logs ps restart reset infra dev-api dev-worker dev-web migrate seed seed-users seed-demo \
	deploy-check backup jobs-logs shell-api shell-web verify

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Build and start everything (source baked into images — re-run after code changes)
	$(COMPOSE) up -d --build

down: ## Stop all services
	$(COMPOSE) down

logs: ## Follow all logs
	$(COMPOSE) logs -f --tail=100

ps: ## Show service status
	$(COMPOSE) ps

restart: ## Restart all services
	$(COMPOSE) restart

reset: ## Stop and wipe all volumes (DESTRUCTIVE — deletes data)
	$(COMPOSE) down -v

infra: ## Start only postgres, redis, seaweedfs (for host-side dev)
	$(COMPOSE) up -d postgres redis seaweedfs seaweed-init

dev-api: ## Run the API on the host with live reload (needs `make infra`)
	@$(HOST_ENV) && cd api && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-worker: ## Run the arq worker on the host (needs `make infra`; set JOBS_MODE=redis in .env)
	@$(HOST_ENV) && cd api && uv run arq app.jobs.WorkerSettings

dev-web: ## Run the Vite dev server on the host with HMR
	cd web && pnpm install && pnpm dev

migrate: ## Apply database migrations (from the host; needs infra running)
	@$(HOST_ENV) && cd api && uv run alembic upgrade head

seed: ## Seed bootstrap data (admin user, roles, permissions)
	@$(HOST_ENV) && cd api && uv run python -m app.core.seed

seed-users: ## Dev only: one test user per role (see api/app/core/seed_test_users.py)
	@$(HOST_ENV) && cd api && uv run python -m app.core.seed_test_users

seed-demo: ## Seed a coherent demo dataset
	@$(HOST_ENV) && cd api && uv run python -m app.core.seed_demo

deploy-check: ## Build the prod compose locally and run the smoke script
	ENVIRONMENT=prod docker compose -f docker-compose.prod.yml up -d --build
	./docker/deploy-smoke.sh
	docker compose -f docker-compose.prod.yml down

backup: ## Dump the dev database (custom format) into dumps/
	@mkdir -p dumps
	$(COMPOSE) exec -T postgres sh -c 'pg_dump --format=custom --dbname "$$POSTGRES_DB"' > dumps/erp-$$(date +%Y%m%dT%H%M%S).dump
	@ls -lh dumps | tail -3

jobs-logs: ## Follow the background worker logs
	$(COMPOSE) logs -f --tail=100 worker

shell-api: ## Shell into the api container
	$(COMPOSE) exec api bash

shell-web: ## Shell into the web container
	$(COMPOSE) exec web sh

verify: ## Run all quality gates on the host (api: lint/format/tests · web: typecheck/lint/tests/build); needs infra
	cd api && uv run ruff check app tests
	cd api && uv run ruff format --check app tests
	@$(HOST_ENV) && cd api && uv run pytest -q
	cd web && pnpm typecheck
	cd web && pnpm lint
	cd web && pnpm test
	cd web && pnpm build
