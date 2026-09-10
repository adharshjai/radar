"""JSON API used by the Mac app."""
import copy
from datetime import datetime, timezone

import pytest

from demo.fixtures import load_demo
from radar import config as cfgmod
from radar.store import Store
from radar.web import create_app

TOKEN = "t0ken"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_HOME", str(tmp_path))
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg["ai"]["enabled"] = False
    cfg["server"]["sync_every_minutes"] = 0
    store = Store(tmp_path / "radar.db")
    load_demo(store, cfg, now=datetime.now(timezone.utc))
    app = create_app(cfg, store, demo=True, app_mode=True, api_token=TOKEN)
    app.config["TESTING"] = True
    c = app.test_client()
    c.environ_base["HTTP_X_RADAR_TOKEN"] = TOKEN
    return c


def test_token_required(client):
    assert client.get("/api/health").status_code == 200                       # health is open
    bare = client.application.test_client()
    assert bare.get("/api/dashboard").status_code == 401
    assert bare.get("/").status_code == 401                                   # HTML routes are guarded in app mode too
    assert bare.get("/api/dashboard?token=" + TOKEN).status_code == 200
    assert bare.get("/static/style.css").status_code == 200


def test_dashboard_shape(client):
    d = client.get("/api/dashboard").get_json()
    for k in ("generated_at", "syncing", "configured", "stats", "buckets", "new_info", "graded", "agenda", "workload", "courses", "problems"):
        assert k in d, k
    assert set(d["buckets"]) == {"overdue", "today", "tomorrow", "week", "later", "nodate"}
    assert d["stats"]["overdue"] >= 2 and len(d["workload"]) == 14 and len(d["agenda"]) == 8
    it = d["buckets"]["overdue"][0]
    for k in ("uid", "kind", "title", "status", "due_at", "due_text", "extra", "priority", "is_new"):
        assert k in it, k
    assert it["due_at"].endswith("+00:00")
    course = next(c for c in d["courses"] if c["id"] == "1001")
    assert course["open_count"] >= 3 and course["code"] == "CHEM 101"
    chem_check = next(i for i in d["buckets"]["nodate"] if i["title"] == "Check LabFlow")
    assert chem_check["extra"]["site_ids"] and chem_check["extra"]["external_url"]


def test_calendar_inbox_sites_course_search_status(client):
    cal = client.get("/api/calendar?days=14").get_json()
    assert len(cal["days"]) == 15 and cal["days"][0]["is_today"]
    assert any(i["title"] == "CHEM 101 Lab" for i in cal["days"][1]["items"])
    mail = client.get("/api/inbox").get_json()["mail"]
    titles = [m["title"] for m in mail]
    assert "Reminder: lab safety quiz due Friday" in titles and "Campus Dining: this week's specials 🍕" not in titles
    assert "Campus Dining: this week's specials 🍕" in [m["title"] for m in client.get("/api/inbox?all=1").get_json()["mail"]]
    sites = client.get("/api/sites").get_json()["sites"]
    assert any(s["platform"] == "LabFlow" and s["course_name"].startswith("CHEM 101") for s in sites)
    course = client.get("/api/course/1001").get_json()
    assert course["course"]["name"].startswith("CHEM 101") and course["groups"]["tasks"] and course["groups"]["syllabus"]
    assert "Exam 1" in course["syllabus_text_preview"]
    assert client.get("/api/course/nope").status_code == 404
    s = client.get("/api/search?q=lab&kind=assignment").get_json()
    assert s["items"] and all(i["kind"] == "assignment" for i in s["items"]) and "assignment" in s["kinds"]
    st = client.get("/api/status").get_json()
    assert st["syncs"] and st["config"]["canvas"]["base_url"] and st["engine"]["version"] and st["counts"]["courses"] == 4
    assert client.get("/api/brief").get_json()["text"].startswith("RADAR")


def test_config_roundtrip(client):
    c = client.get("/api/config").get_json()
    assert c["canvas"]["has_token"] is False and c["ai"]["has_key"] in (True, False)
    r = client.put("/api/config", json={"canvas": {"ics_url": "https://x/feeds/calendars/user_a.ics", "announcement_feeds": "https://x/a.atom, https://x/b.atom",
                                                   "token": "should-not-be-echoed"},
                                        "server": {"sync_every_minutes": 15}, "bogus": {"x": 1}, "ai": {"api_key": "sk-test"}}).get_json()
    assert r["ok"] and "canvas.ics_url" in r["changed"] and "bogus.x" not in r["changed"]
    c = r["config"]
    assert c["canvas"]["announcement_feeds"] == ["https://x/a.atom", "https://x/b.atom"] and c["server"]["sync_every_minutes"] == 15
    assert c["canvas"]["has_token"] is True and "token" not in c["canvas"] and c["ai"]["key_in_config"] is True and "api_key" not in c["ai"]


def test_canvas_login_validation_and_logout(client):
    r = client.post("/api/canvas/login", json={"method": "token", "token": ""})
    assert r.status_code == 400 and "access token" in r.get_json()["error"]
    r = client.post("/api/canvas/login", json={"base_url": "canvas.school.edu", "method": "paste", "cookie": "foo=bar; _ga=1"})
    assert r.status_code == 400 and "session cookie" in r.get_json()["error"]
    r = client.post("/api/canvas/logout").get_json()
    assert r["ok"] and r["auth"] in ("off", "token")


def test_item_actions_and_notifications(client):
    d = client.get("/api/dashboard").get_json()
    uid = d["buckets"]["overdue"][0]["uid"]
    assert client.post(f"/api/item/{uid}/state", json={"action": "done"}).status_code == 200
    d2 = client.get("/api/dashboard").get_json()
    assert uid not in [i["uid"] for i in d2["buckets"]["overdue"]] and uid in [i["uid"] for i in d2["done_recent"]]
    r = client.post("/api/item", json={"title": "Email advisor", "due": "2030-01-01T09:00", "course_id": "1001"}).get_json()
    assert r["ok"] and any(i["title"] == "Email advisor" for i in client.get("/api/dashboard").get_json()["buckets"]["later"])
    n = client.post("/api/notifications/claim").get_json()["items"]
    assert n and all("uid" in i for i in n)
    assert client.post("/api/notifications/claim").get_json()["items"] == []     # claimed once


def test_outlook_login_status_idle(client):
    s = client.get("/api/outlook/login/status").get_json()
    assert s["state"] in ("idle", "pending", "done", "error")
