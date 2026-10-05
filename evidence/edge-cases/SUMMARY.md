# Edge-case runs

The hostile and boundary conditions, actually run: each scenario is a real replay (or a
direct call through the dispatcher) against a fresh mock app, through the same executor,
policy chokepoint and evidence bus as `make demo`. Every run is saved next to this file:
open `<scenario>/run_record.json` for the result and `<scenario>/trace.jsonl` for the
event-by-event story. Regenerate with `make edge-cases`; a deviating scenario fails it.

**22 of 22 scenarios behaved exactly as expected.**

Snapshots are kept only for runs that *stopped* (a human was needed), where they are the
evidence; where a run simply answered they would only repeat what the trace already says.

## Every state a member can be in

| Scenario | What it proves | Input | Expected | Observed | |
|---|---|---|---|---|---|
| [`member-active`](member-active/) | The straightforward case: an answer with three typed outputs. | member_id='12345' | success, exit 0 | success, exit 0, 6 dispatch(es) | PASS |
| [`member-closed-account`](member-closed-account/) | A closed savings account with a $0.00 balance is still an answer, not an error. | member_id='24680' | success, exit 0 | success, exit 0, 6 dispatch(es) | PASS |
| [`member-no-savings`](member-no-savings/) | Checking-only member: a declared business outcome, exit 0. | member_id='13579' | business_outcome, exit 0, no_savings_account | business_outcome, exit 0, no_savings_account, 3 dispatch(es) | PASS |
| [`member-restricted`](member-restricted/) | The application denies the record: `permission_denied`, an answer, exit 0. | member_id='55555' | business_outcome, exit 0, permission_denied | business_outcome, exit 0, permission_denied, 3 dispatch(es) | PASS |
| [`member-not-found`](member-not-found/) | 'No such member' is a legitimate answer the caller needs, not a crash. | member_id='99999' | business_outcome, exit 0, member_not_found | business_outcome, exit 0, member_not_found, 3 dispatch(es) | PASS |

## The declared input pattern, at and around its boundaries

| Scenario | What it proves | Input | Expected | Observed | |
|---|---|---|---|---|---|
| [`input-minimum-length`](input-minimum-length/) | Four digits is the shortest the pattern allows: accepted, and simply not found. | member_id='1234' | business_outcome, exit 0, member_not_found | business_outcome, exit 0, member_not_found, 3 dispatch(es) | PASS |
| [`input-maximum-length`](input-maximum-length/) | Ten digits is the longest the pattern allows: accepted, and simply not found. | member_id='1234567890' | business_outcome, exit 0, member_not_found | business_outcome, exit 0, member_not_found, 3 dispatch(es) | PASS |
| [`input-too-short`](input-too-short/) | Three digits: rejected before the browser moves. | member_id='123' | failed, exit 1, input_validation_failed | failed, exit 1, input_validation_failed, 0 dispatch(es) | PASS |
| [`input-too-long`](input-too-long/) | Eleven digits: rejected before the browser moves. | member_id='12345678901' | failed, exit 1, input_validation_failed | failed, exit 1, input_validation_failed, 0 dispatch(es) | PASS |
| [`input-empty`](input-empty/) | An empty value: rejected, zero actions. | member_id='' | failed, exit 1, input_validation_failed | failed, exit 1, input_validation_failed, 0 dispatch(es) | PASS |
| [`input-padded-with-space`](input-padded-with-space/) | A leading space is not a digit: rejected, never trimmed into validity. | member_id=' 12345' | failed, exit 1, input_validation_failed | failed, exit 1, input_validation_failed, 0 dispatch(es) | PASS |
| [`input-non-ascii-digits`](input-non-ascii-digits/) | Arabic-Indic digits look like a number and are not one: rejected. | member_id='١٢٣٤٥' | failed, exit 1, input_validation_failed | failed, exit 1, input_validation_failed, 0 dispatch(es) | PASS |
| [`input-sql-shaped`](input-sql-shaped/) | Injection-shaped text never reaches the application. | member_id="1'; DROP TABLE members; --" | failed, exit 1, input_validation_failed | failed, exit 1, input_validation_failed, 0 dispatch(es) | PASS |
| [`input-html-shaped`](input-html-shaped/) | Markup in an input is refused at the door. | member_id='<script>alert(1)</script>' | failed, exit 1, input_validation_failed | failed, exit 1, input_validation_failed, 0 dispatch(es) | PASS |

## Faults the application throws at a run

| Scenario | What it proves | Input | Expected | Observed | |
|---|---|---|---|---|---|
| [`fault-transient-recovered`](fault-transient-recovered/) | A 502 matches the declared `transient_load` rule; the run recovers and finishes. | member_id='12345'  + fault transient_load | success, exit 0 | success, exit 0, 7 dispatch(es) | PASS |
| [`fault-transient-exhausted`](fault-transient-exhausted/) | A 502 that never clears: recovery is bounded, so it ends as RECOVERY_EXHAUSTED and asks a human. | member_id='12345'  + fault transient_load | needs_human, exit 2, recovery_exhausted | needs_human, exit 2, recovery_exhausted, 4 dispatch(es) | PASS |
| [`fault-session-expired-reauth`](fault-session-expired-reauth/) | The session silently expires; the declared remedy signs in again through policy and the run finishes. | member_id='12345'  + fault session_timeout | success, exit 0 | success, exit 0, 9 dispatch(es) | PASS |
| [`fault-session-expired-no-remedy`](fault-session-expired-no-remedy/) | The same expiry when the remedy cannot run: recovery is exhausted and a human is asked, rather than the run guessing its way on. | member_id='12345'  + fault session_timeout | needs_human, exit 2, recovery_exhausted | needs_human, exit 2, recovery_exhausted, 1 dispatch(es) | PASS |
| [`fault-undeclared-screen`](fault-undeclared-screen/) | A friendly 'System Notice' nobody declared appears. It is NOT clicked through: unknown state, fail closed. | member_id='12345'  + fault undeclared_dialog | needs_human, exit 2, unexpected_state | needs_human, exit 2, unexpected_state, 1 dispatch(es) | PASS |

## Governance: the system refusing what it must refuse

| Scenario | What it proves | Input | Expected | Observed | |
|---|---|---|---|---|---|
| [`governance-tampered-artifact`](governance-tampered-artifact/) | A capability edited after it was sealed is refused: the content hash is the contract. | (sealed artifact, one word edited) | refused: content hash mismatch | content hash mismatch for corebank.member.savings_balance@1.0.0 | PASS |
| [`governance-off-allowlist-navigation`](governance-off-allowlist-navigation/) | A page that talks the agent into leaving the approved hosts is refused by policy, outside the model. | (direct call through the dispatcher) | DENIED / navigation_blocked, browser did not move | denied / navigation_blocked, browser did not move: True | PASS |
| [`governance-stale-lease`](governance-stale-lease/) | Automation acting after a human took the session is refused (LEASE_LOST) and is not counted as a dispatch. | (direct call through the dispatcher) | LEASE_LOST, nothing dispatched, 0 unauthorized | lease_lost, dispatched=0, unauthorized=0 | PASS |
