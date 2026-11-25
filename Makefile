.PHONY: help install dev api web test lint fmt typecheck bench demo docker clean

PY      := .venv/bin/python
PIP     := .venv/bin/pip
BACKEND := backend
PORT    ?= 8000
HOST    ?= 0.0.0.0

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

.venv:
	python3.11 -m venv .venv
	$(PIP) install --upgrade pip

install: .venv ## Install the backend and dev tools
	$(PIP) install -e "$(BACKEND)[dev]"
	@test -f .env || cp .env.example .env

dev: api ## Run the API (the web console arrives in M6; use `make web` once it does)

api: ## Start the FastAPI app with reload (override with PORT=8099)
	cd $(BACKEND) && ../$(PY) -m uvicorn app.main:app --reload --host $(HOST) --port $(PORT)

web: ## Start the Vite dev server [M6]
	@test -f web/package.json || { echo "web/ is scaffolded in M6"; exit 0; }
	cd web && npm run dev

catalogue: ## Build data/catalogue/catalogue.parquet (CSV from .env, else synthetic)
	cd $(BACKEND) && ../$(PY) -m app.services.catalogue ingest

catalogue-synth: ## Force the deterministic synthetic catalogue
	cd $(BACKEND) && ../$(PY) -m app.services.catalogue ingest --synthetic

agent: ## One agent run (INSTR="..." MODE=normal|faulty FAULT=recurrence_flip)
	cd $(BACKEND) && ../$(PY) -m app.services.agent --instruction "$(INSTR)" \
		--mode "$(or $(MODE),normal)" $(if $(FAULT),--fault "$(FAULT)",)

test: ## Run pytest
	cd $(BACKEND) && ../$(PY) -m pytest -q

lint: ## ruff check
	.venv/bin/ruff check $(BACKEND) bench

fmt: ## ruff format + fix
	.venv/bin/ruff format $(BACKEND) bench
	.venv/bin/ruff check --fix $(BACKEND) bench

typecheck: ## mypy (light)
	cd $(BACKEND) && ../.venv/bin/mypy app

BENCH_ENV := PYTHONPATH=$(CURDIR)/backend:$(CURDIR)
BENCH_N   ?= 500

bench: ## Generate MCB-IN, run every system, write bench/reports/
	$(BENCH_ENV) $(PY) -m bench.generate --instructions $(BENCH_N) --out data/bench/cases.jsonl
	$(BENCH_ENV) $(PY) -m bench.run_eval --cases data/bench/cases.jsonl
	$(BENCH_ENV) $(PY) -m bench.annotate.sample --cases data/bench/cases.jsonl

kappa: ## Fleiss' kappa over the filled annotation sheet
	$(BENCH_ENV) $(PY) -m bench.annotate.agreement

demo: ## Seed data and run the section 9 demo in replay mode [M6]
	@echo "make demo lands in M6"; exit 1

docker: ## Build and run via docker compose
	docker compose up --build

clean: ## Remove caches, the dev database and the venv
	rm -rf .venv .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -f backend/anumati.db anumati.db
