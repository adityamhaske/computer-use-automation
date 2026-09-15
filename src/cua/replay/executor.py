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

from cua.domain.action import ActionRisk, Assert, Extract, Navigate
from cua.domain.action import WaitFor as WaitForAction
from cua.domain.actor import Actor
from cua.domain.approval import CapabilityApproval
from cua.domain.capability import (
    Capability,
    RecoveryRule,
    RunCapability,
    Step,
    WaitPolicy,
)
from cua.domain.capability import WaitFor as WaitForPolicy
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
from cua.hitl.reanchor import reconcile
from cua.policy.config import ReplayGates
from cua.replay.bind import UnboundReferenceError, bind_action
from cua.replay.classify import classify
from cua.replay.inputs import InputValidationError, validate_inputs
from cua.replay.transforms import UnknownTransformError, apply_transform
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

    replay_gates: ReplayGates = field(default_factory=ReplayGates)
    """Which gates an irreversible capability must clear before it runs unattended.

    Mirrors `replay_gates` in `config/policy.yaml`, which was parsed into a typed model, rendered to
    the operator on the Settings page as live policy, and read by no Python at all -- so turning a
    gate off there changed a displayed value and nothing else. Defaults are both-on, because a
    policy file that fails to load must not be the reason a wire transfer runs unattended.
    """

    max_recovery_attempts_total: int = 6
    """Recovery attempts allowed across the whole run, independent of any one rule's budget.

    Mirrors `limits.max_recovery_attempts_total` in `config/policy.yaml`, which was parsed and read
    by nothing. Defaulted rather than required so an executor built without a policy config is still
    bounded -- an unbounded default is how this kind of cap quietly stops existing.
    """

    lease_epoch: int | None = None
    """The epoch this run is authorized under.

    Left unset, a supervised run adopts the broker's epoch when it starts, so a run resumed after a
    handoff is authorized under the epoch the resume produced rather than a stale one.
    """

    def _duration_exceeded(self, state: _State) -> str | None:
        """Whether this run has outlived the budget its own artifact declares."""
        budget = state.capability.policy.max_duration_ms
        if budget <= 0:
            return None
        elapsed = int((time.monotonic() - state.started) * 1000)
        if elapsed <= budget:
            return None
        return f"run exceeded the declared budget of {budget}ms (elapsed {elapsed}ms)"

    def _epochs(self) -> tuple[int, int | None]:
        """The epoch this run holds, and the lease epoch to check it against.

        The second value is read **fresh at every dispatch**, never cached: noticing that control
        changed hands *since this run was authorized* is the entire purpose. Cache it and the check
        degenerates into comparing a value with itself -- which is what it was before, and why the
        dispatcher could refuse a stale epoch while nothing on the automation path ever handed it
        one.

        Unsupervised runs return `None`: with no lease there is no operator, and therefore no race
        to close. The check is skipped because it is meaningless, not because it is inconvenient.
        """
        held = self.lease_epoch if self.lease_epoch is not None else 1
        if self.broker is None:
            return held, None
        return held, int(self.broker.lease.epoch)

    def _lease_available(self) -> str | None:
        """Why automation may not act right now, or None if it may.

        The epoch check alone is not enough, and assuming it was is what left the race open. An
        epoch catches control changing hands *during* a run; it cannot catch a run that *starts*
        while an operator already holds the session, because such a run would simply adopt the
        operator's epoch and match itself.

        Both questions have to be asked, and they are different: *who holds this session* and
        *has it changed hands since I was authorized*.
        """
        if self.broker is None:
            return None
        lease = self.broker.lease
        if lease.holder is not Actor.AUTOMATION:
            return (
                f"{lease.holder.value} holds session {self.session_id} "
                f"(state: {lease.state.value}) -- automation may not act"
            )
        if lease.expired:
            return f"the automation hold on session {self.session_id} has expired"
        return None

    def run(self, capability: Capability, supplied: dict[str, Any]) -> RunResult:
        """Execute the capability and leave a `run_record.json` behind, whichever way it ends.

        The record is written here rather than at each return path so that a failure, a business
        outcome and an escalation are all as well evidenced as a success. The run you most want a
        record of is the one that did not succeed.
        """
        return self._record(self._execute(capability, supplied))

    def resume(
        self,
        capability: Capability,
        supplied: dict[str, Any],
        *,
        from_index: int,
        prior_outputs: dict[str, str] | None = None,
        prior_recovery_attempts: int = 0,
        run_id: str | None = None,
    ) -> RunResult:
        """Continue a run an operator interrupted, on the session they just handed back.

        Not a variant of `run()` with an offset. Four things differ, and each one is a way a naive
        resume goes wrong:

        **It does not navigate to the entrypoint.** `_execute` opens by loading the capability's
        entry URL, which on a frameset app reloads the shell and discards precisely the state the
        operator produced. Resuming re-enters mid-flow, so the screen in front of us *is* the
        starting condition.

        **It re-anchors before acting.** Where the run stopped is not necessarily where it should
        pick up: the operator may have completed several steps by hand, or left the application
        somewhere this capability cannot act from. `reconcile` answers both questions against the
        live screen, and we fail closed when it cannot -- guessing here means acting on a page
        nobody verified.

        **It adopts the epoch the handoff produced.** A full handoff advances the lease four times,
        so the epoch this run was authorized under is stale by definition and every dispatch would
        be refused with LEASE_LOST. Re-adopting is the one legitimate case for it; `run()`
        deliberately does not, which is what keeps a genuinely stale run failing.

        **It carries the run forward.** Outputs already extracted and recovery attempts already
        spent are passed back in, so a resumed run returns everything it read and cannot refresh a
        bounded retry budget by being resumed.
        """
        if self.broker is not None:
            self.lease_epoch = int(self.broker.lease.epoch)

        state = _State(
            capability=capability,
            run_id=run_id or f"rep-{uuid.uuid4().hex[:10]}",
            started=time.monotonic(),
        )
        try:
            bound = validate_inputs(capability, supplied)
        except InputValidationError as exc:
            return self._record(self._fail(state, FailureCode.INPUT_VALIDATION_FAILED, str(exc)))

        plan = reconcile(capability, self.dispatcher.observe(), inputs=bound, from_index=from_index)
        self.evidence.emit(
            EventType.NOTE,
            actor=Actor.AUTOMATION,
            note="re-anchor after handoff",
            resume_index=plan.resume_index,
            skipped=list(plan.skipped),
            reason=plan.reason,
        )
        if plan.resume_index is None:
            return self._record(self._fail(state, FailureCode.UNEXPECTED_STATE, plan.reason))

        return self._record(
            self._execute(
                capability,
                supplied,
                start_index=plan.resume_index,
                prior_outputs=prior_outputs,
                prior_recovery_attempts=prior_recovery_attempts,
                run_id=state.run_id,
            )
        )

    def _record(self, result: RunResult) -> RunResult:
        """Leave a `run_record.json` behind, whichever way the run ended."""
        try:
            write_run_record(
                build_run_record(self.evidence.run_dir, kind=RunKind.REPLAY, result=result),
                self.evidence.run_dir,
                redactor=self.evidence.redactor,
                sensitive_keys=self.evidence.sensitive_keys,
            )
        except OSError as exc:  # pragma: no cover -- evidence must not mask the run's own outcome
            self.evidence.emit(
                EventType.NOTE, actor=Actor.AUTOMATION, note=f"run record not written: {exc}"
            )
        return result

    def _execute(
        self,
        capability: Capability,
        supplied: dict[str, Any],
        *,
        start_index: int = 0,
        prior_outputs: dict[str, str] | None = None,
        prior_recovery_attempts: int = 0,
        run_id: str | None = None,
    ) -> RunResult:
        run_id = run_id or f"rep-{uuid.uuid4().hex[:10]}"
        started = time.monotonic()

        if self.lease_epoch is None and self.broker is not None:
            self.lease_epoch = int(self.broker.lease.epoch)

        # Tell the evidence sinks what this capability considers sensitive, before the first write.
        # The bus has always accepted this and nothing ever supplied it, so `sensitive: true` masked
        # nothing anywhere -- `config/policy.yaml` promises otherwise.
        self.evidence.sensitive_keys = capability.sensitive_names

        self.evidence.emit(
            EventType.RUN_START,
            actor=Actor.AUTOMATION,
            kind="replay",
            capability=capability.ref,
            content_hash=capability.content_hash,
            inputs=supplied,
        )

        state = _State(capability=capability, run_id=run_id, started=started)
        state.outputs.update(prior_outputs or {})
        state.recovery_attempts = prior_recovery_attempts

        if (unavailable := self._lease_available()) is not None:
            return self._fail(state, FailureCode.LEASE_LOST, unavailable)

        # A capability declaring more steps than its own budget allows is a contract violation, and
        # the cheapest place to catch it is before the browser moves. `policy.max_steps` and
        # `max_duration_ms` sat in the schema being read by nothing: a reviewer opening the artifact
        # reasonably concluded replay was bounded, and it was not.
        if len(capability.steps) > capability.policy.max_steps:
            return self._fail(
                state,
                FailureCode.POLICY_DENIED,
                f"{capability.ref} declares {len(capability.steps)} steps but its own policy "
                f"allows {capability.policy.max_steps}",
            )

        try:
            state.inputs = validate_inputs(capability, supplied)
        except InputValidationError as exc:
            return self._fail(state, FailureCode.INPUT_VALIDATION_FAILED, str(exc))

        if (gate := self._approval_gate(capability)) is not None:
            return self._fail(state, FailureCode.POLICY_DENIED, gate)

        # Skipped on a resume: the operator's screen *is* the starting condition, and reloading
        # the entrypoint would discard the state they just produced by hand.
        if start_index == 0 and capability.entrypoint.url_pattern.startswith(
            ("http://", "https://")
        ):
            held, current = self._epochs()
            outcome = self.dispatcher.execute(
                Navigate(url=capability.entrypoint.url_pattern),
                snapshot=self.dispatcher.observe(),
                actor=Actor.AUTOMATION,
                session_id=self.session_id,
                lease_epoch=held,
                expected_epoch=current,
                capability=capability,
            )
            if not outcome.ok:
                return self._fail(
                    state,
                    outcome.failure_code or FailureCode.SURFACE_UNAVAILABLE,
                    f"could not reach the entrypoint: {outcome.message}",
                )

        # `start_index == len(steps)` is a legitimate answer from `reconcile`: the operator
        # finished the flow by hand, so there is nothing left to do but verify it.
        for index, step in enumerate(capability.steps[start_index:], start=start_index):
            result = self._run_step(state, step, index)
            if result is not None:
                return result

        return self._verify_checkpoint(state)

    # ------------------------------------------------------------- the step

    def _run_step(self, state: _State, step: Step, index: int) -> RunResult | None:
        """Execute one step. Returns a terminal result, or None to continue."""
        state.step_index = index
        attempts = 0

        while True:
            # Checked inside the loop, not around it: a recovery rule that keeps clearing and
            # re-detecting is exactly the shape that consumes wall-clock without consuming steps.
            if (overrun := self._duration_exceeded(state)) is not None:
                return self._fail(state, FailureCode.TIMEOUT, overrun, step=step)

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

        # `assert` and `wait_for` change nothing on the surface, so they are evaluated here against
        # the snapshot rather than dispatched. The driver already documented that intent -- and then
        # returned "not a driver-level action" for both, so a step using either hard-failed the run
        # despite being a member of the closed action space and named in the reference
        # artifact's own `allowed_actions`. Nothing reaches a surface, so nothing needs authorizing.
        if isinstance(action, Assert):
            if not action.that.evaluate(snapshot, state.inputs):
                return self._fail(
                    state,
                    FailureCode.PRECONDITION_FAILED,
                    f"assertion did not hold: {action.that.describe()}",
                    step=step,
                    snapshot=snapshot,
                    expected=action.that.describe(),
                )
            state.steps_executed = index + 1
            return None

        if isinstance(action, WaitForAction):
            waited = wait_for(
                WaitPolicy(
                    for_=WaitForPolicy.PREDICATE,
                    until=action.until,
                    timeout_ms=action.timeout_ms,
                ),
                observe=self.dispatcher.observe,
                inputs=state.inputs,
            )
            if not waited.satisfied:
                return self._fail(
                    state,
                    FailureCode.TIMEOUT,
                    f"condition did not hold within {action.timeout_ms}ms: "
                    f"{action.until.describe()}",
                    step=step,
                    snapshot=waited.snapshot,
                    expected=action.until.describe(),
                )
            state.steps_executed = index + 1
            return None

        held, current = self._epochs()
        outcome = self.dispatcher.execute(
            action,
            snapshot=snapshot,
            actor=Actor.AUTOMATION,
            session_id=self.session_id,
            lease_epoch=held,
            expected_epoch=current,
            capability=state.capability,
            declared_risk=step.risk,
        )
        state.steps_executed = index + 1

        if outcome.resolution is not None:
            state.strategies[outcome.resolution.strategy.value] += 1
            state.resolutions += 1
            if outcome.drifted:
                state.drifted += 1

        # `extract` reads from the snapshot the executor already holds; it never reaches a driver,
        # so the dispatcher refuses it and its `status` says nothing about whether the read worked.
        # That check is skipped for extract -- and *only* that check. It used to return here
        # outright, which also skipped this step's own `wait` and `postcondition`: assertions the
        # schema accepts and the engine silently never evaluated.
        extracted = isinstance(action, Extract) and outcome.resolution is not None
        if isinstance(action, Extract) and outcome.resolution is not None:
            node = outcome.resolution.node
            value = node.value or node.name or ""
            if not value.strip():
                # A node that resolved but reads as nothing is not a successful extraction. Storing
                # "" here let a run report SUCCESS with an empty output, which is the worst kind of
                # failure: confident, and wrong in a direction nobody checks.
                return self._fail(
                    state,
                    FailureCode.ACTION_FAILED,
                    f"step {step.id!r} resolved {node.node_id!r} for output "
                    f"{action.into!r}, but it has no readable value",
                    step=step,
                    snapshot=snapshot,
                    resolution=outcome.resolution,
                )
            if action.transform:
                try:
                    value = apply_transform(action.transform, value)
                except UnknownTransformError as exc:
                    return self._fail(
                        state, FailureCode.ACTION_FAILED, str(exc), step=step, snapshot=snapshot
                    )
            state.outputs[action.into] = value

        if not extracted and outcome.status is not DispatchStatus.OK:
            return self._fail(
                state,
                outcome.failure_code or FailureCode.ACTION_FAILED,
                outcome.message,
                step=step,
                snapshot=snapshot,
                resolution=outcome.resolution,
                resolution_debug=outcome.resolution_debug,
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

        # Per-step budgets bound each rule; this bounds the run. Without it, five steps at three
        # attempts each is fifteen recovery cycles against a configured total of six -- every
        # individual rule inside its budget, and the run as a whole far outside it.
        if state.recovery_attempts > self.max_recovery_attempts_total:
            return self._fail(
                state,
                FailureCode.RECOVERY_EXHAUSTED,
                f"this run has attempted recovery {state.recovery_attempts} time(s), over the "
                f"configured total of {self.max_recovery_attempts_total}",
                snapshot=snapshot,
            )

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
            held, current = self._epochs()
            outcome = self.dispatcher.execute(
                remedy,
                snapshot=self.dispatcher.observe(),
                actor=Actor.AUTOMATION,
                session_id=self.session_id,
                lease_epoch=held,
                expected_epoch=current,
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
        resolution_debug: ResolutionDebug | None = None,
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
            # Prefer the debug the dispatcher built: on TARGET_NOT_FOUND and TARGET_AMBIGUOUS --
            # the two failures this field exists for -- there is no `Resolution` to derive one from,
            # because nothing resolved. Deriving only from a *successful* resolution meant the field
            # was empty exactly when it was needed.
            resolution=(
                resolution_debug
                if resolution_debug is not None
                else ResolutionDebug(
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
                outputs=dict(state.outputs),
                # The same `FailureDetail` a FAILED run returns. Escalating used to drop it, so the
                # path that most needs debugging -- a person is being asked to look at this -- was
                # the one that came back with free text and no failure code, no step, no
                # expected/observed. `_shape_matches_status` only forbids an error on SUCCESS.
                error=detail,
                intervention=InterventionRef(
                    intervention_id=intervention_id,
                    reason=message,
                    step_id=step.id if step else None,
                    step_index=state.step_index,
                    outputs_so_far=dict(state.outputs),
                ),
                **common,
            )
        return RunResult(status=RunStatus.FAILED, error=detail, **common)

    # ------------------------------------------------------------- approval

    def _approval_gate(self, capability: Capability) -> str | None:
        """Two independent gates on an irreversible capability, or it does not run unattended."""
        if capability.max_risk is not ActionRisk.IRREVERSIBLE:
            return None
        if self.replay_gates.irreversible_requires_caller_optin and not self.allow_irreversible:
            return (
                "this capability performs an irreversible action and the caller did not opt in "
                "(allow_irreversible)"
            )
        if self.replay_gates.irreversible_requires_approval and (
            self.approval is None
            or not self.approval.permits_unattended_replay(content_hash=capability.content_hash)
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
    step_index: int | None = None
    """Position of the step currently in flight.

    `steps_executed` counts *completed* dispatches, so it cannot answer "which step stopped" -- the
    two differ by one exactly when it matters. A resume needs the failing index, not the count.
    """
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
