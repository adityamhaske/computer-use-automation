"""Fail if anything key-shaped is committed.

The brief's ground rule is "keep secrets out of the repo". This project's whole safety story is
redaction, so a leaked gateway key in the repo would undercut it more than most. The scan is
deliberately dependency-free: it runs the same way in `make check`, in CI and on a laptop.

    python scripts/scan_secrets.py            # every tracked file
    python scripts/scan_secrets.py --history  # and every line ever added on any branch

Patterns are for credentials with a recognisable shape. It is a tripwire, not a guarantee: a
password with no distinguishing shape will not be caught, which is why `.env` is gitignored as well.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS: dict[str, re.Pattern[str]] = {
    "OpenAI/OpenRouter-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}"),
    "Anthropic key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "AWS access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9]{36,}\b|github_pat_\w{40,}"),
    "Private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "Bearer token": re.compile(r"\bBearer\s+(?!\{|\$|<)[A-Za-z0-9._~+/-]{24,}"),
    # A *_KEY / *_TOKEN / *_SECRET assignment with a real-looking literal on the right. The
    # mock app's fixture password is deliberately not matched: it is documented as fake, and the
    # `{$secret: ref}` reference syntax is excluded by the lookbehind -- a reference is the
    # opposite of a leaked value.
    "Assigned credential": re.compile(
        r"""(?i)(?<![$\w])[A-Z0-9_]*(?:API_KEY|SECRET|TOKEN)\b\s*[:=]\s*['"]?(?![\$<{'"\s])[A-Za-z0-9/_+.-]{20,}"""
    ),
}

# Strings that look like credentials but are placeholders, examples or redaction markers.
ALLOWED = ("your-key-here", "not-a-real", "example", "placeholder", "redacted", "xxxxxxxx")

SKIP_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".pdf", ".ico", ".woff", ".woff2"}
MAX_BYTES = 2_000_000


def _findings(text: str, where: str) -> list[str]:
    hits: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for label, pattern in PATTERNS.items():
            match = pattern.search(line)
            if match and not any(token in line.lower() for token in ALLOWED):
                # The matched text is never printed: a scanner that echoes the secret into CI
                # logs is itself a leak.
                hits.append(f"{where}:{lineno}: {label}")
    return hits


def scan_tree() -> list[str]:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.split(b"\0")
    hits: list[str] = []
    for raw in filter(None, tracked):
        path = ROOT / raw.decode()
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        if path.stat().st_size > MAX_BYTES:
            continue
        hits += _findings(path.read_text("utf-8", errors="ignore"), str(path.relative_to(ROOT)))
    return hits


def scan_history() -> list[str]:
    log = subprocess.run(
        ["git", "log", "--all", "-p", "--no-color", "--format=commit %h"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", errors="ignore")
    added = "\n".join(
        line[1:] for line in log.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    return _findings(added, "history")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="also scan every added line")
    args = parser.parse_args()

    hits = scan_tree() + (scan_history() if args.history else [])
    if hits:
        print("Possible secrets committed:", file=sys.stderr)
        for hit in hits:
            print(f"  {hit}", file=sys.stderr)
        return 1
    scope = "tracked files and history" if args.history else "tracked files"
    print(f"scan_secrets: no key-shaped strings in {scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
