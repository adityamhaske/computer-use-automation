"""The ``SurfaceDriver`` port and its implementations.

A driver is the only thing in the system that knows what an accessibility tree, a CDP session, or a
DOM is. It answers two questions: what does the surface look like (``observe``) and please do this
(``dispatch``). Everything above depends on the normalized ``UiSnapshot``, never on how it was
obtained.

Only ``cua.runtime.dispatcher`` may import this package -- that is the policy chokepoint.
"""
