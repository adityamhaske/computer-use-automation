"""Windows UI Automation driver -- INTERFACE ONLY, deliberately unimplemented.

Documents how UIA maps onto the normalized model::

    ControlType              -> UiNode.role
    Name                     -> UiNode.name
    LegacyIAccessible.Value  -> UiNode.value
    BoundingRectangle        -> UiNode.bounds

The point is that the same capability artifact would replay here without change, because
``role: button, name: "Search"`` means the same thing in UIA as it does in ARIA. That is the whole
argument for making the semantic tree -- not the DOM -- the abstraction.

If this grows past a Protocol and this docstring, stop. See AGENTS.md scope discipline.
"""
