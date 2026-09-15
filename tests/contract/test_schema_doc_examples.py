"""The schema reference has to describe the schema that exists.

`cua/domain/capability.py` ends its module docstring with "See docs/design/artifact-schema.md",
so that document is the canonical reference a reviewer is pointed at. Its examples were
hand-written and had drifted badly: `target` shown as a sibling of `action` rather than a field
on it, `cell_text` for what is `text`, predicates written as `{assert: node_exists, role: ...}`
instead of carrying a `query`, an `assert: node_matches` that has never existed, and a
`terminal:` key on outcomes.

Drift like that is invisible until someone writes an artifact from the docs and it will not
load. A snippet the reference publishes is a claim about the schema, so it is tested like one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from pydantic import TypeAdapter

from cua.domain.capability import Outcome, RecoveryRule, Step
from cua.domain.predicates import Predicate

DOC = Path(__file__).resolve().parents[2] / "docs/design/artifact-schema.md"

# A block is checked only when the document declares what it is, with an HTML comment directly
# above it: `<!-- validates: Step -->`. Fragments that illustrate one key in isolation stay
# unmarked rather than being contorted into something loadable.
MODELS: dict[str, type | object] = {
    "Step": Step,
    "Predicate": Predicate,
    "Outcome": Outcome,
    "RecoveryRule": RecoveryRule,
}

MARKED = re.compile(r"<!--\s*validates:\s*(\w+)\s*-->\s*```yaml\n(.*?)```", re.S)


def _marked_blocks() -> list[tuple[str, str]]:
    return MARKED.findall(DOC.read_text(encoding="utf-8"))


def test_the_reference_marks_examples_for_validation() -> None:
    """If every marker were deleted this file would pass while checking nothing."""
    blocks = _marked_blocks()
    assert len(blocks) >= 4, f"expected the reference to publish validated examples, got {blocks}"


@pytest.mark.parametrize("index", range(len(_marked_blocks())))
def test_each_published_example_validates(index: int) -> None:
    name, block = _marked_blocks()[index]
    assert name in MODELS, f"unknown validates: marker {name!r}; known: {sorted(MODELS)}"
    TypeAdapter(MODELS[name]).validate_python(yaml.safe_load(block))
