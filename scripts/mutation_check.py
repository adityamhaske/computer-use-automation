"""Break each invariant on purpose and confirm the test suite notices.

A green suite proves the claims hold today. It does not prove the tests would fail if a claim
stopped holding -- a test that cannot fail is decoration. This script is the other half of that
argument: it applies ONE small deliberate violation at a time (skip the lease check, let an
ambiguous target tie-break, stop redacting, add a forbidden import ...), runs the tests that are
supposed to defend the invariant, and records whether any of them went red. A mutation that nothing
catches is a hole in the suite, reported as SURVIVED and failing the run.

Every source file is restored byte-for-byte after its mutation, in a `finally`, and verified; a file
that does not come back identical aborts the whole run loudly rather than leaving a broken tree.

Run: python scripts/mutation_check.py      (or: make mutation-check)
Writes: evidence/tests/mutation-report.{md,json}
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv/bin/python")
LINT_IMPORTS = str(ROOT / ".venv/bin/lint-imports")
OUT_DIR = ROOT / "evidence" / "tests"


@dataclass(frozen=True)
class Mutation:
    invariant: str
    what: str
    file: str
    old: str
    new: str
    command: tuple[str, ...]


def pytest(*paths: str) -> tuple[str, ...]:
    return (PY, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "--no-header", "-rf", *paths)


LINT = (LINT_IMPORTS,)

MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        "1. Replay never imports the LLM",
        "replay imports the discovery agent",
        "src/cua/replay/inputs.py",
        "from cua.domain.capability import Capability",
        "from cua.domain.capability import Capability\nimport cua.agent.loop  # noqa: F401",
        LINT,
    ),
    Mutation(
        "2. No action reaches a driver without policy authorization",
        "a denied action is dispatched anyway",
        "src/cua/runtime/dispatcher.py",
        "if decision.authorized is None:",
        "if False:",
        pytest(
            "tests/integration/test_dispatcher.py", "tests/invariants/test_policy_chokepoint.py"
        ),
    ),
    Mutation(
        "2. No action reaches a driver without policy authorization",
        "the allowlist stops refusing off-list navigation",
        "src/cua/policy/engine.py",
        "if not verdict.allowed:",
        "if False:",
        pytest("tests/unit/test_policy_pieces.py", "tests/integration/test_dispatcher.py"),
    ),
    Mutation(
        "3. Targets are semantic, never raw selectors",
        "an exact-name match stops comparing the accessible name",
        "src/cua/targeting/candidates.py",
        "return [n for n in pool if normalize_name(n.name) == wanted]",
        "return [n for n in pool]",
        pytest("tests/unit/test_resolver.py", "tests/unit/test_target_ladder.py"),
    ),
    Mutation(
        "4. Human input cannot bypass policy",
        "automation may originate a human-only raw_input",
        "src/cua/policy/engine.py",
        "if action.type in HUMAN_ONLY_ACTIONS and actor is not Actor.HUMAN:",
        "if False:",
        pytest("tests/invariants/test_human_control_safety.py", "tests/unit/test_policy_pieces.py"),
    ),
    Mutation(
        "5. Unknown and ambiguous states fail closed",
        "an ambiguous target is tie-broken to the first candidate",
        "src/cua/targeting/resolver.py",
        "if len(found) > 1:",
        "if False:",
        pytest("tests/unit/test_resolver.py"),
    ),
    Mutation(
        "6. Every sink is redacted",
        "the redactor stops applying its pattern rules",
        "src/cua/policy/redact.py",
        "for label, pattern in self._compiled:",
        "for label, pattern in ():",
        pytest("tests/invariants/test_redaction.py"),
    ),
    Mutation(
        "7. Business outcomes are not failures",
        "'no such member' exits non-zero",
        "src/cua/domain/result.py",
        "RunStatus.BUSINESS_OUTCOME: 0,",
        "RunStatus.BUSINESS_OUTCOME: 1,",
        pytest("tests/unit/test_taxonomy.py", "tests/integration/test_fault_matrix.py"),
    ),
    Mutation(
        "8. Automation acts only while it holds the session",
        "the stale-lease-epoch check is skipped",
        "src/cua/runtime/dispatcher.py",
        "if expected_epoch is not None and lease_epoch != expected_epoch:",
        "if False:",
        pytest("tests/integration/test_dispatcher.py", "tests/integration/test_handoff.py"),
    ),
    Mutation(
        "9. Capabilities are immutable and content-hashed",
        "a tampered artifact is loaded without verifying its hash",
        "src/cua/domain/serde.py",
        "if verify_hash and capability.content_hash and not capability.hash_is_valid():",
        "if False:",
        pytest("tests/contract/test_capability_schema.py", "tests/integration/test_catalog.py"),
    ),
    Mutation(
        "10. domain/ is pure",
        "the domain layer imports the policy package",
        "src/cua/domain/ids.py",
        "import uuid",
        "import uuid\nfrom cua.policy import config  # noqa: F401",
        LINT,
    ),
    # Guards for defects found and fixed in the edge-case pass: each re-introduces the defect.
    Mutation(
        "Edge-case fixes",
        "a malformed URL raises out of the allowlist instead of being denied",
        "src/cua/policy/allowlist.py",
        "except ValueError:",
        "except KeyError:",
        pytest("tests/unit/test_edge_policy_allowlist.py"),
    ),
    Mutation(
        "Edge-case fixes",
        "the wrong-action metric ignores the expected status",
        "src/cua/evals/scorers.py",
        "if expected.expect_status is not RunStatus.SUCCESS:",
        "if False:",
        pytest("tests/unit/test_edge_cli_evals_codegen_scorers.py"),
    ),
    Mutation(
        "Edge-case fixes",
        "a refused stale-lease attempt is counted as an unauthorized dispatch",
        "src/cua/evidence/bus.py",
        'and not event.get("refused")',
        "",
        pytest("tests/integration/test_edge_runtime_dispatch_pipeline.py"),
    ),
    Mutation(
        "Edge-case fixes",
        "minted ids may be all digits and get redacted as account numbers",
        "src/cua/domain/ids.py",
        "if not token.isdigit():",
        "if True:",
        pytest("tests/unit/test_edge_domain_ids.py"),
    ),
    Mutation(
        "Edge-case fixes",
        "an explicit empty container is pruned and the sealed artifact reloads as tampered",
        "src/cua/domain/serde.py",
        "return _prune_model(model, payload) if prune else payload",
        "return _prune(payload) if prune else payload",
        pytest("tests/contract/test_edge_domain_capability_schema.py"),
    ),
)


@dataclass
class Result:
    invariant: str
    what: str
    file: str
    verdict: str  # KILLED | SURVIVED | INCONCLUSIVE
    caught_by: str
    seconds: float


def run(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=600, check=False
    )


def first_failure(mutation: Mutation, proc: subprocess.CompletedProcess[str]) -> str:
    text = proc.stdout + proc.stderr
    if mutation.command == LINT:
        broken = re.findall(r"^(.*) BROKEN", text, flags=re.MULTILINE)
        return broken[0].strip() if broken else "import-linter contract"
    failed = re.findall(r"^FAILED (\S+)", text, flags=re.MULTILINE)
    return failed[0] if failed else "a test"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    baseline_ok: dict[tuple[str, ...], bool] = {}

    for mutation in MUTATIONS:
        path = ROOT / mutation.file
        original = path.read_bytes()
        source = original.decode("utf-8")
        if source.count(mutation.old) != 1:
            print(f"!! {mutation.file}: mutation site found {source.count(mutation.old)}x, not 1")
            results.append(
                Result(
                    mutation.invariant,
                    mutation.what,
                    mutation.file,
                    "INCONCLUSIVE",
                    "site not found",
                    0,
                )
            )
            continue

        # A kill only means something if the same tests pass on the real code.
        if mutation.command not in baseline_ok:
            baseline_ok[mutation.command] = run(mutation.command).returncode == 0
        if not baseline_ok[mutation.command]:
            print(f"!! baseline already failing for {mutation.file}")
            results.append(
                Result(
                    mutation.invariant,
                    mutation.what,
                    mutation.file,
                    "INCONCLUSIVE",
                    "baseline red",
                    0,
                )
            )
            continue

        started = time.monotonic()
        try:
            path.write_text(source.replace(mutation.old, mutation.new), encoding="utf-8")
            proc = run(mutation.command)
        finally:
            path.write_bytes(original)
            if path.read_bytes() != original:  # pragma: no cover -- would be a catastrophe
                raise SystemExit(f"FATAL: {mutation.file} was not restored; restore it by hand")
        elapsed = time.monotonic() - started

        if proc.returncode == 0:
            verdict, caught = "SURVIVED", "nothing -- a hole in the suite"
        elif proc.returncode == 1:
            verdict, caught = "KILLED", first_failure(mutation, proc)
        else:
            verdict, caught = "INCONCLUSIVE", f"exit {proc.returncode} (not a test failure)"
        results.append(
            Result(
                mutation.invariant, mutation.what, mutation.file, verdict, caught, round(elapsed, 1)
            )
        )
        print(f"{verdict:12} {mutation.invariant} :: {mutation.what}")

    killed = sum(1 for r in results if r.verdict == "KILLED")
    (OUT_DIR / "mutation-report.json").write_text(
        json.dumps(
            {"mutations": len(results), "killed": killed, "results": [asdict(r) for r in results]},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    lines = [
        "# Mutation check",
        "",
        "A green suite shows the claims hold today. This shows the tests would **fail** if a",
        "claim stopped holding: each row deliberately breaks the code in one way, runs the tests",
        "meant to defend it, and records whether any went red. The source is restored",
        "byte-for-byte after each one. Regenerate with `make mutation-check`.",
        "",
        f"**{killed} of {len(results)} mutations caught.**"
        + ("" if killed == len(results) else "  Anything not KILLED is a hole in the suite."),
        "",
        "| Invariant / area | Deliberate violation | Verdict | First test to catch it |",
        "|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r.invariant} | {r.what} | **{r.verdict}** | `{r.caught_by}` |")
    lines.append("")
    (OUT_DIR / "mutation-report.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"\n{killed}/{len(results)} mutations caught -> evidence/tests/mutation-report.md")
    return 0 if killed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
