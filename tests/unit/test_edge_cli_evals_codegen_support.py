"""Builders shared by the CLI / evals / codegen edge-case tests.

Named `test_edge_*` so it follows the layout rule for new files in this area; it defines no tests
itself. Everything here builds *values* (capabilities, run records, suite results). Nothing starts a
browser, a server, or touches the network.

Capabilities are built by dumping the checked-in reference artifact to a plain dict and validating a
mutated copy, rather than by hand-assembling pydantic models. The artifact is the schema's own
worked example, so a mutation of it stays a legal capability unless the mutation itself is the
point of the test.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cua.domain.actor import Actor
from cua.domain.capability import Capability
from cua.domain.result import (
    BusinessOutcome,
    FailureCode,
    FailureDetail,
    InterventionRef,
    RunResult,
    RunStatus,
)
from cua.domain.run_record import (
    ResolutionRecord,
    RunKind,
    RunRecord,
    StepRecord,
)
from cua.domain.serde import dump_capability, load_capability, to_dict
from cua.domain.target import ResolutionStrategy
from cua.evals.scorers import GroundTruth, truth_key
from cua.evals.suites import SuiteResult

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "capabilities"
REF = "corebank.member.savings_balance@1.0.0"

Mutation = Callable[[dict[str, Any]], None]


# ------------------------------------------------------------------ capabilities


def capability_dict(name: str = "savings_balance.yaml") -> dict[str, Any]:
    """A fresh, freely mutable dict of a checked-in reference capability (aliases intact)."""
    loaded = load_capability((FIXTURES / name).read_text(encoding="utf-8"))
    return to_dict(loaded, prune=False)


def build_capability(
    mutate: Mutation | None = None, *, name: str = "savings_balance.yaml"
) -> Capability:
    """The reference capability with `mutate` applied, re-sealed so the hash is valid."""
    data = capability_dict(name)
    if mutate is not None:
        mutate(data)
    data["content_hash"] = ""
    return Capability.model_validate(data).with_hash()


def step_of(data: dict[str, Any], step_id: str) -> dict[str, Any]:
    """The step dict with this id, from a `capability_dict`."""
    return next(step for step in data["steps"] if step["id"] == step_id)


def write_catalog(root: Path, *capabilities: Capability) -> list[Path]:
    """Seal and write each capability as `<ref>.yaml` under `root`, the layout the catalog reads."""
    root.mkdir(parents=True, exist_ok=True)
    written = []
    for capability in capabilities:
        path = root / f"{capability.ref}.yaml"
        path.write_text(dump_capability(capability), encoding="utf-8")
        written.append(path)
    return written


# ------------------------------------------------------------------- run records


@dataclass(frozen=True)
class StepSpec:
    """One resolved step of a synthetic run, in the vocabulary the scorers read."""

    step_id: str = "read_balance"
    node_id: str | None = "content:/row[2]/cell[1]"
    strategy: ResolutionStrategy | None = ResolutionStrategy.SEMANTIC_EXACT
    recorded: ResolutionStrategy | None = ResolutionStrategy.SEMANTIC_EXACT
    action_type: str = "extract"
    actor: Actor = Actor.AUTOMATION
    authorized: bool = True
    with_resolution: bool = True


def run_record(
    *,
    inputs: dict[str, Any],
    status: RunStatus = RunStatus.SUCCESS,
    outputs: dict[str, Any] | None = None,
    outcome: str | None = None,
    error: FailureCode | None = None,
    steps: Sequence[StepSpec] | None = None,
    run_id: str = "r",
    duration_ms: int = 0,
    with_result: bool = True,
) -> RunRecord:
    """A `RunRecord` with exactly the fields the scorers and the report consume."""
    result: RunResult | None = None
    if with_result:
        result = RunResult(
            status=status,
            capability_id="corebank.member.savings_balance",
            capability_version="1.0.0",
            run_id=run_id,
            outputs=dict(outputs or {}),
            outcome=BusinessOutcome(code=outcome) if outcome else None,
            error=(
                FailureDetail(
                    code=error,
                    step_id="read_balance",
                    message="refused",
                    expected="x",
                    observed="y",
                )
                if error
                else None
            ),
            intervention=(
                InterventionRef(intervention_id="i-1", reason="needs a person")
                if status is RunStatus.NEEDS_HUMAN
                else None
            ),
        )
    specs = list(steps) if steps is not None else [StepSpec()]
    return RunRecord(
        run_id=run_id,
        kind=RunKind.REPLAY,
        capability_ref=REF,
        inputs=dict(inputs),
        result=result,
        duration_ms=duration_ms,
        steps=tuple(
            StepRecord(
                index=index,
                step_id=spec.step_id,
                action_type=spec.action_type,
                actor=spec.actor,
                authorized=spec.authorized,
                resolution=(
                    ResolutionRecord(
                        target_description=f"target of {spec.step_id}",
                        strategy_used=spec.strategy,
                        strategy_recorded=spec.recorded,
                        resolved_node_id=spec.node_id,
                    )
                    if spec.with_resolution
                    else None
                ),
            )
            for index, spec in enumerate(specs)
        ),
    )


def suite_result(
    *,
    records: Sequence[RunRecord] = (),
    truth: Sequence[GroundTruth] = (),
    name: str = "replay_stability",
    tenant: str | None = None,
    source_hash: str = "sha256:reviewed-source",
    notes: Sequence[str] = (),
    headline: str = "Does it keep working?",
    capability: Capability | None = None,
) -> SuiteResult:
    """A `SuiteResult` over synthetic records, so reports and the eval CLI run with no browser."""
    return SuiteResult(
        name=name,
        headline=headline,
        capability=capability or build_capability(),
        tenant=tenant,
        source_hash=source_hash,
        records=list(records),
        truth={truth_key(t.inputs): t for t in truth},
        notes=list(notes),
    )
