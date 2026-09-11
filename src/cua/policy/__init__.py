"""The authorization chokepoint: allowlist, risk tiers, redaction, secrets.

``PolicyEngine.authorize()`` is the only way to mint an ``AuthorizedAction``, and
``SurfaceDriver.dispatch`` accepts nothing else. Every actor -- discovery, replay, and a human
operator during an intervention -- takes this path.
"""
