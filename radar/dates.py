"""Best-effort extraction of dates mentioned in free text (syllabi, emails, announcements)."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_RE = re.compile(
    rf"\b(?:(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?\.?,?\s+)?"
    rf"(?:{_MONTH_RE}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?"
    rf"|(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH_RE}\.?(?:,?\s+(\d{{4}}))?"
    rf"|(\d{{1,2}})/(\d{{1,2}})(?:/(\d{{2,4}}))?)\b", re.I)
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b|\b(\d{1,2}):(\d{2})\b|\bnoon\b|\bmidnight\b", re.I)
RELATIVE_RE = re.compile(r"\b(today|tomorrow|tonight|this (monday|tuesday|wednesday|thursday|friday|saturday|sunday)|next (monday|tuesday|wednesday|thursday|friday|saturday|sunday)|(?:on |by |before |until |due |us |meet )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b", re.I)

HINT_RULES = [
    ("exam", re.compile(r"\b(final exam|midterm|exam|test\b|quiz)", re.I)),
    ("deadline", re.compile(r"\b(due|deadline|submit|submission|project|paper|essay|report|presentation|proposal|homework|hw\b|lab report|problem set|pset)", re.I)),
    ("no_class", re.compile(r"\b(no class|holiday|break|recess|no lecture|no lab|cancel)", re.I)),
    ("admin", re.compile(r"\b(drop|withdraw|add/drop|registration|census|last day)", re.I)),
    ("lab", re.compile(r"\blab\b", re.I)),
]


def _pick_year(month: int, day: int, anchor: date, window_start: date | None, window_end: date | None) -> int | None:
    candidates = [anchor.year - 1, anchor.year, anchor.year + 1]
    for y in candidates:
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if window_start and window_end and window_start <= d <= window_end:
            return y
    # No window hit: choose the year that puts the date closest to (and not far before) the anchor.
    best, best_score = None, None
    for y in candidates:
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        delta = (d - anchor).days
        score = abs(delta) if delta >= -45 else abs(delta) + 400
        if best_score is None or score < best_score:
            best, best_score = y, score
    return best


def _parse_time(text: str) -> time | None:
    m = TIME_RE.search(text)
    if not m:
        return None
    if m.group(0).lower() == "noon":
        return time(12, 0)
    if m.group(0).lower() == "midnight":
        return time(23, 59)
    if m.group(1):
        h = int(m.group(1)); mi = int(m.group(2) or 0); ap = m.group(3).lower().replace(".", "")
        if h > 12:
            return None
        if ap == "pm" and h != 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        return time(h, mi)
    if m.group(4):
        h, mi = int(m.group(4)), int(m.group(5))
        if h < 24 and mi < 60:
            return time(h, mi)
    return None


def find_dates(text: str, anchor: date, window_start: date | None = None, window_end: date | None = None) -> list[tuple[date, int, int]]:
    """Return (date, start, end) for each absolute date mention in text."""
    out = []
    for m in DATE_RE.finditer(text):
        try:
            if m.group(1):
                month = MONTHS.get(m.group(1).lower()[:3]); day = int(m.group(2)); year = m.group(3)
            elif m.group(5):
                month = MONTHS.get(m.group(5).lower()[:3]); day = int(m.group(4)); year = m.group(6)
            else:
                month = int(m.group(7)); day = int(m.group(8)); year = m.group(9)
                if month > 12 or day > 31:
                    continue
            if not month or not (1 <= day <= 31):
                continue
            if year:
                y = int(year)
                if y < 100:
                    y += 2000
            else:
                y = _pick_year(month, day, anchor, window_start, window_end)
            if not y:
                continue
            out.append((date(y, month, day), m.start(), m.end()))
        except (ValueError, TypeError):
            continue
    return out


def find_relative_dates(text: str, anchor: date) -> list[tuple[date, str]]:
    out = []
    for m in RELATIVE_RE.finditer(text):
        word = m.group(0).lower()
        if word in ("today", "tonight"):
            out.append((anchor, word))
        elif word == "tomorrow":
            out.append((anchor + timedelta(days=1), word))
        else:
            wd = (m.group(2) or m.group(3) or m.group(4) or "").lower()
            if wd in WEEKDAYS:
                delta = (WEEKDAYS.index(wd) - anchor.weekday()) % 7
                if delta == 0 and "next" in word:
                    delta = 7
                if word.startswith("next") and delta < 7:
                    delta += 7 if delta <= 2 else 0
                out.append((anchor + timedelta(days=delta), word))
    return out


def classify_line(line: str) -> str:
    for hint, rx in HINT_RULES:
        if rx.search(line):
            return hint
    return "date"


def dated_lines(text: str, anchor: date, window_start: date | None, window_end: date | None, tz, max_lines=80) -> list[dict]:
    """Split text into lines and return those carrying a date, with a resolved datetime and a hint."""
    results = []
    seen = set()
    for raw in re.split(r"\n|(?<=[.;])\s{2,}", text):
        line = " ".join(raw.split())
        if len(line) < 6 or len(line) > 400:
            continue
        found = find_dates(line, anchor, window_start, window_end)
        if not found:
            continue
        hint = classify_line(line)
        t = _parse_time(line)
        for d, _, _ in found[:3]:
            if window_start and window_end and not (window_start - timedelta(days=21) <= d <= window_end + timedelta(days=21)):
                continue
            key = (d, line[:80])
            if key in seen:
                continue
            seen.add(key)
            local = datetime.combine(d, t or time(23, 59)).replace(tzinfo=tz)
            results.append({"date": d, "when": local.astimezone(timezone.utc), "has_time": t is not None, "line": line, "hint": hint})
            if len(results) >= max_lines:
                return results
    return results
