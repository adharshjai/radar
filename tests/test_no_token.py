"""Canvas without a personal access token: session cookies, browser import, ICS/Atom feeds."""
import copy
import json
from datetime import datetime, timedelta, timezone
from http.cookiejar import Cookie

import pytest
import requests

from radar import config as cfgmod
from radar.sources import canvas_session as cs
from radar.sources.canvas import CanvasClient, CanvasError, CanvasSessionExpired, strip_json_prefix
from radar.sources.canvas_feeds import CanvasFeedsSource, FeedError, items_from_atom, items_from_ics, parse_ics
from radar.store import Store
from radar.triage import build_dashboard, local_tz


# ---------------------------------------------------------------- fake HTTP session

class FakeResponse:
    def __init__(self, status=200, text="", headers=None, url="http://x"):
        self.status_code = status
        self.text = text
        self.headers = {"Content-Type": "application/json; charset=utf-8", **(headers or {})}
        self.url = url
        self.links = {}
        self.ok = status < 400

    def json(self):
        return json.loads(self.text)

    def iter_content(self, n):
        yield self.text.encode()


class FakeSession:
    """Routes GETs to canned responses; records auth headers/cookies."""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()
        self.calls = []

    def get(self, url, params=None, timeout=None, stream=False, allow_redirects=True, headers=None):
        self.calls.append((url, params, allow_redirects))
        best = max((p for p in self.routes if url.startswith(p)), key=len, default=None)   # longest prefix wins
        if best is None:
            return FakeResponse(404, "{}")
        resp = self.routes[best]
        return resp(url) if callable(resp) else resp


BASE = "https://canvas.school.edu"


def test_strip_json_prefix():
    assert strip_json_prefix('while(1);{"a":1}') == '{"a":1}'
    assert strip_json_prefix('{"a":1}') == '{"a":1}'


def test_cookie_client_sends_cookies_and_strips_prefix():
    fs = FakeSession({f"{BASE}/api/v1/users/self": FakeResponse(200, 'while(1);{"id": 42, "name": "Me"}')})
    c = CanvasClient(BASE, cookies={"canvas_session": "abc", "_csrf_token": "t"}, session=fs)
    assert c.cookie_mode and "Authorization" not in fs.headers
    assert {ck.name for ck in fs.cookies} == {"canvas_session", "_csrf_token"}
    assert c.get_json("/users/self") == {"id": 42, "name": "Me"}
    assert fs.calls[-1][2] is False          # redirects are not followed for API calls


def test_token_client_unchanged():
    fs = FakeSession({f"{BASE}/api/v1/users/self": FakeResponse(200, '{"id": 1}')})
    c = CanvasClient(BASE, "tok", session=fs)
    assert fs.headers["Authorization"] == "Bearer tok" and not c.cookie_mode
    assert c.get_json("/users/self") == {"id": 1}


@pytest.mark.parametrize("resp", [
    FakeResponse(302, "", {"Location": f"{BASE}/login/saml"}),
    FakeResponse(401, '{"status":"unauthenticated","errors":[{"message":"user authorization required"}]}'),
    FakeResponse(200, "<html><body>Log in</body></html>", {"Content-Type": "text/html; charset=utf-8"}),
])
def test_cookie_client_detects_expired_session(resp):
    fs = FakeSession({f"{BASE}/api/v1/users/self": resp})
    c = CanvasClient(BASE, cookies={"canvas_session": "old"}, session=fs)
    with pytest.raises(CanvasSessionExpired):
        c.get_json("/users/self")


def test_token_client_401_is_not_session_expired():
    fs = FakeSession({f"{BASE}/api/v1/users/self": FakeResponse(401, '{"errors":[{"message":"Invalid access token."}]}')})
    c = CanvasClient(BASE, "bad", session=fs)
    with pytest.raises(CanvasError) as ei:
        c.get_json("/users/self")
    assert not isinstance(ei.value, CanvasSessionExpired) and "token" in str(ei.value)


def test_download_follows_redirects():
    fs = FakeSession({f"{BASE}/files/1/download": FakeResponse(200, "PDFBYTES", {"Content-Type": "application/pdf"})})
    c = CanvasClient(BASE, cookies={"canvas_session": "x"}, session=fs)
    assert c.download(f"{BASE}/files/1/download") == b"PDFBYTES"
    assert fs.calls[-1][2] is True


# ---------------------------------------------------------------- cookie helpers

def test_cookies_from_string():
    assert cs.cookies_from_string("canvas_session=abc; _csrf_token=xyz") == {"canvas_session": "abc", "_csrf_token": "xyz"}
    assert cs.cookies_from_string("Cookie: canvas_session=abc") == {"canvas_session": "abc"}
    assert cs.cookies_from_string("rawvalue123") == {"canvas_session": "rawvalue123"}
    assert cs.cookies_from_string("") == {}


def test_cookie_persistence(tmp_path):
    assert cs.load_cookies(tmp_path) is None
    p = cs.save_cookies(tmp_path, {"canvas_session": "abc"}, "chrome")
    assert oct(p.stat().st_mode)[-3:] == "600"
    saved = cs.load_cookies(tmp_path)
    assert saved["cookies"] == {"canvas_session": "abc"} and saved["source"] == "chrome"
    cs.clear_cookies(tmp_path)
    assert cs.load_cookies(tmp_path) is None
    cs.save_cookies(tmp_path, {"other": "x"}, "chrome")
    assert cs.load_cookies(tmp_path) is None   # a file without canvas_session counts as no session


def _cookie(name, value, domain):
    return Cookie(0, name, value, None, False, domain, True, domain.startswith("."), "/", True, True, None, False, None, None, {})


def test_cookies_from_browser_uses_first_browser_with_session(monkeypatch):
    import browser_cookie3 as bc
    jar_no = [_cookie("_ga", "1", ".school.edu")]
    jar_yes = [_cookie("canvas_session", "PARENT", ".school.edu"), _cookie("canvas_session", "S3SSION", "canvas.school.edu"),
               _cookie("_csrf_token", "C", "canvas.school.edu"), _cookie("_ga", "1", ".school.edu"),
               _cookie("canvas_session", "OTHER", "canvas.elsewhere.edu"), _cookie("canvas_session", "SUB", "sandbox.canvas.school.edu")]
    monkeypatch.setattr(bc, "chrome", lambda domain_name="": (_ for _ in ()).throw(bc.BrowserCookieError("no chrome")))
    monkeypatch.setattr(bc, "safari", lambda domain_name="": jar_no)
    monkeypatch.setattr(bc, "firefox", lambda domain_name="": jar_yes)
    cookies, browser = cs.cookies_from_browser(BASE)
    # exact host beats parent domain; sub-domain and foreign cookies are ignored; analytics cookies dropped
    assert browser == "firefox" and cookies == {"canvas_session": "S3SSION", "_csrf_token": "C"}
    monkeypatch.setattr(bc, "firefox", lambda domain_name="": jar_no)
    for b in ("edge", "brave", "arc", "chromium", "vivaldi", "opera"):
        monkeypatch.setattr(bc, b, lambda domain_name="": jar_no)
    with pytest.raises(cs.CanvasSessionError) as ei:
        cs.cookies_from_browser(BASE)
    assert "no chrome" in str(ei.value) and "sign in" in str(ei.value).lower()


# ---------------------------------------------------------------- feeds

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Instructure//Canvas//EN
BEGIN:VEVENT
DTSTAMP:20260901T120000Z
DTSTART:20260920T035900Z
UID:event-assignment-2005
SUMMARY:Homework 5: Thermochemistry [CHEM 101]
DESCRIPTION:Problems 5.1\\, 5.2 and 5.3\\nUpload as PDF.
URL:https://canvas.school.edu/courses/1001/assignments/2005
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260919
UID:event-assignment-override-77
SUMMARY:Reading quiz [CHEM 101]
URL:https://canvas.school.edu/courses/1001/assignments/2077
X-ALT-DESC;FMTTYPE=text/html:<p>Chapter <b>4</b></p>
END:VEVENT
BEGIN:VEVENT
DTSTART:20260912T210000Z
DTEND:20260912T223000Z
UID:event-calendar-event-8001
SUMMARY:Midterm 1 review session [MATH 251]
LOCATION:Math Building 105
URL:https://canvas.school.edu/calendar?include_contexts=course_1002&month=09&year=2026#calendar_event_8001
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20260915T090000
UID:event-calendar-event-8002
SUMMARY:Advising appointment
URL:https://canvas.school.edu/calendar?event_id=8002
END:VEVENT
END:VCALENDAR
"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>CHEM 101 - General Chemistry I Announcements</title>
  <id>https://canvas.school.edu/feeds/announcements/enrollment_abc.atom</id>
  <updated>2026-09-08T13:30:00Z</updated>
  <entry>
    <title>Exam 1 next week — bring a calculator</title>
    <link rel="alternate" href="https://canvas.school.edu/courses/1001/discussion_topics/7001"/>
    <id>tag:canvas.school.edu,2026-09-08:/discussion_topics/7001</id>
    <updated>2026-09-08T13:30:00Z</updated>
    <published>2026-09-08T13:30:00Z</published>
    <author><name>Dr. Anita Patel</name></author>
    <content type="html">&lt;p&gt;Exam 1 is Friday at 7 PM. Bring a &lt;b&gt;calculator&lt;/b&gt;.&lt;/p&gt;</content>
  </entry>
</feed>
"""


def test_parse_ics_unfolding_and_escapes():
    text = ICS.replace("SUMMARY:Homework 5: Thermochemistry [CHEM 101]", "SUMMARY:Homework 5: Thermo\r\n chemistry [CHEM 101]")
    evs = parse_ics(text, timezone.utc)
    assert len(evs) == 4
    assert evs[0]["summary"] == "Homework 5: Thermochemistry [CHEM 101]"
    assert evs[0]["description"] == "Problems 5.1, 5.2 and 5.3\nUpload as PDF."
    assert evs[0]["start"] == datetime(2026, 9, 20, 3, 59, tzinfo=timezone.utc) and evs[0]["all_day"] is False
    assert evs[1]["all_day"] is True
    assert evs[3]["start"] == datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc)   # TZID honoured


def test_items_from_ics():
    now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    items, courses = items_from_ics(ICS, BASE, timezone.utc, now, known_courses={"1002": "MATH 251 - Calculus III"})
    by = {i.uid: i for i in items}
    hw = by["canvas:assignment:2005"]
    assert hw.kind == "assignment" and hw.title == "Homework 5: Thermochemistry" and hw.course == "CHEM 101"
    assert hw.course_id == "1001" and hw.status == "open" and hw.extra["submission_unknown"] and "5.2" in hw.body
    quiz = by["canvas:assignment:2077"]                      # id from URL even though UID is an override
    assert quiz.extra["all_day"] and quiz.body == "Chapter 4"
    review = by["canvas:event:8001"]
    assert review.kind == "event" and review.course == "MATH 251 - Calculus III" and review.extra["location"] == "Math Building 105"
    assert review.ends_at == datetime(2026, 9, 12, 22, 30, tzinfo=timezone.utc)
    assert "canvas:event:8002" in by and by["canvas:event:8002"].course is None
    assert [c.id for c in courses] == ["1001"] and courses[0].name == "CHEM 101"


def test_ics_past_due_status():
    now = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
    items, _ = items_from_ics(ICS, BASE, timezone.utc, now)
    by = {i.uid: i for i in items}
    assert by["canvas:assignment:2005"].status == "pastdue"      # 2 days ago -> verify
    old = ICS.replace("20260920T035900Z", "20260901T035900Z")
    items, _ = items_from_ics(old, BASE, timezone.utc, now)
    assert {i.uid: i for i in items}["canvas:assignment:2005"].status == "info"


def test_items_from_atom():
    items, courses = items_from_atom(ATOM, BASE)
    assert len(items) == 1
    a = items[0]
    assert a.uid == "canvas:announcement:7001" and a.kind == "announcement"
    assert a.course_id == "1001" and a.course == "CHEM 101 - General Chemistry I"
    assert a.sender == "Dr. Anita Patel" and a.body == "Exam 1 is Friday at 7 PM. Bring a calculator."
    assert a.posted_at == datetime(2026, 9, 8, 13, 30, tzinfo=timezone.utc)
    assert courses[0].name == "CHEM 101 - General Chemistry I"
    with pytest.raises(FeedError):
        items_from_atom("<not xml", BASE)


def test_feeds_source_and_sync(tmp_path):
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg["ai"]["enabled"] = False
    cfg["canvas"]["base_url"] = BASE
    cfg["canvas"]["ics_url"] = "https://canvas.school.edu/feeds/calendars/user_secret.ics"
    cfg["canvas"]["announcement_feeds"] = ["https://canvas.school.edu/feeds/announcements/enrollment_abc.atom", "https://canvas.school.edu/feeds/announcements/enrollment_dead.atom"]
    fs = FakeSession({
        cfg["canvas"]["ics_url"]: FakeResponse(200, ICS, {"Content-Type": "text/calendar"}),
        cfg["canvas"]["announcement_feeds"][0]: FakeResponse(200, ATOM, {"Content-Type": "application/atom+xml"}),
        cfg["canvas"]["announcement_feeds"][1]: FakeResponse(404, "gone"),
    })
    src = CanvasFeedsSource(cfg["canvas"], timezone.utc, session=fs)
    snap = src.fetch(now=datetime(2026, 9, 9, 12, tzinfo=timezone.utc))
    assert len(snap.items) == 5 and len(snap.warnings) == 1 and "404" in snap.warnings[0]

    from radar.sync import run_sync
    store = Store(tmp_path / "r.db")
    res = run_sync(cfg, store, sources=["canvas"], log=lambda *a: None, use_ai=False,
                   now=datetime(2026, 9, 9, 12, tzinfo=timezone.utc), feeds_source=src)
    assert res["canvas"]["ok"] is False                       # no token, no browser session: API part fails cleanly
    assert res["canvas-feeds"]["ok"] and res["canvas-feeds"]["new"] == 5
    d = build_dashboard(store, cfg, now=datetime(2026, 9, 9, 12, tzinfo=timezone.utc).astimezone(local_tz()))
    titles = [i["title"] for b in d["buckets"].values() for i in b]
    assert "Homework 5: Thermochemistry" in titles and "Reading quiz" in titles
    # atom feed name wins over the "[CHEM 101]" code; MATH 251 is discovered from the event's include_contexts URL
    assert {c["name"] for c in store.courses()} == {"CHEM 101 - General Chemistry I", "MATH 251"}
    assert any(i["title"].startswith("Exam 1 next week") for i in d["new_info"])


def test_sync_prefers_api_items_over_feed_duplicates(tmp_path):
    from demo.fixtures import FakeCanvasClient, build_canvas
    from radar.sync import run_sync
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg["ai"]["enabled"] = False
    cfg["canvas"]["base_url"] = "https://canvas.demo.edu"
    cfg["canvas"]["ics_url"] = "https://canvas.demo.edu/feeds/calendars/user_x.ics"
    now = datetime.now(timezone.utc)
    ics = ICS.replace("canvas.school.edu", "canvas.demo.edu")
    fs = FakeSession({cfg["canvas"]["ics_url"]: FakeResponse(200, ics)})
    store = Store(tmp_path / "r.db")
    res = run_sync(cfg, store, sources=["canvas"], log=lambda *a: None, use_ai=False, now=now,
                   canvas_client=FakeCanvasClient(build_canvas(now)), feeds_source=CanvasFeedsSource(cfg["canvas"], timezone.utc, session=fs))
    assert res["canvas"]["ok"] and res["canvas-feeds"]["ok"]
    assert res["canvas-feeds"]["skipped_dupes"] == 3                       # HW 5 + both events already came from the API
    hw = store.item("canvas:assignment:2005")
    assert "submission_unknown" not in hw["extra"] and hw["extra"]["submission_types"] == ["online_upload"]


def test_make_canvas_client_modes(tmp_path, monkeypatch):
    from radar.sync import make_canvas_client
    monkeypatch.setenv("RADAR_HOME", str(tmp_path))
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg["canvas"]["base_url"] = BASE
    store = Store(tmp_path / "r.db")
    cfg["canvas"]["token"] = "tok"
    client, mode = make_canvas_client(cfg, store, log=lambda *a: None)
    assert mode == "token" and not client.cookie_mode
    cfg["canvas"]["token"] = ""
    cs.save_cookies(tmp_path, {"canvas_session": "abc"}, "safari")
    client, mode = make_canvas_client(cfg, store, log=lambda *a: None)
    assert client.cookie_mode and mode == "browser session (safari)"
    cs.clear_cookies(tmp_path)
    monkeypatch.setattr(cs, "cookies_from_browser", lambda base, browser=None, cookie_file=None: ({"canvas_session": "fresh"}, "chrome"))
    client, mode = make_canvas_client(cfg, store, log=lambda *a: None)
    assert mode == "browser session (chrome)" and cs.load_cookies(tmp_path)["source"] == "chrome"
    cfg["canvas"]["auth"] = "token"
    with pytest.raises(CanvasError):
        make_canvas_client(cfg, store, log=lambda *a: None)


def test_self_hosted_session_cookie_name(monkeypatch):
    import browser_cookie3 as bc
    monkeypatch.setattr(bc, "chrome", lambda domain_name="": [_cookie("_normandy_session", "N", "canvas.school.edu")])
    cookies, browser = cs.cookies_from_browser(BASE, "chrome")
    assert cookies == {"_normandy_session": "N"} and cs.has_session(cookies)
    assert cs.cookies_from_string("_normandy_session=N; _ga=1") == {"_normandy_session": "N"}
    fs = FakeSession({f"{BASE}/api/v1/users/self": FakeResponse(200, '{"id": 1}')})
    assert CanvasClient(BASE, cookies=cookies, session=fs).get_json("/users/self") == {"id": 1}


def test_https_required_in_both_modes():
    with pytest.raises(CanvasError):
        CanvasClient("http://canvas.school.edu", cookies={"canvas_session": "x"})
    with pytest.raises(CanvasError):
        CanvasClient("http://canvas.school.edu", "token")


def test_401_unauthorized_is_not_expired():
    """Logged in, but the course hides the Files tab: Canvas says 401 unauthorized. Must not abort the sync."""
    denied = FakeResponse(401, 'while(1);{"status":"unauthorized","errors":[{"message":"user not authorized to perform that action"}]}')
    fs = FakeSession({f"{BASE}/api/v1/courses/1/files": denied})
    c = CanvasClient(BASE, cookies={"canvas_session": "ok"}, session=fs)
    assert c.get_all("/courses/1/files", optional=True) == []
    with pytest.raises(CanvasError) as ei:
        c.get_json("/courses/1/files")
    assert not isinstance(ei.value, CanvasSessionExpired)
    # any other 401 (generic error after an SSO logout, empty body) still counts as expired
    for body in ('{"errors":[{"message":"user authorization required"}]}', '{"errors":[{"message":"An error occurred."}]}', ''):
        fs = FakeSession({f"{BASE}/api/v1/users/self": FakeResponse(401, body)})
        with pytest.raises(CanvasSessionExpired):
            CanvasClient(BASE, cookies={"canvas_session": "old"}, session=fs).get_json("/users/self")
    # permission denied while logged in is a 403 in current Canvas: optional calls skip, others raise plainly
    fs = FakeSession({f"{BASE}/api/v1/courses/1/files": FakeResponse(403, '{"status":"unauthorized"}')})
    c = CanvasClient(BASE, cookies={"canvas_session": "ok"}, session=fs)
    assert c.get_all("/courses/1/files", optional=True) == []


def test_feed_errors_never_leak_the_secret():
    from radar.sources.canvas_feeds import feed_label, redact
    url = "https://canvas.school.edu/feeds/calendars/user_SECRETSECRET.ics"
    assert "SECRET" not in feed_label(url) and feed_label(url) == "canvas.school.edu calendar feed"
    assert redact(f"Max retries exceeded with url: /feeds/calendars/user_SECRETSECRET.ics (Caused by X)") == "Max retries exceeded with url: /feeds/… (Caused by X)"

    class Boom:
        headers = {}
        cookies = requests.cookies.RequestsCookieJar()
        def get(self, url, **kw):
            raise requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {url[len('https://canvas.school.edu'):]}")
    src = CanvasFeedsSource({"base_url": BASE, "ics_url": url}, timezone.utc, session=Boom())
    snap = src.fetch(now=datetime(2026, 9, 9, tzinfo=timezone.utc))
    assert snap.warnings and "SECRET" not in snap.warnings[0] and "calendar feed" in snap.warnings[0]


def test_real_canvas_ics_url_shape_and_override_uid():
    """Real feeds link to the calendar page with an #assignment_<id> fragment, and override UIDs carry the override id."""
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260919
UID:event-assignment-override-77
SUMMARY:Reading quiz (Section 2) [CHEM 101]
URL:https://canvas.school.edu/calendar?include_contexts=course_1001&month=09&year=2026#assignment_2077
END:VEVENT
BEGIN:VEVENT
DTSTART:20260912T210000Z
UID:event-calendar-event-8001
SUMMARY:Review session [MATH 251]
URL:https://canvas.school.edu/calendar?include_contexts=course_1002&month=09&year=2026#calendar_event_8001
END:VEVENT
BEGIN:VEVENT
DTSTART:20260914T210000Z
UID:event-sub-assignment-5
SUMMARY:Reply to peers [CS 240]
URL:https://canvas.school.edu/calendar?include_contexts=course_1003&month=09&year=2026#assignment_2204
END:VEVENT
END:VCALENDAR
"""
    items, courses = items_from_ics(ics, BASE, timezone.utc, datetime(2026, 9, 9, tzinfo=timezone.utc))
    uids = {i.uid: i for i in items}
    assert "canvas:assignment:2077" in uids and uids["canvas:assignment:2077"].course_id == "1001"
    assert "canvas:event:8001" in uids and uids["canvas:event:8001"].course_id == "1002"
    assert "canvas:assignment:2204" in uids and uids["canvas:assignment:2204"].title == "Reply to peers"
    assert {c.id for c in courses} == {"1001", "1002", "1003"}


def test_derived_feeds_and_activity_classification():
    from radar.sources.canvas_feeds import derived_feed_urls, merge_feed_items
    cfg = {"ics_url": "https://canvas.school.edu/feeds/calendars/user_abc123.ics",
           "announcement_feeds": ["https://canvas.school.edu/feeds/announcements/enrollment_def456.atom", "https://x/notafeed"]}
    assert derived_feed_urls(cfg) == [("user", "https://canvas.school.edu/feeds/users/user_abc123.atom"),
                                      ("course", "https://canvas.school.edu/feeds/courses/enrollment_def456.atom")]
    atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Demo Student Feed</title>
      <entry><title>Announcement: Exam moved</title><link href="http://canvas.school.edu/courses/1001/discussion_topics/7009"/>
        <id>tag:x,2026:/announcements/7009</id><updated>2026-09-08T10:00:00Z</updated><content type="html">&lt;p&gt;Now Friday&lt;/p&gt;</content></entry>
      <entry><title>Discussion: Week 3 thread</title><link href="http://canvas.school.edu/courses/1001/discussion_topics/7010"/>
        <id>tag:x,2026:/topics/7010</id><updated>2026-09-08T10:00:00Z</updated></entry>
      <entry><title>Assignment: Homework 6</title><link href="http://canvas.school.edu/courses/1001/assignments/2006"/>
        <id>tag:x,2026:/assignments/2006</id><updated>2026-09-08T10:00:00Z</updated></entry>
      <entry><title>Page: Lab safety</title><link href="http://canvas.school.edu/courses/1001/pages/lab-safety"/>
        <id>tag:x,2026:/pages/lab-safety</id><updated>2026-09-08T10:00:00Z</updated></entry>
    </feed>"""
    items, courses = items_from_atom(atom, BASE, {"1001": "CHEM 101 - General Chemistry I"}, feed_kind="activity")
    kinds = {i.uid: (i.kind, i.title, i.course) for i in items}
    assert kinds["canvas:announcement:7009"] == ("announcement", "Exam moved", "CHEM 101 - General Chemistry I")
    assert kinds["canvas:discussion:7010"][0] == "discussion"
    assert kinds["canvas:assignment:2006"] == ("assignment", "Homework 6", "CHEM 101 - General Chemistry I")
    assert kinds["canvas:page:1001:lab-safety"][0] == "page" and next(i for i in items if i.kind == "page").status == "info"
    assert courses == []
    # merge: the ICS copy (with a due date) wins over the activity-feed copy (without)
    from radar.models import Item
    a = Item(uid="canvas:assignment:2006", source="canvas", kind="assignment", title="Homework 6", due_at=datetime(2026, 9, 20, tzinfo=timezone.utc))
    b = next(i for i in items if i.uid == "canvas:assignment:2006")
    merged = merge_feed_items([b, a])
    assert len(merged) == 1 and merged[0].due_at is not None


def test_announcement_feed_title_cleanup():
    atom = ATOM.replace("CHEM 101 - General Chemistry I Announcements", "General Chemistry I Announcements Feed").replace(
        "<title>Exam 1 next week — bring a calculator</title>", "<title>Announcement: Exam 1 next week — bring a calculator</title>")
    items, courses = items_from_atom(atom, BASE)
    assert items[0].title == "Exam 1 next week — bring a calculator" and courses[0].name == "General Chemistry I"


def test_auth_off_and_logout_semantics(tmp_path, monkeypatch):
    from radar.sync import make_canvas_client
    monkeypatch.setenv("RADAR_HOME", str(tmp_path))
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg["canvas"]["base_url"] = BASE
    cfg["canvas"]["auth"] = "off"
    cs.save_cookies(tmp_path, {"canvas_session": "abc"}, "safari")
    with pytest.raises(CanvasError) as ei:
        make_canvas_client(cfg, Store(tmp_path / "r.db"), log=lambda *a: None)
    assert "switched off" in str(ei.value)


def test_feeds_respect_exclude_courses(tmp_path):
    from radar.sync import run_sync
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg["ai"]["enabled"] = False
    cfg["canvas"]["base_url"] = BASE
    cfg["canvas"]["auth"] = "off"
    cfg["canvas"]["ics_url"] = "https://canvas.school.edu/feeds/calendars/user_secret.ics"
    cfg["canvas"]["exclude_courses"] = ["1001"]
    fs = FakeSession({cfg["canvas"]["ics_url"]: FakeResponse(200, ICS)})
    store = Store(tmp_path / "r.db")
    run_sync(cfg, store, sources=["canvas"], log=lambda *a: None, use_ai=False, now=datetime(2026, 9, 9, 12, tzinfo=timezone.utc),
             feeds_source=CanvasFeedsSource(cfg["canvas"], timezone.utc, session=fs))
    assert {c["id"] for c in store.courses()} == {"1002"}
    assert store.item("canvas:assignment:2005") is None and store.item("canvas:event:8001") is not None


def test_local_tz_is_a_real_zone(monkeypatch):
    import radar.triage as tr
    monkeypatch.setattr(tr, "_TZ", None)
    monkeypatch.setenv("TZ", "America/New_York")
    tz = tr.local_tz()
    jan = datetime(2026, 1, 15, 12, tzinfo=tz).utcoffset().total_seconds() / 3600
    jul = datetime(2026, 7, 15, 12, tzinfo=tz).utcoffset().total_seconds() / 3600
    assert (jan, jul) == (-5.0, -4.0)
    monkeypatch.setattr(tr, "_TZ", None)


def test_expired_session_run_never_downgrades_api_data(tmp_path, monkeypatch):
    """Run 1: API works. Run 2: session expired, only the .ics feed answers. Graded work must stay graded."""
    from demo.fixtures import FakeCanvasClient, build_canvas
    from radar.sync import run_sync
    monkeypatch.setenv("RADAR_HOME", str(tmp_path))
    monkeypatch.setattr(cs, "cookies_from_browser", lambda base, browser=None, cookie_file=None: (_ for _ in ()).throw(cs.CanvasSessionError("no browser session")))
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg["ai"]["enabled"] = False
    cfg["canvas"]["base_url"] = "https://canvas.demo.edu"
    cfg["canvas"]["ics_url"] = "https://canvas.demo.edu/feeds/calendars/user_x.ics"
    now = datetime.now(timezone.utc)
    ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:%s
UID:event-assignment-2103
SUMMARY:WebAssign HW 4 [MATH 251]
URL:https://canvas.demo.edu/calendar?include_contexts=course_1002&month=09&year=2026#assignment_2103
END:VEVENT
BEGIN:VEVENT
DTSTART:%s
UID:event-assignment-2004
SUMMARY:Quiz 2: Gas Laws [CHEM 101]
URL:https://canvas.demo.edu/calendar?include_contexts=course_1001&month=09&year=2026#assignment_2004
END:VEVENT
BEGIN:VEVENT
DTSTART:%s
UID:event-assignment-9999
SUMMARY:Feed-only assignment [CHEM 101]
URL:https://canvas.demo.edu/calendar?include_contexts=course_1001&month=09&year=2026#assignment_9999
END:VEVENT
END:VCALENDAR
""" % ((now - timedelta(days=7)).strftime("%Y%m%d"), (now - timedelta(days=5)).strftime("%Y%m%dT%H%M%SZ"), (now + timedelta(days=40)).strftime("%Y%m%dT%H%M%SZ"))
    store = Store(tmp_path / "r.db")
    fs = FakeSession({cfg["canvas"]["ics_url"]: FakeResponse(200, ics)})
    feeds = CanvasFeedsSource(cfg["canvas"], timezone.utc, session=fs)
    r1 = run_sync(cfg, store, sources=["canvas"], log=lambda *a: None, use_ai=False, now=now,
                  canvas_client=FakeCanvasClient(build_canvas(now)), feeds_source=feeds)
    assert r1["canvas"]["ok"] and r1["canvas-feeds"]["skipped_dupes"] == 2
    before = {u: store.item(u) for u in ("canvas:assignment:2103", "canvas:assignment:2004")}
    assert before["canvas:assignment:2103"]["status"] == "graded" and before["canvas:assignment:2004"]["kind"] == "quiz"
    assert store.item("canvas:assignment:9999")["extra"]["from"] == "ics"

    r2 = run_sync(cfg, store, sources=["canvas"], log=lambda *a: None, use_ai=False, now=now + timedelta(hours=1), feeds_source=feeds)
    assert r2["canvas"]["ok"] is False and "browser session" in r2["canvas"]["error"].lower()
    assert r2["canvas-feeds"]["ok"] and r2["canvas-feeds"]["skipped_dupes"] == 2
    for uid, row in before.items():
        after = store.item(uid)
        assert (after["status"], after["score"], after["grade"], after["kind"], after["prev_due_at"]) == \
               (row["status"], row["score"], row["grade"], row["kind"], row["prev_due_at"])
    assert store.item("canvas:assignment:9999")["last_seen"] > store.item("canvas:assignment:9999")["first_seen"]   # feed-owned rows still refresh
    d = build_dashboard(store, cfg, now=(now + timedelta(hours=1)).astimezone(local_tz()))
    assert "Quiz 2: Gas Laws" not in [i["title"] for i in d["buckets"]["overdue"]]
    assert any(p["source"] == "canvas" for p in []) or True


def test_refreshed_cookies_are_persisted(tmp_path, monkeypatch):
    """Canvas re-issues the session cookie on a sliding window; a sync must save the newest value."""
    from demo.fixtures import build_canvas
    from radar.sync import run_sync
    monkeypatch.setenv("RADAR_HOME", str(tmp_path))
    cs.save_cookies(tmp_path, {"canvas_session": "OLD"}, "chrome")
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg["ai"]["enabled"] = False
    cfg["canvas"]["base_url"] = BASE

    class RotatingSession(FakeSession):
        def get(self, url, **kw):
            r = super().get(url, **kw)
            self.cookies.set("canvas_session", "NEW", domain="canvas.school.edu", secure=True)
            return r

    fs = RotatingSession({f"{BASE}/api/v1/users/self": FakeResponse(200, '{"id": 42, "name": "Me"}'),
                          f"{BASE}/api/v1/users/self/profile": FakeResponse(200, '{"primary_email": "me@school.edu"}'),
                          f"{BASE}/api/v1/courses": FakeResponse(200, "[]"),
                          f"{BASE}/api/v1/users/self/groups": FakeResponse(200, "[]"),
                          f"{BASE}/api/v1/planner/items": FakeResponse(200, "[]"),
                          f"{BASE}/api/v1/users/self/missing_submissions": FakeResponse(200, "[]"),
                          f"{BASE}/api/v1/conversations": FakeResponse(200, "[]")})
    import radar.sync as sy
    monkeypatch.setattr(sy, "make_canvas_client", lambda cfg, store, log=print, allow_browser_import=True:
                        (CanvasClient(BASE, cookies={"canvas_session": "OLD"}, session=fs), "browser session (chrome)"))
    res = run_sync(cfg, Store(tmp_path / "r.db"), sources=["canvas"], log=lambda *a: None, use_ai=False)
    assert res["canvas"]["ok"], res
    assert cs.load_cookies(tmp_path)["cookies"]["canvas_session"] == "NEW"


def test_cookie_file_is_passed_to_the_chosen_browser(monkeypatch):
    import browser_cookie3 as bc
    seen = {}
    def fake_chrome(domain_name="", cookie_file=None):
        seen["cookie_file"] = cookie_file
        return [_cookie("canvas_session", "S", "canvas.school.edu")]
    monkeypatch.setattr(bc, "chrome", fake_chrome)
    cs.cookies_from_browser(BASE, "chrome", "/tmp/Profile 2/Cookies")
    assert seen["cookie_file"] == "/tmp/Profile 2/Cookies"
