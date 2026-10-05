"""Which compiled capabilities the demo may publish into the catalog.

A live model sometimes calls `finish` without ever recording a value, and the run still "succeeds".
It compiles to a draft that returns nothing. Published, that would sit in the catalog and be offered
to an agent as a tool with no output -- so it stays in the run's own evidence directory instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cua.cli.demo import publish_path
from cua.domain.capability import Capability
from cua.domain.serde import load_capability

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/capabilities/savings_balance.yaml"


@pytest.fixture
def capability() -> Capability:
    cap = load_capability(FIXTURE.read_text(encoding="utf-8"))
    assert cap.outputs, "the fixture must declare outputs for these tests to mean anything"
    return cap


def test_a_capability_that_returns_something_is_published_into_an_empty_slot(
    capability: Capability, tmp_path: Path
) -> None:
    catalog, run_dir = tmp_path / "catalog", tmp_path / "run"

    assert publish_path(capability, catalog, run_dir) == catalog / f"{capability.ref}.yaml"


def test_an_occupied_slot_is_never_overwritten(capability: Capability, tmp_path: Path) -> None:
    catalog, run_dir = tmp_path / "catalog", tmp_path / "run"
    catalog.mkdir()
    (catalog / f"{capability.ref}.yaml").write_text("earlier discovery", encoding="utf-8")

    assert publish_path(capability, catalog, run_dir) == run_dir / "compiled.yaml"


def test_a_capability_with_no_outputs_stays_in_its_own_run_directory(
    capability: Capability, tmp_path: Path
) -> None:
    returns_nothing = capability.model_copy(update={"outputs": ()})
    catalog, run_dir = tmp_path / "catalog", tmp_path / "run"

    assert publish_path(returns_nothing, catalog, run_dir) == run_dir / "compiled.yaml"


def test_a_capability_with_no_outputs_in_an_occupied_slot_is_also_kept_out(
    capability: Capability, tmp_path: Path
) -> None:
    returns_nothing = capability.model_copy(update={"outputs": ()})
    catalog, run_dir = tmp_path / "catalog", tmp_path / "run"
    catalog.mkdir()
    (catalog / f"{returns_nothing.ref}.yaml").write_text("earlier", encoding="utf-8")

    assert publish_path(returns_nothing, catalog, run_dir) == run_dir / "compiled.yaml"


def test_publishing_decides_a_path_and_does_not_touch_the_filesystem(
    capability: Capability, tmp_path: Path
) -> None:
    catalog, run_dir = tmp_path / "catalog", tmp_path / "run"

    publish_path(capability, catalog, run_dir)

    assert not catalog.exists() and not run_dir.exists()
