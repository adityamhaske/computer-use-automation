"""What an AI agent's side of this looks like.

The rest of the system is about producing a capability that can be trusted. This is the consumer:
a caller that knows nothing about frames, accessibility trees or resolution ladders, looks a
capability up by name, calls it with typed arguments, and branches on the result.

Deliberately not an LLM. Putting a model here would demonstrate nothing -- the interesting claim is
that by this point **no model is needed**, because the artifact is a typed function and its outcomes
are declared. A caller that handles these four statuses correctly is the whole integration contract.

The branching is the point:

    SUCCESS           -> use the outputs
    BUSINESS_OUTCOME  -> a real answer; tell the user, do not retry, do not page anyone
    NEEDS_HUMAN       -> a person is already holding the session; hand off, do not retry
    FAILED            -> a defect or a bad call; surface it

An agent that collapses the middle two into "error" will retry a question the system already
answered, and eventually escalate it to a human who has nothing to do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import typer

from cua.catalog.store import CapabilityStore
from cua.domain.result import RunResult, RunStatus


@dataclass
class AgentTurn:
    """What the calling agent decided to do about one run."""

    disposition: str
    message: str
    payload: dict[str, Any]

    @property
    def ok(self) -> bool:
        """True when the agent got an answer it can act on -- including a negative one."""
        return self.disposition in {"answered", "answered_negative"}


def interpret(result: RunResult) -> AgentTurn:
    """Turn a run result into the agent's next move.

    Pure, so the contract is testable without a browser: this is the function every integrating
    team writes, and getting it wrong is how a correct system becomes a noisy one.
    """
    if result.status is RunStatus.SUCCESS:
        return AgentTurn(
            disposition="answered",
            message="Here is the balance.",
            payload=dict(result.outputs),
        )

    if result.status is RunStatus.BUSINESS_OUTCOME:
        outcome = result.outcome
        code = outcome.code if outcome else "unknown"
        return AgentTurn(
            disposition="answered_negative",
            message={
                "member_not_found": "No member with that number exists.",
                "no_savings_account": "That member has no savings account.",
                "account_closed": "That member's savings account is closed.",
                "permission_denied": "This teller is not authorised to view that member.",
            }.get(code, f"The system reported: {code}."),
            payload=dict(outcome.data) if outcome and outcome.data else {},
        )

    if result.status is RunStatus.NEEDS_HUMAN:
        intervention = result.intervention
        return AgentTurn(
            disposition="handed_off",
            message=(
                "I could not complete this safely, so an operator has the session. "
                f"Reference {intervention.intervention_id if intervention else 'unknown'}."
            ),
            payload={"reason": intervention.reason if intervention else ""},
        )

    error = result.error
    code = error.code.value if error else "unknown"
    return AgentTurn(
        disposition="failed",
        message=f"That request could not be completed ({code}).",
        payload={"step": error.step_id if error else "", "detail": error.message if error else ""},
    )


def run_agent_demo(*, capability_ref: str, member_id: str, base_url: str, headless: bool) -> int:
    """Look a capability up by name and invoke it, as an agent would."""
    from cua.cli.main import _replay_once

    store = CapabilityStore()
    entry = store.resolve(capability_ref)
    schema = entry.capability.tool_schema()

    typer.secho("\nThe agent's view of the catalog", bold=True)
    typer.echo(f"  tool      {schema['name']}")
    typer.echo(f"  purpose   {schema['description']}")
    typer.echo(f"  arguments {sorted(schema['input_schema']['properties'])}")
    typer.echo(f"  returns   {sorted(schema['output_schema']['properties'])}")
    typer.echo(f"  outcomes  {[o['code'] for o in schema['outcomes']]}")
    typer.echo("\n  (all of that came from the artifact -- there is no second tool definition)\n")

    result, _ = _replay_once(
        capability=store.load(capability_ref),
        inputs={"member_id": member_id},
        base_url=base_url,
        headless=headless,
        run_id=f"agent-demo-{member_id}",
    )
    turn = interpret(result)

    colour = typer.colors.GREEN if turn.ok else typer.colors.YELLOW
    typer.secho(f"  invoke({{'member_id': '{member_id}'}})", bold=True)
    typer.secho(f"  -> {result.status.value}  [{turn.disposition}]", fg=colour)
    typer.echo(f"     {turn.message}")
    if turn.payload:
        typer.echo(f"     {turn.payload}")
    typer.echo()

    # An agent exits 0 for an answer, positive or negative. Only a defect or a handoff is non-zero.
    return 0 if turn.ok else (2 if turn.disposition == "handed_off" else 1)
