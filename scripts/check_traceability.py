"""Verify that the traceability matrix points at things that exist.

The matrix is the document that says "every requirement maps to real code, a real test and real
evidence". It is also the document most likely to rot, because it is written alongside a plan and
read long after. An earlier version accumulated 37 references to files that were never created --
which is worse than having no matrix, because it reads like proof.

So the matrix is checked mechanically. Every module path, every `file::test` reference and every
evidence path in it must resolve, or `make check` fails.

Run: python scripts/check_traceability.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/prd/01-requirements-traceability.md"

TEST_DIRS = ("unit/", "integration/", "contract/", "invariants/", "e2e/")
SOURCE_DIRS = (
    "cli/",
    "agent/",
    "domain/",
    "perception/",
    "policy/",
    "recorder/",
    "replay/",
    "runtime/",
    "surfaces/",
    "targeting/",
    "hitl/",
    "evidence/",
    "catalog/",
)
LITERAL_PATHS = (".importlinter", "config/", "apps/", "docs/", "scripts/", "src/", "tests/")


def resolves(reference: str) -> bool:
    """Does this reference point at something on disk?"""
    if "::" in reference:
        file_part, test_name = reference.split("::", 1)
        if not file_part:
            # `::name` is the matrix's shorthand for "another test in the file just named".
            return any(
                f"def {test_name}(" in path.read_text(encoding="utf-8")
                for path in (ROOT / "tests").rglob("test_*.py")
            )
        path = ROOT / "tests" / file_part
        if not path.is_file():
            return False
        return f"def {test_name}(" in path.read_text(encoding="utf-8")

    if reference.startswith("evidence/") and not reference.endswith(".py"):
        # Evidence lives at evidence/<kind>/<run_id>/..., so a single `*` in the matrix means
        # "any run". `src/cua/evidence/*.py` is a module path and handled below.
        return any(ROOT.glob(reference)) or any(ROOT.glob(reference.replace("*/", "*/*/", 1)))

    if reference.startswith(TEST_DIRS):
        return (ROOT / "tests" / reference).is_file()

    if reference.startswith(LITERAL_PATHS):
        return any(ROOT.glob(reference)) or (ROOT / reference).exists()

    if reference.startswith(SOURCE_DIRS):
        return any((ROOT / "src/cua").glob(reference))

    if reference == "file::test":  # the sentence explaining the notation
        return True

    return True  # not a path -- a YAML key, a field name, an event type


def main() -> int:
    text = MATRIX.read_text(encoding="utf-8")
    # Everything in backticks, plus the markdown links in the deliverables table.
    references = set(re.findall(r"`([^`\n]+)`", text)) - {"file::test"}

    checkable = {
        r
        for r in references
        if "::" in r
        or r.endswith((".py", ".yaml", ".md", ".jsonl", ".json"))
        or r.startswith(("evidence/", "config/", "apps/", "docs/", "src/", "tests/"))
        or r == ".importlinter"
    }

    broken = sorted(r for r in checkable if not resolves(r))
    if broken:
        print(f"{MATRIX.relative_to(ROOT)}: {len(broken)} reference(s) do not resolve:\n")
        for reference in broken:
            print(f"  {reference}")
        print(
            "\nThe matrix must describe what exists. Fix the reference, or build the thing it "
            "promises."
        )
        return 1

    print(f"traceability: {len(checkable)} reference(s) resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
