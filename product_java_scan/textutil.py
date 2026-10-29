"""Plain-text cleanup and posting-date normalization."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

_RELATIVE_DATE = re.compile(r"posted|today|yesterday|day|hour", re.I)
_RELATIVE_COUNT = re.compile(r"(\d+)\s*(hour|day|week)s?\b", re.I)
POSTING_MAX_AGE_DAYS = 7


def plain_text(value: str) -> str:
    raw = value or ""
    if "<" in raw and ">" in raw:
        raw = BeautifulSoup(raw, "lxml").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", raw).strip()


def normalize_posted(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 10_000_000_000:
            stamp = stamp / 1000.0
        if stamp > 1_000_000_000:
            return datetime.fromtimestamp(stamp, tz=UTC).date().isoformat()
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    try:
        return date_parser.parse(raw).date().isoformat()
    except (ValueError, OverflowError, TypeError):
        if _RELATIVE_DATE.search(raw):
            return raw[:48]
        return ""


def posted_within_week(value: str, today: date) -> bool | None:
    """True when the posting date is within the last 7 days. None if unknown."""
    raw = (value or "").strip()
    if not raw:
        return None
    lowered = raw.casefold()
    if re.search(r"\b(today|yesterday)\b", lowered) or re.search(r"\bhours?\b", lowered):
        return True
    counted = _RELATIVE_COUNT.search(lowered)
    if counted and not re.search(r"\d{4}", lowered):
        count = int(counted.group(1))
        unit = counted.group(2)
        if unit == "hour":
            return True
        if unit == "day":
            return count <= POSTING_MAX_AGE_DAYS
        return count <= 1
    try:
        posted = date_parser.parse(raw).date()
    except (ValueError, OverflowError, TypeError):
        return None
    age = (today - posted).days
    return -1 <= age <= POSTING_MAX_AGE_DAYS
