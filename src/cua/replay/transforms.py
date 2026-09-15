"""Named normalizers an artifact can declare on an extraction.

`Extract.transform` is part of the published contract -- `OutputSpec.json_schema()` emits
`{"type": "string", "format": "money"}` for a money output, so a calling agent is told the
shape it will receive. Applying the transform is what makes that a promise rather than a
label: without it the field was declared, documented, used by the reviewed artifact, and read
by nothing.

Normalization is deliberately *shape-preserving where the shape is the information*. A balance stays
a string: `"$4,210.55"` becomes `"4210.55"`, not a float, because binary floating point is the wrong
type for money and a caller that wants a decimal should parse the canonical form itself.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_THOUSANDS = re.compile(r"[,\s]")
_CURRENCY = re.compile(r"[^\d.\-]")
_US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


class UnknownTransformError(ValueError):
    """An artifact declared a transform this executor does not implement.

    Raised rather than ignored: silently returning the raw value would mean an artifact whose
    contract says `money` hands the caller `"$4,210.55"` on one deployment and `"4210.55"` on
    another, which is exactly the kind of difference a typed contract exists to remove.
    """


def _money(value: str) -> str:
    """`"$4,210.55"` -> `"4210.55"`. Sign preserved; currency symbol and grouping removed."""
    cleaned = _CURRENCY.sub("", _THOUSANDS.sub("", value.strip()))
    return cleaned or value.strip()


def _date(value: str) -> str:
    """US `MM/DD/YYYY` -> ISO `YYYY-MM-DD`. Anything already ISO is returned unchanged."""
    text = value.strip()
    match = _US_DATE.match(text)
    if not match:
        return text
    month, day, year = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}"


TRANSFORMS: dict[str, Callable[[str], str]] = {"money": _money, "date": _date}


def apply_transform(name: str, value: str) -> str:
    """Apply a declared transform by name, refusing one this executor does not know."""
    try:
        transform = TRANSFORMS[name]
    except KeyError:
        raise UnknownTransformError(
            f"unknown transform {name!r}; this executor implements {sorted(TRANSFORMS)}"
        ) from None
    return transform(value)
