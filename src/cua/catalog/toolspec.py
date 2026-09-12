"""The catalog as an agent-facing tool contract.

Nearly free, and that is the design working rather than a shortcut: `Capability.tool_schema()`
already emits JSON Schema from the same `inputs`/`outputs` the executor binds at replay. **The
artifact is the tool contract.** There is no second definition to drift out of sync, which is why
`inputs` were typed in the schema rather than left as a free-form dict.

The one thing added here is `outcomes`, surfaced to the caller as part of the contract. An agent
that does not know `member_not_found` is a legitimate answer will treat it as an error, retry, and
eventually escalate a question the system already answered.
"""

from __future__ import annotations

from typing import Any

from cua.catalog.store import CapabilityStore


def tool_definitions(store: CapabilityStore) -> list[dict[str, Any]]:
    """Every capability in the catalog, as tool definitions an agent can be handed."""
    return [entry.capability.tool_schema() for entry in store.list()]
