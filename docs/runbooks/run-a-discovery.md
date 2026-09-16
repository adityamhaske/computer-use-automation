# Runbook — run a discovery

Discovery is the **probabilistic** half: an LLM drives a live UI to a goal and we record what it did.
It is the expensive path, run once per capability.

## Prerequisites

```bash
cp .env.example .env     # set OPENROUTER_API_KEY
make app                 # the mock back-office on :8811
```

## Run

```bash
cua discover \
  --goal "Look up member 12345 and read their current savings balance" \
  --target http://localhost:8811 \
  --out evidence/capabilities/
```

## What happens

1. The driver observes the live surface and normalizes it into a `UiSnapshot`.
2. The snapshot is rendered for the model — **not raw HTML**, and redacted on the way out.
3. The model emits a tool call from the closed action space. It cannot invent an action.
4. `PolicyEngine` authorizes it. An off-allowlist or irreversible action is blocked, not performed.
5. The resolver resolves the target; the dispatcher dispatches; the bus records everything.
6. Loop until the goal is met or a stop condition fires (max steps, timeout, token budget, dead-end).
7. On success, the compiler emits a **draft** `Capability`.

## Output

```
evidence/discovery/<run_id>/
  trace.jsonl        authorize / resolve / dispatch / observe, actor-tagged
  snapshots/         UiSnapshot per step
  screenshots/       redacted
  run_record.json    status, timings, budget
evidence/capabilities/<id>@<version>.yaml
```

## Reading the result

The artifact is a **draft** (`CapabilityApproval.state = draft`). Review before trusting it:

- Are the **typed inputs** right — and did the compiler lift the correct literals to parameters?
- Is the **checkpoint** goal-specific, or does it pass trivially on any loaded page?
- Do the targets read semantically (role + name + anchor), with CSS only under `hints`?
- Are the scaffolded `outcomes` and `recovery` rules correct? They are drafts, not truths.

## If it fails

| Symptom | Look at |
|---|---|
| Stops at `max_steps` | the `llm_call` events in `trace.jsonl` — is it looping on one screen? Usually a thin snapshot. |
| `POLICY_DENIED` | Expected if the goal needs an irreversible action. Check `config/policy.yaml`. |
| `TARGET_AMBIGUOUS` | The screen genuinely has two matching controls. Good — it refused. |
| Model can't find a control | Check the `UiSnapshot` in `snapshots/`. If the control isn't there, it's a perception gap, not a model gap. |

---

[Repository](https://github.com/adityamhaske/computer-use-automation) · [Documentation](https://adityamhaske.github.io/computer-use-automation/) · [Design write-up](https://adityamhaske.github.io/computer-use-automation/report/)
