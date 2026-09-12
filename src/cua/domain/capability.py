"""The capability artifact -- the centerpiece of this system.

A capability is what an LLM's discovery run compiles down to, what a human reviews and approves, and
what an AI agent invokes in production. It serves three readers with different needs:

    the replay engine   unambiguous executable steps, with explicit success and failure conditions
    a human reviewer    what this does, what it touches, what could go wrong -- without running it
    a calling agent     a typed contract: what to pass, what comes back, what can happen

**Immutable and content-addressed** (ADR 0002). Anything that changes because you *ran* a capability
lives in a sibling document -- `RunRecord`, `CapabilityEvaluation`, `CapabilityApproval`. A
definition that accumulates telemetry stops being reviewable, and "which version produced that run?"
stops being answerable.

See docs/design/artifact-schema.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cua.domain.action import Action, ActionRisk
from cua.domain.predicates import Predicate
from cua.domain.result import FailureCode
from cua.domain.values import ValueExpr, ValueType

SCHEMA_VERSION = "1.0.0"


class _Doc(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


# --------------------------------------------------------------- the contract


class InputSpec(_Doc):
    """A parameter the calling agent supplies per invocation."""

    name: str
    type: ValueType = "string"
    required: bool = True
    description: str = ""
    pattern: str | None = None
    """Validated *before* the browser is touched, so a malformed member id is
    INPUT_VALIDATION_FAILED rather than a confusing mid-flow failure."""
    enum: tuple[str, ...] | None = None
    default: ValueExpr | None = None
    example: str | None = None

    sensitive: bool = False
    """Propagates everywhere: masked in logs, excluded from evidence, blurred in screenshots, and
    stripped from outbound model prompts. Marking an input sensitive is the single declaration that
    makes all of that happen."""

    def json_schema(self) -> dict[str, Any]:
        """This input as JSON Schema -- part of the agent-facing tool contract."""
        mapping = {
            "string": "string",
            "integer": "integer",
            "number": "number",
            "boolean": "boolean",
            "money": "string",
            "date": "string",
            "enum": "string",
        }
        schema: dict[str, Any] = {"type": mapping[self.type]}
        if self.description:
            schema["description"] = self.description
        if self.pattern:
            schema["pattern"] = self.pattern
        if self.enum:
            schema["enum"] = list(self.enum)
        if self.example is not None:
            schema["examples"] = [self.example]
        if self.type in ("money", "date"):
            schema["format"] = self.type
        return schema


class OutputSource(_Doc):
    step: str
    """The step whose `extract` action populates this output."""


class OutputSpec(_Doc):
    name: str
    type: ValueType = "string"
    description: str = ""
    source: OutputSource | None = None
    sensitive: bool = False

    def json_schema(self) -> dict[str, Any]:
        mapping = {
            "string": "string",
            "integer": "integer",
            "number": "number",
            "boolean": "boolean",
            "money": "string",
            "date": "string",
            "enum": "string",
        }
        schema: dict[str, Any] = {"type": mapping[self.type]}
        if self.description:
            schema["description"] = self.description
        if self.type in ("money", "date"):
            schema["format"] = self.type
        return schema


# ----------------------------------------------------------------- the surface


class SurfaceKind(StrEnum):
    WEB = "web"
    LEGACY_WEB = "legacy_web"
    DESKTOP = "desktop"


class AppIdentity(_Doc):
    """The vendor product this capability automates -- NOT the tenant running it.

    Namespacing by product rather than by institution is the precondition for cross-tenant reuse:
    it is what makes one recording applicable to the next two hundred credit unions running the
    same core banking software. See ADR 0005.
    """

    vendor: str
    product: str
    version_range: str | None = None


class SurfaceSpec(_Doc):
    kind: SurfaceKind = SurfaceKind.WEB
    driver_capabilities: tuple[str, ...] = ("semantic_tree",)
    """What a driver must be able to do to execute this -- *not* which library does it.

    Note the absence of "playwright" here. Declaring capabilities rather than an implementation is
    what allows a Windows UIA driver to claim the same artifact.
    """
    app: AppIdentity


class Entrypoint(_Doc):
    url_pattern: str
    """May contain `{base_url}`, supplied per tenant by a `TenantBinding`."""


# ------------------------------------------------------------------- the flow


class WaitFor(StrEnum):
    SNAPSHOT_STABLE = "snapshot_stable"
    NAVIGATION = "navigation"
    PREDICATE = "predicate"


class WaitPolicy(_Doc):
    """How to wait. Never how long to sleep.

    A duration that works on the developer's machine and fails on a loaded CI runner is the worst
    kind of flake, because it looks like a real failure exactly often enough to be ignored.
    """

    for_: WaitFor = Field(default=WaitFor.SNAPSHOT_STABLE, alias="for")
    timeout_ms: int = 5000
    until: Predicate | None = None

    @model_validator(mode="after")
    def _predicate_wait_has_a_predicate(self) -> Self:
        if self.for_ is WaitFor.PREDICATE and self.until is None:
            raise ValueError("wait `for: predicate` requires `until`")
        return self


class Step(_Doc):
    """One action, with what must be true before and after it.

    Both conditions are the point. A step that only acts is a step that assumes the click worked,
    and assuming is what this system exists not to do.
    """

    id: str
    description: str = ""
    action: Action
    precondition: Predicate | None = None
    wait: WaitPolicy = Field(default_factory=WaitPolicy)
    postcondition: Predicate | None = None
    risk: ActionRisk = ActionRisk.SAFE


class Outcome(_Doc):
    """A declared, legitimate result of the business process.

    Declaring these explicitly is what forces the question "is this an answer or a failure?" to be
    settled once, at review time, rather than guessed at runtime by a generic detector that cannot
    tell "no records found" from "the page did not load".
    """

    code: str
    detect: Predicate
    description: str = ""
    terminal: bool = True
    returns: dict[str, ValueExpr] = Field(default_factory=dict)


class Backoff(StrEnum):
    NONE = "none"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


class RecoveryScope(StrEnum):
    ANY_STEP = "any_step"
    NAMED_STEPS = "named_steps"


class RunCapability(_Doc):
    """A remedy that invokes another capability -- typically re-authentication."""

    type: Literal["run_capability"] = "run_capability"
    capability_id: str
    inputs: dict[str, ValueExpr] = Field(default_factory=dict)


Remedy: TypeAlias = Action | RunCapability


class RecoveryRule(_Doc):
    """A bounded, declared response to a known transient condition.

    `max_attempts` is mandatory rather than optional. Unbounded recovery is how an automation turns
    one transient 502 into a thousand retries against a core banking system that is already
    struggling.
    """

    id: str
    detect: Predicate
    remedy: tuple[Remedy, ...]
    max_attempts: int = Field(default=2, ge=1, le=10)
    backoff: Backoff = Backoff.LINEAR
    scope: RecoveryScope = RecoveryScope.ANY_STEP
    steps: tuple[str, ...] = ()
    description: str = ""


class EscalationDisposition(StrEnum):
    PAUSE_AND_REQUEST_HUMAN = "pause_and_request_human"
    FAIL_CLOSED = "fail_closed"


class EscalationPolicy(_Doc):
    triggers: tuple[FailureCode, ...] = (
        FailureCode.TARGET_AMBIGUOUS,
        FailureCode.TARGET_NOT_FOUND,
        FailureCode.CHECKPOINT_FAILED,
        FailureCode.RECOVERY_EXHAUSTED,
        FailureCode.UNEXPECTED_STATE,
    )
    policy: EscalationDisposition = EscalationDisposition.PAUSE_AND_REQUEST_HUMAN
    """Named `triggers` rather than `on`: YAML 1.1 resolves a bare `on` key to the boolean True,
    so an artifact using it would fail to load in a way that reads as a schema bug. The loader also
    disables that coercion (see serde.py), but the field name should not require the fix."""


class CapabilityPolicy(_Doc):
    """Per-capability guardrails, narrowing the global policy. Never widening it."""

    allowed_domains: tuple[str, ...] = ()
    allowed_actions: tuple[str, ...] = ()
    max_steps: int = 40
    max_duration_ms: int = 120_000


# ------------------------------------------------------------- provenance


class DiscoveredBy(_Doc):
    model: str
    run_id: str
    at: str


class Provenance(_Doc):
    """How this frozen thing came to exist. Discovery-time, and therefore immutable.

    `transcript_ref` is a *reference*. §3.2 of the brief requires the artifact be decoupled from the
    raw model transcript, and inlining a transcript would also make the artifact unreviewable.
    """

    discovered_by: DiscoveredBy | None = None
    transcript_ref: str | None = None
    notes: str = ""


# ------------------------------------------------------------- the artifact


class Capability(_Doc):
    """A reusable, versioned, agent-invocable automation."""

    schema_version: str = SCHEMA_VERSION
    id: str
    version: str
    title: str
    description: str = ""
    """Written for the *calling agent*: what this does and when to use it."""

    surface: SurfaceSpec
    entrypoint: Entrypoint

    inputs: tuple[InputSpec, ...] = ()
    outputs: tuple[OutputSpec, ...] = ()

    steps: tuple[Step, ...]
    checkpoint: Predicate
    """Required. A capability without a success condition cannot tell you it worked -- it can only
    tell you it finished, which is not the same thing."""

    outcomes: tuple[Outcome, ...] = ()
    recovery: tuple[RecoveryRule, ...] = ()
    escalation: EscalationPolicy = Field(default_factory=EscalationPolicy)
    policy: CapabilityPolicy = Field(default_factory=CapabilityPolicy)
    provenance: Provenance = Field(default_factory=Provenance)

    content_hash: str = ""
    """sha256 over the canonical form, excluding this field. Verified on load, so a run record can
    name exactly what executed."""

    # ------------------------------------------------------------ validation

    @model_validator(mode="after")
    def _internally_consistent(self) -> Self:
        """Catch the mistakes that would otherwise surface as a confusing mid-replay failure."""
        if not re.fullmatch(r"\d+\.\d+\.\d+", self.version):
            raise ValueError(f"version must be semver, got {self.version!r}")

        step_ids = [s.id for s in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step ids must be unique")

        input_names = {i.name for i in self.inputs}
        output_names = {o.name for o in self.outputs}

        for output in self.outputs:
            if output.source and output.source.step not in step_ids:
                raise ValueError(
                    f"output {output.name!r} sources unknown step {output.source.step!r}"
                )

        for step in self.steps:
            into = getattr(step.action, "into", None)
            if into is not None and into not in output_names:
                raise ValueError(f"step {step.id!r} extracts into undeclared output {into!r}")

        for ref_name in self._input_refs():
            if ref_name not in input_names:
                raise ValueError(f"reference to undeclared input {ref_name!r}")

        codes = [o.code for o in self.outcomes]
        if len(codes) != len(set(codes)):
            raise ValueError("outcome codes must be unique")

        rule_ids = [r.id for r in self.recovery]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("recovery rule ids must be unique")

        return self

    def _input_refs(self) -> set[str]:
        """Every `{$input: ...}` mentioned anywhere, so undeclared ones fail at load."""
        found: set[str] = set()

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if "$input" in node and isinstance(node["$input"], str):
                    found.add(node["$input"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list | tuple):
                for item in node:
                    walk(item)

        walk(self.model_dump(by_alias=True, mode="json"))
        return found

    # ----------------------------------------------------------- identity

    @property
    def ref(self) -> str:
        """`id@version` -- how run records, approvals and evaluations point at this exact thing."""
        return f"{self.id}@{self.version}"

    def canonical_bytes(self) -> bytes:
        """Deterministic serialization, excluding `content_hash`."""
        payload = self.model_dump(by_alias=True, mode="json", exclude={"content_hash"})
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def compute_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()

    def with_hash(self) -> Capability:
        return self.model_copy(update={"content_hash": self.compute_hash()})

    def hash_is_valid(self) -> bool:
        return bool(self.content_hash) and self.content_hash == self.compute_hash()

    # ------------------------------------------------- agent-facing contract

    def tool_schema(self) -> dict[str, Any]:
        """This capability as a callable tool definition.

        Derived from the same `inputs`/`outputs` the executor uses, so **the artifact is the tool
        contract** -- there is no second source of truth to drift out of sync.
        """
        return {
            "name": self.id.replace(".", "_"),
            "description": self.description or self.title,
            "input_schema": {
                "type": "object",
                "properties": {i.name: i.json_schema() for i in self.inputs},
                "required": [i.name for i in self.inputs if i.required],
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {o.name: o.json_schema() for o in self.outputs},
            },
            "outcomes": [
                {"code": o.code, "description": o.description or o.code} for o in self.outcomes
            ],
        }

    def step(self, step_id: str) -> Step | None:
        return next((s for s in self.steps if s.id == step_id), None)

    @property
    def sensitive_names(self) -> frozenset[str]:
        """Input and output names this capability declared `sensitive: true`.

        Lives on the capability because the capability is what declares it. Two places need it --
        masking evidence and blurring screenshot regions -- and deriving it independently in each
        is how one of them ends up not doing it. That is exactly what happened: screenshot blurring
        read these declarations, and nothing told the evidence bus about them, so `sensitive: true`
        blurred a region in an image while the value sat in the clear in the run record.
        """
        return frozenset(
            {spec.name for spec in self.inputs if spec.sensitive}
            | {spec.name for spec in self.outputs if spec.sensitive}
        )

    @property
    def max_risk(self) -> ActionRisk:
        """The riskiest thing this capability does. Drives the approval gate."""
        order = [ActionRisk.SAFE, ActionRisk.ELEVATED, ActionRisk.IRREVERSIBLE]
        return max((s.risk for s in self.steps), key=order.index, default=ActionRisk.SAFE)
