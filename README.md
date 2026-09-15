# Computer-Use Automation (CUA)

[![CI](https://github.com/adityamhaske/interface.ai/actions/workflows/ci.yml/badge.svg)](https://github.com/adityamhaske/interface.ai/actions/workflows/ci.yml)

> **An LLM is allowed to be uncertain exactly once.** That uncertainty is compiled into a
> typed artifact, which then runs with no model in the loop.

An LLM works out how to accomplish a goal in a legacy application that has no API — **once**. That
run is compiled into a typed, versioned **capability artifact**, which is then replayed with no model
in the decision loop and invoked by AI agents as a normal typed function. When a replay cannot safely
continue, it hands the live session to a human and takes it back.

```text
Goal + Target ──► LLM Discovery ──► Capability Artifact ──► Deterministic Replay ──► Typed Outputs
                      (once)         (reviewed, sealed)       (every time after)
                                                                       │
                                                          stuck? ───────┴──► Human takes the
                                                                             live session, acts,
                                                                             hands it back
```

## Setup

Python 3.11 or 3.12, `git`, and ~400 MB for Chromium. Nothing else — no database, no Docker, no
account, no API key.

```bash
make setup && make demo
```

That is the whole assessment path: `make setup` builds the venv and installs Chromium, `make demo`
runs all twelve stages end to end in about a minute. Everything in this repository works offline
with no key. The single exception is a live discovery run — copy `.env.example` to `.env` and set
`OPENROUTER_API_KEY` for that; without one, discovery replays a recorded transcript and says so, in
the output and in the evidence it writes.

`make` on its own lists every target.

## Poking at it by hand

```bash
make app        # the mock back-office on :8811
```

The sign-on screen prints the fixture credentials, and the search screen lists the seeded members
and what each one demonstrates, so nothing below needs to be looked up:

| Sign on | `teller01` / `not-a-real-password` |
|---|---|
| `12345`, `67890` | a member with a savings account — the straightforward case |
| `24680` | savings closed, $0.00 — an empty account is still an answer |
| `13579` | checking only → business outcome `no_savings_account` |
| `55555` | restricted record → business outcome `permission_denied` |
| `99999` | no such member → business outcome `member_not_found` |

Every member, balance and credential here is invented; this is a mock application built for the
exercise.

## Demo path

```bash
make demo          # the whole story end to end, ~60s, no key required
```

Or the two commands the system is actually built around:

```bash
make app &         # the hostile mock back-office on :8811

# 1. Discover — drive a live surface with a model until the goal is met, and record what worked.
#    Needs a model in .env; everything below this line does not.
make discover

# 2. Replay the compiled artifact deterministically, with an input discovery never saw.
cua replay evidence/capabilities/memberdesk.savings_balance@1.0.0.yaml \
           --input member_number=67890 --base-url http://localhost:8811 --sign-in
#   -> SUCCESS - 2 output(s)   {"savings_balance": "18730.00", ...}

# A member who does not exist is an answer, not a crash.
cua replay evidence/capabilities/corebank.member.savings_balance@1.0.0.yaml \
           --input member_id=99999 --base-url http://localhost:8811 --sign-in
#   -> BUSINESS OUTCOME - member_not_found   (exit 0)
```

## The rest of the surface

```bash
cua catalog list                       # what an agent can call, with integrity and approval state
cua catalog approve <ref> --from-eval  # gate unattended replay; refused if the evidence is thin
cua codegen <ref> --input member_id=12345 --out test_gen.py   # a runnable Playwright test
cua replay <artifact> --assist         # one policed model-chosen step, only after a failure
cua eval                               # 20 replays + a second tenant, written to evidence/evals/
```

Approval is pinned to the artifact's content hash, so editing it invalidates the approval rather
than inheriting it. `--assist` is opt-in and never a default: a plain `cua replay` makes zero model
calls, which is the guarantee the rest of the system rests on.

## See the handoff

The requirement most submissions fake. This runs a capability on the console's own session with a
fault armed, so a real intervention is waiting when the page opens:

```bash
cua console --capability corebank.member.savings_balance@1.0.0 \
            --arm-fault undeclared_dialog --input member_id=12345 --sign-in
# then open the console it prints → claim it → click the page → hand it back
```

The operator's clicks travel the same policy chokepoint as the machine's. From
[`evidence/escalation/demo-escalation/trace.jsonl`](evidence/escalation/demo-escalation/trace.jsonl):

```text
 3 authorize  actor=automation epoch=1  safe (default)
14 escalate   actor=system     epoch=2  needs_human
16 lease      actor=human      epoch=3  demo-operator
18 authorize  actor=human      epoch=3  elevated (action:raw_input)
19 dispatch   actor=human      epoch=3
28 lease      actor=human      epoch=4  released
29 lease      actor=automation epoch=5  resumed
```

## Where to read more

| | |
|---|---|
| **The design write-up** | [`REPORT.md`](REPORT.md) — architecture, schema, determinism, escalation, safety, cuts. Also [rendered in three pages](https://adityamhaske.github.io/interface.ai/report/index.html), with [PDF](https://adityamhaske.github.io/interface.ai/report/REPORT.pdf) and Markdown downloads |
| **The artifact** | [`evidence/capabilities/`](evidence/capabilities/) — start here; it is the focal point |
| **Evidence from every run** | [`evidence/`](evidence/) — all four terminal statuses, with traces |
| **Everything else** | **[Documentation site](https://adityamhaske.github.io/interface.ai/)** — CLI reference, walkthrough, architecture, ADRs, runbooks |
| **Working agreement** | [`AGENTS.md`](AGENTS.md) — the invariants, and how they are enforced |

MIT licensed. The mock application is seeded with fictional data; no real credentials or PII exist in
this repository.
