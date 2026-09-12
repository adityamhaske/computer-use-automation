"""Capability storage, tenant overlays, and the agent-facing tool contract.

Tenants overlay a capability; they never fork it. The artifact's typed inputs and outputs emit JSON
Schema directly, so the artifact *is* the tool contract -- there is no second source of truth.
"""
