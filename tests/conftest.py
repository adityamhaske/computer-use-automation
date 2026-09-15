"""Test-session bootstrap.

`tests/e2e/test_live_discovery.py` gates itself on `OPENROUTER_API_KEY` at *collection* time,
and pytest never imports `cua.cli.main` -- so the CLI's own `load_dotenv()` cannot help it.
Loading here, at conftest import, is the only point early enough for that `skipif` to see a
key from `.env`.

Every other test runs without a model and is unaffected.
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()
