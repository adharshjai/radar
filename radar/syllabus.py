"""Turn syllabus text (Canvas syllabus page, front page, syllabus PDF/DOCX) into dated items."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from .dates import dated_lines
from .models import Item, content_hash, parse_dt


def term_window(course: dict | None, now: datetime) -> tuple[date, date]:
    start = end = None
    if course:
        ex = course.get("extra") or {}
        start = parse_dt(ex.get("term_start"))
        end = parse_dt(ex.get("term_end"))
    s = start.date() if start else (now - timedelta(days=60)).date()
    e = end.date() if end else (now + timedelta(days=150)).date()
    return s, e


def items_from_text(course: dict, text: str, now: datetime, tz) -> list[Item]:
    """Regex fallback: every line with a date becomes a syllabus_event."""
    start, end = term_window(course, now)
    out = []
    for hit in dated_lines(text, now.astimezone(tz).date(), start, end, tz):
        title = hit["line"]
        if len(title) > 140:
            title = title[:137] + "..."
        actionable = hit["hint"] in ("exam", "deadline", "lab")
        out.append(Item(
            uid=f"syllabus:{course['id']}:{content_hash(hit['date'].isoformat(), hit['line'][:80])}",
            source="syllabus", kind="syllabus_event", title=title, course=course["name"], course_id=course["id"],
            due_at=hit["when"] if actionable else None, starts_at=None if actionable else hit["when"],
            url=f"{course.get('url', '')}/assignments/syllabus", status="open" if actionable else "info",
            body=hit["line"], extra={"hint": hit["hint"], "all_day": not hit["has_time"], "from": "syllabus-regex"},
        ))
    return out


def items_from_ai(course: dict, ai: dict, now: datetime, tz) -> list[Item]:
    """Convert a structured syllabus extraction (see enrich/ai.py) into items."""
    out: list[Item] = []
    base_url = course.get("url", "")

    def when(d: str | None, t: str | None, default_time: time) -> datetime | None:
        if not d:
            return None
        try:
            day = date.fromisoformat(d[:10])
        except ValueError:
            return None
        tm = default_time
        if t:
            try:
                hh, mm = t.split(":")[:2]
                tm = time(int(hh), int(mm))
            except ValueError:
                pass
        return datetime.combine(day, tm).replace(tzinfo=tz).astimezone(timezone.utc)

    for ex in ai.get("exams") or []:
        w = when(ex.get("date"), ex.get("time"), time(23, 59))
        if not w:
            continue
        out.append(Item(
            uid=f"syllabus:{course['id']}:exam:{content_hash(ex.get('name'), ex.get('date'))}", source="syllabus",
            kind="syllabus_event", title=ex.get("name") or "Exam", course=course["name"], course_id=course["id"],
            due_at=w, url=f"{base_url}/assignments/syllabus", status="open",
            body=" · ".join(x for x in [ex.get("location"), ex.get("notes")] if x),
            extra={"hint": "exam", "all_day": not ex.get("time"), "from": "syllabus-ai", "location": ex.get("location")},
        ))
    for dl in ai.get("deadlines") or []:
        w = when(dl.get("date"), dl.get("time"), time(23, 59))
        if not w:
            continue
        out.append(Item(
            uid=f"syllabus:{course['id']}:deadline:{content_hash(dl.get('title'), dl.get('date'))}", source="syllabus",
            kind="syllabus_event", title=dl.get("title") or "Deadline", course=course["name"], course_id=course["id"],
            due_at=w, url=f"{base_url}/assignments/syllabus", status="open", body=dl.get("notes") or "",
            extra={"hint": "deadline", "all_day": not dl.get("time"), "from": "syllabus-ai"},
        ))
    for ev in ai.get("other_dates") or []:
        w = when(ev.get("date"), ev.get("time"), time(9, 0))
        if not w:
            continue
        out.append(Item(
            uid=f"syllabus:{course['id']}:date:{content_hash(ev.get('title'), ev.get('date'))}", source="syllabus",
            kind="syllabus_event", title=ev.get("title") or "Syllabus date", course=course["name"], course_id=course["id"],
            starts_at=w, url=f"{base_url}/assignments/syllabus", status="info", body=ev.get("notes") or "",
            extra={"hint": ev.get("kind") or "date", "all_day": not ev.get("time"), "from": "syllabus-ai"},
        ))
    return out
