"""A bounded, policed model call *after* a deterministic replay has already failed.

This package exists because the obvious way to build an assisted fallback destroys the thing this
system is for. Replay's guarantee is that no model is in the decision loop -- enforced by the
``no-llm-in-replay`` import contract -- and the moment ``cua.replay`` can reach a model client, the
contract is a comment and the guarantee is a claim.

So the model never enters replay. It sits above it::

    cua.cli
    cua.assist        <- here: may import cua.agent AND cua.replay
    cua.evals
    cua.agent | cua.replay | cua.recorder | cua.catalog
    cua.runtime       <- still the only module that touches a driver

`AssistedReplay` runs a capability to completion deterministically, and only if that run has
already ended in failure does it ask a model for **one** corrective action, dispatch it through the
same policy chokepoint everything else uses, and hand back to `ReplayExecutor.resume`. A replay
that succeeds never sees a model. A replay invoked without `--assist` never sees a model. The
import contract is unchanged and still means exactly what it said.

What keeps it honest, in the code rather than in a prompt:

- one model call, one step, one attempt per run -- counted, not requested
- the corrective action comes from the agent's existing closed tool schema, so it is a role and an
  accessible name, never free-form code and never a URL
- it is dispatched through `Dispatcher`, so the allowlist, the risk tiers, the lease and the
  evidence trail all apply to it exactly as they would to an automated step
- anything classified `irreversible` is refused outright, whatever the policy tier would allow
- the run's evidence records that a model touched it, so an assisted success can never be mistaken
  for a deterministic one
"""

from __future__ import annotations

from cua.assist.recovery import AssistedReplay, AssistOutcome

__all__ = ["AssistOutcome", "AssistedReplay"]
