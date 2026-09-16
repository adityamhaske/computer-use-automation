"""The architectural contracts exist, and they pass.

`make invariants` runs import-linter directly. This test exists so that `make test` alone also
catches a violation -- the invariants are the design, and they should not depend on anyone
remembering to run a second command.

The name check matters as much as the pass check: a contract that is *deleted* makes the suite go
green, which is precisely the wrong signal. Each name here corresponds to an invariant documented in
AGENTS.md, and removing one should require deliberately editing this list.
"""

from __future__ import annotations

import configparser
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".importlinter"

# Invariant name -> the claim it backs, for the failure message.
REQUIRED_CONTRACTS = {
    "no-llm-in-replay": "AGENTS.md invariant 1: replay never calls or imports the LLM",
    "policy-chokepoint": "AGENTS.md invariant 2: no action reaches a driver without authorization",
    "surface-neutral-targeting": "AGENTS.md invariant 3: target identity is semantic, not CSS",
    "pure-domain": "AGENTS.md invariant 9: domain/ is pure",
    "layers": "the dependency direction of the system",
}


def test_all_required_contracts_are_declared() -> None:
    parser = configparser.ConfigParser()
    parser.read(CONFIG)
    declared = {
        section.removeprefix("importlinter:contract:")
        for section in parser.sections()
        if section.startswith("importlinter:contract:")
    }

    missing = set(REQUIRED_CONTRACTS) - declared
    assert not missing, "\n".join(
        [f"Architectural contract(s) missing from {CONFIG.name}:"]
        + [f"  - {name}: {REQUIRED_CONTRACTS[name]}" for name in sorted(missing)]
    )


def test_contracts_hold() -> None:
    # Not `python -m importlinter.cli lint`: that module has no `__main__` guard, so the
    # module form prints nothing, runs no contracts and exits 0 regardless of what is broken --
    # the same dead entry point `make invariants` used to call (see the Makefile). Invoking
    # `lint_imports()` directly runs the same check `lint-imports` does, without depending on
    # that console script existing on PATH.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\nfrom importlinter.cli import lint_imports\nsys.exit(lint_imports())\n",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "An architectural invariant is broken. These are not style rules -- each one backs a "
        "claim made in REPORT.md. See AGENTS.md.\n\n" + result.stdout + result.stderr
    )
