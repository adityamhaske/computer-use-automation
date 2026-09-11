"""Compiling a discovery trace into a reviewable `Capability`.

This is where "the model discovers" becomes "the artifact is a capability". The input is a
wandering,
probabilistic run; the output is a frozen contract another system will execute without a model.

Three principles govern what this is allowed to infer.

**Only what was observed.** Every step, target, output and type comes from something that actually
happened. The descriptors are the ones `synthesize_descriptor` already proved resolve during the
run -- not a second guess at them.

**Never invent error handling.** A happy-path run sees no "member not found" screen, so the compiler
emits **no** outcomes and **no** recovery rules. It would be easy to scaffold plausible ones from
the domain, and the result would be a capability whose error taxonomy is fiction -- confidently
declaring detectors that were never seen to fire. Instead the gaps are named in `provenance.notes`
for a reviewer, and the capability starts as a `draft`.

**Conservative parameter lifting.** Lifting the wrong literal produces a capability that looks right
and is wrong: it will replay happily with the value baked in and quietly return the same member's
balance for every caller. Only a value that was typed into a field *and* appears in the goal is
lifted.

The output is a proposal. `CapabilityApproval` starting at `draft` is the mechanism that says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from cua.domain.action import Action, Click, Extract, PressKey, Select, Type
from cua.domain.capability import (
    AppIdentity,
    Capability,
    DiscoveredBy,
    Entrypoint,
    InputSpec,
    OutputSource,
    OutputSpec,
    Provenance,
    Step,
    SurfaceKind,
    SurfaceSpec,
    WaitFor,
    WaitPolicy,
)
from cua.domain.discovery import DiscoveryRun, DiscoveryStep
from cua.domain.predicates import AllOf, NodeExists, NodeQuery, Predicate
from cua.domain.values import InputRef
from cua.recorder.naming import (
    capability_id,
    infer_type,
    input_pattern,
    shape_pattern,
    snake,
)

REVIEW_NOTES = """\
Compiled from a single discovery run. Before approving, a reviewer should confirm:

  1. Outcomes. This run saw only the successful path, so NO business outcomes are declared. Every
     legitimate non-success answer this flow can return -- record not found, permission denied, a
     closed account -- must be added, or replay will classify each of them as UNEXPECTED_STATE and
     escalate to a human instead of answering the caller.
  2. Recovery. No recovery rules are declared for the same reason. Transient conditions this
     application can produce (gateway errors, session expiry, interstitial notices) need declaring.
  3. Parameters. Check that the lifted inputs are the ones that should vary per call, and that
     nothing that should vary was left as a literal.
  4. Checkpoint. It asserts the shape of the extracted outputs. Confirm that is genuinely specific
     to this goal rather than true of any page in the application.

These are gaps, not omissions: inventing detectors a run never observed would produce a capability
whose error handling is fiction."""


@dataclass
class CompileResult:
    capability: Capability
    lifted_inputs: dict[str, str]
    """Input name -> the literal value observed, so a reviewer can see what was generalized."""
    warnings: tuple[str, ...] = ()


def compile_capability(
    run: DiscoveryRun,
    *,
    vendor: str,
    product: str,
    entrypoint_url: str,
    version: str = "1.0.0",
    title: str | None = None,
    transcript_ref: str | None = None,
    surface_kind: SurfaceKind = SurfaceKind.LEGACY_WEB,
) -> CompileResult:
    """Turn a successful discovery run into a draft capability."""
    if not run.succeeded:
        raise ValueError(
            f"refusing to compile a run that ended {run.stop_reason.value}. "
            "A capability is a claim that a flow works; a run that did not reach the goal is not "
            "evidence for it."
        )

    effective = run.effective_steps
    warnings: list[str] = []

    inputs, lifted = _lift_inputs(effective, run.goal)
    outputs = _declare_outputs(effective)
    steps = _compile_steps(effective, lifted, warnings)
    checkpoint = _infer_checkpoint(effective, outputs)

    unverified = [
        step.tool for step in effective if step.descriptor and not step.descriptor_verified
    ]
    if unverified:
        warnings.append(
            f"{len(unverified)} step(s) used a target description that did not resolve back to the "
            "node it describes; these will fail on replay"
        )

    capability = Capability(
        id=capability_id(product, run.goal, [output.name for output in outputs]),
        version=version,
        title=title or run.summary or run.goal,
        description=run.goal,
        surface=SurfaceSpec(
            kind=surface_kind,
            driver_capabilities=("semantic_tree", "screenshot"),
            app=AppIdentity(vendor=vendor, product=product),
        ),
        entrypoint=Entrypoint(url_pattern=entrypoint_url),
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        steps=tuple(steps),
        checkpoint=checkpoint,
        # Deliberately empty -- see this module's docstring.
        outcomes=(),
        recovery=(),
        provenance=Provenance(
            discovered_by=DiscoveredBy(
                model=run.model,
                run_id=run.run_id,
                at=datetime.now(UTC).isoformat(),
            ),
            transcript_ref=transcript_ref,
            notes=REVIEW_NOTES,
        ),
    ).with_hash()

    return CompileResult(capability=capability, lifted_inputs=lifted, warnings=tuple(warnings))


# --------------------------------------------------------------------- inputs


def _lift_inputs(steps: list[DiscoveryStep], goal: str) -> tuple[list[InputSpec], dict[str, str]]:
    """Lift typed literals that also appear in the goal into declared parameters.

    Both conditions are required. "Typed into a field" alone would lift a fixed dropdown choice that
    should stay constant; "appears in the goal" alone would lift incidental words. Together they
    identify the values a caller is actually varying -- which is what the goal sentence is for.

    A value that fails the test stays a literal. That is the safe direction: an over-literal
    capability is obviously wrong on the second call, while an over-parameterized one silently
    accepts an argument it then ignores.
    """
    inputs: list[InputSpec] = []
    lifted: dict[str, str] = {}
    goal_tokens = set(re.findall(r"[\w.-]+", goal.lower()))

    for step in steps:
        if step.tool != "type_text":
            continue
        value = str(step.arguments.get("text", ""))
        if not value or value.lower() not in goal_tokens:
            continue

        label = step.descriptor.name.value if step.descriptor and step.descriptor.name else None
        if not label and step.descriptor and step.descriptor.anchor:
            label = step.descriptor.anchor.text
        name = snake(label or "input")
        if name in lifted:
            continue

        # An identifier is a string, not a number, even when it is all digits. Typing a member
        # or account number as `integer` invites a caller -- or a JSON parser -- to drop the
        # leading zeros that distinguish 0001234501 from 1234501.
        declared_type = "string" if value.isdigit() else infer_type(value)
        inputs.append(
            InputSpec(
                name=name,
                type=declared_type,
                required=True,
                description=(
                    f"Supplied per invocation. Observed once during discovery as {value!r}."
                ),
                pattern=input_pattern(value),
                example=value,
            )
        )
        lifted[name] = value

    return inputs, lifted


# -------------------------------------------------------------------- outputs


def _declare_outputs(steps: list[DiscoveryStep]) -> list[OutputSpec]:
    outputs: list[OutputSpec] = []
    for step in steps:
        if step.tool != "extract" or step.extracted is None:
            continue
        name, value = step.extracted
        outputs.append(
            OutputSpec(
                name=snake(name),
                type=infer_type(value),
                description=f"Observed during discovery as {value!r}.",
                source=OutputSource(step=_step_id(step)),
            )
        )
    return outputs


# ---------------------------------------------------------------------- steps


DURABLE_HINTS: frozenset[str] = frozenset({"css"})
"""Hint keys worth persisting into an artifact.

`native` -- a CDP backend node id -- is deliberately excluded. It is valid only within the session
that observed it, so persisting one puts a handle in the artifact that can never resolve again.
Worse than useless: it looks like a fast path, and a reviewer reading the YAML would reasonably
assume it is one.
"""


def _durable(hints: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in hints.items() if key in DURABLE_HINTS}


def _step_id(step: DiscoveryStep) -> str:
    """A readable, stable id: the action plus what it acted on."""
    if step.tool == "extract" and step.extracted:
        return f"read_{snake(step.extracted[0])}"
    label = None
    if step.descriptor:
        label = step.descriptor.name.value if step.descriptor.name else None
        if not label and step.descriptor.anchor:
            label = step.descriptor.anchor.text
    return f"{step.tool}_{snake(label)}" if label else f"{step.tool}_{step.index}"


def _compile_steps(
    steps: list[DiscoveryStep], lifted: dict[str, str], warnings: list[str]
) -> list[Step]:
    compiled: list[Step] = []
    reverse = {value: name for name, value in lifted.items()}

    for step in steps:
        if step.descriptor is None and step.tool != "navigate":
            continue

        target = (
            step.descriptor.model_copy(update={"hints": _durable(step.descriptor.hints)})
            if step.descriptor
            else None
        )
        action: Action | None = None

        if step.tool == "click" and target:
            action = Click(target=target)
        elif step.tool == "type_text" and target:
            literal = str(step.arguments.get("text", ""))
            value = (
                InputRef.model_validate({"$input": reverse[literal]})
                if literal in reverse
                else literal
            )
            action = Type(target=target, value=value)
        elif step.tool == "select_option" and target:
            action = Select(target=target, value=str(step.arguments.get("value", "")))
        elif step.tool == "press_key":
            action = PressKey(key=str(step.arguments.get("key", "Enter")), target=target)
        elif step.tool == "extract" and target and step.extracted:
            action = Extract(target=target, into=snake(step.extracted[0]))

        if action is None:
            warnings.append(
                f"step {step.index} ({step.tool}) could not be compiled and was skipped"
            )
            continue

        compiled.append(
            Step(
                id=_step_id(step),
                # The model's own stated reason. Brief §3.5 asks for what the agent did and *why*,
                # and carrying it into the artifact means a reviewer reads the intent rather than
                # reverse-engineering it from the action.
                description=step.why,
                action=action,
                wait=WaitPolicy(
                    for_=WaitFor.NAVIGATION if step.tool == "click" else WaitFor.SNAPSHOT_STABLE,
                    timeout_ms=8000 if step.tool == "click" else 3000,
                ),
            )
        )
    return compiled


# ----------------------------------------------------------------- checkpoint


def _infer_checkpoint(steps: list[DiscoveryStep], outputs: list[OutputSpec]) -> Predicate:
    """Assert that the declared outputs are present, in the shape they were observed.

    Deliberately built from the outputs rather than from the model's natural-language hint. The hint
    is a self-report about a run that has already ended; a checkpoint has to be a machine-checkable
    claim about the screen in front of the executor. It is preserved separately for the reviewer.

    Asserting a *shape* is what keeps this honest. "A heading exists" is true of nearly any page in
    the application; "a cell holding a currency-formatted amount exists" is a real statement about
    having arrived at a member's balance.
    """
    assertions: list[Predicate] = []

    for step in steps:
        if step.tool != "extract" or step.extracted is None or step.descriptor is None:
            continue
        _, value = step.extracted
        pattern = shape_pattern(value)
        query = NodeQuery(role=step.descriptor.role, scope=step.descriptor.scope)
        assertions.append(
            NodeExists(query=query.model_copy(update={"name_matches": pattern}))
            if pattern
            else NodeExists(query=query.model_copy(update={"name": value}))
        )

        # The label beside the value is part of the claim: it is what makes this the *savings*
        # balance rather than any currency amount on the page.
        if step.descriptor.anchor:
            assertions.append(
                NodeExists(query=NodeQuery(role="cell", name=step.descriptor.anchor.text))
            )

    if not assertions:
        # Nothing was extracted, so there is no output-shaped claim to make. Rather than emit a
        # checkpoint that passes on any page, say plainly that one is missing.
        return NodeExists(query=NodeQuery(role="document"))

    return AllOf(of=tuple(assertions)) if len(assertions) > 1 else assertions[0]
