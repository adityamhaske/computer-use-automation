# Runbook — run a discovery

Discovery is the **probabilistic** half: an LLM drives a live UI to a goal and we record what it did.
It is the expensive path, run once per capability.

## Prerequisites

```bash
cp .env.example .env     # set OMNIROUTE_API_KEY, CUA_LLM_BASE_URL, CUA_LLM_MODEL
make app                 # the mock back-office on :8811
```

Two choices worth making deliberately:

- **Pin a concrete model**, for example `CUA_LLM_MODEL=kr/claude-sonnet-4.5` on an OmniRoute gateway,
  rather than an alias such as `auto`. An alias lets the gateway pick, and the run record can then
  only say that *something* answered. The client records the model the response reports (and the
  gateway's provider and request id), so a pinned model makes "which model ran, and can that be
  checked against the gateway's own logs?" answerable.
- **Keep a local gateway on loopback.** OmniRoute binds every interface by default, which exposes
  `/v1/*` to anything on the network. Run it as
  `OMNIROUTE_SERVER_HOST=127.0.0.1 omniroute serve --port 20128 --no-open --daemon`, and use
  `http://localhost:20128/v1` as `CUA_LLM_BASE_URL`.

`make verify-live RUN=evidence/discovery/<run_id>` then proves the run was a live model rather than
a recorded script.

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
  trace.jsonl        authorize / resolve / dispatch / observe, actor-tagged;
                     run_end carries the stop reason and budget (steps, tokens, elapsed)
  snapshots/         UiSnapshot per step
  screenshots/       redacted
  run_record.json    timings, model, tokens_used -- summed from the llm_call events,
                     and equal to the budget's token count
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
