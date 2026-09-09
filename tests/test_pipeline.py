import copy
from datetime import datetime, timedelta, timezone

import pytest

from demo.fixtures import load_demo
from radar import config as cfgmod
from radar.mailrules import score_email
from radar.models import Item
from radar.store import Store
from radar.triage import build_dashboard, local_tz, similar


@pytest.fixture
def cfg():
    c = copy.deepcopy(cfgmod.DEFAULTS)
    c["ai"]["enabled"] = False
    c["server"]["sync_every_minutes"] = 0
    return c


@pytest.fixture
def demo_store(tmp_path, cfg):
    store = Store(tmp_path / "radar.db")
    now = datetime.now(timezone.utc)
    res = load_demo(store, cfg, now=now)
    assert res["canvas"]["ok"], res
    assert res["outlook"]["ok"], res
    return store


def titles(items):
    return [i["title"] for i in items]


def test_demo_dashboard_buckets(demo_store, cfg):
    d = build_dashboard(demo_store, cfg, now=datetime.now(local_tz()))
    b = d["buckets"]
    assert "Homework 4: Stoichiometry" in titles(b["overdue"])            # Canvas says missing
    assert "Old WebAssign HW 2 (no passback)" in titles(b["overdue"])     # external tool past due -> verify
    assert "WebAssign HW 5: Partial Derivatives" in titles(b["today"])
    assert "Weekly Check-in 4" in titles(b["today"])                      # graded discussion
    assert "Pre-Lab 3: Acid-Base Titration" in titles(b["tomorrow"])
    assert "Buy lab goggles + notebook" in titles(b["tomorrow"])          # planner note
    assert "Lab 3: Stacks & Queues" in titles(b["week"])
    assert "Project 1: Linked Lists" in titles(b["week"])
    assert "Essay 1: Draft" in titles(b["week"])
    assert "Homework 5: Thermochemistry" in titles(b["later"])
    # unpublished / submitted / graded / attendance are not tasks
    all_task_titles = titles(sum(b.values(), []))
    for t in ("Unpublished draft", "Reading Response 3", "Quiz 2: Gas Laws", "Attendance", "Old Course (Spring)"):
        assert t not in all_task_titles
    # module requirement + page todo collapse into one task
    assert sum(1 for t in all_task_titles if t.startswith("Reading: Chapter 4 (Linked Lists)")) == 1
    # site checks appear in the no-date bucket
    assert any(t.startswith("Check LabFlow") for t in titles(b["nodate"]))
    assert d["stats"]["overdue"] >= 2 and d["stats"]["today"] >= 2


def test_platform_tagging_and_sites(demo_store, cfg):
    d = build_dashboard(demo_store, cfg, now=datetime.now(local_tz()))
    prelab = next(i for i in d["items"] if i["title"].startswith("Pre-Lab 3"))
    assert prelab["platform"] == "LabFlow"
    proj = next(i for i in d["items"] if i["title"].startswith("Project 1"))
    assert proj["platform"] == "Gradescope"
    sites = demo_store.sites()
    names = {(s["course_id"], s["platform"]) for s in sites}
    assert ("1001", "LabFlow") in names and ("1003", "Gradescope") in names and ("1003", "Piazza") in names
    assert any(s["platform"] == "GitHub Classroom" for s in sites)        # from an assignment description
    assert any(s["platform"] == "Google Form" for s in sites)             # from the front page
    assert any(s["platform"] == "YouTube" and s["category"] == "media" for s in sites)
    nav = [s for s in sites if s["where_found"] == "course navigation"]
    assert nav and all(s["check_every_days"] == 3 for s in nav)
    from_desc = [s for s in sites if s["where_found"].startswith("assignment:") and s["category"] == "tool"]
    assert from_desc and all(s["check_every_days"] == 0 for s in from_desc)
    d = build_dashboard(demo_store, cfg, now=datetime.now(local_tz()))
    checks = [c["title"] for c in d["checks"]]
    assert checks.count("Check LabFlow") == 1 and "Check Gradescope" in checks and "Check GitHub Classroom" not in checks


def test_syllabus_items_and_dedupe(demo_store, cfg):
    d = build_dashboard(demo_store, cfg, now=datetime.now(local_tz()))
    syl = [i for i in d["items"] if i["kind"] == "syllabus_event"]
    assert syl, "syllabus page/docx should yield dated items"
    exam1 = [i for i in syl if "Exam 1" in i["title"] and i["course_id"] == "1001"]
    assert exam1 and exam1[0]["extra"]["hint"] == "exam" and exam1[0]["due_at"] is not None
    assert len(exam1) == 2 and sum(1 for i in exam1 if not i["extra"].get("dup_of")) == 1   # page + docx -> one task
    graded = next(i for i in d["items"] if i["title"] == "Quiz 2: Gas Laws")
    assert graded["due_text"].startswith("was due")
    # syllabus docx text made it into the course record
    c = demo_store.course("1001")
    assert "Field trip" in c["syllabus_text"] and "Syllabus page" in c["syllabus_text"]
    # MATH syllabus "Midterm 1" duplicates the Canvas quiz on the same day -> hidden from tasks
    mid = [i for i in syl if "Midterm 1" in i["title"] and i["course_id"] == "1002"]
    assert mid and mid[0]["extra"].get("dup_of") == "canvas:assignment:2102"
    assert "Midterm 1" in titles(d["buckets"]["later"]) or "Midterm 1" in titles(d["buckets"]["week"])
    # the CS "Course Schedule" page contributed dates too
    assert any("Project 2" in i["title"] for i in syl if i["course_id"] == "1003")


def test_email_triage(demo_store, cfg):
    d = build_dashboard(demo_store, cfg, now=datetime.now(local_tz()))
    emails = {i["title"]: i for i in d["items"] if i["kind"] == "email"}
    lab = emails["Reminder: lab safety quiz due Friday"]
    assert lab["extra"]["action_required"] and lab["extra"]["relevance"] >= 60
    assert lab["course_id"] == "1001"                                       # matched via instructor name
    assert lab["extra"].get("soft_due") and lab["due_text"].startswith("mentions")   # "by Friday" -> dated bucket
    assert any(lab in v for v in d["buckets"].values())
    club = emails["Robotics Club: meeting Thursday 6pm"]
    assert club["extra"].get("soft_due")
    finaid = emails["Action required: verify your financial aid documents"]
    assert finaid["extra"]["action_required"] and finaid["extra"]["mentioned_dates"]
    dining = emails["Campus Dining: this week's specials 🍕"]
    assert dining["extra"]["bulk"] and dining["extra"]["relevance"] < 40
    canvas_note = emails["Assignment Created - Homework 5: Thermochemistry, CHEM 101 - General Chemistry I"]
    assert canvas_note["extra"]["canvas_notification"] and canvas_note["extra"].get("dup_of") == "canvas:assignment:2005"
    cs = emails["CS 240 Project 1 clarification"]
    assert cs["course_id"] == "1003" and cs["extra"]["flagged"]
    ms = emails["Your Outlook storage is almost full"]
    assert ms["extra"]["relevance"] < 40


def test_new_info_and_agenda(demo_store, cfg):
    d = build_dashboard(demo_store, cfg, now=datetime.now(local_tz()))
    new_titles = titles(d["new_info"])
    assert "Exam 1 next week — bring a calculator" in new_titles
    assert "Regrade request for PA0" in new_titles
    assert "Essay 1 rubric.pdf" in new_titles
    assert "Campus Dining: this week's specials 🍕" not in new_titles
    assert titles(d["graded"])[:2] == ["Quiz 2: Gas Laws", "PA0: Environment Setup"]
    tomorrow = d["agenda"][1]["items"]
    assert "CHEM 101 Lab" in titles(tomorrow) and "Lab safety training (required)" in titles(tomorrow)
    assert d["workload"][0]["count"] >= 2


def test_store_preserves_user_state_and_tracks_changes(tmp_path):
    store = Store(tmp_path / "x.db")
    now = datetime.now(timezone.utc)
    it = Item(uid="canvas:assignment:1", source="canvas", kind="assignment", title="A", due_at=now + timedelta(days=1))
    assert store.upsert_items([it]) == {"new": 1, "changed": 0, "total": 1}
    store.set_user_state("canvas:assignment:1", "done")
    store.set_note("canvas:assignment:1", "hello")
    it.due_at = now + timedelta(days=3)
    assert store.upsert_items([it])["changed"] == 1
    row = store.item("canvas:assignment:1")
    assert row["user_state"] == "done" and row["note"] == "hello"
    assert row["prev_due_at"] is not None and row["changed_at"] is not None
    assert store.upsert_items([it])["changed"] == 0
    store.prune("canvas", None, set())
    assert store.item("canvas:assignment:1") is None


def test_similar():
    assert similar("Midterm 1", "Midterm 1 in class")
    assert similar("Exam 1: Friday, September 19, 2026, 7:00 PM", "Exam 1")
    assert not similar("Lab Report 2", "Homework 4")


def test_score_email_direct():
    now = datetime.now(timezone.utc)
    it = Item(uid="x", source="outlook", kind="email", title="Please submit the form by Friday", body="Fill out the form.",
              sender="Anita Patel <apatel@school.edu>", posted_at=now, extra={"from_addr": "apatel@school.edu", "from_name": "Anita Patel"})
    score_email(it, [{"id": "1", "name": "CHEM 101", "code": "CHEM 101", "teachers": ["Dr. Anita Patel"]}],
                {"university_domains": ["school.edu"]}, now, local_tz())
    assert it.course_id == "1" and it.extra["action_required"] and it.extra["relevance"] >= 80
    assert it.extra["mentioned_dates"]
