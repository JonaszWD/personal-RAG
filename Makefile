.DEFAULT_GOAL := help
UV := uv run --extra tui

help: ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install dev dependencies and git hooks
	uv sync --group dev --extra tui
	$(UV) pre-commit install

check: lint typecheck test ## Everything CI runs (minus Docker)

lint: ## Ruff lint + format check
	$(UV) ruff check .
	$(UV) ruff format --check .

format: ## Auto-format
	$(UV) ruff check --fix .
	$(UV) ruff format .

typecheck: ## mypy --strict
	$(UV) mypy

test: ## Unit tests with coverage
	$(UV) pytest --cov=personal_rag --cov-report=term-missing --cov-fail-under=80

audit: ## Dependency vulnerability scan
	uv export --frozen --no-dev --no-emit-project -o /tmp/rag-req.txt
	$(UV) pip-audit -r /tmp/rag-req.txt --no-deps --disable-pip

up: ## Start backend + Ollama
	docker compose up -d --build

down: ## Stop everything (data volumes are kept)
	docker compose down

models: ## One-time model download
	docker compose --profile setup run --rm pull-models

eval: ## Retrieval + answer quality on the synthetic demo corpus
	docker compose --profile eval run --rm --build eval

eval-fast: ## Retrieval metrics only (no LLM generation)
	docker compose --profile eval run --rm --build eval --no-answers

screenshots: ## Regenerate docs/images/*.svg
	$(UV) python scripts/make_screenshots.py

.PHONY: help install check lint format typecheck test audit up down models eval eval-fast screenshots
