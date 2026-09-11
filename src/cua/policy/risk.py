"""Classifying how much damage an action could do if it is wrong.

Three independent signals, because each one alone is fooled:

1. **The action type.** A `wait_for` cannot move money. But a `click` could be anything.
2. **The target's semantics.** A control named "Transfer Funds" is dangerous whatever the action
   type says. But a button labelled "Continue" that posts a wire is not, by its name, dangerous at
   all -- which is exactly how this signal is defeated.
3. **The artifact's explicit annotation.** A reviewer marked the step. But an artifact compiled from
   a discovery run has only a heuristic annotation until someone reviews it.

The **highest tier wins**. Combining conservatively means a false positive costs a confirmation
prompt, while a false negative could cost a customer's money. Those are not symmetric, and the
classifier is not tuned as though they were.
"""

from __future__ import annotations

from dataclasses import dataclass

from cua.domain.action import READ_ONLY_ACTIONS, Action, ActionRisk
from cua.domain.snapshot import UiNode
from cua.policy.config import RiskPolicy

_ORDER: tuple[ActionRisk, ...] = (ActionRisk.SAFE, ActionRisk.ELEVATED, ActionRisk.IRREVERSIBLE)


@dataclass(frozen=True)
class RiskAssessment:
    tier: ActionRisk
    signals: tuple[str, ...]
    """Which signals contributed, so a surprising classification can be explained rather than
    argued with."""

    def explain(self) -> str:
        return f"{self.tier.value} ({', '.join(self.signals) or 'default'})"


@dataclass
class RiskClassifier:
    config: RiskPolicy

    def classify(
        self,
        action: Action,
        *,
        target: UiNode | None = None,
        declared: ActionRisk | None = None,
    ) -> RiskAssessment:
        signals: list[str] = []
        tiers: list[ActionRisk] = [self.config.default_tier]

        # Signal 1: the action type.
        if action.type in READ_ONLY_ACTIONS:
            # These cannot change state whatever they are pointed at, so they short-circuit. A
            # read of a "Delete Account" label must not be classified irreversible, or every
            # extraction from a dangerous-looking screen would demand a human.
            return RiskAssessment(ActionRisk.SAFE, ("read_only_action",))

        by_action = self.config.tiers_by_action.get(action.type)
        if by_action is not None:
            tiers.append(by_action)
            if by_action is not ActionRisk.SAFE:
                signals.append(f"action:{action.type}")

        # Signal 2: what the control says it does.
        if target is not None and target.name:
            name = target.name.lower()
            if any(word in name for word in self.config.lexicon.irreversible):
                tiers.append(ActionRisk.IRREVERSIBLE)
                signals.append(f"lexicon:{target.name!r}")
            elif any(word in name for word in self.config.lexicon.elevated):
                tiers.append(ActionRisk.ELEVATED)
                signals.append(f"lexicon:{target.name!r}")

        # Signal 3: what the artifact declares.
        annotated = declared or action.risk
        if annotated is not None:
            tiers.append(annotated)
            if annotated is not ActionRisk.SAFE:
                signals.append(f"declared:{annotated.value}")

        return RiskAssessment(max(tiers, key=_ORDER.index), tuple(signals))
