"""The operator console: a view onto the control-transfer model.

Built last, deliberately. The lease, the policed input path, the intervention queue and the
re-anchoring resume are all provable headless, and they are what is actually load-bearing. The
console is how a person reaches them.

**Documented scope.** Single operator, no authentication, local only. Multi-operator routing, SSO,
queue assignment and supervisor sign-off are designed but not built -- see REPORT.md §5.

What *is* real: the operator drives the same live Chromium session the automation was using, their
input travels the same policy chokepoint, every action is recorded with `actor=HUMAN`, and handing
back triggers the same reconciliation the executor uses.
"""
