"""Turning suite results into something a reviewer reads and a system can consume.

Three outputs per suite, deliberately: markdown for a person, JSON for a tool, and a
`CapabilityEvaluation` document -- which is the same data in the shape the domain already defines
for it. The evaluation is written *separately from the capability* because it is derived data about
a frozen definition (ADR 0002). Folding stability into the artifact would mean the artifact changed
every time it ran, and "which version produced that run?" would stop having an answer.
"""

from __future__ import annotations

import json
from pathlib import Path

from cua.domain.evaluation import CapabilityEvaluation
from cua.domain.result import RunStatus
from cua.evals import scorers
from cua.evals.suites import SuiteResult

REPORT_DIR = Path("evidence/evals")


def evaluate(suite: SuiteResult) -> CapabilityEvaluation:
    """Project a suite's runs into the domain's evaluation document."""
    records = suite.records
    statuses = [r.result.status for r in records if r.result is not None]
    return CapabilityEvaluation(
        capability_ref=suite.capability.ref,
        # The hash of what was reviewed, not of the bound copy that ran -- see
        # SuiteResult.source_hash.
        content_hash=suite.source_hash or suite.capability.content_hash or "",
        tenant=suite.tenant,
        runs=len(records),
        successes=sum(1 for s in statuses if s is RunStatus.SUCCESS),
        business_outcomes=sum(1 for s in statuses if s is RunStatus.BUSINESS_OUTCOME),
        needs_human=sum(1 for s in statuses if s is RunStatus.NEEDS_HUMAN),
        failures=sum(1 for s in statuses if s is RunStatus.FAILED),
        wrong_actions=scorers.wrong_action_count(records, suite.truth),
        ambiguity_refusals=scorers.refusal_count(records),
        determinism_holds=scorers.determinism_holds(records),
        mean_drift=scorers.mean_drift(records),
        strategy_mix=scorers.strategy_mix(records),
        fallback_rate=scorers.fallback_rate(records),
    )


def _verdict(evaluation: CapabilityEvaluation, unauthorized: int) -> str:
    if unauthorized:
        return f"**UNSOUND** — {unauthorized} dispatch(es) reached a surface unauthorized."
    if evaluation.wrong_actions:
        return (
            f"**UNTRUSTWORTHY** — {evaluation.wrong_actions} wrong action(s). "
            "A system that acts on the wrong control is not made acceptable by a high success rate."
        )
    if evaluation.determinism_holds is False:
        return (
            "**NON-DETERMINISTIC** — replays of the same inputs disagreed on which control to act "
            "on."
        )
    return "**SOUND** — zero wrong actions, zero unauthorized dispatches, decisions reproducible."


def to_markdown(suite: SuiteResult, evaluation: CapabilityEvaluation) -> str:
    records = suite.records
    unauthorized = scorers.unauthorized_dispatches(records)
    lines = [
        f"# {suite.name}",
        "",
        f"> {suite.headline}",
        "",
        _verdict(evaluation, unauthorized),
        "",
        f"Capability `{suite.capability.ref}`"
        + (f", tenant `{suite.tenant}`" if suite.tenant else ", own tenant"),
        "",
        "## Headline metrics",
        "",
        "| Metric | Value | Target |",
        "|---|---|---|",
        f"| **Wrong actions** | **{evaluation.wrong_actions}** | **0 — always** |",
        f"| Unauthorized dispatches | {unauthorized} | 0 — always |",
        f"| Determinism holds | {evaluation.determinism_holds} | true |",
        f"| Success rate | {scorers.success_rate(records):.0%} | high, but never at the "
        "expense of the two above |",
        f"| Refusals (ambiguous / undeclared) | {evaluation.ambiguity_refusals} | not minimised |",
        f"| Escalations | {scorers.escalation_count(records)} | not minimised |",
        f"| Stability score | {evaluation.stability_score:.0%} | — |",
        f"| Trustworthy | {evaluation.is_trustworthy} | true |",
        "",
        "## Targeting",
        "",
        f"| Mean drift | {evaluation.mean_drift:.2f} |",
        "|---|---|",
        f"| Fallback rate | {evaluation.fallback_rate:.0%} |",
        "",
        "Drift is descent *below the rung the artifact recorded*, not merely a weak rung winning.",
        "A step recorded at `structural_anchor` that resolves there has drifted by zero -- the",
        "surface is exactly what the artifact expects. Drift above zero is the early",
        "warning that a surface is moving -- it shows before replays begin to fail.",
        "",
        "Which rung of the ladder won, summed across runs:",
        "",
        "| Strategy | Resolutions |",
        "|---|---|",
    ]
    for strategy, count in evaluation.strategy_mix.items():
        lines.append(f"| `{strategy}` | {count} |")

    lines += [
        "",
        "## Runs",
        "",
        "| Inputs | Status | Outputs / outcome | Drift |",
        "|---|---|---|---|",
    ]
    for record in records:
        result = record.result
        if result is None:  # pragma: no cover -- every harness run carries a result
            continue
        if result.status is RunStatus.BUSINESS_OUTCOME and result.outcome:
            detail = f"`{result.outcome.code}`"
        elif result.status is RunStatus.SUCCESS:
            detail = ", ".join(f"{k}={v}" for k, v in sorted(result.outputs.items()))
        elif result.error:
            detail = f"`{result.error.code}`"
        else:
            detail = "—"
        inputs = ", ".join(f"{k}={v}" for k, v in sorted(record.inputs.items()))
        lines.append(f"| {inputs} | {result.status.value} | {detail} | {record.drift_score:.2f} |")

    offenders = scorers.nondeterministic_cases(records)
    if offenders:
        lines += [
            "",
            "## Non-deterministic cases",
            "",
            "Same artifact, same inputs, different decisions. Each of these is a defect:",
            "",
        ] + [f"- `{case}`" for case in offenders]

    if suite.notes:
        lines += ["", "## Notes", ""] + [f"- {note}" for note in suite.notes]

    lines += [
        "",
        "## How to read this",
        "",
        "Refusal rate and wrong-action rate are reported separately and are **never traded off**.",
        "A system tuned to refuse less will eventually act on a control it was not sure about, and",
        "in a core banking screen that is the expensive outcome. A run that stops and escalates is",
        "the design working; a run that confidently returns another member's balance is not.",
        "",
        "Regenerate with `make eval`.",
        "",
    ]
    return "\n".join(lines)


def write(suite: SuiteResult, *, report_dir: Path = REPORT_DIR) -> dict[str, Path]:
    """Write markdown, JSON and the evaluation document. Returns the paths written."""
    report_dir.mkdir(parents=True, exist_ok=True)
    evaluation = evaluate(suite)

    markdown = report_dir / f"{suite.name}.md"
    markdown.write_text(to_markdown(suite, evaluation), encoding="utf-8")

    payload = {
        "suite": suite.name,
        "capability": suite.capability.ref,
        "content_hash": suite.source_hash or suite.capability.content_hash,
        "tenant": suite.tenant,
        "metrics": {
            "wrong_actions": evaluation.wrong_actions,
            "unauthorized_dispatches": scorers.unauthorized_dispatches(suite.records),
            "determinism_holds": evaluation.determinism_holds,
            "nondeterministic_cases": scorers.nondeterministic_cases(suite.records),
            "success_rate": scorers.success_rate(suite.records),
            "refusals": evaluation.ambiguity_refusals,
            "escalations": scorers.escalation_count(suite.records),
            "mean_drift": evaluation.mean_drift,
            "fallback_rate": evaluation.fallback_rate,
            "strategy_mix": evaluation.strategy_mix,
            "stability_score": evaluation.stability_score,
            "trustworthy": evaluation.is_trustworthy,
        },
        "runs": [
            {
                "inputs": record.inputs,
                "status": record.result.status.value if record.result else None,
                "outputs": record.result.outputs if record.result else {},
                "drift_score": record.drift_score,
                "strategy_mix": record.strategy_mix,
            }
            for record in suite.records
        ],
    }
    data = report_dir / f"{suite.name}.json"
    data.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    document = report_dir / f"{suite.name}.evaluation.json"
    document.write_text(evaluation.model_dump_json(indent=2), encoding="utf-8")

    return {"markdown": markdown, "json": data, "evaluation": document}
