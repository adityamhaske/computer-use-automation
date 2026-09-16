"""A test generated from an artifact is a real test, not a plausible-looking file.

The claim `cua codegen` makes is that the artifact is complete enough to describe automation
outside this system. A generator that emits syntactically valid Python nobody runs does not
support that claim -- it looks like it does, which is worse. So the generated file is executed
here, against the same mock application everything else runs against, and the build fails when it
stops working.

The second assertion is about *how* it works. The artifact carries a CSS hint for every target as
a resolution cache, and emitting those would be the shortest route to a passing test. It would
also couple the generated file to markup, so it would break on a restyle while the capability it
came from kept working -- the exact coupling the artifact design exists to avoid.
"""

from __future__ import annotations

import ast
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from apps.mock_bank.server import create_app

from cua.domain.capability import Capability
from cua.domain.predicates import NodeExists, NodeQuery, TextPresent
from cua.domain.serde import dump_capability, load_capability
from cua.domain.target import MatchMode, NameMatch
from cua.domain.values import OutputRef, SecretRef
from cua.recorder.codegen import UnsupportedStepError, render_test

pytestmark = [pytest.mark.browser, pytest.mark.slow]

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/capabilities/savings_balance.yaml"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Server:
    def __init__(self) -> None:
        self.port = _free_port()
        self._server = uvicorn.Server(
            uvicorn.Config(create_app("base"), host="127.0.0.1", port=self.port, log_level="error")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> _Server:
        self._thread.start()
        while not self._server.started:
            threading.Event().wait(0.05)
        return self

    def __exit__(self, *_: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


@pytest.fixture(scope="module")
def app() -> Iterator[_Server]:
    with _Server() as server:
        yield server


@pytest.fixture(scope="module")
def capability() -> Capability:
    """The reference artifact, sealed -- which is what the catalog holds in practice."""
    return load_capability(dump_capability(load_capability(FIXTURE.read_text("utf-8"))))


def test_the_generated_file_is_valid_python(capability: Capability) -> None:
    source = render_test(capability, {"member_id": "12345"}, base_url="http://127.0.0.1:8811")
    ast.parse(source)


def test_no_css_hint_reaches_the_generated_file(capability: Capability) -> None:
    """Targets are emitted semantically, never from the artifact's CSS cache.

    Asserted against the hint values actually present in this artifact rather than by grepping for
    anything selector-shaped, so the test fails for the real reason -- a hint leaked -- and not
    because a locator happened to contain a bracket.
    """
    source = render_test(capability, {"member_id": "12345"}, base_url="http://127.0.0.1:8811")

    hints = [
        value
        for step in capability.steps
        if (target := getattr(step.action, "target", None)) is not None
        for value in target.hints.values()
    ]
    assert hints, "this artifact carries no CSS hints, so the test would prove nothing"
    for hint in hints:
        assert hint not in source, f"generated test leaked the CSS hint {hint!r}"


def test_a_missing_input_is_refused_rather_than_guessed(capability: Capability) -> None:
    """Emitting a placeholder would produce a file that runs and tests nothing."""
    with pytest.raises(UnsupportedStepError, match="member_id"):
        render_test(capability, {}, base_url="http://127.0.0.1:8811")


def test_the_generated_test_actually_passes(
    capability: Capability, app: _Server, tmp_path: Path
) -> None:
    """The assertion the whole feature rests on: run the thing it produced."""
    generated = tmp_path / "test_generated_capability.py"
    generated.write_text(
        render_test(capability, {"member_id": "12345"}, base_url=app.url), encoding="utf-8"
    )

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(generated), "-q", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=Path(__file__).resolve().parents[2],
        check=False,
    )
    assert completed.returncode == 0, (
        f"the generated test failed:\n{completed.stdout[-3000:]}\n{completed.stderr[-2000:]}"
    )


def test_a_hostile_accessible_name_cannot_inject_code(capability: Capability) -> None:
    """Names come from the page, and the page is not trusted.

    Every accessible name, anchor text and frame name in an artifact was read off the application
    the discovery run drove. Interpolating those between double quotes by hand was not merely a
    syntax-error risk: a closing quote lets a recorded label inject arbitrary expressions into a
    file this project then executes under pytest -- in its own suite, and in whatever CI a reader
    points `--out` at.

    Asserted on the parse tree rather than by string matching, because the payload is *supposed* to
    appear in the output; what must never appear is an executable node built from it.
    """
    payload = (
        'Member Number", exact=True) or __import__("os").system("touch /tmp/pwned") '
        'or page.get_by_role("textbox", name="Member Number'
    )
    first = capability.steps[0]
    hostile = capability.model_copy(
        update={
            "steps": (
                first.model_copy(
                    update={
                        "action": first.action.model_copy(
                            update={
                                "target": first.action.target.model_copy(
                                    update={"name": NameMatch(value=payload)}
                                )
                            }
                        )
                    }
                ),
                *capability.steps[1:],
            )
        }
    )

    source = render_test(hostile, {"member_id": "12345"}, base_url="http://127.0.0.1:8811")
    tree = ast.parse(source)

    executable = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "__import__"]
    assert not executable, "a recorded accessible name reached the output as executable code"

    inert = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and "__import__" in n.value
    ]
    assert len(inert) == 1, "the payload should survive exactly once, as inert data"


def test_a_backslash_in_a_name_is_not_silently_reinterpreted(capability: Capability) -> None:
    """`C:\\x41 path` must stay that string, not decode to `C:A path` and match a different cell."""
    first = capability.steps[0]
    odd = capability.model_copy(
        update={
            "steps": (
                first.model_copy(
                    update={
                        "action": first.action.model_copy(
                            update={
                                "target": first.action.target.model_copy(
                                    update={"name": NameMatch(value="C:\\x41 path")}
                                )
                            }
                        )
                    }
                ),
                *capability.steps[1:],
            )
        }
    )
    source = render_test(odd, {"member_id": "12345"}, base_url="http://127.0.0.1:8811")
    names = [
        kw.value.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "name" and isinstance(kw.value, ast.Constant)
    ]
    assert "C:\\x41 path" in names, f"the name was re-interpreted: {names}"


def test_a_secret_or_chained_output_is_refused_not_stringified(capability: Capability) -> None:
    """Both used to emit the pydantic model's repr as the literal to type.

    A password step became `.fill("secret_name='core.pw'")`: the generated test typed that string
    into the field and failed later, looking like a broken application rather than a mistranslated
    artifact.
    """
    first = capability.steps[0]

    def _with_value(value: object) -> Capability:
        return capability.model_copy(
            update={
                "steps": (
                    first.model_copy(
                        update={"action": first.action.model_copy(update={"value": value})}
                    ),
                    *capability.steps[1:],
                )
            }
        )

    with pytest.raises(UnsupportedStepError, match=r"core\.pw"):
        render_test(_with_value(SecretRef(secret_name="core.pw")), {}, base_url="http://x")

    with pytest.raises(UnsupportedStepError, match="savings_balance"):
        render_test(_with_value(OutputRef(output_name="savings_balance")), {}, base_url="http://x")


def test_the_generated_test_asserts_the_capabilitys_checkpoint(capability: Capability) -> None:
    """Regression: codegen used to translate every step but never the checkpoint itself.

    A generated test that only checked each output's shape could pass on a run that never actually
    reached the goal state -- the per-output assertions and "did we get there" are different
    claims, and only the checkpoint makes the second one. Asserted on the source containing a
    reference to the checkpoint's own condition, so this fails for the right reason if the
    translation is ever silently dropped again rather than just reworded.
    """
    source = render_test(capability, {"member_id": "12345"}, base_url="http://127.0.0.1:8811")
    assert "checkpoint" in source
    assert ".count()" in source
    for sub in capability.checkpoint.of:  # type: ignore[attr-defined]
        assert sub.describe() in source


def test_checkpoint_translation_refuses_an_unsupported_predicate(capability: Capability) -> None:
    """A predicate shape codegen cannot translate must stop generation, not be dropped silently.

    Dropping it would mean the generated test passes on a run whose checkpoint condition never
    actually held -- the same failure mode `UnsupportedStepError` exists everywhere else in this
    module to prevent.
    """
    untranslatable = capability.model_copy(
        update={"checkpoint": TextPresent(value="Member Record")}
    )
    with pytest.raises(UnsupportedStepError, match="text_present"):
        render_test(untranslatable, {"member_id": "12345"}, base_url="http://127.0.0.1:8811")


def test_checkpoint_translation_covers_a_query_with_no_declared_frame(
    capability: Capability,
) -> None:
    """Regression: a checkpoint query with no `scope.frame` matches in any frame in the domain
    model, but a plain `page.get_by_role(...)` only searches the main document -- under-counting on
    this project's frameset applications. This caught a real bug: the first cut of the translation
    read as a checkpoint failure on a run that had actually reached the goal state.
    """
    frame_blind = capability.model_copy(
        update={"checkpoint": NodeExists(query=NodeQuery(role="cell", name="Status"), min_count=1)}
    )
    source = render_test(frame_blind, {"member_id": "12345"}, base_url="http://127.0.0.1:8811")
    assert "page.frames" in source, "an unscoped checkpoint query must search every frame"


def test_locator_only_forces_exact_match_for_match_mode_exact(capability: Capability) -> None:
    """Regression: `exact=True` was emitted for every locator regardless of the declared mode.

    `Anchor.match` defaults to `NORMALIZED`, so a step that declares nothing at all still resolves
    case/whitespace/punctuation-tolerant at replay time. Forcing `exact=True` in the generated test
    made it stricter than the resolver its own artifact would use -- a test that could fail on a run
    replay itself would pass. Playwright has no native "normalized" comparator, so the honest
    translation is its own default (no `exact`), not a forced exact match.
    """
    read_balance = next(s for s in capability.steps if s.id == "read_balance")
    assert read_balance.action.target.anchor.match is MatchMode.NORMALIZED, (
        "this test needs a step whose anchor uses the default (unspecified) match mode"
    )
    enter_member_id = next(s for s in capability.steps if s.id == "enter_member_id")
    assert enter_member_id.action.target.name.match is MatchMode.EXACT, (
        "this test needs a step whose name declares an explicit exact match"
    )

    source = render_test(capability, {"member_id": "12345"}, base_url="http://127.0.0.1:8811")

    balance_line = next(line for line in source.splitlines() if "Savings Balance" in line)
    assert "exact=True" not in balance_line, (
        f"a NORMALIZED-mode anchor should not force an exact match: {balance_line!r}"
    )

    member_line = next(line for line in source.splitlines() if "Member Number" in line)
    assert "exact=True" in member_line, (
        f"an EXACT-mode name should still force an exact match: {member_line!r}"
    )


def test_locator_translates_a_regex_match_mode(capability: Capability) -> None:
    """`MatchMode.REGEX` becomes a compiled Python regex, not a literal string comparison."""
    first = capability.steps[0]
    regexed = capability.model_copy(
        update={
            "steps": (
                first.model_copy(
                    update={
                        "action": first.action.model_copy(
                            update={
                                "target": first.action.target.model_copy(
                                    update={
                                        "name": NameMatch(value=r"^Member", match=MatchMode.REGEX)
                                    }
                                )
                            }
                        )
                    }
                ),
                *capability.steps[1:],
            )
        }
    )
    source = render_test(regexed, {"member_id": "12345"}, base_url="http://127.0.0.1:8811")
    assert "name=re.compile('^Member')" in source
