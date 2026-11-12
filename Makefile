.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE := docker compose
# Agent CLIs can export their own PYTHONPATH; keep uv on this project's venv.
UV_RUN := env -u PYTHONPATH uv run

.PHONY: help
help:  ## Show available targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---- Setup ----

.PHONY: env
env:  ## Create .env from .env.example if missing
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env from .env.example"; else echo ".env already exists, skipping"; fi

.PHONY: sync
sync:  ## Install project and development dependencies
	uv sync

.PHONY: setup
setup: env sync  ## Prepare local environment and Python dependencies

.PHONY: lock
lock:  ## Update uv.lock
	uv lock

# ---- Compose lifecycle ----

.PHONY: up
up: env  ## Start all containers and wait for healthchecks
	$(COMPOSE) up --wait -d
	@echo ""
	@set -a; source .env; set +a; \
		echo "API:      http://localhost:$${API_PORT:-18100}"; \
		echo "RabbitMQ: http://localhost:$${RABBITMQ_MANAGEMENT_PORT:-15673} (guest / guest)"; \
		echo "Webhook:  make webhook"; \
		echo "Logs:     make logs"

.PHONY: down
down:  ## Stop all containers, keep volumes
	$(COMPOSE) down

.PHONY: restart
restart: down up  ## Restart all containers

.PHONY: build
build:  ## Build API and consumer images
	$(COMPOSE) build

.PHONY: rebuild
rebuild: down build up  ## Stop, rebuild images and start the stack

# ---- Observability and shells ----

.PHONY: ps
ps:  ## Show service status
	$(COMPOSE) ps

.PHONY: logs
logs:  ## Tail logs; use SERVICE=api or SERVICE=consumer to narrow
	$(COMPOSE) logs -f --tail=100 $(SERVICE)

.PHONY: queues
queues:  ## Show RabbitMQ queue depths, including the DLQ
	$(COMPOSE) exec rabbitmq rabbitmqctl list_queues name messages

.PHONY: psql
psql:  ## Open psql in the PostgreSQL container
	$(COMPOSE) exec postgres psql -U payments -d payments

.PHONY: shell-api
shell-api:  ## Open a shell in the API container
	$(COMPOSE) exec api /bin/sh

# ---- Database ----

.PHONY: migrate
migrate:  ## Apply migrations in the running API container
	$(COMPOSE) exec api alembic upgrade head

# ---- Local helpers ----

.PHONY: webhook
webhook: env  ## Run the local webhook receiver
	@set -a; source .env; set +a; python3 examples/webhook_server.py "$${WEBHOOK_PORT:-9001}"

# ---- Quality gates ----

.PHONY: lint
lint:  ## Check formatting and lint with Ruff
	$(UV_RUN) ruff format --check app tests alembic examples
	$(UV_RUN) ruff check app tests alembic examples

.PHONY: format
format:  ## Auto-format and autofix with Ruff
	$(UV_RUN) ruff format app tests alembic examples
	$(UV_RUN) ruff check --fix app tests alembic examples

.PHONY: test
test:  ## Run unit tests; use ARGS="-k idempotent" to narrow
	$(UV_RUN) pytest -q $(ARGS)

.PHONY: ready
ready: lint test  ## Full local gate: formatting, lint and tests
	@echo ""
	@echo "ready: all checks passed."

# ---- Cleanup ----

.PHONY: clean
clean:  ## Remove containers, networks and local database/broker volumes
	$(COMPOSE) down -v --remove-orphans
