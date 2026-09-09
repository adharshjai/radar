"""Heuristic relevance and action detection for email (works without any AI)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .dates import classify_line, find_dates, find_relative_dates
from .models import Item, iso

ACTION_RE = re.compile(
    r"\b(due|deadline|submit|submission|register|registration|sign[- ]?up|rsvp|required|mandatory|reminder|"
    r"exam|quiz|midterm|final|lab\b|action required|respond|reply by|confirm|complete the|fill out|form\b|survey|"
    r"apply by|application|last day|expires?|overdue|payment|balance|hold on your account|schedule|appointment|"
    r"interview|meeting|attend|attendance|office hours|cancel+ed|rescheduled|moved to|extension|makeup|make-up)\b", re.I)
BULK_RE = re.compile(r"unsubscribe|view (this|in) browser|manage (your )?preferences|email preferences", re.I)
NOREPLY_RE = re.compile(r"no-?reply|do-?not-?reply|donotreply|noreply|notifications?@|mailer-daemon|newsletter", re.I)
CANVAS_RE = re.compile(r"instructure\.com|canvas", re.I)
COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,5})\s*-?\s*(\d{2,4}[A-Z]?)\b")


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z]+", (name or "").lower()) if len(t) >= 4]


def course_code_variants(code: str) -> list[re.Pattern]:
    """'CHEM 101-002 Fall 2026' -> patterns matching 'CHEM 101', 'CHEM101', 'chem-101'."""
    m = COURSE_CODE_RE.search(code or "")
    if not m:
        return []
    dept, num = m.group(1), m.group(2)
    return [re.compile(rf"\b{dept}\s*-?\s*{num}\b", re.I)]


def score_email(item: Item, courses: list[dict], profile: dict, now: datetime, tz) -> None:
    """Mutates item.extra with relevance, action_required, mentioned_dates, course match."""
    ex = item.extra
    from_addr = (ex.get("from_addr") or "").lower()
    from_name = ex.get("from_name") or (item.sender or "")
    domain = from_addr.split("@")[-1] if "@" in from_addr else ""
    subject = item.title or ""
    text = f"{subject}\n{item.body or ''}"
    head = text[:6000]

    relevance = 10
    reasons: list[str] = []

    uni_domains = [d.lower().lstrip("@") for d in (profile.get("university_domains") or []) if d]
    if domain and any(domain == d or domain.endswith("." + d) for d in uni_domains):
        relevance += 35
        reasons.append("school address")

    canvas_notification = bool(CANVAS_RE.search(from_addr) or "notifications@" in from_addr and CANVAS_RE.search(head))
    if canvas_notification:
        ex["canvas_notification"] = True
        relevance += 10
        reasons.append("Canvas notification")

    # course match: code in subject/body, or a teacher's name as sender
    matched_course = None
    for c in courses:
        for rx in course_code_variants(c.get("code") or c.get("name") or ""):
            if rx.search(head):
                matched_course = c
                reasons.append(f"mentions {c.get('code') or c.get('name')}")
                break
        if matched_course:
            break
        for teacher in c.get("teachers") or []:
            toks = _tokens(teacher)
            if toks and any(t in from_name.lower() or t in from_addr for t in toks[-1:]):
                matched_course = c
                reasons.append(f"from {teacher}")
                break
        if matched_course:
            break
    if matched_course:
        relevance += 35
        item.course = matched_course.get("name")
        item.course_id = matched_course.get("id")

    for kw in profile.get("extra_keywords") or []:
        if kw and kw.lower() in head.lower():
            relevance += 15
            reasons.append(f"keyword {kw}")

    bulk = bool(BULK_RE.search(text)) or bool(NOREPLY_RE.search(from_addr)) and not canvas_notification
    if bulk:
        relevance -= 25
        reasons.append("bulk mail")

    action_hits = ACTION_RE.findall(subject) + ACTION_RE.findall(head[:2500])
    action_required = len(action_hits) >= 1 and not bulk or len(ACTION_RE.findall(subject)) >= 1
    if action_required:
        relevance += 15

    anchor = (item.posted_at or now).astimezone(tz).date()
    dates = []
    for d, _, _ in find_dates(head[:4000], anchor, anchor - timedelta(days=7), anchor + timedelta(days=120)):
        if anchor - timedelta(days=3) <= d <= anchor + timedelta(days=120):
            dates.append(d)
    for d, _ in find_relative_dates(head[:4000], anchor):
        dates.append(d)
    dates = sorted(set(dates))
    if dates and action_required:
        relevance += 10

    ex.update({
        "relevance": max(0, min(100, relevance)),
        "action_required": bool(action_required),
        "action_words": sorted({h.lower() for h in action_hits})[:8],
        "mentioned_dates": [d.isoformat() for d in dates[:6]],
        "bulk": bulk,
        "reasons": reasons,
        "hint": classify_line(subject),
    })
    if ex.get("flagged"):
        ex["relevance"] = min(100, ex["relevance"] + 20)
        ex["action_required"] = True
