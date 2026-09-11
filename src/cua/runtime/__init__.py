"""The dispatcher: the ONLY module permitted to import ``cua.surfaces``.

::

    Action -> PolicyEngine -> TargetResolver -> SurfaceDriver

A guardrail with one bypass is not a guardrail, so there is exactly one path to a surface.
"""
