# ADR 0005 — Tenants overlay a capability; they never fork it

- **Status:** Accepted
- **Date:** 2026-09-11
- **Context:** Brief §3.7 — hundreds of tenants, ~20 apps each, many running the same vendor product
  configured, branded, and versioned differently

## Context

The economics of this system live or die here. If automating a flow for one credit union means
re-recording it for the next 200, the product does not work. The brief is explicit that automation
"should ideally generalize — or degrade gracefully — across the others rather than being rebuilt
from scratch each time."

The naive approach is to record per tenant and accept the cost. The next-naive approach is to record
once and hope the UI matches, which fails on the first rebranded button.

## Decision

**Capabilities are namespaced by vendor product, not by tenant**, and per-tenant differences are
expressed as a separate overlay document:

```yaml
# cua.tenant_binding/v1
capability_ref: "corebank.member.savings_balance@1.2.0"
tenant: first-valley-cu
vars: { base_url: "https://fvcu.internal" }
overrides:
  steps:
    enter_member_id:
      target: { name: { match: exact, value: "Member #" } }   # this tenant rebranded the label
recovery_extra: [ ... ]
```

Resolution is `base ⊕ overlay`, computed at load into an effective — still immutable — capability
whose `content_hash` covers the overlay.

**Drift is measured, not assumed.** Each step records the strategy that won at discovery
(`recorded_strategy`). At replay we record the strategy that actually won. Descending the ladder —
say `semantic_exact` at discovery but `structural_anchor` at replay — is a **drift signal**, not a
silent success. Per-step drift aggregates into a run-level `drift_score`, which is the signal that
would open a re-record task in production.

## Consequences

**Good.** A rebranded label costs a four-line overlay, not a re-record. Most tenants need no overlay
at all, because the resolution ladder already degrades from exact naming to structural anchoring.
The drift score tells us *which* tenants are diverging and by how much, before a replay starts
failing.

**Demonstrated, not asserted.** Variant B of the mock app carries two distinct kinds of divergence:

- *Markup churn* — a control keeps its label but changes CSS class and form field name. The ladder
  absorbs this automatically, which is the falsifiable form of "this system does not depend on CSS
  selectors."
- *Rebranding* — a control is relabeled. This defeats `semantic_exact`, `semantic_normalized`
  **and** `structural_anchor` (the anchor text is the label). There is deliberately no automatic
  recovery: the replay fails closed with a high drift score, and a four-line overlay fixes it.

The second case is the one that justifies this ADR. If the ladder silently absorbed rebranding, the
overlay would be redundant — and the system would be guessing that "Savings Bal." means "Savings
Balance". In a system that reads account balances, that guess is not one to make.

`tests/integration/test_variant_b.py` asserts both cases; the `cross_tenant` eval measures them.

**Bad.** Overlays can rot independently of the base capability, and a base version bump may
invalidate overrides that refer to steps that no longer exist. We validate `capability_ref` against
the loaded version and fail closed on an override targeting an unknown step id.

**Explicitly not built.** Overlay *storage*, tenant registries, and rollout tooling. The brief says
designing the abstraction so it could scale is valuable and prematurely building the infrastructure
is not. This is the abstraction; the plumbing is a documented cut.

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
