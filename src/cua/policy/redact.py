"""Redaction at every sink.

Logs, artifacts, evidence files, screenshots, **and outbound model prompts**. That last one is the
easily-missed sink: during discovery an LLM is shown the page, and regulated data should not leave
the process just because a model asked for context.

Two rules shape the implementation:

**Redact values, preserve shapes.** `member_id=<redacted:string[5]>` still tells a debugger the
field was present and well-formed. A wholesale `***` destroys the evidence trail's usefulness and
pushes people toward turning redaction off to debug -- which is how it ends up off in production.

**Over-redaction is the correct failure direction.** These patterns will sometimes catch a reference
number that merely looks like an account number. That is a legible cost. The opposite error is a
customer's account number sitting in a log file, and those are not symmetric.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from cua.policy.config import RedactionPolicy

MASK = "<redacted:{label}>"


@dataclass
class Redactor:
    """Applies the configured redaction rules to anything on its way to a sink."""

    config: RedactionPolicy
    extra_secrets: set[str] = field(default_factory=set)
    """Literal values that must never appear anywhere -- resolved credentials, chiefly.

    Pattern matching cannot catch a password like "hunter2", so the secret resolver registers every
    value it hands out and the redactor scrubs those literals unconditionally.
    """

    _compiled: tuple[tuple[str, re.Pattern[str]], ...] = field(init=False, default=())

    def __post_init__(self) -> None:
        self._compiled = tuple((rule.name, re.compile(rule.pattern)) for rule in self.config.rules)

    def register_secret(self, value: str) -> None:
        """Record a literal that must be scrubbed wherever it appears.

        Short values are ignored: scrubbing every occurrence of a two-character password would
        corrupt unrelated text far more than it would protect anything.
        """
        if value and len(value) >= 4:
            self.extra_secrets.add(value)

    def text(self, value: str) -> str:
        """Redact free text -- a page's contents, a log line, a model prompt."""
        if not value:
            return value
        redacted = value
        for secret in self.extra_secrets:
            redacted = redacted.replace(secret, MASK.format(label="secret"))
        for label, pattern in self._compiled:
            redacted = pattern.sub(MASK.format(label=label), redacted)
        return redacted

    def value(self, value: Any, *, sensitive: bool = False, type_name: str = "string") -> Any:
        """Redact a single field value.

        A `sensitive`-declared input is masked by shape regardless of whether it matches a pattern:
        the declaration is the authority, not the format.
        """
        if sensitive and self.config.always_redact_sensitive_inputs:
            length = len(str(value)) if value is not None else 0
            return f"<redacted:{type_name}[{length}]>"
        if isinstance(value, str):
            return self.text(value)
        return value

    def mapping(
        self, data: dict[str, Any], *, sensitive_keys: frozenset[str] = frozenset()
    ) -> dict[str, Any]:
        return {
            key: self.value(item, sensitive=key in sensitive_keys) for key, item in data.items()
        }

    def structure(self, data: Any, *, sensitive_keys: frozenset[str] = frozenset()) -> Any:
        """Recursively redact an arbitrary JSON-able structure.

        Used on run records and evidence payloads, where the shape is not known up front.
        """
        if isinstance(data, dict):
            return {
                key: (
                    self.value(item, sensitive=True)
                    if key in sensitive_keys
                    else self.structure(item, sensitive_keys=sensitive_keys)
                )
                for key, item in data.items()
            }
        if isinstance(data, list):
            return [self.structure(item, sensitive_keys=sensitive_keys) for item in data]
        if isinstance(data, str):
            return self.text(data)
        return data

    def is_clean(self, haystack: str) -> bool:
        """Whether text is free of anything the rules would redact.

        Used by the redaction invariant test to assert that no sink contains a leak -- the check has
        to live next to the rules, or the two drift apart and the test starts passing vacuously.
        """
        if any(secret in haystack for secret in self.extra_secrets):
            return False
        return not any(pattern.search(haystack) for _, pattern in self._compiled)
