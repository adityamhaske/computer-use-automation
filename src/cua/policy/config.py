"""The policy document: what the agent may do, and how risky each thing is.

Loaded from `config/policy.yaml`. Deliberately a *file* rather than code: the set of domains an
automation may touch, and the words that mark an irreversible action, are the kind of thing a
security reviewer should be able to read and change without a deploy.

Every field mirrors the YAML exactly, and `extra="forbid"` means a typo in that file fails loudly at
load instead of silently disabling a guardrail -- a misspelled `irreversable:` key that quietly
emptied the danger lexicon would be the worst possible failure here.
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from cua.domain.action import ActionRisk
from cua.domain.actor import Actor


class _Doc(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Allowlist(_Doc):
    """Where the agent may go. Anything else is NAVIGATION_BLOCKED and is not followed."""

    domains: tuple[str, ...] = ()
    url_patterns: tuple[str, ...] = ()


class ActionPolicy(_Doc):
    allowed: tuple[str, ...] = ()
    human_only: tuple[str, ...] = ()
    """Actions automation may never originate. `raw_input` lives here: it exists so a human
    operator's console input is a policed first-class action, not a bypass."""


class RiskLexicon(_Doc):
    """Words that mark a control as dangerous, matched against its accessible name.

    One of three independent signals. A lexicon alone is fooled by a button labelled "Continue" that
    posts a wire transfer, which is why it is never consulted alone.
    """

    elevated: tuple[str, ...] = ()
    irreversible: tuple[str, ...] = ()


class RiskPolicy(_Doc):
    default_tier: ActionRisk = ActionRisk.SAFE
    tiers_by_action: dict[str, ActionRisk] = Field(default_factory=dict)
    lexicon: RiskLexicon = Field(default_factory=RiskLexicon)


class Disposition(_Doc):
    """What to do with each risk tier. Keyed by actor, because escalation deliberately widens a
    human's authority -- auditably -- rather than disabling the chokepoint."""

    safe: str = "allow"
    elevated: str = "allow"
    irreversible: str = "block_and_escalate"


class ReplayGates(_Doc):
    irreversible_requires_approval: bool = True
    irreversible_requires_caller_optin: bool = True


class Budgets(_Doc):
    max_steps: int = 40
    max_duration_ms: int = 120_000
    max_recovery_attempts_total: int = 6


class RedactionRule(_Doc):
    name: str
    pattern: str


class RedactionPolicy(_Doc):
    preserve_shape: bool = True
    rules: tuple[RedactionRule, ...] = ()
    always_redact_sensitive_inputs: bool = True
    blur_sensitive_regions_in_screenshots: bool = True


class PolicyConfig(_Doc):
    version: int = 1
    allowlist: Allowlist = Field(default_factory=Allowlist)
    actions: ActionPolicy = Field(default_factory=ActionPolicy)
    risk: RiskPolicy = Field(default_factory=RiskPolicy)
    dispositions: dict[Actor, Disposition] = Field(default_factory=dict)
    replay_gates: ReplayGates = Field(default_factory=ReplayGates)
    budgets: Budgets = Field(default_factory=Budgets)
    redaction: RedactionPolicy = Field(default_factory=RedactionPolicy)

    def disposition_for(self, actor: Actor, risk: ActionRisk) -> str:
        """How this actor's action at this tier should be handled.

        Unknown actor or tier falls through to the most restrictive answer. A policy lookup that
        misses should never be the reason something dangerous is permitted.
        """
        disposition = self.dispositions.get(actor)
        if disposition is None:
            return "block_and_escalate"
        return {
            ActionRisk.SAFE: disposition.safe,
            ActionRisk.ELEVATED: disposition.elevated,
            ActionRisk.IRREVERSIBLE: disposition.irreversible,
        }[risk]

    def compiled_redaction_rules(self) -> tuple[tuple[str, re.Pattern[str]], ...]:
        return tuple((rule.name, re.compile(rule.pattern)) for rule in self.redaction.rules)


def parse_policy(text: str) -> PolicyConfig:
    """Parse a policy document.

    Keys are upper-cased actor names in the YAML (`AUTOMATION`, `HUMAN`) because that reads better
    to a reviewer than lowercase enum values.
    """
    raw: dict[str, Any] = yaml.safe_load(text) or {}
    if isinstance(raw.get("dispositions"), dict):
        raw["dispositions"] = {key.lower(): value for key, value in raw["dispositions"].items()}
    return PolicyConfig.model_validate(raw)
