"""Date parsing helpers (deterministic). All dates normalise to ISO YYYY-MM-DD."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

import dateparser

_SETTINGS = {"DATE_ORDER": "DMY", "PREFER_DAY_OF_MONTH": "first", "RETURN_AS_TIMEZONE_AWARE": False,
             "REQUIRE_PARTS": ["day", "month", "year"],
             # never use 'relative-time' (needs the machine's local timezone and is non-deterministic)
             "PARSERS": ["custom-formats", "absolute-time"]}
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DMY_TEXT = re.compile(r"^(\d{1,2})[-/ ]([A-Za-z]{3,9})\.?[-/ ,]+(\d{4})$")
_MDY_TEXT = re.compile(r"^([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})$")
_DMY_NUM = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _fast(text: str) -> Optional[str]:
    """Deterministic parser for the formats clinical documents actually use (day-first)."""
    t = text.strip().rstrip(".")
    try:
        m = _DMY_TEXT.match(t)
        if m and m.group(2)[:3].lower() in _MONTHS:
            return date(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1))).isoformat()
        m = _MDY_TEXT.match(t)
        if m and m.group(1)[:3].lower() in _MONTHS:
            return date(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2))).isoformat()
        m = _DMY_NUM.match(t)
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    except ValueError:
        return None
    return None

# Dates as they appear in clinical prose: 12-Mar-2026, 12 March 2026, 2026-03-12, 12/03/2026, March 12, 2026
DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[-/ ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?[-/ ,]+\d{4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.? \d{1,2},? \d{4}"
    r"|\d{1,2}/\d{1,2}/\d{4})\b",
    re.IGNORECASE,
)


def to_iso(text: str) -> Optional[str]:
    if not text:
        return None
    text = text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    fast = _fast(text)
    if fast:
        return fast
    if len(text) > 40 or not re.search(r"\d{4}", text):
        return None
    try:
        dt = dateparser.parse(text, settings=_SETTINGS)
    except Exception:
        return None
    return dt.date().isoformat() if dt else None


def dates_in(text: str) -> list[str]:
    """ISO dates found in free text."""
    out = []
    for m in DATE_RE.finditer(text or ""):
        iso = to_iso(m.group(0))
        if iso:
            out.append(iso)
    return out


def parse_iso(iso: str) -> Optional[date]:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").date()
    except Exception:
        return None
