"""One corrective step, asked for only after a deterministic replay has already failed."""

from __future__ import annotations

from dataclasses import dataclass, field

from cua.agent.llm import LlmError, LlmPort
from cua.agent.render import render_snapshot
from cua.agent.tools import TOOLS
from cua.domain.action import Action, ActionRisk, Click, Select, Type
from cua.domain.actor import Actor
from cua.domain.capability import Capability
from cua.domain.result import FailureCode, RunResult, RunStatus
from cua.domain.target import TargetDescriptor
from cua.evidence.bus import EventType, EvidenceBus
from cua.policy.redact import Redactor
from cua.replay.executor import ReplayExecutor
from cua.runtime.dispatcher import Dispatcher
from cua.targeting.synthesize import synthesize_descriptor

# The failures worth asking about: the screen was not the one recorded, or the control could not be
# found on it. These are the cases where a human would look and say "it moved" -- and where a
# semantic correction is a real answer.
#
# Deliberately excluded: input validation (the caller sent bad data and a model cannot fix that),
# lease loss (someone else owns the session), policy refusals (the answer is no, and asking a model
# to find another way is exactly the behaviour a chokepoint exists to prevent), and anything that
# already produced a business outcome.
ASSISTABLE = frozenset(
    {
        FailureCode.TARGET_NOT_FOUND,
        FailureCode.TARGET_AMBIGUOUS,
        FailureCode.PRECONDITION_FAILED,
    }
)

SYSTEM = """You are correcting a single step of an automation that has already failed.

A deterministic replay stopped because it could not find the control it recorded. You are shown the
page as it is now. Choose ONE action that performs the step described, using a control that is
actually present.

Rules:
- exactly one tool call, then stop
- choose a control from the page shown; do not invent a node id
- if nothing on this page can perform the step, call `give_up`
"""


@dataclass
class AssistOutcome:
    """What the assist attempt did, for the caller and the report to be honest about."""

    attempted: bool = False
    succeeded: bool = False
    model_calls: int = 0
    reason: str = ""
    action: str = ""


@dataclass
class AssistedReplay:
    """Deterministic replay, with one policed model-chosen step available on failure.

    Composition, not a subclass or a hook: `ReplayExecutor` is used exactly as any other caller
    uses it, through `run()` and `resume()`. It has no knowledge that this exists, which is what
    keeps `no-llm-in-replay` true rather than merely configured.
    """

    executor: ReplayExecutor
    dispatcher: Dispatcher
    llm: LlmPort
    evidence: EvidenceBus
    redactor: Redactor
    session_id: str = "assist"
    lease_epoch: int = 0

    outcome: AssistOutcome = field(default_factory=AssistOutcome)

    def run(self, capability: Capability, supplied: dict[str, str]) -> RunResult:
        """Replay; on an assistable failure, correct one step and continue."""
        result = self.executor.run(capability, supplied)
        if not self._is_assistable(result):
            return result

        self.outcome.attempted = True
        step_index = self._failed_index(capability, result)
        if step_index is None:
            self.outcome.reason = "could not locate the failing step in the capability"
            return result

        action = self._ask(capability, result)
        if action is None:
            return result

        snapshot = self.dispatcher.observe()
        dispatched = self.dispatcher.execute(
            action,
            snapshot=snapshot,
            actor=Actor.AUTOMATION,
            session_id=self.session_id,
            lease_epoch=self.lease_epoch,
            capability=capability,
        )
        if not dispatched.ok:
            self.outcome.reason = f"corrective action refused: {dispatched.message}"
            self._note("assisted action was refused by policy", detail=dispatched.message)
            return result

        # Back to determinism for everything that remains. The model chose one control; it does
        # not get to drive the rest of the run.
        resumed = self.executor.resume(
            capability,
            supplied,
            from_index=step_index,
            prior_outputs=dict(result.outputs),
        )
        self.outcome.succeeded = resumed.status is RunStatus.SUCCESS
        self.outcome.reason = (
            "corrected and completed" if self.outcome.succeeded else "corrected but still failed"
        )
        return resumed

    # ----------------------------------------------------------------- internals

    def _is_assistable(self, result: RunResult) -> bool:
        if self.outcome.attempted:
            # One attempt per run, counted here rather than requested in a prompt. A model that
            # can be asked again on its own failure is an open-ended loop wearing a bound.
            return False
        if result.status not in {RunStatus.FAILED, RunStatus.NEEDS_HUMAN}:
            return False
        return result.error is not None and result.error.code in ASSISTABLE

    @staticmethod
    def _failed_index(capability: Capability, result: RunResult) -> int | None:
        """Where to resume: the step that failed, found by its id.

        Derived rather than carried on `FailureDetail`, which names the step but not its position.
        Adding a field to a frozen domain model to avoid one lookup would be the wrong trade.
        """
        if result.error is None or not result.error.step_id:
            return None
        for index, step in enumerate(capability.steps):
            if step.id == result.error.step_id:
                return index
        return None

    def _ask(self, capability: Capability, result: RunResult) -> Action | None:
        """One model call, returning one policed action or nothing."""
        snapshot = self.dispatcher.observe()
        step_id = result.error.step_id if result.error else ""
        step = next((s for s in capability.steps if s.id == step_id), None)
        wanted = (step.description or step.id) if step else step_id

        messages = [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": (
                    f"The step that failed: {wanted}\n"
                    f"Why it failed: {result.error.message if result.error else ''}\n\n"
                    f"{render_snapshot(snapshot, redactor=self.redactor)}"
                ),
            },
        ]

        try:
            response = self.llm.complete(messages=messages, tools=TOOLS)
        except LlmError as exc:
            self.outcome.reason = f"model unreachable: {exc}"
            return None

        self.outcome.model_calls += 1
        self.evidence.emit(
            EventType.LLM_CALL,
            actor=Actor.AUTOMATION,
            model=self.llm.model_name,
            purpose="assisted_recovery",
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
        )

        if not response.tool_calls:
            self.outcome.reason = "model proposed nothing"
            return None

        call = response.tool_calls[0]
        node = snapshot.node(str(call.arguments.get("node_id", "")))
        if node is None:
            self.outcome.reason = f"model named a control that is not on the page ({call.name})"
            self._note(self.outcome.reason)
            return None

        descriptor = synthesize_descriptor(node, snapshot).descriptor
        action = self._action_for(call.name, descriptor, call.arguments)
        if action is None:
            self.outcome.reason = f"tool {call.name!r} is not available to assisted recovery"
            self._note(self.outcome.reason)
            return None

        if action.risk is ActionRisk.IRREVERSIBLE:
            # Refused here as well as at the chokepoint. A model choosing a money-moving control
            # on a page it has just been shown is not a case to leave to configuration.
            self.outcome.reason = "refused: assisted recovery may not take an irreversible action"
            self._note(self.outcome.reason)
            return None

        self.outcome.action = f"{call.name} {descriptor.describe()}"
        return action

    @staticmethod
    def _action_for(
        tool: str, target: TargetDescriptor, arguments: dict[str, object]
    ) -> Action | None:
        """The closed set. `navigate` is absent on purpose: a model may not choose a destination."""
        if tool == "click":
            return Click(target=target)
        if tool == "type_text":
            return Type(target=target, value=str(arguments.get("text", "")))
        if tool == "select_option":
            return Select(target=target, value=str(arguments.get("value", "")))
        return None

    def _note(self, note: str, detail: str = "") -> None:
        self.evidence.emit(
            EventType.NOTE, actor=Actor.AUTOMATION, note=note, detail=detail, source="assist"
        )
