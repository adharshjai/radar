"""Canvas LMS source: pulls everything a student can see through the REST API."""
from __future__ import annotations

import io
import json
import re
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlparse

import requests

from .. import config as cfgmod
from ..models import Course, Item, Site, content_hash, html_links, html_to_text, parse_dt, utcnow
from .platforms import detect_platform, is_interesting_link, normalize_url

SUBMITTABLE = {"online_upload", "online_text_entry", "online_url", "online_quiz", "discussion_topic",
               "media_recording", "student_annotation", "external_tool", "basic_lti_launch"}
SYLLABUS_PAGE_RE = re.compile(r"syllab|schedule|calendar|course info|overview|policies|lab manual|due dates|important dates", re.I)
SYLLABUS_FILE_RE = re.compile(r"syllab|schedule|course[ _-]?info|policies", re.I)
CANVAS_FILE_LINK_RE = re.compile(r"/(?:courses/\d+/)?files/(\d+)")


class CanvasError(RuntimeError):
    pass


class CanvasSessionExpired(CanvasError):
    """Cookie-based session is no longer valid: the user needs to log in to Canvas in the browser again."""


JSON_HIJACK_PREFIX = "while(1);"          # removed from Canvas in 2014, still stripped for old self-hosted forks
KEEP_COOKIE_NAMES = {"canvas_session", "_normandy_session", "_legacy_normandy_session", "_csrf_token", "log_session_id", "pseudonym_credentials"}


def strip_json_prefix(text: str) -> str:
    return text[len(JSON_HIJACK_PREFIX):] if text.startswith(JSON_HIJACK_PREFIX) else text


class CanvasClient:
    """Talks to /api/v1 with either a personal access token or the browser's session cookies."""

    def __init__(self, base_url: str, token: str | None = None, timeout: int = 40, session: requests.Session | None = None,
                 cookies: dict | None = None):
        if not base_url or not (token or cookies):
            raise CanvasError(f"Canvas base_url plus a token or browser session are required (run: {cfgmod.CMD} setup)")
        self.base = base_url.rstrip("/")
        self.host = urlparse(self.base).hostname or ""
        self.timeout = timeout
        self.s = session or requests.Session()
        self.cookie_mode = not token
        if urlparse(self.base).scheme != "https":
            raise CanvasError(f"Canvas URL must start with https:// (got {self.base}); credentials are never sent over plain http")
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"
        else:
            for k, v in (cookies or {}).items():
                self.s.cookies.set(k, v, domain=self.host, secure=True)
            self.s.headers["Accept"] = "application/json"
            self.s.headers["User-Agent"] = "Mozilla/5.0 (Macintosh) radar/0.1"
        self.calls = 0

    def _expired(self, detail: str) -> CanvasSessionExpired:
        return CanvasSessionExpired(
            f"Canvas browser session expired or missing ({detail}). Sign in to Canvas in your browser, "
            f"then sync again (or run: {cfgmod.CMD} login canvas)")

    @staticmethod
    def _unauthenticated(r) -> bool:
        """A Canvas 401 means 'not logged in' ({"status":"unauthenticated"}, or a generic error after an SSO
        logout). Permission problems while logged in come back as 403 {"status":"unauthorized"}, but some
        forks send that with 401 - only an explicit "unauthorized" status is treated as 'still logged in'."""
        try:
            body = json.loads(strip_json_prefix(r.text))
        except (ValueError, TypeError):
            return True
        if isinstance(body, dict) and str(body.get("status", "")).lower() == "unauthorized":
            return False
        return True

    def fresh_cookies(self) -> dict:
        """Canvas re-issues the session cookie on a sliding window; persist what the server last sent us."""
        return {c.name: c.value for c in self.s.cookies if c.name in KEEP_COOKIE_NAMES}

    def _request(self, url: str, params=None, optional=False, stream=False, follow=False):
        for attempt in range(5):
            self.calls += 1
            try:
                r = self.s.get(url, params=params, timeout=self.timeout, stream=stream, allow_redirects=follow)
            except requests.RequestException as e:
                if attempt == 4:
                    raise CanvasError(f"network error talking to Canvas: {e}") from e
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code in (301, 302, 303, 307, 308) and not follow:
                loc = r.headers.get("Location", "")
                if self.cookie_mode or "/login" in loc:
                    raise self._expired(f"redirected to {loc[:80] or 'login'}")
                raise CanvasError(f"Canvas redirected {url} to {loc[:120]}")
            if r.status_code == 403 and "Rate Limit" in r.text:
                time.sleep(2 * (attempt + 1))
                continue
            try:
                # cookie-mode calls share the student's own throttle bucket with their browser tab: be polite
                if float(r.headers.get("X-Rate-Limit-Remaining", "1000")) < 150:
                    time.sleep(1.0)
            except ValueError:
                pass
            if r.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 401:
                if self.cookie_mode and self._unauthenticated(r):
                    raise self._expired("401 unauthenticated")
                if not self.cookie_mode and self._unauthenticated(r):
                    raise CanvasError("Canvas rejected the token (401). Generate a new one under Account > Settings > New Access Token, "
                                      f"or switch to browser-session login: {cfgmod.CMD} login canvas")
                if optional:
                    return None          # logged in, but this course hides that tab: skip it
                raise CanvasError(f"Canvas 401 unauthorized for {url}: {r.text[:160]}")
            if optional and r.status_code in (403, 404):
                return None
            if r.status_code >= 400:
                raise CanvasError(f"Canvas {r.status_code} for {url}: {r.text[:200]}")
            if not stream and "/api/" in url and "text/html" in (r.headers.get("Content-Type") or ""):
                raise self._expired("got the login page instead of JSON")
            return r
        raise CanvasError(f"Canvas kept failing for {url}")

    def url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base}/api/v1{path}"

    @staticmethod
    def _json(r):
        text = strip_json_prefix(r.text)
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise CanvasError(f"Canvas returned non-JSON for {r.url}: {text[:120]!r}") from e

    def get_json(self, path: str, params=None, optional=False):
        r = self._request(self.url(path), params, optional)
        return None if r is None else self._json(r)

    def get_all(self, path: str, params=None, optional=False, max_pages=30) -> list:
        params = dict(params or {})
        params.setdefault("per_page", 100)
        out: list = []
        url = self.url(path)
        first = True
        pages = 0
        while url and pages < max_pages:
            r = self._request(url, params if first else None, optional)
            if r is None:
                return out
            data = self._json(r)
            if isinstance(data, list):
                out.extend(data)
            else:
                return data
            url = r.links.get("next", {}).get("url")
            first = False
            pages += 1
        return out

    def download(self, url: str, max_bytes: int = 15_000_000) -> bytes | None:
        r = self._request(url, optional=True, stream=True, follow=True)
        if r is None:
            return None
        buf = io.BytesIO()
        for chunk in r.iter_content(65536):
            buf.write(chunk)
            if buf.tell() > max_bytes:
                return None
        return buf.getvalue()


# ---------------------------------------------------------------- text extraction

def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf8", "ignore")
    except Exception:
        return ""
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return re.sub(r"\n{3,}", "\n\n", xml).strip()


def _pdf_text(data: bytes, max_pages: int = 40) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        parts = []
        for page in reader.pages[:max_pages]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(parts).strip()
    except Exception:
        return ""


def _textutil(data: bytes, suffix: str) -> str:
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(data)
            path = f.name
        out = subprocess.run(["textutil", "-convert", "txt", "-stdout", path], capture_output=True, timeout=30)
        return out.stdout.decode("utf8", "ignore").strip()
    except Exception:
        return ""


def extract_text(data: bytes, content_type: str = "", filename: str = "") -> str:
    ct = (content_type or "").lower()
    name = (filename or "").lower()
    if "pdf" in ct or name.endswith(".pdf"):
        return _pdf_text(data)
    if "wordprocessingml" in ct or name.endswith(".docx"):
        return _docx_text(data) or _textutil(data, ".docx")
    if name.endswith(".doc") or ct == "application/msword":
        return _textutil(data, ".doc")
    if name.endswith(".rtf") or "rtf" in ct:
        return _textutil(data, ".rtf")
    if "html" in ct or name.endswith((".html", ".htm")):
        return html_to_text(data.decode("utf8", "ignore"))
    if ct.startswith("text/") or name.endswith((".txt", ".md")):
        return data.decode("utf8", "ignore")
    return ""


# ---------------------------------------------------------------- mapping helpers

def _course_url(base: str, cid: str) -> str:
    return f"{base}/courses/{cid}"


def derive_assignment_status(a: dict, sub: dict, now) -> str:
    due = parse_dt(a.get("due_at"))
    lock = parse_dt(a.get("lock_at"))
    unlock = parse_dt(a.get("unlock_at"))
    types = set(a.get("submission_types") or [])
    ws = sub.get("workflow_state")
    if sub.get("excused"):
        return "excused"
    if ws == "graded" and (sub.get("score") is not None or sub.get("grade")):
        return "graded"
    if ws in ("submitted", "pending_review") or sub.get("submitted_at"):
        return "submitted"
    if sub.get("missing"):
        return "missing"
    if types and types <= {"none", "not_graded"}:
        return "info"          # nothing to hand in (attendance, participation)
    if unlock and unlock > now:
        return "locked"
    past_due = due is not None and due < now
    closed = lock is not None and lock < now
    if past_due or closed:
        if types & {"none", "on_paper", "not_graded"} or not types:
            return "info"          # nothing to submit online - the due date just passed
        if types & {"external_tool", "basic_lti_launch"}:
            return "pastdue"       # Canvas may never hear back from the external tool
        return "missing"
    return "open"


def map_assignment(a: dict, course: Course, now, base: str) -> Item:
    sub = a.get("submission") or {}
    types = a.get("submission_types") or []
    kind = "assignment"
    if a.get("is_quiz_assignment") or a.get("quiz_id") or "online_quiz" in types:
        kind = "quiz"
    elif a.get("discussion_topic") or "discussion_topic" in types:
        kind = "discussion"
    ext = ((a.get("external_tool_tag_attributes") or {}).get("url")) or ""
    platform = None
    if "external_tool" in types or "basic_lti_launch" in types or ext:
        platform, _ = detect_platform(ext, a.get("name"))
        platform = platform or "External tool"
    status = derive_assignment_status(a, sub, now)
    score = sub.get("score")
    return Item(
        uid=f"canvas:assignment:{a['id']}", source="canvas", kind=kind, title=a.get("name") or "Untitled",
        course=course.name, course_id=course.id, due_at=parse_dt(a.get("due_at")),
        starts_at=parse_dt(a.get("unlock_at")), ends_at=parse_dt(a.get("lock_at")),
        url=a.get("html_url") or f"{_course_url(base, course.id)}/assignments/{a['id']}",
        platform=platform, status=status, points=a.get("points_possible"),
        score=float(score) if score is not None else None, grade=sub.get("grade"),
        body=html_to_text(a.get("description"))[:3000], posted_at=parse_dt(a.get("created_at")),
        extra={
            "submission_types": types, "external_url": ext or None, "late": bool(sub.get("late")),
            "submitted_at": sub.get("submitted_at"), "graded_at": sub.get("graded_at"),
            "locked_for_user": bool(a.get("locked_for_user")), "lock_explanation": a.get("lock_explanation"),
            "assignment_group_id": a.get("assignment_group_id"), "has_rubric": bool(a.get("rubric")),
            "allowed_attempts": a.get("allowed_attempts"), "peer_reviews": bool(a.get("peer_reviews")),
        },
    )


def map_discussion(t: dict, course: Course, base: str) -> Item:
    unread = (t.get("unread_count") or 0) > 0 or t.get("read_state") == "unread"
    author = (t.get("author") or {}).get("display_name")
    return Item(
        uid=f"canvas:discussion:{t['id']}", source="canvas", kind="discussion", title=t.get("title") or "Discussion",
        course=course.name, course_id=course.id, due_at=parse_dt(t.get("todo_date")),
        url=t.get("html_url") or f"{_course_url(base, course.id)}/discussion_topics/{t['id']}",
        status="unread" if unread else "read", body=html_to_text(t.get("message"))[:3000], sender=author,
        posted_at=parse_dt(t.get("posted_at") or t.get("last_reply_at") or t.get("created_at")),
        extra={"unread_count": t.get("unread_count") or 0, "replies": t.get("discussion_subentry_count"),
               "locked": bool(t.get("locked")), "last_reply_at": t.get("last_reply_at"), "pinned": bool(t.get("pinned"))},
    )


def map_announcement(a: dict, course: Course | None, base: str) -> Item:
    author = (a.get("author") or {}).get("display_name")
    return Item(
        uid=f"canvas:announcement:{a['id']}", source="canvas", kind="announcement", title=a.get("title") or "Announcement",
        course=course.name if course else a.get("context_code"), course_id=course.id if course else None,
        url=a.get("html_url"), status="unread" if a.get("read_state") == "unread" else "read",
        body=html_to_text(a.get("message"))[:4000], sender=author,
        posted_at=parse_dt(a.get("posted_at") or a.get("created_at")),
        extra={"context_code": a.get("context_code")},
    )


def map_event(e: dict, course: Course | None, base: str) -> Item:
    return Item(
        uid=f"canvas:event:{e['id']}", source="canvas", kind="event", title=e.get("title") or "Event",
        course=course.name if course else None, course_id=course.id if course else None,
        starts_at=parse_dt(e.get("start_at")), ends_at=parse_dt(e.get("end_at")), url=e.get("html_url"),
        status="info", body=html_to_text(e.get("description"))[:2000],
        extra={"location": e.get("location_name"), "all_day": bool(e.get("all_day")), "context_code": e.get("context_code")},
    )


def map_conversation(c: dict, base: str, my_id: str | None) -> Item:
    people = [p.get("name") for p in (c.get("participants") or []) if str(p.get("id")) != str(my_id)]
    return Item(
        uid=f"canvas:conversation:{c['id']}", source="canvas", kind="conversation", title=c.get("subject") or "(no subject)",
        course=c.get("context_name"), url=f"{base}/conversations/{c['id']}",
        status="unread" if c.get("workflow_state") == "unread" else "read",
        body=(c.get("last_message") or "")[:3000], sender=", ".join(p for p in people if p) or None,
        posted_at=parse_dt(c.get("last_message_at")),
        extra={"message_count": c.get("message_count"), "starred": bool(c.get("starred"))},
    )


def map_planner_note(p: dict, course: Course | None, base: str) -> Item:
    pl = p.get("plannable") or {}
    override = p.get("planner_override") or {}
    return Item(
        uid=f"canvas:planner_note:{pl.get('id') or p.get('plannable_id')}", source="canvas", kind="planner_note",
        title=pl.get("title") or "Planner note", course=course.name if course else p.get("context_name"),
        course_id=course.id if course else None, due_at=parse_dt(pl.get("todo_date") or p.get("plannable_date")),
        url=f"{base}/?#planner", status="complete" if override.get("marked_complete") else "open",
        body=(pl.get("details") or "")[:2000],
    )


def map_planner_page(p: dict, course: Course | None, base: str) -> Item:
    pl = p.get("plannable") or {}
    override = p.get("planner_override") or {}
    return Item(
        uid=f"canvas:page:{pl.get('id') or p.get('plannable_id')}", source="canvas", kind="page",
        title=pl.get("title") or "Page", course=course.name if course else p.get("context_name"),
        course_id=course.id if course else None, due_at=parse_dt(pl.get("todo_date") or p.get("plannable_date")),
        url=p.get("html_url") if str(p.get("html_url", "")).startswith("http") else f"{base}{p.get('html_url') or ''}",
        status="complete" if override.get("marked_complete") else "open",
    )


def map_file(f: dict, course: Course, base: str) -> Item:
    return Item(
        uid=f"canvas:file:{f['id']}", source="canvas", kind="file", title=f.get("display_name") or f.get("filename") or "File",
        course=course.name, course_id=course.id, url=f.get("url") or f"{_course_url(base, course.id)}/files/{f['id']}",
        status="info", posted_at=parse_dt(f.get("updated_at") or f.get("created_at")),
        extra={"size": f.get("size"), "content_type": f.get("content-type") or f.get("content_type"),
               "folder_id": f.get("folder_id")},
    )


def map_quiz(q: dict, course: Course, now, base: str) -> Item:
    due = parse_dt(q.get("due_at"))
    lock = parse_dt(q.get("lock_at"))
    status = "open"
    if q.get("locked_for_user") and ((due and due < now) or (lock and lock < now)):
        status = "info"
    return Item(
        uid=f"canvas:quiz:{q['id']}", source="canvas", kind="quiz", title=q.get("title") or "Quiz",
        course=course.name, course_id=course.id, due_at=due, starts_at=parse_dt(q.get("unlock_at")), ends_at=lock,
        url=q.get("html_url"), status=status, points=q.get("points_possible"),
        body=html_to_text(q.get("description"))[:2000],
        extra={"quiz_type": q.get("quiz_type"), "time_limit": q.get("time_limit"), "allowed_attempts": q.get("allowed_attempts")},
    )


def links_to_sites(html: str | None, course_id: str, where: str, canvas_host: str, limit: int = 40) -> list[Site]:
    out: list[Site] = []
    seen: set[str] = set()
    for href, text in html_links(html):
        href = normalize_url(href)
        if not is_interesting_link(href, canvas_host) or href in seen:
            continue
        seen.add(href)
        platform, cat = detect_platform(href, text)
        name = text or platform or (urlparse(href).hostname or href)
        out.append(Site(course_id=course_id, url=href, name=name[:120], platform=platform, category=cat, where_found=where))
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- snapshot

@dataclass
class Snapshot:
    me: dict = field(default_factory=dict)
    courses: list[Course] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    sites: list[Site] = field(default_factory=list)
    syllabi: dict[str, str] = field(default_factory=dict)       # course_id -> combined syllabus text
    warnings: list[str] = field(default_factory=list)
    api_calls: int = 0


class CanvasSource:
    def __init__(self, client: CanvasClient, cfg: dict, now=None, log=None):
        self.c = client
        self.cfg = cfg
        self.now = now or utcnow()
        self.log = log or (lambda *a: None)

    def fetch(self) -> Snapshot:
        snap = Snapshot()
        cfg = self.cfg
        now = self.now
        base = self.c.base
        lookback = now - timedelta(days=int(cfg.get("lookback_days", 45)))
        lookahead = now + timedelta(days=int(cfg.get("lookahead_days", 90)))

        me = self.c.get_json("/users/self")
        snap.me = me or {}
        profile = self.c.get_json("/users/self/profile", optional=True) or {}
        snap.me["primary_email"] = profile.get("primary_email")
        my_id = str(snap.me.get("id"))

        raw_courses = self.c.get_all("/courses", {
            "enrollment_state": "active",
            "include[]": ["term", "total_scores", "syllabus_body", "favorites", "tabs", "teachers"],
        })
        include = {str(x) for x in cfg.get("include_courses") or []}
        exclude = {str(x) for x in cfg.get("exclude_courses") or []}
        course_by_id: dict[str, Course] = {}
        for rc in raw_courses:
            cid = str(rc.get("id"))
            if rc.get("access_restricted_by_date") or rc.get("workflow_state") not in (None, "available"):
                continue
            if include and cid not in include:
                continue
            if cid in exclude:
                continue
            term = rc.get("term") or {}
            end = parse_dt(term.get("end_at")) or parse_dt(rc.get("end_at"))
            if end and end < now - timedelta(days=14):
                continue
            score = grade = None
            for e in rc.get("enrollments") or []:
                if e.get("type") in ("student", "StudentEnrollment"):
                    score, grade = e.get("computed_current_score"), e.get("computed_current_grade")
            course = Course(
                id=cid, name=rc.get("name") or rc.get("course_code") or f"Course {cid}", code=rc.get("course_code") or "",
                term=term.get("name") or "", url=_course_url(base, cid),
                teachers=[t.get("display_name") for t in rc.get("teachers") or [] if t.get("display_name")],
                current_score=score, current_grade=grade, syllabus_html=rc.get("syllabus_body") or "",
                extra={"favorite": bool(rc.get("is_favorite")), "term_start": term.get("start_at"), "term_end": term.get("end_at")},
            )
            course_by_id[cid] = course
            snap.courses.append(course)
            for tab in rc.get("tabs") or []:
                if tab.get("type") == "external" or str(tab.get("id", "")).startswith("context_external_tool"):
                    url = tab.get("html_url") or tab.get("full_url") or ""
                    if url and not url.startswith("http"):
                        url = base + url
                    if not url:
                        continue
                    platform, _ = detect_platform(url, tab.get("label"))
                    snap.sites.append(Site(course_id=cid, url=url, name=tab.get("label") or "External tool",
                                           platform=platform, category="tool", where_found="course navigation"))
        self.log(f"{len(snap.courses)} active courses")

        groups = self.c.get_all("/users/self/groups", optional=True) or []
        group_names = {f"group_{g['id']}": g.get("name") for g in groups}

        for course in snap.courses:
            self._fetch_course(course, snap, lookback)

        ctx = [f"course_{c.id}" for c in snap.courses] + list(group_names)
        if ctx:
            anns = self.c.get_all("/announcements", {
                "context_codes[]": ctx, "start_date": lookback.date().isoformat(),
                "end_date": (now + timedelta(days=2)).date().isoformat(), "per_page": 50,
            }, optional=True) or []
            for a in anns:
                course = course_by_id.get(str(a.get("context_code", "")).replace("course_", ""))
                it = map_announcement(a, course, base)
                if not course:
                    it.course = group_names.get(a.get("context_code"), it.course)
                snap.items.append(it)
                if course:
                    snap.sites.extend(links_to_sites(a.get("message"), course.id, f"announcement: {it.title[:40]}", self.c.host, limit=8))

            events = self.c.get_all("/calendar_events", {
                "type": "event", "context_codes[]": ctx + [f"user_{my_id}"],
                "start_date": (now - timedelta(days=1)).isoformat(), "end_date": lookahead.isoformat(),
            }, optional=True) or []
            for e in events:
                if e.get("hidden") or e.get("workflow_state") == "deleted":
                    continue
                course = course_by_id.get(str(e.get("context_code", "")).replace("course_", ""))
                it = map_event(e, course, base)
                if not course and e.get("context_code") in group_names:
                    it.course = group_names[e["context_code"]]
                snap.items.append(it)

        planner = self.c.get_all("/planner/items", {
            "start_date": lookback.isoformat(), "end_date": lookahead.isoformat(),
        }, optional=True) or []
        completed_assignments: set[str] = set()
        for p in planner:
            course = course_by_id.get(str(p.get("course_id") or ""))
            ptype = p.get("plannable_type")
            override = p.get("planner_override") or {}
            if ptype == "planner_note":
                snap.items.append(map_planner_note(p, course, base))
            elif ptype == "wiki_page":
                snap.items.append(map_planner_page(p, course, base))
            elif ptype in ("assignment", "quiz", "discussion_topic") and override.get("marked_complete"):
                completed_assignments.add(f"{ptype}:{p.get('plannable_id')}")

        missing = self.c.get_all("/users/self/missing_submissions", {
            "include[]": ["course"], "filter[]": ["submittable"],
        }, optional=True) or []
        missing_ids = {f"canvas:assignment:{m['id']}" for m in missing}
        by_uid = {it.uid: it for it in snap.items}
        for uid in missing_ids:
            it = by_uid.get(uid)
            if it and it.status in ("open", "pastdue", "info"):
                it.status = "missing"
        for key in completed_assignments:
            ptype, pid = key.split(":")
            uid = f"canvas:assignment:{pid}"
            it = by_uid.get(uid)
            if it:
                it.extra["canvas_marked_complete"] = True

        convos = self.c.get_all("/conversations", {"scope": "inbox", "per_page": 50}, optional=True, max_pages=2) or []
        for cv in convos:
            snap.items.append(map_conversation(cv, base, my_id))

        snap.api_calls = self.c.calls
        self.log(f"{len(snap.items)} items, {len(snap.sites)} external links, {self.c.calls} API calls")
        return snap

    # -- per-course ----------------------------------------------------------
    def _fetch_course(self, course: Course, snap: Snapshot, lookback):
        c = self.c
        base = c.base
        cid = course.id
        now = self.now
        self.log(f"  {course.name}")

        assignments = c.get_all(f"/courses/{cid}/assignments", {"include[]": ["submission"], "order_by": "due_at"}, optional=True) or []
        assignment_ids: set[str] = set()
        for a in assignments:
            if a.get("published") is False:
                continue
            assignment_ids.add(str(a.get("id")))
            it = map_assignment(a, course, now, base)
            snap.items.append(it)
            if a.get("description"):
                snap.sites.extend(links_to_sites(a.get("description"), cid, f"assignment: {it.title[:40]}", c.host, limit=10))

        topics = c.get_all(f"/courses/{cid}/discussion_topics", {"per_page": 50, "order_by": "recent_activity"}, optional=True, max_pages=2) or []
        for t in topics:
            if t.get("assignment_id"):
                continue
            snap.items.append(map_discussion(t, course, base))

        quizzes = c.get_all(f"/courses/{cid}/quizzes", {"per_page": 50}, optional=True, max_pages=2) or []
        for q in quizzes:
            if q.get("assignment_id") or q.get("published") is False:
                continue
            snap.items.append(map_quiz(q, course, now, base))

        modules = c.get_all(f"/courses/{cid}/modules", {"include[]": ["items", "content_details"], "per_page": 50}, optional=True) or []
        for m in modules:
            unlock = parse_dt(m.get("unlock_at"))
            if unlock and unlock > now:
                snap.items.append(Item(
                    uid=f"canvas:module:{m['id']}", source="canvas", kind="module", title=f"Module unlocks: {m.get('name')}",
                    course=course.name, course_id=cid, starts_at=unlock, status="locked",
                    url=f"{_course_url(base, cid)}/modules#module_{m['id']}",
                ))
            items = m.get("items")
            if items is None:
                items = c.get_all(f"/courses/{cid}/modules/{m['id']}/items", {"include[]": ["content_details"]}, optional=True) or []
            for mi in items:
                mtype = mi.get("type")
                if mtype in ("ExternalUrl", "ExternalTool"):
                    url = mi.get("external_url") or mi.get("html_url") or ""
                    if not url:
                        continue
                    platform, cat = detect_platform(url, mi.get("title"))
                    if mtype == "ExternalTool":
                        cat = "tool"
                        url = mi.get("html_url") or url   # Canvas launch URL keeps the LTI session working
                    if mtype == "ExternalUrl" and not is_interesting_link(url, c.host):
                        continue
                    snap.sites.append(Site(course_id=cid, url=normalize_url(url), name=mi.get("title") or platform or url,
                                           platform=platform, category=cat, where_found=f"module: {m.get('name')}"))
                req = mi.get("completion_requirement")
                if req and not req.get("completed") and mtype in ("Page", "File", "ExternalUrl", "ExternalTool", "SubHeader") and mtype != "SubHeader":
                    details = mi.get("content_details") or {}
                    if mi.get("published") is False:
                        continue
                    snap.items.append(Item(
                        uid=f"canvas:module_item:{mi['id']}", source="canvas", kind="module_item",
                        title=f"{mi.get('title')} ({req.get('type', 'requirement').replace('_', ' ')})",
                        course=course.name, course_id=cid, due_at=parse_dt(details.get("due_at")),
                        starts_at=parse_dt(details.get("unlock_at")), url=mi.get("html_url"), status="open",
                        extra={"module": m.get("name"), "requirement": req.get("type"), "module_item_type": mtype},
                    ))

        tools = c.get_all(f"/courses/{cid}/external_tools", {"per_page": 50, "include_parents": "true"}, optional=True) or []
        for t in tools:
            raw = t.get("url") or t.get("domain") or ""
            if not t.get("id"):
                continue
            platform, _ = detect_platform(raw, t.get("name"))
            placements = [k for k in ("course_navigation", "assignment_selection", "link_selection", "editor_button") if t.get(k)]
            launch = f"{_course_url(base, cid)}/external_tools/{t['id']}"
            snap.sites.append(Site(course_id=cid, url=launch, name=t.get("name") or platform or raw, platform=platform,
                                   category="tool", where_found="installed tool" + (f" ({', '.join(placements)})" if placements else "")))

        # --- syllabus corpus: syllabus page + front page + relevant pages + syllabus files
        corpus: list[str] = []
        if course.syllabus_html:
            corpus.append("# Syllabus page\n" + html_to_text(course.syllabus_html))
            snap.sites.extend(links_to_sites(course.syllabus_html, cid, "syllabus", c.host))
        front = c.get_json(f"/courses/{cid}/front_page", optional=True)
        if front and front.get("body"):
            corpus.append(f"# Front page: {front.get('title')}\n" + html_to_text(front.get("body")))
            snap.sites.extend(links_to_sites(front.get("body"), cid, "front page", c.host))
        pages = c.get_all(f"/courses/{cid}/pages", {"per_page": 100, "sort": "updated_at", "order": "desc"}, optional=True, max_pages=2) or []
        fetched = 0
        for p in pages:
            title = p.get("title") or ""
            if fetched >= 6 or not SYLLABUS_PAGE_RE.search(title) or (front and p.get("url") == front.get("url")):
                continue
            full = c.get_json(f"/courses/{cid}/pages/{p.get('url')}", optional=True)
            if not full or not full.get("body"):
                continue
            fetched += 1
            corpus.append(f"# Page: {title}\n" + html_to_text(full.get("body")))
            snap.sites.extend(links_to_sites(full.get("body"), cid, f"page: {title[:40]}", c.host))

        file_ids: set[str] = set()
        if self.cfg.get("parse_syllabus_files", True):
            for m in CANVAS_FILE_LINK_RE.finditer(course.syllabus_html or ""):
                file_ids.add(m.group(1))
        if self.cfg.get("fetch_files", True):
            files = c.get_all(f"/courses/{cid}/files", {"per_page": 100, "sort": "updated_at", "order": "desc"}, optional=True, max_pages=2) or []
            for f in files:
                if f.get("hidden") or f.get("locked_for_user"):
                    continue
                upd = parse_dt(f.get("updated_at") or f.get("created_at"))
                if upd and upd >= lookback:
                    snap.items.append(map_file(f, course, base))
                if self.cfg.get("parse_syllabus_files", True) and SYLLABUS_FILE_RE.search(f.get("display_name") or ""):
                    file_ids.add(str(f.get("id")))
        for fid in list(file_ids)[:4]:
            meta = c.get_json(f"/files/{fid}", optional=True)
            if not meta or not meta.get("url") or (meta.get("size") or 0) > 15_000_000:
                continue
            data = c.download(meta["url"])
            if not data:
                continue
            text = extract_text(data, meta.get("content-type") or "", meta.get("display_name") or meta.get("filename") or "")
            if text.strip():
                corpus.append(f"# File: {meta.get('display_name')}\n" + text[:60000])

        if corpus:
            snap.syllabi[cid] = "\n\n".join(corpus)
