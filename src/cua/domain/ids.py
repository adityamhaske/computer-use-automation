"""Identifiers the system mints for runs, decisions, sessions and interventions.

An id is written into every evidence sink, and every sink is redacted. The redactor reads a bare
run of digits as an account number (`\\b\\d{8,17}\\b`), so an id whose hex happens to come out all
digits -- about 0.9% of ten-character ids, 0.35% of twelve -- would be rewritten to
`<redacted:account_number>` in the trace. For a decision id that corrupts the very identifier used
to match a dispatch to its authorization, and it does so rarely enough to look like a flaky test
rather than a defect. Drawing again is free, so an id always contains at least one letter.
"""

from __future__ import annotations

import uuid


def new_id(prefix: str, length: int = 10) -> str:
    """`<prefix>-<length hex characters>`, never all digits."""
    while True:
        token = uuid.uuid4().hex[:length]
        if not token.isdigit():
            return f"{prefix}-{token}"
