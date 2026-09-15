"""Resolving `{$secret: name}` references, without ever persisting the value.

The design point is that an artifact holds a *reference*, never a credential. A recorded discovery
run has real passwords typed into it; if those were captured as literals they would land in the
artifact, the trace, the evidence and every screenshot -- in a system handling regulated financial
data.

Because the artifact never holds the value, there is no code path that can persist it. That is a
stronger guarantee than "we remember to redact it", which is a guarantee that holds until someone
adds a new sink.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from cua.domain.values import SecretRef
from cua.policy.redact import Redactor


class SecretNotFoundError(RuntimeError):
    """A referenced secret is not configured.

    Deliberately fatal. Continuing with an empty string would type nothing into a password field and
    produce a confusing authentication failure several steps later.
    """


@dataclass
class SecretResolver:
    """Resolves secret references from the environment at dispatch time.

    Environment variables are used here for standard reference runtime; the seam to an
    enterprise secret manager (e.g. HashiCorp Vault, AWS Secrets Manager) is a single
    method. What matters architecturally is that resolution happens *at dispatch*, inside
    the action, and the value is never returned to anything that writes to disk.
    """

    prefix: str = "CUA_SECRET_"
    overrides: dict[str, str] = field(default_factory=dict)
    redactor: Redactor | None = None
    """The redactor every issued value is registered with, at the moment it is issued.

    Pattern matching cannot recognise an arbitrary password, so the literal has to be handed to the
    redactor explicitly. Doing it *here* rather than at the call site is deliberate: `resolve()` is
    the single point every secret passes through, so no future caller can obtain a value without the
    redactor learning about it. Optional only so a resolver can be constructed in a unit test.
    """

    _issued: set[str] = field(default_factory=set, init=False)

    def resolve(self, ref: SecretRef) -> str:
        name = ref.secret_name
        if name in self.overrides:
            value = self.overrides[name]
        else:
            env_key = self.prefix + name.upper().replace(".", "_").replace("-", "_")
            found = os.environ.get(env_key)
            if found is None:
                raise SecretNotFoundError(
                    f"secret {name!r} is not configured (looked for ${env_key}). "
                    "Secrets are referenced by name and resolved at dispatch; they are never "
                    "stored in an artifact."
                )
            value = found

        self._issued.add(value)
        if self.redactor is not None:
            self.redactor.register_secret(value)
        return value

    @property
    def issued_values(self) -> frozenset[str]:
        """Every value handed out this session, for the redactor to scrub unconditionally.

        Pattern matching cannot recognise an arbitrary password, so the resolver tells the redactor
        exactly what to look for.
        """
        return frozenset(self._issued)
