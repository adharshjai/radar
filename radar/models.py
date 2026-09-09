"""Core data types shared by every source."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value) -> datetime | None:
    """Parse ISO-8601 (Canvas/Graph style) into an aware UTC datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Graph returns 7 fractional digits; fromisoformat only accepts 6.
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def iso(d: datetime | None) -> str | None:
    return d.astimezone(timezone.utc).isoformat() if d else None


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "section"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href = None
        self._text_buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text_buf = []

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(self._text_buf).strip()))
            self._href = None

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._text_buf.append(data.strip())


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    text = "".join(p.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def html_links(html: str | None) -> list[tuple[str, str]]:
    """Return (href, anchor text) pairs from an HTML fragment."""
    if not html:
        return []
    p = _TextExtractor()
    try:
        p.feed(html)
    except Exception:
        return []
    return p.links


def content_hash(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode())
    return h.hexdigest()[:16]


@dataclass
class Course:
    id: str
    name: str
    code: str = ""
    term: str = ""
    url: str = ""
    teachers: list[str] = field(default_factory=list)
    current_score: float | None = None
    current_grade: str | None = None
    syllabus_html: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Site:
    """An external website or tool discovered inside a course (LabFlow, Gradescope, a lab manual, ...)."""
    course_id: str
    url: str
    name: str
    platform: str | None = None     # labflow, gradescope, ... (see sources/platforms.py)
    category: str = "link"          # tool | link | media
    where_found: str = ""           # "course nav", "module: Week 3", "syllabus", ...


@dataclass
class Item:
    """One thing the user might need to act on or know about."""
    uid: str
    source: str            # canvas | outlook | applemail | syllabus
    kind: str              # assignment quiz discussion announcement event module module_item planner_note page
                           # conversation email calendar file syllabus_event
    title: str
    course: str | None = None
    course_id: str | None = None
    due_at: datetime | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    url: str | None = None
    platform: str | None = None
    status: str = "open"   # open submitted graded missing pastdue excused locked unread read info complete
    points: float | None = None
    score: float | None = None
    grade: str | None = None
    body: str = ""
    sender: str | None = None
    posted_at: datetime | None = None
    extra: dict = field(default_factory=dict)

    def fingerprint(self) -> str:
        return content_hash(self.title, iso(self.due_at), iso(self.starts_at), self.status,
                            self.score, self.grade, self.body[:2000], self.url)
