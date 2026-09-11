"""The deterministic replay engine -- the production execution path.

No model, no improvisation. Assert what must be true, resolve, authorize, dispatch, wait, assert
what must now be true, classify anything unexpected, and return a structured result.

This module may not import `cua.agent` or any model client. That is enforced by the
`no-llm-in-replay` import contract *and* by a test that replays a real capability with the client
patched to raise -- because an import rule alone would not catch a lazy import inside a function.

Determinism here is a property of what this module refuses to do:

- no clock reads that influence a decision (only timeouts, which produce a `TIMEOUT` *result*)
- no randomness, and no iteration over unordered collections
- no vision rung in the resolver
- recovery bounded by counts declared in the artifact, never by elapsed time

Same artifact, same inputs, same application state produces the same decisions.
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from cua.domain.action import ActionRisk, Navigate
from cua.domain.actor import Actor
from cua.domain.approval import CapabilityApproval
from cua.domain.capability import Capability, RecoveryRule, RunCapability, Step
from cua.domain.result import (
    BusinessOutcome,
    FailureCode,
    FailureDetail,
    InterventionRef,
    ObservationClass,
    ResolutionDebug,
    RunResult,
    RunStatus,
)
from cua.domain.run_record import RunKind
from cua.domain.snapshot import UiSnapshot
from cua.evidence.bus import EventType, EvidenceBus
from cua.evidence.record import build_run_record, write_run_record
from cua.replay.bind import UnboundReferenceError, bind_action
from cua.replay.classify import classify
from cua.replay.inputs import InputValidationError, validate_inputs
from cua.replay.wait import wait_for
from cua.runtime.capture import FailureCapture
from cua.runtime.dispatcher import Dispatcher, DispatchStatus


@dataclass
class ReplayExecutor:
    """Executes one capability against one live surface."""

    dispatcher: Dispatcher
    evidence: EvidenceBus
    capture: FailureCapture | None = None
    approval: CapabilityApproval | None = None
    allow_irreversible: bool = False
    """The caller's explicit opt-in. Required *in addition to* an approved capability before an
    irreversible step runs unattended -- two independent gates, because either alone is one
    accident away from a wire transfer."""

    broker: Any = None
    """An optional `SessionBroker`. When present, an escalating failure opens a real intervention
    and pauses the lease rather than merely returning NEEDS_HUMAN.

    Optional because a replay invoked by an agent with no operator on duty still has to terminate
    cleanly -- it reports NEEDS_HUMAN and stops, which is the honest answer when there is nobody to
    hand the session to. Typed loosely: `cua.replay` sits above `cua.hitl` in the layering and calls
    it without depending on its type."""

    session_id: str = field(default_factory=lambda: f"replay-{uuid.uuid4().hex[:8]}")
    lease_epoch: int = 1

    def run(self, capability: Capability, supplied: dict[str, Any]) -> RunResult:
        """Execute the capability and leave a `run_record.json` behind, whichever way it ends.

        The record is written here rather than at each return path so that a failure, a business
        outcome and an escalation are all as well evidenced as a success. The run you most want a
        record of is the one that did not succeed.
        """
        result = self._execute(capability, supplied)
        try:
            write_run_record(
                build_run_record(self.evidence.run_dir, kind=RunKind.REPLAY, result=result),
                self.evidence.run_dir,
            )
        except OSError as exc:  # pragma: no cover -- evidence must not mask the run's own outcome
            self.evidence.emit(
                EventType.NOTE, actor=Actor.AUTOMATION, note=f"run record not written: {exc}"
            )
        return result

    def _execute(self, capability: Capability, supplied: dict[str, Any]) -> RunResult:
        run_id = f"rep-{uuid.uuid4().hex[:10]}"
        started = time.monotonic()

        self.evidence.emit(
            EventType.RUN_START,
            actor=Actor.AUTOMATION,
            kind="replay",
            capability=capability.ref,
            content_hash=capability.content_hash,
            inputs=supplied,
        )

        state = _State(capability=capability, run_id=run_id, started=started)

        try:
            state.inputs = validate_inputs(capability, supplied)
        except InputValidationError as exc:
            return self._fail(state, FailureCode.INPUT_VALIDATION_FAILED, str(exc))

        if (gate := self._approval_gate(capability)) is not None:
            return self._fail(state, FailureCode.POLICY_DENIED, gate)

        if capability.entrypoint.url_pattern.startswith(("http://", "https://")):
            outcome = self.dispatcher.execute(
                Navigate(url=capability.entrypoint.url_pattern),
                snapshot=self.dispatcher.observe(),
                actor=Actor.AUTOMATION,
                session_id=self.session_id,
                lease_epoch=self.lease_epoch,
                capability=capability,
            )
            if not outcome.ok:
                return self._fail(
                    state,
                    outcome.failure_code or FailureCode.SURFACE_UNAVAILABLE,
                    f"could not reach the entrypoint: {outcome.message}",
                )

        for index, step in enumerate(capability.steps):
            result = self._run_step(state, step, index)
            if result is not None:
                return result

        return self._verify_checkpoint(state)

    # ------------------------------------------------------------- the step

    def _run_step(self, state: _State, step: Step, index: int) -> RunResult | None:
        """Execute one step. Returns a terminal result, or None to continue."""
        attempts = 0

        while True:
            snapshot = self.dispatcher.observe()

            verdict = classify(
                snapshot, capability=state.capability, inputs=state.inputs, step=step
            )
            self.evidence.emit(
                EventType.CLASSIFY,
                actor=Actor.AUTOMATION,
                step=step.id,
                observation=verdict.observation.value,
                reason=verdict.reason,
            )

            if verdict.observation is ObservationClass.BUSINESS_OUTCOME:
                assert verdict.outcome is not None
                return self._business_outcome(state, verdict.outcome, snapshot)

            if verdict.observation is ObservationClass.RECOVERABLE:
                assert verdict.recovery is not None
                attempts += 1
                recovered = self._recover(state, verdict.recovery, attempts, snapshot)
                if recovered is not None:
                    return recovered
                continue

            if verdict.observation is ObservationClass.UNEXPECTED_STATE:
                return self._fail(
                    state,
                    FailureCode.UNEXPECTED_STATE,
                    verdict.reason,
                    step=step,
                    snapshot=snapshot,
                    expected=step.precondition.describe() if step.precondition else "",
                )

            return self._dispatch_step(state, step, index, snapshot)

    def _dispatch_step(
        self, state: _State, step: Step, index: int, snapshot: UiSnapshot
    ) -> RunResult | None:
        # Resolve {$input}, {$output} and {$secret} references into values, here and nowhere
        # earlier. A secret exists only for the duration of this dispatch, so it cannot reach a
        # snapshot, a trace, or a screenshot -- none of which are produced from inside it.
        try:
            action = bind_action(
                step.action,
                inputs=state.inputs,
                outputs=state.outputs,
                secrets=self.dispatcher.secrets,
            )
        except UnboundReferenceError as exc:
            return self._fail(
                state, FailureCode.INPUT_VALIDATION_FAILED, str(exc), step=step, snapshot=snapshot
            )

        outcome = self.dispatcher.execute(
            action,
            snapshot=snapshot,
            actor=Actor.AUTOMATION,
            session_id=self.session_id,
            lease_epoch=self.lease_epoch,
            capability=state.capability,
            declared_risk=step.risk,
        )
        state.steps_executed = index + 1

        if outcome.resolution is not None:
            state.strategies[outcome.resolution.strategy.value] += 1
            state.resolutions += 1
            if outcome.drifted:
                state.drifted += 1

        # `extract` never reaches a driver: it reads from the snapshot the executor already has.
        if action.type == "extract" and outcome.resolution is not None:
            node = outcome.resolution.node
            state.outputs[action.into] = node.value or node.name or ""
            return None

        if outcome.status is not DispatchStatus.OK:
            return self._fail(
                state,
                outcome.failure_code or FailureCode.ACTION_FAILED,
                outcome.message,
                step=step,
                snapshot=snapshot,
                resolution=outcome.resolution,
            )

        waited = wait_for(
            step.wait,
            observe=self.dispatcher.observe,
            inputs=state.inputs,
            before=snapshot,
        )
        if not waited.satisfied:
            return self._fail(
                state, FailureCode.TIMEOUT, waited.reason, step=step, snapshot=waited.snapshot
            )

        if step.postcondition is not None and not step.postcondition.evaluate(
            waited.snapshot, state.inputs
        ):
            # The action reported success but the screen does not show its effect. On legacy forms
            # this is common and silent: a field that reformats or truncates what was typed looks
            # exactly like a field that accepted it.
            return self._fail(
                state,
                FailureCode.PRECONDITION_FAILED,
                f"postcondition for step {step.id!r} does not hold after the action",
                step=step,
                snapshot=waited.snapshot,
                expected=step.postcondition.describe(),
            )

        return None

    # --------------------------------------------------------------- recovery

    def _recover(
        self, state: _State, rule: RecoveryRule, attempts: int, snapshot: UiSnapshot
    ) -> RunResult | None:
        """Apply a declared remedy. Returns a terminal result if recovery is exhausted."""
        state.recovery_attempts += 1

        if attempts > rule.max_attempts:
            return self._fail(
                state,
                FailureCode.RECOVERY_EXHAUSTED,
                f"recovery rule {rule.id!r} did not clear the condition after "
                f"{rule.max_attempts} attempt(s)",
                snapshot=snapshot,
            )

        self.evidence.emit(
            EventType.RECOVERY,
            actor=Actor.AUTOMATION,
            rule=rule.id,
            attempt=attempts,
            max_attempts=rule.max_attempts,
        )

        for remedy in rule.remedy:
            if isinstance(remedy, RunCapability):
                # Chaining another capability (re-authentication, typically) is designed but not
                # built. Reported honestly rather than skipped silently, which would leave the run
                # looping on an unfixed condition until it exhausted its attempts.
                return self._fail(
                    state,
                    FailureCode.RECOVERY_EXHAUSTED,
                    f"recovery rule {rule.id!r} requires running capability "
                    f"{remedy.capability_id!r}, which is not implemented",
                    snapshot=snapshot,
                )
            outcome = self.dispatcher.execute(
                remedy,
                snapshot=self.dispatcher.observe(),
                actor=Actor.AUTOMATION,
                session_id=self.session_id,
                lease_epoch=self.lease_epoch,
                capability=state.capability,
            )
            if not outcome.ok:
                # A remedy that was refused or failed is not a remedy that did not help. Retrying
                # it would burn the attempt budget against an action that never ran, and report
                # RECOVERY_EXHAUSTED for a condition nothing ever tried to clear.
                return self._fail(
                    state,
                    outcome.failure_code or FailureCode.ACTION_FAILED,
                    f"recovery rule {rule.id!r} could not run its remedy "
                    f"({remedy.type}): {outcome.message}",
                    snapshot=snapshot,
                )
        return None

    # ------------------------------------------------------------ checkpoint

    def _verify_checkpoint(self, state: _State) -> RunResult:
        snapshot = self.dispatcher.observe()
        verdict = classify(
            snapshot,
            capability=state.capability,
            inputs=state.inputs,
            checking_checkpoint=True,
        )

        if verdict.observation is ObservationClass.BUSINESS_OUTCOME:
            assert verdict.outcome is not None
            return self._business_outcome(state, verdict.outcome, snapshot)

        if verdict.observation is not ObservationClass.EXPECTED:
            return self._fail(
                state,
                FailureCode.CHECKPOINT_FAILED,
                verdict.reason,
                snapshot=snapshot,
                expected=state.capability.checkpoint.describe(),
            )

        self.evidence.emit(
            EventType.RUN_END,
            actor=Actor.AUTOMATION,
            status=RunStatus.SUCCESS.value,
            outputs=sorted(state.outputs),
            drift_score=state.drift_score,
        )
        return RunResult(
            status=RunStatus.SUCCESS,
            capability_id=state.capability.id,
            capability_version=state.capability.version,
            run_id=state.run_id,
            outputs=dict(state.outputs),
            steps_executed=state.steps_executed,
            steps_total=len(state.capability.steps),
            duration_ms=state.elapsed_ms,
            drift_score=state.drift_score,
            strategy_mix=dict(state.strategies),
            recovery_attempts=state.recovery_attempts,
            evidence_ref=str(self.evidence.run_dir),
        )

    # --------------------------------------------------------------- results

    def _business_outcome(self, state: _State, outcome: Any, snapshot: UiSnapshot) -> RunResult:
        """A declared, legitimate answer. Exits 0 -- it is not a failure."""
        data = {
            key: (state.inputs.get(value.input_name, "") if hasattr(value, "input_name") else value)
            for key, value in outcome.returns.items()
        }
        self.evidence.emit(
            EventType.RUN_END,
            actor=Actor.AUTOMATION,
            status=RunStatus.BUSINESS_OUTCOME.value,
            outcome=outcome.code,
        )
        return RunResult(
            status=RunStatus.BUSINESS_OUTCOME,
            capability_id=state.capability.id,
            capability_version=state.capability.version,
            run_id=state.run_id,
            outcome=BusinessOutcome(code=outcome.code, data=data),
            steps_executed=state.steps_executed,
            steps_total=len(state.capability.steps),
            duration_ms=state.elapsed_ms,
            drift_score=state.drift_score,
            strategy_mix=dict(state.strategies),
            evidence_ref=str(self.evidence.run_dir),
        )

    def _fail(
        self,
        state: _State,
        code: FailureCode,
        message: str,
        *,
        step: Step | None = None,
        snapshot: UiSnapshot | None = None,
        expected: str = "",
        resolution: Any = None,
    ) -> RunResult:
        """Terminate. Escalates rather than failing when the capability says a human decides."""
        capture_ref = None
        if snapshot is not None and self.capture is not None:
            capture_ref = self.capture.capture(
                snapshot, label=code.value, capability=state.capability, reason=message
            )

        detail = FailureDetail(
            code=code,
            message=message,
            step_id=step.id if step else None,
            expected=expected or None,
            observed=(snapshot.url if snapshot else None),
            resolution=(
                ResolutionDebug(
                    target_description=resolution.node.role,
                    strategies_tried=(resolution.strategy,),
                    candidates_considered=resolution.candidates_considered,
                    ambiguity_score=resolution.ambiguity,
                )
                if resolution is not None
                else None
            ),
        )

        escalates = (
            code in state.capability.escalation.triggers
            and state.capability.escalation.policy.value == "pause_and_request_human"
        )

        intervention_id = f"int-{uuid.uuid4().hex[:8]}"
        if escalates and self.broker is not None:
            # Open a real intervention and pause the lease. From here the session belongs to
            # nobody until an operator claims it -- so any automation action still in flight is
            # refused rather than landing on a page a human is about to work in.
            request = self.broker.escalate(
                run_id=state.run_id,
                capability_ref=state.capability.ref,
                goal=state.capability.description or state.capability.title,
                reason=message,
                step_id=step.id if step else None,
                failure_code=code.value,
                snapshot=snapshot,
                snapshot_ref=capture_ref.snapshot_ref if capture_ref else None,
                screenshot_ref=capture_ref.screenshot_ref if capture_ref else None,
            )
            intervention_id = request.intervention_id

        self.evidence.emit(
            EventType.ESCALATE if escalates else EventType.RUN_END,
            actor=Actor.AUTOMATION,
            status=(RunStatus.NEEDS_HUMAN if escalates else RunStatus.FAILED).value,
            code=code.value,
            step=step.id if step else None,
            reason=message,
            snapshot_ref=capture_ref.snapshot_ref if capture_ref else None,
            screenshot_ref=capture_ref.screenshot_ref if capture_ref else None,
        )

        common = {
            "capability_id": state.capability.id,
            "capability_version": state.capability.version,
            "run_id": state.run_id,
            "steps_executed": state.steps_executed,
            "steps_total": len(state.capability.steps),
            "duration_ms": state.elapsed_ms,
            "drift_score": state.drift_score,
            "strategy_mix": dict(state.strategies),
            "recovery_attempts": state.recovery_attempts,
            "evidence_ref": str(self.evidence.run_dir),
        }

        if escalates:
            return RunResult(
                status=RunStatus.NEEDS_HUMAN,
                intervention=InterventionRef(
                    intervention_id=intervention_id,
                    reason=message,
                    step_id=step.id if step else None,
                ),
                **common,
            )
        return RunResult(status=RunStatus.FAILED, error=detail, **common)

    # ------------------------------------------------------------- approval

    def _approval_gate(self, capability: Capability) -> str | None:
        """Two independent gates on an irreversible capability, or it does not run unattended."""
        if capability.max_risk is not ActionRisk.IRREVERSIBLE:
            return None
        if not self.allow_irreversible:
            return (
                "this capability performs an irreversible action and the caller did not opt in "
                "(allow_irreversible)"
            )
        if self.approval is None or not self.approval.permits_unattended_replay(
            content_hash=capability.content_hash
        ):
            return (
                f"this capability performs an irreversible action and {capability.ref} is not "
                "approved at this exact content hash"
            )
        return None


@dataclass
class _State:
    """Mutable run state, kept out of the executor so the executor stays reusable."""

    capability: Capability
    run_id: str
    started: float
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    steps_executed: int = 0
    resolutions: int = 0
    drifted: int = 0
    recovery_attempts: int = 0
    strategies: Counter[str] = field(default_factory=Counter)

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    @property
    def drift_score(self) -> float:
        return self.drifted / self.resolutions if self.resolutions else 0.0
