.DEFAULT_GOAL := help
SHELL := /bin/bash

# Agent CLIs can export their own PYTHONPATH; keep uv on this project's venv.
UV_RUN := env -u PYTHONPATH uv run

.PHONY: help
help:  ## Show available targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

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

.PHONY: lint
lint:  ## Check formatting and lint with Ruff
	$(UV_RUN) ruff format --check app
	$(UV_RUN) ruff check app

.PHONY: format
format:  ## Auto-format and autofix with Ruff
	$(UV_RUN) ruff format app
	$(UV_RUN) ruff check --fix app
