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
from cua.domain.serde import dump_capability, load_capability
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
