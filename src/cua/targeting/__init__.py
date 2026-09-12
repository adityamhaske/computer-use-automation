"""Resolve a semantic ``TargetDescriptor`` against a ``UiSnapshot`` -- or refuse.

The seven-rung ladder, pure scoring with document-order tie-breaks, explicit ambiguity refusal, and
drift detection. Determinism is won or lost here.
"""
