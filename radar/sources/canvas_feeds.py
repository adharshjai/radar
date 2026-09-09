"""Token-free Canvas data: the personal calendar feed (.ics) and per-course announcement feeds (.atom).

These URLs contain their own secret and work with no login at all, so they keep working even when
the API session is gone. They know nothing about submissions or grades, though.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

import requests

from ..models import Course, Item, html_to_text, parse_dt, utcnow

ATOM = "{http://www.w3.org/2005/Atom}"
COURSE_IN_URL = re.compile(r"/courses/(\d+)|(?:include_contexts|context_codes?(?:%5B%5D|\[\])?)=course_(\d+)")
# Canvas ICS URLs look like https://host/calendar?include_contexts=course_123&month=09&year=2026#assignment_456
ASSIGNMENT_IN_URL = re.compile(r"/assignments/(\d+)|#assignment_(\d+)")
EVENT_IN_URL = re.compile(r"calendar_events?/(\d+)|event_id=(\d+)|#calendar_event_(\d+)")
TOPIC_IN_URL = re.compile(r"/discussion_topics/(\d+)|/announcements/(\d+)")
PAGE_IN_URL = re.compile(r"/pages/([^/?#]+)")
UID_RE = re.compile(r"event-(assignment|calendar-event|planner-note|assignment-override|sub-assignment)-(\d+)")
BRACKET_RE = re.compile(r"\s*\[([^\]]+)\]\s*$")
TITLE_PREFIX_RE = re.compile(r"^(Announcement|Discussion|Assignment|Page|Quiz|Event|Wiki Page|Discussion Topic|Calendar Event)\s*:\s*", re.I)
CALENDAR_FEED_RE = re.compile(r"^(https?://[^/]+)/feeds/calendars/(user_[A-Za-z0-9]+)(?:\.ics)?$")
ANNOUNCEMENT_FEED_RE = re.compile(r"^(https?://[^/]+)/feeds/announcements/((?:enrollment|course)_[A-Za-z0-9]+)(?:\.(?:atom|rss))?$")


def _first(m) -> str | None:
    return next((g for g in m.groups() if g), None) if m else None


def derived_feed_urls(cfg: dict) -> list[tuple[str, str]]:
    """Extra feeds we can reach with the codes the user already gave us: (kind, url).

    The calendar feed's user_<uuid> also serves /feeds/users/<code>.atom (last 7 days of activity in every course);
    an announcement feed's enrollment_<uuid> also serves /feeds/courses/<code>.atom (that course's activity)."""
    out = []
    m = CALENDAR_FEED_RE.match((cfg.get("ics_url") or "").strip())
    if m:
        out.append(("user", f"{m.group(1)}/feeds/users/{m.group(2)}.atom"))
    for url in cfg.get("announcement_feeds") or []:
        m = ANNOUNCEMENT_FEED_RE.match(url.strip())
        if m:
            out.append(("course", f"{m.group(1)}/feeds/courses/{m.group(2)}.atom"))
    return out


class FeedError(RuntimeError):
    pass


SECRET_RE = re.compile(r"/feeds/[^\s'\"]+")


def redact(text: str) -> str:
    """Feed URLs carry a permanent bearer secret - keep them out of logs and error messages."""
    return SECRET_RE.sub("/feeds/…", str(text))


def feed_label(url: str) -> str:
    from urllib.parse import urlparse
    host = urlparse(url).hostname or "canvas"
    kind = "calendar feed" if "/feeds/calendars/" in url else "announcement feed" if "/feeds/announcements/" in url \
        else "user activity feed" if "/feeds/users/" in url else "course activity feed" if "/feeds/courses/" in url else "feed"
    return f"{host} {kind}"


# ---------------------------------------------------------------- iCalendar

def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(s: str) -> str:
    return s.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _parse_ics_dt(value: str, params: dict, tz) -> tuple[datetime | None, bool]:
    """Return (aware UTC datetime, all_day)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        try:
            d = date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        except ValueError:
            return None, True
        return datetime.combine(d, time(23, 59)).replace(tzinfo=tz).astimezone(timezone.utc), True
    m = re.match(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})?(Z?)", value)
    if not m:
        return None, False
    y, mo, d, hh, mm, ss, z = m.groups()
    try:
        naive = datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0))
    except ValueError:
        return None, False
    if z:
        return naive.replace(tzinfo=timezone.utc), False
    tzid = params.get("TZID")
    if tzid:
        try:
            from zoneinfo import ZoneInfo
            return naive.replace(tzinfo=ZoneInfo(tzid)).astimezone(timezone.utc), False
        except Exception:
            pass
    return naive.replace(tzinfo=tz).astimezone(timezone.utc), False


def parse_ics(text: str, tz) -> list[dict]:
    """Minimal VEVENT parser: returns dicts with uid, summary, description, url, location, start, end, all_day."""
    events: list[dict] = []
    cur: dict | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
            continue
        if line == "END:VEVENT":
            if cur is not None:
                events.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue
        head, _, value = line.partition(":")
        name, *plist = head.split(";")
        params = {}
        for p in plist:
            if "=" in p:
                k, v = p.split("=", 1)
                params[k.upper()] = v
        name = name.upper()
        if name == "DTSTART":
            cur["start"], cur["all_day"] = _parse_ics_dt(value, params, tz)
        elif name == "DTEND":
            cur["end"], _ = _parse_ics_dt(value, params, tz)
        elif name in ("UID", "SUMMARY", "DESCRIPTION", "URL", "LOCATION", "X-ALT-DESC"):
            cur[name.lower().replace("-", "_")] = _unescape(value)
    return events


def _course_id_from_url(url: str) -> str | None:
    m = COURSE_IN_URL.search(url or "")
    return (m.group(1) or m.group(2)) if m else None


def _resolve_course(course_id: str | None, code: str | None, known: dict[str, str]) -> tuple[str | None, str | None]:
    """Map (id from URL, '[CHEM 101]' code) onto a course we already know about."""
    if course_id and course_id in known:
        return course_id, known[course_id]
    if code:
        for cid, name in known.items():
            if name and (name.lower().startswith(code.lower()) or code.lower() in name.lower()):
                return course_id or cid, name
    return course_id, code


def items_from_ics(text: str, base_url: str, tz, now: datetime | None = None,
                   known_courses: dict[str, str] | None = None) -> tuple[list[Item], list[Course]]:
    now = now or utcnow()
    known = known_courses or {}
    items: list[Item] = []
    courses: dict[str, Course] = {}
    for ev in parse_ics(text, tz):
        start = ev.get("start")
        if not start:
            continue
        summary = ev.get("summary") or "Untitled"
        url = ev.get("url") or ""
        course_code = None
        m = BRACKET_RE.search(summary)
        if m:
            course_code = m.group(1).strip()
            summary = summary[: m.start()].strip()
        course_id, course_name = _resolve_course(_course_id_from_url(url), course_code, known)
        if course_id and course_id not in known:
            courses.setdefault(course_id, Course(id=course_id, name=course_name or f"Course {course_id}", code=course_code or "",
                                                 url=f"{base_url}/courses/{course_id}", extra={"from": "ics"}))
        uid = ev.get("uid") or ""
        um = UID_RE.search(uid)
        am = ASSIGNMENT_IN_URL.search(url)
        desc = html_to_text(ev.get("x_alt_desc") or "") or (ev.get("description") or "")
        if am or (um and um.group(1) in ("assignment", "assignment-override", "sub-assignment")):
            # prefer the URL fragment: override/sub-assignment UIDs carry a different id than the assignment
            aid = _first(am) if am else um.group(2)
            past = start < now
            status = "open"
            if past:
                status = "pastdue" if start > now - timedelta(days=5) else "info"
            items.append(Item(
                uid=f"canvas:assignment:{aid}", source="canvas", kind="assignment", title=summary,
                course=course_name, course_id=course_id, due_at=start, url=url or None, status=status,
                body=desc[:3000], extra={"from": "ics", "all_day": ev.get("all_day", False), "submission_unknown": True},
            ))
        else:
            em = EVENT_IN_URL.search(url)
            eid = _first(em) if em else (um.group(2) if um else None)
            key = eid or re.sub(r"[^a-z0-9]+", "-", uid.lower())[:60] or f"{summary}-{start.isoformat()}"
            items.append(Item(
                uid=f"canvas:event:{key}", source="canvas", kind="event", title=summary, course=course_name,
                course_id=course_id, starts_at=start, ends_at=ev.get("end"), url=url or None, status="info",
                body=desc[:2000], extra={"from": "ics", "all_day": ev.get("all_day", False), "location": ev.get("location")},
            ))
    return items, list(courses.values())


# ---------------------------------------------------------------- Atom announcement feeds

def items_from_atom(text: str, base_url: str, known_courses: dict[str, str] | None = None,
                    feed_kind: str = "announcements") -> tuple[list[Item], list[Course]]:
    """feed_kind: 'announcements' (every entry is an announcement) or 'activity' (user/course feeds mixing
    announcements, discussions, assignments and pages - classified by link and title prefix)."""
    known = known_courses or {}
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise FeedError(f"could not parse Atom feed: {e}") from e
    feed_title = (root.findtext(f"{ATOM}title") or "").strip()
    feed_course = re.sub(r"\s*(announcements?|discussion topics|discussions|activity|feed)\s*$", "", feed_title, flags=re.I).strip()
    feed_course = re.sub(r"\s*(announcements?|discussion topics|discussions|activity|feed)\s*$", "", feed_course, flags=re.I).strip() or None
    if feed_kind == "activity" and feed_course and (not known or feed_course.lower() in ("canvas", "user")):
        feed_course = None
    items: list[Item] = []
    courses: dict[str, Course] = {}
    for entry in root.findall(f"{ATOM}entry"):
        raw_title = (entry.findtext(f"{ATOM}title") or "Announcement").strip()
        pm = TITLE_PREFIX_RE.match(raw_title)
        prefix = (pm.group(1).lower() if pm else "")
        title = raw_title[pm.end():].strip() if pm else raw_title
        link = ""
        for l in entry.findall(f"{ATOM}link"):
            if l.get("rel") in (None, "alternate") and l.get("href"):
                link = l.get("href")
                break
        entry_id = entry.findtext(f"{ATOM}id") or ""
        course_id, course_name = _resolve_course(_course_id_from_url(link), feed_course if feed_kind == "announcements" else None, known)
        if course_id and course_id not in known:
            courses.setdefault(course_id, Course(id=course_id, name=course_name or f"Course {course_id}",
                                                 url=f"{base_url}/courses/{course_id}", extra={"from": "atom"}))
        content = entry.findtext(f"{ATOM}content") or entry.findtext(f"{ATOM}summary") or ""
        author_el = entry.find(f"{ATOM}author/{ATOM}name")
        author = author_el.text if author_el is not None else None
        posted = parse_dt(entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated"))
        body = html_to_text(content)[:4000]
        common = dict(source="canvas", course=course_name, course_id=course_id, url=link or None, sender=author,
                      posted_at=posted, body=body)

        tm = TOPIC_IN_URL.search(link) or TOPIC_IN_URL.search(entry_id)
        am = ASSIGNMENT_IN_URL.search(link)
        pg = PAGE_IN_URL.search(link)
        is_announcement = feed_kind == "announcements" or prefix == "announcement" or "/announcements/" in link
        if tm and is_announcement:
            items.append(Item(uid=f"canvas:announcement:{_first(tm)}", kind="announcement", title=title, status="read",
                              extra={"from": "atom"}, **common))
        elif tm:
            items.append(Item(uid=f"canvas:discussion:{_first(tm)}", kind="discussion", title=title, status="read",
                              extra={"from": "atom"}, **common))
        elif am:
            items.append(Item(uid=f"canvas:assignment:{_first(am)}", kind="assignment", title=title, status="open",
                              extra={"from": "atom", "submission_unknown": True, "due_unknown": True}, **common))
        elif pg:
            items.append(Item(uid=f"canvas:page:{course_id or 'x'}:{pg.group(1)}", kind="page", title=title, status="info",
                              extra={"from": "atom"}, **common))
        elif feed_kind == "announcements":
            key = re.sub(r"[^a-z0-9]+", "-", entry_id.lower())[-40:] or title
            items.append(Item(uid=f"canvas:announcement:{key}", kind="announcement", title=title, status="read",
                              extra={"from": "atom"}, **common))
    return items, list(courses.values())


def merge_feed_items(items: list[Item]) -> list[Item]:
    """Same uid from several feeds: keep the richer one (a due date beats none; longer body wins ties)."""
    merged: dict[str, Item] = {}
    for it in items:
        prev = merged.get(it.uid)
        if prev is None:
            merged[it.uid] = it
            continue
        keep, other = (prev, it) if (prev.due_at or prev.starts_at) else (it, prev)
        if (keep.due_at or keep.starts_at) == (other.due_at or other.starts_at) and len(other.body) > len(keep.body):
            keep, other = other, keep
        if not keep.body and other.body:
            keep.body = other.body
        if not keep.sender and other.sender:
            keep.sender = other.sender
        if not keep.course and other.course:
            keep.course, keep.course_id = other.course, other.course_id
        merged[it.uid] = keep
    return list(merged.values())


# ---------------------------------------------------------------- source

@dataclass
class FeedSnapshot:
    items: list[Item] = field(default_factory=list)
    courses: list[Course] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class CanvasFeedsSource:
    def __init__(self, cfg: dict, tz, known_courses: dict[str, str] | None = None, log=None, session=None):
        self.cfg = cfg
        self.tz = tz
        self.known = known_courses or {}
        self.log = log or (lambda *a: None)
        self.s = session or requests.Session()

    def _fetch(self, url: str) -> str:
        label = feed_label(url)
        try:
            r = self.s.get(url, timeout=40, headers={"User-Agent": "radar/0.1"}, allow_redirects=True)
        except requests.RequestException as e:
            raise FeedError(f"network error fetching the {label}: {e.__class__.__name__}: {redact(e)[:160]}") from e
        if r.status_code >= 400:
            raise FeedError(f"HTTP {r.status_code} fetching the {label} (copy the link again from Canvas?)")
        if r.status_code == 200 and "text/html" in (r.headers.get("Content-Type") or "") and not r.text.lstrip().startswith("BEGIN:"):
            raise FeedError(f"the {label} returned a web page instead of a feed (copy the link again from Canvas?)")
        return r.text

    def fetch(self, now=None) -> FeedSnapshot:
        now = now or utcnow()
        snap = FeedSnapshot()
        base = (self.cfg.get("base_url") or "").rstrip("/")
        ics_url = (self.cfg.get("ics_url") or "").strip()
        if ics_url:
            try:
                items, courses = items_from_ics(self._fetch(ics_url), base, self.tz, now, self.known)
                snap.items.extend(items)
                snap.courses.extend(courses)
                self.log(f"calendar feed: {len(items)} entries")
            except FeedError as e:
                snap.warnings.append(str(e))
                self.log(str(e))
        for url in self.cfg.get("announcement_feeds") or []:
            try:
                items, courses = items_from_atom(self._fetch(url), base, self.known)
                snap.items.extend(items)
                snap.courses.extend(courses)
                self.log(f"announcement feed: {len(items)} entries")
            except FeedError as e:
                snap.warnings.append(str(e))
                self.log(str(e))
        for kind, url in derived_feed_urls(self.cfg):
            try:
                items, courses = items_from_atom(self._fetch(url), base, self.known, feed_kind="activity")
                snap.items.extend(items)
                snap.courses.extend(courses)
                self.log(f"{kind} activity feed: {len(items)} entries")
            except FeedError as e:
                # derived feeds are a bonus: note it, don't fail the sync
                self.log(f"(optional) {e}")
        snap.items = merge_feed_items(snap.items)
        return snap
