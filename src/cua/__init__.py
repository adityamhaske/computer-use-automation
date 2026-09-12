"""Computer-use automation: discover once with an LLM, replay deterministically forever.

An LLM is allowed to be uncertain exactly once -- during discovery, when it works out how to
accomplish a goal in an application it has never seen. That uncertainty is compiled into a typed,
versioned capability artifact. From then on the artifact is executed by a deterministic engine with
no model in the loop.

See AGENTS.md for the nine invariants that protect that line.
"""

__version__ = "0.1.0"
