# Mutation check

A green suite shows the claims hold today. This shows the tests would **fail** if a
claim stopped holding: each row deliberately breaks the code in one way, runs the tests
meant to defend it, and records whether any went red. The source is restored
byte-for-byte after each one. Regenerate with `make mutation-check`.

**18 of 18 mutations caught.**

| Invariant / area | Deliberate violation | Verdict | First test to catch it |
|---|---|---|---|
| 1. Replay never imports the LLM | replay imports the discovery agent | **KILLED** | `Replay must not import the agent or any LLM client` |
| 2. No action reaches a driver without policy authorization | a denied action is dispatched anyway | **KILLED** | `tests/integration/test_dispatcher.py::test_a_denied_action_is_authorized_against_but_never_dispatched` |
| 2. No action reaches a driver without policy authorization | the allowlist stops refusing off-list navigation | **KILLED** | `tests/integration/test_dispatcher.py::test_an_entrypoint_outside_the_allowlist_is_refused` |
| 3. Targets are semantic, never raw selectors | an exact-name match stops comparing the accessible name | **KILLED** | `tests/unit/test_resolver.py::test_normalized_matching_absorbs_cosmetic_differences` |
| 4. Human input cannot bypass policy | automation may originate a human-only raw_input | **KILLED** | `tests/invariants/test_human_control_safety.py::test_automation_cannot_originate_raw_input` |
| 5. Unknown and ambiguous states fail closed | an ambiguous target is tie-broken to the first candidate | **KILLED** | `tests/unit/test_resolver.py::test_ambiguity_is_refused_not_tiebroken` |
| 6. Every sink is redacted | the redactor stops applying its pattern rules | **KILLED** | `tests/invariants/test_redaction.py::test_no_sink_retains_seeded_secrets` |
| 7. Business outcomes are not failures | 'no such member' exits non-zero | **KILLED** | `tests/unit/test_taxonomy.py::test_business_outcome_exits_zero` |
| 8. Automation acts only while it holds the session | the stale-lease-epoch check is skipped | **KILLED** | `tests/integration/test_dispatcher.py::test_a_stale_lease_epoch_is_refused_before_anything_else` |
| 9. Capabilities are immutable and content-hashed | a tampered artifact is loaded without verifying its hash | **KILLED** | `tests/contract/test_capability_schema.py::test_tampering_is_detected` |
| 10. domain/ is pure | the domain layer imports the policy package | **KILLED** | `Domain is pure (no I/O, no other cua packages)` |
| Edge-case fixes | a malformed URL raises out of the allowlist instead of being denied | **KILLED** | `tests/unit/test_edge_policy_allowlist.py::test_a_spelling_that_could_name_a_different_host_is_refused[NUL` |
| Edge-case fixes | the wrong-action metric ignores the expected status | **KILLED** | `tests/unit/test_edge_cli_evals_codegen_scorers.py::test_a_success_where_the_seeded_truth_is_a_business_outcome_is_a_wrong_action` |
| Edge-case fixes | a refused stale-lease attempt is counted as an unauthorized dispatch | **KILLED** | `tests/integration/test_edge_runtime_dispatch_pipeline.py::test_a_refused_stale_dispatch_is_not_an_unauthorized_dispatch` |
| Edge-case fixes | minted ids may be all digits and get redacted as account numbers | **KILLED** | `tests/unit/test_edge_domain_ids.py::test_an_id_always_contains_a_letter_however_many_are_drawn[8]` |
| Edge-case fixes | the CLI prints a traceback instead of a clean error when the target is down | **KILLED** | `tests/integration/test_edge_cli_commands.py::test_replay_against_a_target_that_is_not_running_is_a_clean_error` |
| Edge-case fixes | a repeated --input is silently last-one-wins | **KILLED** | `tests/integration/test_edge_cli_commands.py::test_a_malformed_input_pair_is_a_usage_error[duplicate]` |
| Edge-case fixes | an explicit empty container is pruned and the sealed artifact reloads as tampered | **KILLED** | `tests/contract/test_edge_domain_capability_schema.py::test_a_declared_empty_collection_round_trips[driver_capabilities=()]` |
