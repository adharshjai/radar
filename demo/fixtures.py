"""Sample Canvas + Outlook data so the dashboard can be seen (and tested) without any accounts.

The payloads mimic the real API shapes and go through the real mapping code.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, time, timedelta, timezone

from radar.sources.outlook import map_calendar, map_message
from radar.triage import local_tz

BASE = "https://canvas.demo.edu"


def _local(now, days: int, hour: int = 23, minute: int = 59) -> str:
    tz = local_tz()
    d = now.astimezone(tz).date() + timedelta(days=days)
    return datetime.combine(d, time(hour, minute)).replace(tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(now, days: int) -> str:
    return (now.astimezone(local_tz()).date() + timedelta(days=days)).strftime("%B %-d, %Y")


def _docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{p}</w:t></w:r></w:p>" for p in paragraphs)
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           f"{body}</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def build_canvas(now: datetime) -> dict:
    L = lambda d, h=23, m=59: _local(now, d, h, m)  # noqa: E731
    courses = [
        {"id": 1001, "name": "CHEM 101 - General Chemistry I", "course_code": "CHEM 101", "workflow_state": "available",
         "term": {"name": "Fall 2026", "start_at": L(-21, 8, 0), "end_at": L(100)},
         "enrollments": [{"type": "student", "computed_current_score": 88.4, "computed_current_grade": "B+"}],
         "teachers": [{"display_name": "Dr. Anita Patel"}],
         "tabs": [{"id": "home", "type": "internal", "label": "Home"},
                  {"id": "context_external_tool_77", "type": "external", "label": "LabFlow", "html_url": "/courses/1001/external_tools/77"}],
         "syllabus_body": f"""<h2>CHEM 101 General Chemistry I — Fall 2026</h2>
<p>Lecture MWF 9:00–9:50 in Science Hall 120. Lab Wednesdays 2:00–4:50 PM in SH 214.</p>
<p>All pre-lab quizzes and lab reports are completed in <a href="https://app.labflow.com/course/chem101">LabFlow</a>. Pre-labs close at 1:00 PM on lab day. Lab reports are due by 11:59 PM the Tuesday after each lab.</p>
<h3>Important dates</h3>
<ul><li>Exam 1: Friday, {_date(now, 10)}, 7:00 PM, Science Hall 120 (bring a calculator)</li>
<li>Exam 2: {_date(now, 45)}, 7:00 PM</li>
<li>Last day to drop without a W: {_date(now, 7)}</li>
<li>Final Exam: {_date(now, 95)}, 10:30 AM – 12:30 PM</li></ul>
<p>Late work: 10% per day, up to 3 days. See the <a href="/courses/1001/files/5001">full syllabus (PDF)</a> and the <a href="https://drive.google.com/drive/folders/labmanual">lab manual</a>.</p>"""},
        {"id": 1002, "name": "MATH 251 - Calculus III", "course_code": "MATH 251", "workflow_state": "available",
         "term": {"name": "Fall 2026", "start_at": L(-21, 8, 0), "end_at": L(100)},
         "enrollments": [{"type": "student", "computed_current_score": 92.0, "computed_current_grade": "A-"}],
         "teachers": [{"display_name": "Prof. Marcus Lee"}],
         "tabs": [{"id": "context_external_tool_78", "type": "external", "label": "WebAssign", "html_url": "/courses/1002/external_tools/78"}],
         "syllabus_body": f"<p>Homework is on WebAssign every Friday at 11:59 PM. Midterm 1 on {_date(now, 9)} in class. Midterm 2 on {_date(now, 50)}. No class {_date(now, 20)} (fall break).</p>"},
        {"id": 1003, "name": "CS 240 - Data Structures", "course_code": "CS 240", "workflow_state": "available",
         "term": {"name": "Fall 2026", "start_at": L(-21, 8, 0), "end_at": L(100)},
         "enrollments": [{"type": "student", "computed_current_score": 95.5, "computed_current_grade": "A"}],
         "teachers": [{"display_name": "Dr. Yuki Tanaka"}],
         "tabs": [{"id": "context_external_tool_79", "type": "external", "label": "Piazza", "html_url": "/courses/1003/external_tools/79"},
                  {"id": "context_external_tool_80", "type": "external", "label": "Gradescope", "html_url": "/courses/1003/external_tools/80"}],
         "syllabus_body": "<p>Projects are submitted on <a href='https://www.gradescope.com/courses/cs240'>Gradescope</a>. Questions go on Piazza. Weekly check-in discussions are due Tuesdays.</p>"},
        {"id": 1004, "name": "ENGL 110 - College Writing", "course_code": "ENGL 110", "workflow_state": "available",
         "term": {"name": "Fall 2026", "start_at": L(-21, 8, 0), "end_at": L(100)},
         "enrollments": [{"type": "student", "computed_current_score": None, "computed_current_grade": None}],
         "teachers": [{"display_name": "Sofia Alvarez"}], "tabs": [], "syllabus_body": ""},
        {"id": 900, "name": "Old Course (Spring)", "course_code": "OLD 100", "workflow_state": "available",
         "term": {"name": "Spring 2026", "end_at": L(-60)}, "enrollments": [], "tabs": [], "syllabus_body": ""},
    ]

    def sub(state=None, score=None, grade=None, submitted=False, missing=False, late=False, graded_at=None):
        return {"workflow_state": state or ("submitted" if submitted else "unsubmitted"), "score": score, "grade": grade,
                "submitted_at": L(-2, 20, 0) if submitted else None, "missing": missing, "late": late,
                "graded_at": graded_at, "excused": False}

    assignments = {
        1001: [
            {"id": 2001, "name": "Pre-Lab 3: Acid-Base Titration", "due_at": L(1, 13, 0), "points_possible": 10,
             "submission_types": ["external_tool"], "external_tool_tag_attributes": {"url": "https://app.labflow.com/launch/prelab3"},
             "html_url": f"{BASE}/courses/1001/assignments/2001", "submission": sub(), "published": True,
             "description": "<p>Complete the pre-lab in LabFlow before lab. Watch the <a href='https://www.youtube.com/watch?v=titration'>titration demo</a>.</p>"},
            {"id": 2002, "name": "Lab Report 2: Calorimetry", "due_at": L(3), "points_possible": 50,
             "submission_types": ["external_tool"], "external_tool_tag_attributes": {"url": "https://app.labflow.com/launch/report2"},
             "html_url": f"{BASE}/courses/1001/assignments/2002", "submission": sub(), "published": True, "description": ""},
            {"id": 2003, "name": "Homework 4: Stoichiometry", "due_at": L(-2), "points_possible": 20,
             "submission_types": ["online_upload"], "html_url": f"{BASE}/courses/1001/assignments/2003", "submission": sub(missing=True),
             "published": True, "description": "<p>Problems 4.1–4.30.</p>"},
            {"id": 2004, "name": "Quiz 2: Gas Laws", "due_at": L(-5), "points_possible": 20, "is_quiz_assignment": True,
             "submission_types": ["online_quiz"], "html_url": f"{BASE}/courses/1001/quizzes/301",
             "submission": sub(state="graded", score=18, grade="18", submitted=True, graded_at=L(-1, 9, 0)), "published": True, "description": ""},
            {"id": 2005, "name": "Homework 5: Thermochemistry", "due_at": L(8), "points_possible": 20,
             "submission_types": ["online_upload"], "html_url": f"{BASE}/courses/1001/assignments/2005", "submission": sub(), "published": True, "description": ""},
        ],
        1002: [
            {"id": 2101, "name": "WebAssign HW 5: Partial Derivatives", "due_at": L(0), "points_possible": 15,
             "submission_types": ["external_tool"], "external_tool_tag_attributes": {"url": "https://www.webassign.net/launch/hw5"},
             "html_url": f"{BASE}/courses/1002/assignments/2101", "submission": sub(), "published": True, "description": ""},
            {"id": 2102, "name": "Midterm 1", "due_at": L(9, 10, 50), "points_possible": 100, "is_quiz_assignment": True,
             "submission_types": ["online_quiz"], "html_url": f"{BASE}/courses/1002/quizzes/302", "submission": sub(), "published": True,
             "description": "<p>In class. Covers chapters 12–14.</p>"},
            {"id": 2103, "name": "WebAssign HW 4", "due_at": L(-7), "points_possible": 15, "submission_types": ["external_tool"],
             "external_tool_tag_attributes": {"url": "https://www.webassign.net/launch/hw4"},
             "html_url": f"{BASE}/courses/1002/assignments/2103", "submission": sub(state="graded", score=15, grade="15", submitted=True, graded_at=L(-6)),
             "published": True, "description": ""},
            {"id": 2104, "name": "Old WebAssign HW 2 (no passback)", "due_at": L(-4), "points_possible": 15, "submission_types": ["external_tool"],
             "external_tool_tag_attributes": {"url": "https://www.webassign.net/launch/hw2"},
             "html_url": f"{BASE}/courses/1002/assignments/2104", "submission": sub(), "published": True, "description": ""},
        ],
        1003: [
            {"id": 2201, "name": "Project 1: Linked Lists", "due_at": L(5), "points_possible": 100,
             "submission_types": ["external_tool"], "external_tool_tag_attributes": {"url": "https://www.gradescope.com/courses/cs240/assignments/p1"},
             "html_url": f"{BASE}/courses/1003/assignments/2201", "submission": sub(), "published": True,
             "description": "<p>Starter code on <a href='https://classroom.github.com/a/cs240-p1'>GitHub Classroom</a>.</p>"},
            {"id": 2202, "name": "Lab 3: Stacks & Queues", "due_at": L(2, 17, 0), "points_possible": 10,
             "submission_types": ["online_upload"], "html_url": f"{BASE}/courses/1003/assignments/2202", "submission": sub(), "published": True, "description": ""},
            {"id": 2203, "name": "PA0: Environment Setup", "due_at": L(-14), "points_possible": 100,
             "submission_types": ["online_url"], "html_url": f"{BASE}/courses/1003/assignments/2203",
             "submission": sub(state="graded", score=95, grade="95", submitted=True, graded_at=L(-3, 15, 0)), "published": True, "description": ""},
            {"id": 2204, "name": "Weekly Check-in 4", "due_at": L(0, 23, 59), "points_possible": 5,
             "submission_types": ["discussion_topic"], "discussion_topic": {"id": 4401}, "html_url": f"{BASE}/courses/1003/discussion_topics/4401",
             "submission": sub(), "published": True, "description": ""},
            {"id": 2205, "name": "Unpublished draft", "due_at": L(3), "points_possible": 1, "submission_types": ["online_upload"],
             "html_url": f"{BASE}/courses/1003/assignments/2205", "submission": sub(), "published": False, "description": ""},
        ],
        1004: [
            {"id": 2301, "name": "Essay 1: Draft", "due_at": L(6), "points_possible": 30, "submission_types": ["online_upload"],
             "html_url": f"{BASE}/courses/1004/assignments/2301", "submission": sub(), "published": True,
             "description": "<p>Upload as PDF. See rubric. Annotations happen in <a href='https://app.perusall.com/courses/engl110'>Perusall</a>.</p>"},
            {"id": 2302, "name": "Reading Response 3", "due_at": L(-1), "points_possible": 5, "submission_types": ["online_text_entry"],
             "html_url": f"{BASE}/courses/1004/assignments/2302", "submission": sub(submitted=True, late=True), "published": True, "description": ""},
            {"id": 2303, "name": "Attendance", "due_at": None, "points_possible": 0, "submission_types": ["none"],
             "html_url": f"{BASE}/courses/1004/assignments/2303", "submission": sub(), "published": True, "description": ""},
        ],
    }
    discussions = {
        1003: [{"id": 4402, "title": "Ask anything: Project 1", "message": "<p>Post your questions here.</p>", "unread_count": 3, "read_state": "unread",
                "html_url": f"{BASE}/courses/1003/discussion_topics/4402", "posted_at": L(-1, 10, 0), "author": {"display_name": "Dr. Yuki Tanaka"}, "discussion_subentry_count": 12}],
        1004: [{"id": 4403, "title": "Introduce yourself", "message": "<p>Say hi!</p>", "unread_count": 0, "read_state": "read", "todo_date": L(4),
                "html_url": f"{BASE}/courses/1004/discussion_topics/4403", "posted_at": L(-10, 10, 0), "author": {"display_name": "Sofia Alvarez"}}],
    }
    modules = {
        1001: [{"id": 501, "name": "Week 4: Titration", "unlock_at": None, "items": [
                    {"id": 6001, "type": "ExternalTool", "title": "LabFlow: Pre-Lab 3", "html_url": f"{BASE}/courses/1001/modules/items/6001", "external_url": "https://app.labflow.com/launch/prelab3", "published": True},
                    {"id": 6002, "type": "ExternalUrl", "title": "Lab safety video", "external_url": "https://www.youtube.com/watch?v=labsafety", "html_url": f"{BASE}/courses/1001/modules/items/6002", "published": True},
                    {"id": 6003, "type": "Assignment", "title": "Lab Report 2: Calorimetry", "content_id": 2002, "html_url": f"{BASE}/courses/1001/assignments/2002", "published": True},
                ]}],
        1003: [{"id": 502, "name": "Week 4: Lists", "items": [
                    {"id": 6101, "type": "Page", "title": "Reading: Chapter 4 (Linked Lists)", "html_url": f"{BASE}/courses/1003/pages/reading-ch4",
                     "completion_requirement": {"type": "must_view", "completed": False}, "published": True, "content_details": {}},
                    {"id": 6102, "type": "Page", "title": "Reading: Chapter 3", "html_url": f"{BASE}/courses/1003/pages/reading-ch3",
                     "completion_requirement": {"type": "must_view", "completed": True}, "published": True, "content_details": {}},
                ]},
               {"id": 503, "name": "Week 5: Trees", "unlock_at": L(4, 8, 0), "items": []}],
    }
    external_tools = {
        1001: [{"id": 77, "name": "LabFlow", "url": "https://app.labflow.com/lti/launch", "domain": "labflow.com", "course_navigation": {"enabled": True}}],
        1002: [{"id": 78, "name": "WebAssign", "url": "https://www.webassign.net/lti", "course_navigation": {"enabled": True}}],
        1003: [{"id": 79, "name": "Piazza", "url": "https://piazza.com/lti", "course_navigation": {"enabled": True}},
               {"id": 80, "name": "Gradescope", "url": "https://www.gradescope.com/lti", "assignment_selection": {"enabled": True}}],
        1004: [{"id": 81, "name": "Perusall", "url": "https://app.perusall.com/lti", "course_navigation": {"enabled": True}}],
    }
    front_pages = {
        1001: {"title": "Welcome to CHEM 101", "url": "welcome", "body": "<p>Weekly plan: read before lecture. Office hours Tue 3–4 PM, SH 310. <a href='https://forms.gle/chem101survey'>Intro survey</a>.</p>"},
    }
    pages = {
        1003: [{"title": "Course Schedule", "url": "course-schedule", "updated_at": L(-3)}, {"title": "Reading: Chapter 4 (Linked Lists)", "url": "reading-ch4", "updated_at": L(-1)}],
    }
    page_bodies = {
        (1003, "course-schedule"): {"title": "Course Schedule", "body": f"<table><tr><td>{_date(now, 12)}</td><td>Exam 1 (in class)</td></tr><tr><td>{_date(now, 26)}</td><td>Project 2 due</td></tr></table>"},
    }
    files = {
        1001: [{"id": 5001, "display_name": "CHEM101_Syllabus_Fall2026.docx", "filename": "CHEM101_Syllabus_Fall2026.docx", "size": 4000,
                "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "updated_at": L(-20), "url": f"{BASE}/files/5001/download?verifier=x"}],
        1004: [{"id": 5002, "display_name": "Essay 1 rubric.pdf", "filename": "Essay 1 rubric.pdf", "size": 90000, "content-type": "application/pdf",
                "updated_at": L(-1, 14, 0), "url": f"{BASE}/files/5002/download"}],
    }
    file_meta = {5001: {**files[1001][0], "url": f"{BASE}/files/5001/download?verifier=x"}}
    file_bytes = {f"{BASE}/files/5001/download?verifier=x": _docx([
        "CHEM 101 General Chemistry I — Full Syllabus", "Instructor: Dr. Anita Patel, apatel@demo.edu, office SH 310",
        f"Exam 1: {_date(now, 10)} at 7:00 PM in Science Hall 120.", f"Exam 2: {_date(now, 45)} at 7:00 PM.",
        f"Final exam: {_date(now, 95)}, 10:30 AM.", "Grading: exams 60%, labs 25%, homework 15%.",
        "Attendance: lab attendance is mandatory; a missed lab cannot be made up.",
        f"Field trip to the water treatment plant on {_date(now, 33)} (meet at the bus loop, 1:00 PM).",
    ])}
    announcements = [
        {"id": 7001, "context_code": "course_1001", "title": "Exam 1 next week — bring a calculator", "read_state": "unread", "posted_at": L(-1, 8, 30),
         "html_url": f"{BASE}/courses/1001/discussion_topics/7001", "author": {"display_name": "Dr. Anita Patel"},
         "message": f"<p>Exam 1 is on {_date(now, 10)} at 7 PM in SH 120. Bring a non-graphing calculator and your student ID. Review sheet is posted under Files. Practice problems on <a href='https://app.labflow.com/course/chem101/practice'>LabFlow</a>.</p>"},
        {"id": 7002, "context_code": "course_1002", "title": "Office hours moved to Thursday", "read_state": "read", "posted_at": L(-3, 12, 0),
         "html_url": f"{BASE}/courses/1002/discussion_topics/7002", "author": {"display_name": "Prof. Marcus Lee"}, "message": "<p>This week only, office hours are Thursday 2–3 PM.</p>"},
        {"id": 7003, "context_code": "course_1004", "title": "Class canceled Monday", "read_state": "unread", "posted_at": L(0, 7, 15),
         "html_url": f"{BASE}/courses/1004/discussion_topics/7003", "author": {"display_name": "Sofia Alvarez"}, "message": "<p>I'm at a conference. Use the time to work on your draft; upload a paragraph to Perusall by Wednesday.</p>"},
    ]
    events = [
        {"id": 8001, "context_code": "course_1002", "title": "Midterm 1 review session", "start_at": L(3, 17, 0), "end_at": L(3, 18, 30),
         "location_name": "Math Building 105", "html_url": f"{BASE}/calendar?event_id=8001", "description": "<p>Bring questions.</p>", "all_day": False},
        {"id": 8002, "context_code": "user_42", "title": "Advising appointment", "start_at": L(2, 10, 0), "end_at": L(2, 10, 30),
         "location_name": "Student Center 210", "html_url": f"{BASE}/calendar?event_id=8002", "all_day": False},
        {"id": 8003, "context_code": "course_1001", "title": "Lab safety training (required)", "start_at": L(1, 14, 0), "end_at": L(1, 15, 0),
         "location_name": "SH 214", "html_url": f"{BASE}/calendar?event_id=8003", "all_day": False},
    ]
    planner = [
        {"plannable_type": "planner_note", "plannable_id": 9001, "course_id": 1001, "plannable": {"id": 9001, "title": "Buy lab goggles + notebook", "todo_date": L(1, 9, 0), "details": "Bookstore, before lab"}, "planner_override": None},
        {"plannable_type": "wiki_page", "plannable_id": 9002, "course_id": 1003, "html_url": "/courses/1003/pages/reading-ch4", "plannable": {"id": 9002, "title": "Reading: Chapter 4 (Linked Lists)", "todo_date": L(1)}, "planner_override": None},
        {"plannable_type": "assignment", "plannable_id": 2202, "course_id": 1003, "planner_override": {"marked_complete": False}},
    ]
    missing = [{"id": 2003, "name": "Homework 4: Stoichiometry", "course_id": 1001}]
    conversations = [
        {"id": 3001, "subject": "Regrade request for PA0", "workflow_state": "unread", "last_message": "Hi, I looked at your PA0 — can you resubmit the README by Friday? Then I can regrade.",
         "last_message_at": L(-1, 16, 0), "participants": [{"id": 42, "name": "You"}, {"id": 55, "name": "Priya (TA)"}], "context_name": "CS 240 - Data Structures", "message_count": 2},
        {"id": 3002, "subject": "Study group Sunday?", "workflow_state": "read", "last_message": "Library room 3B at 2pm",
         "last_message_at": L(-2, 19, 0), "participants": [{"id": 42, "name": "You"}, {"id": 56, "name": "Jordan"}], "context_name": "MATH 251 - Calculus III", "message_count": 5},
    ]
    return {
        "me": {"id": 42, "name": "Demo Student", "primary_email": "student@demo.edu"},
        "profile": {"primary_email": "student@demo.edu"},
        "courses": courses, "assignments": assignments, "discussions": discussions, "quizzes": {}, "modules": modules,
        "external_tools": external_tools, "front_pages": front_pages, "pages": pages, "page_bodies": page_bodies,
        "files": files, "file_meta": file_meta, "file_bytes": file_bytes, "announcements": announcements, "events": events,
        "planner": planner, "missing": missing, "conversations": conversations, "groups": [],
    }


class FakeCanvasClient:
    """Serves the fixture payloads through the same interface as CanvasClient."""

    def __init__(self, data: dict):
        self.d = data
        self.base = BASE
        self.host = "canvas.demo.edu"
        self.calls = 0

    def _course_id(self, path):
        parts = path.split("/")
        return int(parts[2]) if len(parts) > 2 and parts[1] == "courses" and parts[2].isdigit() else None

    def get_json(self, path, params=None, optional=False):
        self.calls += 1
        if path == "/users/self":
            return self.d["me"]
        if path == "/users/self/profile":
            return self.d["profile"]
        cid = self._course_id(path)
        if path.endswith("/front_page"):
            return self.d["front_pages"].get(cid)
        if "/pages/" in path:
            return self.d["page_bodies"].get((cid, path.rsplit("/", 1)[1]))
        if path.startswith("/files/"):
            return self.d["file_meta"].get(int(path.split("/")[2]))
        return None

    def get_all(self, path, params=None, optional=False, max_pages=30):
        self.calls += 1
        if path == "/courses":
            return self.d["courses"]
        if path == "/users/self/groups":
            return self.d["groups"]
        if path == "/announcements":
            return self.d["announcements"]
        if path == "/calendar_events":
            return self.d["events"]
        if path == "/planner/items":
            return self.d["planner"]
        if path == "/users/self/missing_submissions":
            return self.d["missing"]
        if path == "/conversations":
            return self.d["conversations"]
        cid = self._course_id(path)
        key = path.rsplit("/", 1)[1]
        table = {"assignments": "assignments", "discussion_topics": "discussions", "quizzes": "quizzes", "modules": "modules",
                 "external_tools": "external_tools", "pages": "pages", "files": "files"}.get(key)
        if table:
            return self.d[table].get(cid, [])
        return []

    def download(self, url, max_bytes=15_000_000):
        return self.d["file_bytes"].get(url)


def build_outlook(now: datetime) -> tuple[dict, list]:
    L = lambda d, h=9, m=0: _local(now, d, h, m)  # noqa: E731

    def msg(i, subject, name, addr, days, hour, body, read=False, flagged=False, focused=True):
        return {"id": f"AAMk{i}", "subject": subject, "from": {"emailAddress": {"name": name, "address": addr}},
                "receivedDateTime": L(days, hour), "bodyPreview": body[:120], "body": {"contentType": "text", "content": body},
                "isRead": read, "webLink": f"https://outlook.office.com/mail/id/{i}", "importance": "normal",
                "flag": {"flagStatus": "flagged" if flagged else "notFlagged"}, "hasAttachments": False, "categories": [],
                "conversationId": f"conv{i}", "inferenceClassification": "focused" if focused else "other"}

    messages = [
        msg(1, "Reminder: lab safety quiz due Friday", "Anita Patel", "apatel@demo.edu", 0, 8,
            "Hi all, the online lab safety quiz in LabFlow must be completed by Friday at 5 PM or you cannot attend lab next week. — Dr. Patel"),
        msg(2, "Action required: verify your financial aid documents", "Office of Financial Aid", "finaid@demo.edu", -1, 14,
            f"Your file is incomplete. Please upload the verification worksheet to the student portal by {_date(now, 11)} to avoid a hold on your account."),
        msg(3, "Campus Dining: this week's specials 🍕", "Campus Dining", "dining-news@demo.edu", -1, 11,
            "Taco Tuesday is back! ... To unsubscribe from these emails click here.", read=True, focused=False),
        msg(4, "Assignment Created - Homework 5: Thermochemistry, CHEM 101 - General Chemistry I", "Dr. Anita Patel", "notifications@instructure.com", -2, 9,
            f"A new assignment has been created for your course, CHEM 101. Homework 5: Thermochemistry due {_date(now, 8)} at 11:59pm.", read=True),
        msg(5, f"Last day to drop without a W is {_date(now, 7)}", "Registrar", "registrar@demo.edu", -2, 10,
            f"Reminder: {_date(now, 7)} is the last day to drop a full-term class without a W. Drops are done in the portal.", read=True),
        msg(6, "Robotics Club: meeting Thursday 6pm", "Robotics Club", "robotics@clubs.demo.edu", -3, 18,
            "Join us Thursday at 6 PM in Eng 120. Pizza provided. RSVP on the form.", read=True),
        msg(7, "CS 240 Project 1 clarification", "Priya Shah", "pshah@demo.edu", 0, 7,
            "For Project 1, the iterator must be fail-fast. Also: submit on Gradescope, not Canvas. Regrade requests close 48h after grades post.", flagged=True),
        msg(8, "Your Outlook storage is almost full", "Microsoft", "no-reply@microsoft.com", -4, 3, "You have used 95% of your storage.", read=True, focused=False),
        msg(9, "Re: Study group", "Jordan Kim", "jkim@demo.edu", -1, 21, "Sunday 2pm works, library room 3B. Bring the HW 5 problems.", read=True),
    ]
    calendar = [
        {"id": "cal1", "subject": "CHEM 101 Lab", "start": {"dateTime": L(1, 14, 0)}, "end": {"dateTime": L(1, 16, 50)}, "location": {"displayName": "SH 214"},
         "webLink": "https://outlook.office.com/calendar/item/cal1", "isAllDay": False, "bodyPreview": "", "organizer": {"emailAddress": {"name": "Demo Student"}}},
        {"id": "cal2", "subject": "Office hours — Dr. Patel", "start": {"dateTime": L(2, 15, 0)}, "end": {"dateTime": L(2, 16, 0)}, "location": {"displayName": "SH 310"},
         "webLink": "https://outlook.office.com/calendar/item/cal2", "isAllDay": False, "bodyPreview": "", "organizer": {"emailAddress": {"name": "Demo Student"}}},
        {"id": "cal3", "subject": "Career fair", "start": {"dateTime": L(6, 0, 0)}, "end": {"dateTime": L(7, 0, 0)}, "location": {"displayName": "Student Center"},
         "webLink": "https://outlook.office.com/calendar/item/cal3", "isAllDay": True, "bodyPreview": "", "organizer": {"emailAddress": {"name": "Career Services"}}},
    ]
    return {"displayName": "Demo Student", "mail": "student@demo.edu"}, [map_message(m) for m in messages] + [map_calendar(c) for c in calendar]


class FakeOutlookSource:
    def __init__(self, now):
        self.now = now

    def fetch(self, now=None):
        return build_outlook(now or self.now)


def load_demo(store, cfg: dict, now: datetime | None = None) -> dict:
    from radar.sync import run_sync
    now = now or datetime.now(timezone.utc)
    cfg["canvas"]["base_url"] = BASE
    cfg["profile"]["university_domains"] = ["demo.edu"]
    return run_sync(cfg, store, sources=["canvas", "outlook"], log=lambda *a: None, use_ai=False, now=now,
                    canvas_client=FakeCanvasClient(build_canvas(now)), outlook_source=FakeOutlookSource(now))
