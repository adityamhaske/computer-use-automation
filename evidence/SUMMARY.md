# Evidence summary

One page: what was run on the final code, what it showed, and where to look. Everything below was
produced by the commands named next to it and can be regenerated (`make evidence` refreshes the lot).

| What | Result | Command | Evidence |
|---|---|---|---|
| Full gate (lint, `mypy --strict`, import contracts, secret scan, tests, UI smoke test) | **All pass** — 96 source files type-clean, 5 of 5 contracts kept | `make check` | — |
| Test suite | **2,134 passed, 0 failed**, offline, no API key; 1,800 are edge-case tests | `make test-report` | [`tests/test-report.md`](tests/test-report.md) |
| Coverage of `src/cua` | 82.8% lines, 74.4% branches (the CLI is 44%: its long-running commands run in the demo's own processes, which an in-process measurement cannot see) | `make test-report` | [`tests/test-report.md`](tests/test-report.md) |
| Do the tests fail when they should? | **18 of 18** deliberate violations caught — all ten invariants plus a guard per fixed defect | `make mutation-check` | [`tests/mutation-report.md`](tests/mutation-report.md) |
| End-to-end story | **14 of 14 stages.** Discovery by a **live model, pinned to `kr/claude-sonnet-4.5`** through a local OmniRoute gateway (the gateway reports serving `claude-sonnet-4.5`; 4 calls, median 4.2 s, 35,367 tokens, a gateway request id on every call), verified by `make verify-live` | `make demo` | [`demo-transcript.txt`](demo-transcript.txt), [`discovery/demo-discovery/`](discovery/demo-discovery/) |
| Replay stability and second tenant | 20 runs each: **0 wrong actions, 0 unauthorized dispatches, determinism holds** | `make eval` | [`evals/`](evals/) |
| The edge cases, actually run | **22 of 22 scenarios** behaved exactly as declared, each saved as a run you can open: every member state, the input pattern at its boundaries, faults, governance refusals | `make edge-cases` | [`edge-cases/SUMMARY.md`](edge-cases/SUMMARY.md) |
| Every edge case the suite defends | Listed by name, per subsystem | `make test-report` | [`tests/edge-case-catalogue.md`](tests/edge-case-catalogue.md) |
| Continuous verification | CI on every push (gate, demo, smoke); a separate **Verify** workflow nightly and on code pushes runs the edge cases, coverage and the mutation check and uploads the evidence as a downloadable artifact | GitHub Actions | `.github/workflows/` |

## What the edge-case pass found, and fixed

The edge-case suites were written to find defects. Each was confirmed, fixed in `src/`, and is now
guarded by a test and, for the important ones, by a mutation above. None is left open.

- **Allowlist:** a malformed URL raised out of the allowlist instead of being denied; a host-less URL
  and an empty allowlist entry could be read as allowed; credentials in a URL were echoed into the
  trace.
- **Evidence integrity:** a refused stale-lease attempt was counted as an unauthorized dispatch; an
  identifier that happened to be all digits (about 1 in 300) was redacted as an account number.
- **Headline metric:** the wrong-action score ignored the expected status, and determinism was
  reported as holding when nothing had been replayed twice.
- **Artifact integrity:** a sealed artifact that declared an empty list, or contained U+0085,
  reloaded as *tampered*; non-ASCII or leading-zero versions, duplicate input names and uncompilable
  regular expressions were accepted.
- **Redaction:** the email rule took minutes on one long unbroken token; the operator queue stored
  un-redacted text.
- **Human handoff:** an expired hold left the session unclaimable; a failed claim and a refused
  second release could corrupt the audit record.
- **Replay inputs:** a declared `integer` was never enforced, and a JSON `null` was typed into the
  application as the text `None`.
- **Matching:** whitespace inside an outcome banner was not folded although documented as folded.
- **Command line:** a target that was not running ended in a Playwright traceback; a missing or
  tampered artifact did too; a repeated `--input` was silently last-one-wins. All are now a sentence
  and an exit code, tested through the real entry point.
- **Discovery:** a model could call `finish` without ever recording a value, and the run compiled to a
  capability that returns nothing — which the demo then published into the catalog. The prompt now
  says to `extract` before finishing, and a capability with no outputs is never published.

## Honest limits

- The application under test is a purpose-built mock; the evidence shows the mechanism, not a
  production deployment. "A second tenant" is `variant_b` of that mock — the same product, rebranded
  and restyled — not a real second institution.
- Discovery is probabilistic by design. It reached the goal with the pinned model after the prompt
  fix; another model may behave differently, which is why every later stage is model-free and why
  `make verify-live` exists.
- The CLI's long-running commands (`discover`, `console`, `demo`) are proven by `make demo` and
  `./start.sh ui`, not by the coverage percentage.
- Stale working runs from earlier sessions were moved out of this tree, not deleted.
