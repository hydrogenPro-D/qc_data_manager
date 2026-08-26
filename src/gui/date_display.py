"""Shared timestamp formatting.

Timestamps arrive in mixed spellings, DATETIMEOFFSET from the database, ISO,
and dd.mm.yyyy. Every tab renders them through this one module so a date column
reads the same wherever it appears.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

_BLANKS = {"-", "nan", "nat", "none", "<na>", ""}

# Date-only spellings, the fallback when a value carries no time. dbo.MetaData
# holds a full timestamp, but a date typed by hand or read off a report does
# not, and both reach this module.
_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%Y/%m/%d", "%d/%m/%Y")

_TS_FORMATS = (
    # Offset-aware first (DATETIMEOFFSET columns arrive as '…+00:00'); %z would
    # otherwise fail to match and the value would fall through unformatted.
    "%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
    "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M",
)


def parse_timestamp(value: Any) -> dt.datetime | None:
    """Parse a creation timestamp (DATETIME2, ISO, dd.mm.yyyy HH:MM) to a
    ``datetime``. Falls back to a date-only value (time 00:00). None if blank."""
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    text = str(value).strip()
    if not text or text.lower() in _BLANKS:
        return None
    for fmt in _TS_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    day = _parse_date(text)
    return dt.datetime(day.year, day.month, day.day) if day else None


def _parse_date(text: str) -> dt.date | None:
    """A value carrying no time at all, which is most of what gets typed."""
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def format_timestamp_for_display(value: Any) -> str:
    """Render a creation timestamp as ``dd.mm.yyyy HH:MM``; blank/NULL → ''."""
    ts = parse_timestamp(value)
    return ts.strftime("%d.%m.%Y %H:%M") if ts else ""
