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

# Every target below needs the venv. Without this the first thing a reviewer who skipped the
# README sees is `make: .venv/bin/python: No such file or directory`, which says nothing about
# what to do. Depending on this target instead says it in one line.
.PHONY: require-venv
require-venv:
	@test -x $(PY) || { \
	  echo ""; \
	  echo "  No virtualenv yet. Run this first:"; \
	  echo ""; \
	  echo "      make setup"; \
	  echo ""; \
	  echo "  (creates .venv, installs dependencies and Chromium -- a few minutes, once)"; \
	  echo ""; \
	  exit 1; \
	}

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ setup

.PHONY: setup
setup: ## Create the venv, install deps, install Chromium
	@python3 -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info < (3,13) else 1)' || { \
	  echo ""; \
	  echo "  This project needs Python 3.11 or 3.12. Yours is $$(python3 -V 2>&1)."; \
	  echo "  Playwright and the typing features used here do not both work outside that range,"; \
	  echo "  so failing here is clearer than failing deep inside pip."; \
	  echo ""; \
	  exit 1; \
	}
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
	$(PY) -m ruff format src tests apps scripts
	$(PY) -m ruff check --fix src tests apps scripts

.PHONY: lint
lint: require-venv ## Lint (no fixes)
	$(PY) -m ruff check src tests apps scripts
	$(PY) -m ruff format --check src tests apps scripts

.PHONY: typecheck
typecheck: require-venv ## Strict type check
	$(PY) -m mypy

.PHONY: invariants
invariants: require-venv ## Verify the architectural contracts and that the docs describe what exists
	$(PY) -m importlinter.cli lint
	$(PY) scripts/check_traceability.py

.PHONY: test
test: require-venv ## Full test suite, offline, no API key required
	$(PY) -m pytest

.PHONY: test-live
test-live: ## The one test that spends real tokens (needs OPENROUTER_API_KEY)
	$(PY) -m pytest -m live

.PHONY: check
check: require-venv lint typecheck invariants test ## Everything CI runs

# ------------------------------------------------------------------ running

.PHONY: app
app: require-venv ## Run the hostile mock back-office app
	$(PY) -m apps.mock_bank.server --port $(MOCK_APP_PORT)

.PHONY: app-variant-b
app-variant-b: require-venv ## Run the "second tenant" variant of the same product
	$(PY) -m apps.mock_bank.server --port $(MOCK_APP_PORT) --variant variant_b

.PHONY: console
console: require-venv ## Run the human operator console
	$(PY) -m cua.cli.main console --port $(CONSOLE_PORT)

.PHONY: demo
demo: require-venv ## THE GRADING STORY: discover -> artifact -> replay -> outcome -> fault -> escalate -> takeover -> resume
	$(PY) -m cua.cli.main demo

.PHONY: discover
discover: require-venv ## Live LLM-driven discovery against a running `make app` -- needs a model in .env
	$(PY) -m cua.cli.main discover --sign-in \
	  --goal "Look up member 12345 and report their savings account balance, the account status, and the as-of date." \
	  --target http://127.0.0.1:$(MOCK_APP_PORT)

.PHONY: walkthrough
walkthrough: require-venv ## Rebuild the end-to-end walkthrough page from its captured screenshots
	$(PY) scripts/build_walkthrough.py

.PHONY: walkthrough-capture
walkthrough-capture: require-venv ## Re-capture the walkthrough screenshots -- needs `make app` running
	$(PY) scripts/capture_walkthrough.py app
	@echo "  console shots: start \`cua console --sign-in --capability ... --arm-fault ...\`,"
	@echo "  then: $(PY) scripts/capture_walkthrough.py console"

.PHONY: report
report: require-venv ## Render REPORT.md to site/report/ as three pages, a PDF and a Markdown download
	$(PY) scripts/build_report_page.py

.PHONY: eval
eval: require-venv ## Stability + cross-tenant measurement -> evidence/evals/
	$(PY) -m cua.cli.main eval

# ------------------------------------------------------------------ housekeeping

.PHONY: clean
clean: ## Remove caches and build artifacts (keeps evidence/)
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: clean-evidence
clean-evidence: ## Delete generated run evidence (NOT the committed reference runs)
	rm -rf evidence/discovery/* evidence/replay/* evidence/escalation/* evidence/evals/* evidence/evals-runs
