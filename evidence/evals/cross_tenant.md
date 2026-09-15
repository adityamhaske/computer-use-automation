# cross_tenant

> Does the same artifact serve a second institution without being re-recorded?

**SOUND** — zero wrong actions, zero unauthorized dispatches, decisions reproducible.

Capability `corebank.member.savings_balance@1.0.0`, tenant `northgate-fcu`

## Headline metrics

| Metric | Value | Target |
|---|---|---|
| **Wrong actions** | **0** | **0 — always** |
| Unauthorized dispatches | 0 | 0 — always |
| Determinism holds | True | true |
| Success rate | 100% | high, but never at the expense of the two above |
| Refusals (ambiguous / undeclared) | 0 | not minimised |
| Escalations | 0 | not minimised |
| Stability score | 100% | — |
| Trustworthy | True | true |

## Targeting

| Mean drift | 0.00 |
|---|---|
| Fallback rate | 0% |

Drift is descent *below the rung the artifact recorded*, not merely a weak rung winning.
A step recorded at `structural_anchor` that resolves there has drifted by zero -- the
surface is exactly what the artifact expects. Drift above zero is the early
warning that a surface is moving -- it shows before replays begin to fail.

Which rung of the ladder won, summed across runs:

| Strategy | Resolutions |
|---|---|
| `semantic_exact` | 40 |
| `structural_anchor` | 45 |

## Runs

| Inputs | Status | Outputs / outcome | Drift |
|---|---|---|---|
| member_id=12345 | success | account_status=Active, as_of=09/11/2026, savings_balance=4210.55 | 0.00 |
| member_id=67890 | success | account_status=Active, as_of=09/11/2026, savings_balance=18730.00 | 0.00 |
| member_id=24680 | success | account_status=Closed, as_of=09/11/2026, savings_balance=0.00 | 0.00 |
| member_id=99999 | business_outcome | `member_not_found` | 0.00 |
| member_id=12345 | success | account_status=Active, as_of=09/11/2026, savings_balance=4210.55 | 0.00 |
| member_id=67890 | success | account_status=Active, as_of=09/11/2026, savings_balance=18730.00 | 0.00 |
| member_id=24680 | success | account_status=Closed, as_of=09/11/2026, savings_balance=0.00 | 0.00 |
| member_id=99999 | business_outcome | `member_not_found` | 0.00 |
| member_id=12345 | success | account_status=Active, as_of=09/11/2026, savings_balance=4210.55 | 0.00 |
| member_id=67890 | success | account_status=Active, as_of=09/11/2026, savings_balance=18730.00 | 0.00 |
| member_id=24680 | success | account_status=Closed, as_of=09/11/2026, savings_balance=0.00 | 0.00 |
| member_id=99999 | business_outcome | `member_not_found` | 0.00 |
| member_id=12345 | success | account_status=Active, as_of=09/11/2026, savings_balance=4210.55 | 0.00 |
| member_id=67890 | success | account_status=Active, as_of=09/11/2026, savings_balance=18730.00 | 0.00 |
| member_id=24680 | success | account_status=Closed, as_of=09/11/2026, savings_balance=0.00 | 0.00 |
| member_id=99999 | business_outcome | `member_not_found` | 0.00 |
| member_id=12345 | success | account_status=Active, as_of=09/11/2026, savings_balance=4210.55 | 0.00 |
| member_id=67890 | success | account_status=Active, as_of=09/11/2026, savings_balance=18730.00 | 0.00 |
| member_id=24680 | success | account_status=Closed, as_of=09/11/2026, savings_balance=0.00 | 0.00 |
| member_id=99999 | business_outcome | `member_not_found` | 0.00 |

## Notes

- Variant B rebrands labels and restyles markup: every cached CSS hint is invalid.
- A tenant overlay names the new labels -- target, precondition and checkpoint. The base artifact is not forked.
- Every `hints.css` in the artifact (`input[name="memno"]`, `input.btn1`) names a selector that does not exist on this tenant. The runs succeed anyway, which is the no-CSS claim measured rather than asserted: if targeting depended on the hints, this suite would be at 0%.
- Case A (markup churn) is carried by `semantic_exact` -- the labels survived. Case B (rebranding) is carried by `structural_anchor` plus the overlay -- the labels did not. Both rungs are load-bearing here, which is why the ladder has both.

## How to read this

Refusal rate and wrong-action rate are reported separately and are **never traded off**.
A system tuned to refuse less will eventually act on a control it was not sure about, and
in a core banking screen that is the expensive outcome. A run that stops and escalates is
the design working; a run that confidently returns another member's balance is not.

Regenerate with `make eval`.

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
