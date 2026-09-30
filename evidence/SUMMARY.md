# Evidence summary

One page: what was run on the final code, what it showed, and where to look. Everything below was
produced by the commands named next to it and can be regenerated.

| What | Result | Command | Evidence |
|---|---|---|---|
| Full gate (lint, `mypy --strict`, import contracts, secret scan, tests, UI smoke test) | **All pass** — 96 source files type-clean, 5 of 5 contracts kept | `make check` | — |
| Test suite | **2,067 passed, 0 failed**, offline, no API key; 1,733 are edge-case tests | `make test-report` | [`tests/test-report.md`](tests/test-report.md) |
| Coverage of `src/cua` | 78.9% lines, 71.8% branches (CLI and browser driver run mostly in child processes the measurement cannot see) | `make test-report` | [`tests/test-report.md`](tests/test-report.md) |
| Do the tests fail when they should? | **16 of 16** deliberate violations caught — all ten invariants plus a guard per fixed defect | `make mutation-check` | [`tests/mutation-report.md`](tests/mutation-report.md) |
| End-to-end story | **14 of 14 stages**; discovery by a **live model** through a local OmniRoute gateway (4 model calls, median 2.4 s, gateway request ids recorded), verified by `make verify-live` | `make demo` | [`demo-transcript.txt`](demo-transcript.txt), [`README.md`](README.md) |
| Replay stability and second tenant | 20 runs each: **0 wrong actions, 0 unauthorized dispatches, determinism holds** | `make eval` | [`evals/`](evals/) |
| Edge cases handled | Every claim listed by name | `make test-report` | [`tests/edge-case-catalogue.md`](tests/edge-case-catalogue.md) |

## What the edge-case pass found, and fixed

The edge-case suites were written to find defects. Each was confirmed, fixed in `src/`, and is now
guarded by a test and by a mutation above. None is left open.

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

## Honest limits

- The application under test is a purpose-built mock; the evidence shows the mechanism, not a
  production deployment.
- The CLI's own branches are proven by `make demo`, not by the coverage percentage.
- Stale working runs from earlier sessions were moved out of this tree, not deleted.
