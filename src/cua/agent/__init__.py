"""The discovery loop -- the probabilistic half of the system.

An LLM observes a normalized snapshot, decides, and acts through the same chokepoint replay uses.
The model sees a rendered ``UiSnapshot`` rather than raw HTML, so it reasons in the vocabulary the
artifact will store.

``cua.replay`` may never import this package.
"""
