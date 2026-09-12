"""The deterministic executor -- the production execution path.

No LLM, no improvisation. Assert preconditions, resolve, authorize, dispatch, wait, assert
postconditions, verify the checkpoint, classify everything observed, return a structured result.

Enforced by ``no-llm-in-replay``: this package may not import ``cua.agent`` or any model client.
"""
