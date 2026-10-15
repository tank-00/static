# Canonical developer commands (see docs/ARCHITECTURE.md §12).
SHELL := /bin/bash
.DEFAULT_GOAL := help
export UV_SYSTEM_CERTS := 1
unexport UV_NATIVE_TLS

BACKEND := backend
FRONTEND := frontend
UV := uv --directory $(BACKEND)
NPM := npm --prefix $(FRONTEND)
E2E_PORT ?= 8765

.PHONY: help setup check lint fmt typecheck backend-lint backend-typecheck backend-test \
        frontend-lint frontend-typecheck frontend-test frontend-build dev api web seed openapi \
        openapi-check e2e run traceability clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

setup: ## Install backend (uv) and frontend (npm) dependencies from lockfiles
	$(UV) sync --all-extras --locked
	$(NPM) ci

check: lint typecheck backend-test frontend-test ## Lint + typecheck + all unit tests (the step gate)

lint: backend-lint frontend-lint ## Lint backend and frontend

typecheck: backend-typecheck frontend-typecheck ## Typecheck backend and frontend

fmt: ## Auto-format backend code
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

backend-lint:
	$(UV) run ruff format --check .
	$(UV) run ruff check .

backend-typecheck:
	$(UV) run mypy

backend-test: ## Run backend tests (parallel)
	$(UV) run pytest -q -n 4 --dist loadfile --durations=10

frontend-lint:
	$(NPM) run lint

frontend-typecheck:
	$(NPM) run typecheck

frontend-test: ## Run frontend unit tests
	$(NPM) run test

frontend-build: ## Production build of the SPA
	$(NPM) run build

api: ## Run the API with autoreload on :8000
	$(UV) run uvicorn tariff_app.main:app --reload --host 127.0.0.1 --port 8000

web: ## Run the Vite dev server on :5173 (proxies /api to :8000)
	$(NPM) run dev

dev: ## Run API and web dev servers together
	@trap 'kill 0' EXIT; $(MAKE) api & $(MAKE) web & wait

seed: ## Reset and load the SYNTHETIC demo workspace
	$(UV) run python -m tariff_app.seed --reset

openapi: ## Export OpenAPI schema and regenerate frontend API types
	$(UV) run python -m tariff_app.openapi_export ../$(FRONTEND)/src/api/openapi.json
	$(NPM) run gen:api

openapi-check: ## Fail if the committed OpenAPI schema/types are stale (non-mutating)
	@tmp=$$(mktemp -d) && \
	$(UV) run python -m tariff_app.openapi_export $$tmp/openapi.json && \
	cmp -s $$tmp/openapi.json $(FRONTEND)/src/api/openapi.json && \
	(cd $(FRONTEND) && npx openapi-typescript $$tmp/openapi.json -o $$tmp/schema.d.ts >/dev/null) && \
	cmp -s $$tmp/schema.d.ts $(FRONTEND)/src/api/schema.d.ts || \
	(echo "OpenAPI schema/types are stale: run 'make openapi'"; exit 1)

e2e: frontend-build ## Build SPA, start single-process server on a seeded temp workspace, run Playwright
	./scripts/e2e.sh $(E2E_PORT)

run: frontend-build ## Serve API + built SPA on :8000
	TARIFF_SERVE_FRONTEND_DIST=$(abspath $(FRONTEND))/dist $(UV) run uvicorn tariff_app.main:app --host 127.0.0.1 --port 8000

traceability: ## Regenerate docs/TRACEABILITY.md from pytest req markers
	$(UV) run python -m tariff_app.traceability ../docs/requirements.yaml ../docs/TRACEABILITY.md

clean: ## Remove caches and build output (keeps data)
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache $(FRONTEND)/dist $(FRONTEND)/test-results $(FRONTEND)/playwright-report
