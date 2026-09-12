"""Measuring what the write-up claims.

Tests assert correctness; evals measure quality under variance. The fault matrix and the determinism
check are *tests*, because they are deterministic assertions -- calling them evals would double the
harness for nothing. What belongs here is the aggregate: how often does replay succeed, how often
does it refuse, how far down the ladder does it fall on a different tenant, and -- the one that
matters in a bank -- how often does it act on the wrong control.

Lives under `src/cua/` rather than at the repository root on purpose. A harness that drives a real
browser must sit where the invariants reach it: `.importlinter` is rooted at `cua`, the chokepoint
AST scan walks `src/cua`, and mypy's strict mode covers the `cua` package. At the root it would have
escaped all three, and a harness that could reach around the chokepoint would invalidate the very
measurements it produces.
"""
