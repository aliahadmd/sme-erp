.DEFAULT_GOAL := help
COMPOSE := docker compose

.PHONY: help up down logs ps restart reset migrate seed shell-api shell-web verify

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Build and start all services
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

migrate: ## Apply database migrations (in api container)
	$(COMPOSE) exec api alembic upgrade head

seed: ## Seed bootstrap data (admin user, roles, permissions)
	$(COMPOSE) exec api python -m app.core.seed

seed-demo: ## Seed a coherent demo dataset (requires services up)
	$(COMPOSE) exec api python -m app.core.seed_demo

shell-api: ## Shell into the api container
	$(COMPOSE) exec api bash

shell-web: ## Shell into the web container
	$(COMPOSE) exec web sh

verify: ## Run all quality gates (api: lint/format/tests · web: typecheck/lint/tests/build)
	$(COMPOSE) exec -T api uv run ruff check app tests
	$(COMPOSE) exec -T api uv run ruff format --check app tests
	$(COMPOSE) exec -T api uv run pytest -q
	$(COMPOSE) exec -T web pnpm typecheck
	$(COMPOSE) exec -T web pnpm lint
	$(COMPOSE) exec -T web pnpm test
	$(COMPOSE) exec -T web pnpm build
