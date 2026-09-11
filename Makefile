# Computer-Use Automation System
#
# The two commands a reviewer needs:
#     make setup && make demo
#
# Everything except the live discovery run works offline with no API key.

SHELL := /bin/bash
.DEFAULT_GOAL := help
PY := .venv/bin/python
UV := $(shell command -v uv 2>/dev/null)

MOCK_APP_PORT ?= 8811
CONSOLE_PORT  ?= 8812

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ setup

.PHONY: setup
setup: ## Create the venv, install deps, install Chromium
ifdef UV
	uv venv --python 3.11 .venv
	uv pip install --python $(PY) -e ".[dev]"
else
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -e ".[dev]"
endif
	$(PY) -m playwright install chromium --with-deps || \
		echo "NOTE: chromium may already be provisioned (PLAYWRIGHT_BROWSERS_PATH)"
	@echo "Setup complete. Copy .env.example to .env if you want the live discovery run."

# ------------------------------------------------------------------ quality

.PHONY: fmt
fmt: ## Auto-format and auto-fix
	$(PY) -m ruff format src tests apps evals
	$(PY) -m ruff check --fix src tests apps evals

.PHONY: lint
lint: ## Lint (no fixes)
	$(PY) -m ruff check src tests apps evals
	$(PY) -m ruff format --check src tests apps evals

.PHONY: typecheck
typecheck: ## Strict type check
	$(PY) -m mypy

.PHONY: invariants
invariants: ## Verify the architectural contracts (import-linter)
	$(PY) -m importlinter.cli lint

.PHONY: test
test: ## Full test suite, offline, no API key required
	$(PY) -m pytest

.PHONY: test-live
test-live: ## The one test that spends real tokens (needs OPENROUTER_API_KEY)
	$(PY) -m pytest -m live

.PHONY: check
check: lint typecheck invariants test ## Everything CI runs

# ------------------------------------------------------------------ running

.PHONY: app
app: ## Run the hostile mock back-office app
	$(PY) -m apps.mock_bank.server --port $(MOCK_APP_PORT)

.PHONY: app-variant-b
app-variant-b: ## Run the "second tenant" variant of the same product
	$(PY) -m apps.mock_bank.server --port $(MOCK_APP_PORT) --variant variant_b

.PHONY: console
console: ## Run the human operator console
	$(PY) -m cua.cli.main console --port $(CONSOLE_PORT)

.PHONY: demo
demo: ## THE GRADING STORY: discover -> artifact -> replay -> outcome -> fault -> escalate -> takeover -> resume
	$(PY) -m cua.cli.main demo

.PHONY: eval
eval: ## Stability + cross-tenant evaluation reports
	$(PY) -m evals.runner --all

# ------------------------------------------------------------------ housekeeping

.PHONY: clean
clean: ## Remove caches and build artifacts (keeps evidence/)
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: clean-evidence
clean-evidence: ## Delete generated run evidence (NOT the committed reference runs)
	rm -rf evidence/discovery/* evidence/replay/* evidence/escalation/* evidence/evals/*
