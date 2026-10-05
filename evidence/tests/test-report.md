# Test report

Generated 2026-10-05 by `make test-report`: the whole suite, offline, with no
API key, under line + branch coverage.

**2134 passed, 0 failed** of 2134 tests in 108.4s of test time. 1800 of them are edge-case tests from the hardening pass.

**Coverage of `src/cua`: 82.8% of lines, 74.4% of branches** (5424 statements).

## By layer

| Layer | Tests | What it proves |
|---|---:|---|
| `unit/` | 537 | Pure logic: schema, resolver, policy, redaction |
| `integration/` | 1192 | Full flows against the mock app, real browser where it matters |
| `contract/` | 217 | Schema stability and artifact validity |
| `invariants/` | 188 | The ten architectural claims |

## Coverage by package

| Package | Statements | Lines | Branches |
|---|---:|---:|---:|
| `cua (top level)` | 1 | 100.0% | 100.0% |
| `cua.agent` | 407 | 83.8% | 65.3% |
| `cua.assist` | 120 | 80.8% | 62.5% |
| `cua.catalog` | 128 | 88.3% | 64.3% |
| `cua.cli` | 687 | 44.4% | 30.6% |
| `cua.domain` | 1141 | 96.3% | 89.0% |
| `cua.evals` | 284 | 81.3% | 95.2% |
| `cua.evidence` | 131 | 96.9% | 91.7% |
| `cua.hitl` | 566 | 77.0% | 52.5% |
| `cua.perception` | 111 | 94.6% | 81.2% |
| `cua.policy` | 380 | 95.8% | 91.5% |
| `cua.recorder` | 270 | 89.6% | 76.6% |
| `cua.replay` | 468 | 96.2% | 92.0% |
| `cua.runtime` | 213 | 84.0% | 75.0% |
| `cua.surfaces` | 296 | 67.9% | 59.6% |
| `cua.targeting` | 221 | 89.6% | 75.0% |

Read the percentages with one caveat: the CLI entry points and the browser driver run
largely in child processes (`make demo`, the UI smoke test), which an in-process
measurement cannot see, so `cua.cli` and `cua.surfaces` look lower than they are
exercised. The end-to-end story is proven by `make demo` and its evidence, not by this
table.

## Edge-case suites

Each file targets one subsystem with boundary, malformed, hostile and out-of-order input.
The claims they defend are listed in [`edge-case-catalogue.md`](edge-case-catalogue.md).

| Suite | Tests |
|---|---:|
| `tests/contract/test_edge_domain_capability_schema.py` | 190 |
| `tests/integration/test_edge_catalog_store.py` | 117 |
| `tests/integration/test_edge_cli_commands.py` | 62 |
| `tests/integration/test_edge_hitl_broker.py` | 50 |
| `tests/integration/test_edge_hitl_queue.py` | 36 |
| `tests/integration/test_edge_mock_app_auth.py` | 124 |
| `tests/integration/test_edge_mock_app_faults.py` | 132 |
| `tests/integration/test_edge_mock_app_live.py` | 8 |
| `tests/integration/test_edge_mock_app_pages.py` | 289 |
| `tests/integration/test_edge_replay_rig.py` | 2 |
| `tests/integration/test_edge_runtime_dispatch_pipeline.py` | 184 |
| `tests/invariants/test_edge_hitl_lease_state_machine.py` | 154 |
| `tests/unit/test_edge_cli_evals_codegen_report.py` | 41 |
| `tests/unit/test_edge_cli_evals_codegen_scorers.py` | 81 |
| `tests/unit/test_edge_demo_publish.py` | 5 |
| `tests/unit/test_edge_domain_ids.py` | 13 |
| `tests/unit/test_edge_policy_allowlist.py` | 119 |
| `tests/unit/test_edge_replay_bind_transforms.py` | 78 |
| `tests/unit/test_edge_replay_classify_wait.py` | 38 |
| `tests/unit/test_edge_replay_inputs.py` | 77 |

Proof that these tests can fail: [`mutation-report.md`](mutation-report.md).
