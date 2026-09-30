"""Run the whole suite under coverage and write a report a person can review.

"All tests pass" is a claim; this produces the evidence behind it: how many tests sit in each layer,
what each edge-case suite defends, and how much of the source the suite actually executes (line and
branch). It also writes a catalogue of every edge-case claim by name, because the test names state
the behaviour they defend -- reading the list is reading the specification of what is handled.

Run: python scripts/test_report.py       (or: make test-report)
Writes: evidence/tests/test-report.{md,json} and evidence/tests/edge-case-catalogue.md
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv/bin/python")
OUT = ROOT / "evidence" / "tests"

LAYERS = ("unit", "integration", "contract", "invariants", "e2e")


def run_suite(workdir: Path) -> tuple[int, Path, str]:
    junit = workdir / "junit.xml"
    proc = subprocess.run(
        [
            PY, "-m", "coverage", "run", "--branch", "--source=src/cua",
            f"--data-file={workdir / '.coverage'}",
            "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}",
        ],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    return proc.returncode, junit, proc.stdout[-400:]


def parse_junit(junit: Path) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    for case in ET.parse(junit).getroot().iter("testcase"):
        status = "passed"
        for child in case:
            if child.tag in {"failure", "error"}:
                status = "failed"
            elif child.tag == "skipped":
                status = "skipped"
        module = case.get("classname", "").replace(".", "/")
        cases.append(
            {
                "file": module,
                "name": case.get("name", ""),
                "status": status,
                "seconds": case.get("time", "0"),
            }
        )
    return cases


def coverage_by_package(workdir: Path) -> dict[str, object]:
    out = workdir / "coverage.json"
    subprocess.run(
        [PY, "-m", "coverage", "json", f"--data-file={workdir / '.coverage'}", "-o", str(out)],
        cwd=ROOT, capture_output=True, check=True,
    )  # fmt: skip
    data = json.loads(out.read_text(encoding="utf-8"))
    per: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])  # stmts, covered, branches, hit
    for path, info in data["files"].items():
        parts = Path(path).parts
        anchor = parts.index("cua")
        package = f"cua.{parts[anchor + 1]}" if len(parts) - anchor > 2 else "cua (top level)"
        summary = info["summary"]
        row = per[package]
        row[0] += summary["num_statements"]
        row[1] += summary["covered_lines"]
        row[2] += summary.get("num_branches", 0)
        row[3] += summary.get("covered_branches", 0)
    totals = data["totals"]
    branches = totals["num_branches"]
    return {
        "line_percent": round(totals["percent_statements_covered"], 1),
        "branch_percent": round(100 * totals["covered_branches"] / branches, 1) if branches else 0,
        "statements": totals["num_statements"],
        "packages": {
            name: {
                "statements": r[0],
                "line_percent": round(100 * r[1] / r[0], 1) if r[0] else 100.0,
                "branch_percent": round(100 * r[3] / r[2], 1) if r[2] else 100.0,
            }
            for name, r in sorted(per.items())
        },
    }


def claim(name: str) -> str:
    """`test_a_malformed_url_is_denied_not_raised` -> `a malformed url is denied not raised`."""
    return re.sub(r"^test_", "", name).replace("_", " ")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        code, junit, tail = run_suite(work)
        if not junit.exists():
            print(tail)
            return code or 1
        cases = parse_junit(junit)
        cov = coverage_by_package(work)

    by_layer: Counter[str] = Counter()
    by_status: Counter[str] = Counter(c["status"] for c in cases)
    edge_files: dict[str, list[dict[str, str]]] = defaultdict(list)
    for c in cases:
        layer = next((p for p in c["file"].split("/") if p in LAYERS), "other")
        by_layer[layer] += 1
        if "/test_edge_" in "/" + c["file"]:
            edge_files[c["file"]].append(c)
    edge_total = sum(len(v) for v in edge_files.values())
    seconds = round(sum(float(c["seconds"]) for c in cases), 1)

    report = {
        "generated": dt.date.today().isoformat(),
        "tests": len(cases),
        "passed": by_status["passed"],
        "failed": by_status["failed"],
        "skipped_or_deselected": by_status["skipped"],
        "edge_case_tests": edge_total,
        "tests_by_layer": dict(by_layer),
        "suite_seconds": seconds,
        "coverage": cov,
    }
    (OUT / "test-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    md = [
        "# Test report",
        "",
        f"Generated {report['generated']} by `make test-report`: the whole suite, offline, with no",
        "API key, under line + branch coverage.",
        "",
        f"**{report['passed']} passed, {report['failed']} failed** of {report['tests']} tests in "
        f"{seconds}s of test time. {edge_total} of them are edge-case tests from the hardening "
        "pass.",
        "",
        f"**Coverage of `src/cua`: {cov['line_percent']}% of lines, {cov['branch_percent']}% of "
        f"branches** ({cov['statements']} statements).",
        "",
        "## By layer",
        "",
        "| Layer | Tests | What it proves |",
        "|---|---:|---|",
        f"| `unit/` | {by_layer['unit']} | Pure logic: schema, resolver, policy, redaction |",
        f"| `integration/` | {by_layer['integration']} | Full flows against the mock app, "
        "real browser where it matters |",
        f"| `contract/` | {by_layer['contract']} | Schema stability and artifact validity |",
        f"| `invariants/` | {by_layer['invariants']} | The ten architectural claims |",
        "",
        "## Coverage by package",
        "",
        "| Package | Statements | Lines | Branches |",
        "|---|---:|---:|---:|",
    ]
    packages = cov["packages"]
    assert isinstance(packages, dict)
    for name, row in packages.items():
        md.append(
            f"| `{name}` | {row['statements']} | {row['line_percent']}% | "
            f"{row['branch_percent']}% |"
        )
    md += [
        "",
        "Read the percentages with one caveat: the CLI entry points and the browser driver run",
        "largely in child processes (`make demo`, the UI smoke test), which an in-process",
        "measurement cannot see, so `cua.cli` and `cua.surfaces` look lower than they are",
        "exercised. The end-to-end story is proven by `make demo` and its evidence, not by this",
        "table.",
        "",
        "## Edge-case suites",
        "",
        "Each file targets one subsystem with boundary, malformed, hostile and out-of-order input.",
        "The claims they defend are listed in [`edge-case-catalogue.md`](edge-case-catalogue.md).",
        "",
        "| Suite | Tests |",
        "|---|---:|",
    ]
    for file, items in sorted(edge_files.items()):
        md.append(f"| `{file}.py` | {len(items)} |")
    md += [
        "",
        "Proof that these tests can fail: [`mutation-report.md`](mutation-report.md).",
        "",
    ]
    (OUT / "test-report.md").write_text("\n".join(md), encoding="utf-8")

    cat = [
        "# Edge-case catalogue",
        "",
        "Every behaviour the edge-case suites defend, by subsystem. A test name states the",
        "claim it defends, so this list is the specification of what the system must handle.",
        "`xN` means the claim is checked against N inputs.",
        "",
    ]
    for file, items in sorted(edge_files.items()):
        counts: Counter[str] = Counter(re.sub(r"\[.*\]$", "", c["name"]) for c in items)
        cat += [f"## `{file}.py` -- {len(items)} tests", ""]
        for name, n in counts.items():
            cat.append(f"- {claim(name)}" + (f" (x{n})" if n > 1 else ""))
        cat.append("")
    (OUT / "edge-case-catalogue.md").write_text("\n".join(cat), encoding="utf-8")

    print(
        f"{report['passed']} passed, {report['failed']} failed; coverage "
        f"{cov['line_percent']}% lines / {cov['branch_percent']}% branches -> evidence/tests/"
    )
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
