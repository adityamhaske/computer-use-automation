"""Pure domain types. No I/O, no network, no browser, no clock.

The artifact schema is the centerpiece of this system, so it has to be reasonable about -- and
testable -- in isolation. Enforced by the ``pure-domain`` import contract: this package imports
nothing else from ``cua``.
"""
