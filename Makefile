.DEFAULT_GOAL := help

.PHONY: help venv install sync lock format format-check lint typecheck test test-fast coverage security check doctor pipeline-demo parse-demo reconcile-demo live-collect bulk-collect-laptops collection-plan db-upgrade db-current embedding-status embedding-backfill playwright-install pre-commit dev-up dev-down dev-logs dev-db-shell docker-build docker-doctor compose-validate

COMPOSE_DEV := docker compose -f compose.dev.yml
LOCAL_IMAGE := certikart-pipeline:local

help: ## Show the available commands.
	@awk 'BEGIN {FS = ":.*## "; printf "Certikart pipeline commands:\n\n"} /^[a-zA-Z_-]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

venv: ## Create the local Python 3.11 virtual environment with uv.
	uv venv --python 3.11

install: ## Install all locked runtime and development dependencies.
	uv sync --frozen --all-groups

sync: ## Resolve and synchronize dependencies after pyproject changes.
	uv sync --all-groups

lock: ## Refresh the dependency lock file.
	uv lock

format: ## Format Python files and apply safe lint fixes.
	uv run ruff check --fix src tests migrations
	uv run ruff format src tests migrations

format-check: ## Verify formatting without modifying files.
	uv run ruff format --check src tests migrations

lint: ## Run the Ruff linter.
	uv run ruff check src tests migrations

typecheck: ## Run strict static type checking.
	uv run mypy src tests migrations

test: ## Run the complete test suite with coverage enforcement.
	uv run pytest

test-fast: ## Run tests without coverage for quick local feedback.
	uv run pytest --no-cov

coverage: ## Generate an HTML coverage report.
	uv run pytest --cov-report=term-missing --cov-report=html

security: ## Audit dependencies and scan application source.
	uv run pip-audit
	uv run bandit -q -r src

check: format-check lint typecheck test ## Run the required local/CI quality gate.

doctor: ## Print a sanitized pipeline configuration report.
	PYTHONPATH=src uv run python -m jobs.cli doctor

pipeline-demo: ## Run normalization and exact-variant matching examples.
	PYTHONPATH=src uv run python -m jobs.cli pipeline-demo

parse-demo: ## Run HTML/JSON-LD parser examples for configured retailers.
	PYTHONPATH=src uv run python -m jobs.cli parse-demo --source flipkart

reconcile-demo: ## Deduplicate and link products across all configured sources.
	PYTHONPATH=src uv run python -m jobs.cli reconcile-demo

benchmark: ## Run empirical accuracy & benchmark evaluation on deterministic matcher.
	PYTHONPATH=src uv run python -m matching.benchmark_runner

live-collect: ## Fetch live product pages over HTTP, parse, and reconcile.
	PYTHONPATH=src uv run python -m jobs.cli live-collect

bulk-collect: ## Discover, scrape, and reconcile products across any/all categories and sources.
	PYTHONPATH=src uv run python -m jobs.cli bulk-collect --category all --sources all --limit 80

scrape-all: bulk-collect ## Alias for bulk-collect across all categories and sources.

bulk-collect-laptops: ## Discover, scrape, and reconcile laptops across Flipkart, Amazon, and Croma.
	PYTHONPATH=src uv run python -m jobs.cli bulk-collect --category laptop --sources all --limit 50

collection-plan: ## Show the bounded default collection volume (zero until products are seeded).
	PYTHONPATH=src uv run python -m jobs.cli collection-plan --source amazon --category laptop --available-products 0

db-upgrade: ## Apply managed catalog migrations to the configured database.
	PYTHONPATH=src uv run python -m jobs.cli db-upgrade

db-current: ## Report the configured database's current Alembic revision.
	PYTHONPATH=src uv run python -m jobs.cli db-current

embedding-status: ## Report canonical-document and configured-embedding coverage.
	PYTHONPATH=src uv run python -m jobs.cli embedding-status

embedding-backfill: ## Generate missing/stale embeddings when explicitly enabled.
	PYTHONPATH=src uv run python -m jobs.cli embedding-backfill

playwright-install: ## Install Chromium only when a permitted source requires browser rendering.
	uv run playwright install chromium

pre-commit: ## Install the repository pre-commit hooks.
	uv run pre-commit install

dev-up: ## Start PostgreSQL, Valkey, the catalog worker, and DB Visualizer UI.
	$(COMPOSE_DEV) up -d --wait postgres valkey worker
	$(COMPOSE_DEV) up -d db-ui

db-ui: ## Open the database visualizer Web UI in browser (http://localhost:8081).
	$(COMPOSE_DEV) up -d db-ui
	@echo "Database Visualizer UI is available at: http://localhost:8081"
	@open http://localhost:8081 2>/dev/null || true

dev-down: ## Stop development services without deleting PostgreSQL data.
	$(COMPOSE_DEV) down

dev-logs: ## Follow development database, queue, and worker logs.
	$(COMPOSE_DEV) logs -f postgres valkey worker

dev-db-shell: ## Open psql inside the development PostgreSQL container.
	$(COMPOSE_DEV) exec postgres psql -U $${POSTGRES_USER:-certikart} -d $${POSTGRES_DB:-certikart}

docker-build: ## Build the locked, rootless production image locally.
	docker build --target runtime --tag $(LOCAL_IMAGE) .

docker-doctor: docker-build ## Run the image configuration smoke test without exposing secrets.
	docker run --rm --env CERTIKART_ENVIRONMENT=production --env CERTIKART_SCHEMA_MANAGEMENT=migrations --env CERTIKART_DATABASE_URL=postgresql+psycopg://placeholder:placeholder@db.invalid/certikart $(LOCAL_IMAGE) doctor

compose-validate: ## Validate both Compose models with non-secret placeholders.
	docker compose -f compose.dev.yml config --quiet
	CERTIKART_PIPELINE_IMAGE=$(LOCAL_IMAGE) CERTIKART_DATABASE_URL=postgresql+psycopg://placeholder:placeholder@db.invalid/certikart CERTIKART_REDIS_URL=redis://queue.invalid:6379/0 docker compose -f compose.prod.yml config --quiet
