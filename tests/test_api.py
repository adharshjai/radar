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
    assert bare.post("/api/sync").status_code == 401                          # app-level routes too, not only the blueprint
    assert bare.post("/api/item/x/state", json={"action": "done"}).status_code == 401
    assert bare.get("/static/style.css").status_code == 200
    # a ?token= visit moves the secret into an HttpOnly cookie and redirects to a clean URL
    r = bare.get("/?token=" + TOKEN + "&course=1001")
    assert r.status_code == 303 and r.headers["Location"].endswith("/?course=1001")
    assert "radar_token=" in r.headers.get("Set-Cookie", "") and "HttpOnly" in r.headers["Set-Cookie"]
    assert bare.get("/calendar").status_code == 200                           # cookie now carries the token


def test_cross_site_posts_are_rejected(client):
    assert client.post("/api/sync", headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/sync", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/visited", headers={"Origin": "http://localhost"}).status_code == 200


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
                                                   "token": "should-not-be-echoed", "include_courses": 1001},
                                        "server": {"sync_every_minutes": "15"}, "bogus": {"x": 1}, "ai": {"api_key": "sk-test"}}).get_json()
    assert r["ok"] and "canvas.ics_url" in r["changed"] and "bogus.x" not in r["changed"]
    c = r["config"]
    assert c["canvas"]["announcement_feeds"] == ["https://x/a.atom", "https://x/b.atom"] and c["server"]["sync_every_minutes"] == 15
    assert c["canvas"]["include_courses"] == ["1001"]                          # scalars become one-element lists
    assert c["canvas"]["has_token"] is True and "token" not in c["canvas"] and c["ai"]["key_in_config"] is True and "api_key" not in c["ai"]
    bad = client.put("/api/config", json={"server": {"sync_every_minutes": "soon"}})
    assert bad.status_code == 400 and "sync_every_minutes" in bad.get_json()["error"]
    assert client.get("/api/config").get_json()["server"]["sync_every_minutes"] == 15   # nothing changed on a rejected write
    assert client.put("/api/config", json={"canvas": {"fetch_files": "nope"}}).status_code == 400


def test_canvas_login_blocked_in_demo_and_logout(client):
    r = client.post("/api/canvas/login", json={"method": "token", "token": "x"})
    assert r.status_code == 400 and "Demo" in r.get_json()["error"]
    assert client.post("/api/outlook/login/start").status_code == 400
    client.application.config["RADAR_CFG"]["canvas"]["token"] = "tok"
    r = client.post("/api/canvas/logout").get_json()
    assert r["ok"] and r["auth"] == "off"
    assert client.get("/api/config").get_json()["canvas"]["has_token"] is False     # Disconnect really disconnects


def test_canvas_login_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("RADAR_HOME", str(tmp_path))
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg["ai"]["enabled"] = False
    cfg["server"]["sync_every_minutes"] = 0
    app = create_app(cfg, Store(tmp_path / "r.db"), demo=False, app_mode=True, api_token=TOKEN)
    app.config["TESTING"] = True
    c = app.test_client()
    c.environ_base["HTTP_X_RADAR_TOKEN"] = TOKEN
    r = c.post("/api/canvas/login", json={"method": "token", "token": "x"})
    assert r.status_code == 400 and "URL" in r.get_json()["error"]
    r = c.post("/api/canvas/login", json={"base_url": "canvas.school.edu", "method": "paste", "cookie": "foo=bar; _ga=1"})
    assert r.status_code == 400 and "session cookie" in r.get_json()["error"]
    d = c.get("/api/dashboard").get_json()
    assert d["configured"] is False                                             # nothing connected yet -> Welcome screen
    assert c.put("/api/config", json={"outlook": {"enabled": True}}).status_code == 200
    assert c.get("/api/dashboard").get_json()["configured"] is True             # mail-only setups count as configured


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


def test_last_sync_only_when_something_ran(tmp_path, monkeypatch):
    from radar.sync import run_sync
    monkeypatch.setenv("RADAR_HOME", str(tmp_path))
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg["ai"]["enabled"] = False
    store = Store(tmp_path / "r.db")
    run_sync(cfg, store, log=lambda *a: None, use_ai=False)
    assert store.get_meta("last_sync") is None
