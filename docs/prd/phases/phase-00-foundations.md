# Phase 00 — Foundations & repo scaffold

**Objective.** Stand up the repository so that every later phase inherits type safety, lint, tests,
and — most importantly — the *mechanically enforced architectural invariants*.

**Why first.** The invariants are the design. If they arrive after the code, they arrive after the
code has already violated them.

## Scope

- `pyproject.toml`: deps, ruff, mypy `strict`, pytest markers (`live`, `browser`, `slow`)
- `.importlinter`: five contracts — `no-llm-in-replay`, `policy-chokepoint`,
  `surface-neutral-targeting`, `pure-domain`, `layers`
- `Makefile`: `setup`, `check`, `test`, `invariants`, `app`, `console`, `demo`, `eval`
- `.pre-commit-config.yaml`, `.gitignore`, `.env.example`
- `.github/workflows/ci.yml` — offline; **no API key configured, on purpose**
- `AGENTS.md` (nine invariants) + `CLAUDE.md` pointer
- Docs tree: 5 ADRs, 4 design docs, PRD, traceability, these phase docs
- Package skeleton under `src/cua/` with module docstrings stating each package's responsibility
- `evidence/` skeleton with `.gitkeep`s

## Non-scope

Any behaviour. This phase produces structure and guarantees, not features.

## Exit criteria

- [x] `make check` passes (lint, typecheck, invariants, tests)
- [x] **`lint-imports` fails loudly when an invariant is deliberately violated**
- [x] `docs/README.md` links resolve
- [ ] CI green *(verified on first push)*

### Verification record

`ruff` clean, `ruff format --check` clean, `mypy --strict` clean (17 source files), `lint-imports`
5/5 contracts kept.

The contracts were then **deliberately broken** to confirm they are not decorative —
`import cua.agent` added to `cua/replay`, `import httpx` added to `cua/domain`:

```
Replay must not import the agent or any LLM client   BROKEN
Domain is pure (no I/O, no other cua packages)       BROKEN
Package layering                                     BROKEN
Contracts: 2 kept, 3 broken.                         exit code 1
```

Both imports were then removed and the contracts returned to 5/5 kept.

**Caveat, recorded honestly:** with no real code yet, import-linter analyzed *0 dependencies*, so
the contracts currently pass partly by having nothing to check. Each contract becomes meaningful
only once its source package has real imports. Phases 02–09 must re-verify: the relevant contract is
re-checked at the end of the phase that first gives its package something to import.

`tests/invariants/test_import_contracts.py` asserts both that every required contract is *declared*
(so one cannot be silently deleted to make the suite green) and that they all pass.

## Risks

| Risk | Mitigation |
|---|---|
| Import contracts written against packages that don't exist yet pass vacuously | Phase 02+ adds real modules; a contract is only trustworthy once its source package has code. Re-verify each contract when its package first gains an import. |
| `mypy --strict` on Pydantic needs the plugin | Configured in `pyproject.toml`. |

---

[Repository](https://github.com/adityamhaske/interface.ai) · [Documentation](https://adityamhaske.github.io/interface.ai/) · [Design write-up](https://adityamhaske.github.io/interface.ai/report/)
