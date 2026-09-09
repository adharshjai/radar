import copy
from datetime import datetime, timezone

import pytest

from demo.fixtures import load_demo
from radar import config as cfgmod
from radar.store import Store
from radar.web import create_app


@pytest.fixture
def client(tmp_path):
    cfg = copy.deepcopy(cfgmod.DEFAULTS)
    cfg["ai"]["enabled"] = False
    cfg["server"]["sync_every_minutes"] = 0
    store = Store(tmp_path / "radar.db")
    load_demo(store, cfg, now=datetime.now(timezone.utc))
    app = create_app(cfg, store, demo=True)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.parametrize("path", ["/", "/?course=1001", "/calendar", "/calendar?days=60", "/inbox", "/inbox?all=1", "/sites",
                                  "/course/1001", "/course/1003", "/all", "/all?q=lab&kind=assignment", "/status", "/brief"])
def test_pages_render(client, path):
    r = client.get(path)
    assert r.status_code == 200, path
    body = r.get_data(as_text=True)
    assert "Traceback" not in body


def test_today_shows_key_items(client):
    body = client.get("/").get_data(as_text=True)
    for s in ("Homework 4: Stoichiometry", "Pre-Lab 3: Acid-Base Titration", "LabFlow", "Check LabFlow", "Exam 1 next week",
              "Regrade request for PA0", "Reminder: lab safety quiz due Friday"):
        assert s in body, s


def test_item_actions(client):
    uid = "canvas:assignment:2003"
    assert client.post(f"/api/item/{uid}/state", json={"action": "done"}).status_code == 200
    assert "Homework 4: Stoichiometry" not in client.get("/").get_data(as_text=True).split("Done recently")[0]
    assert client.post(f"/api/item/{uid}/state", json={"action": "reopen"}).status_code == 200
    assert client.post(f"/api/item/{uid}/state", json={"action": "snooze", "hours": 48}).status_code == 200
    assert "Homework 4: Stoichiometry" not in client.get("/").get_data(as_text=True).split("Snoozed")[0]
    assert client.post(f"/api/item/{uid}/state", json={"action": "note", "note": "ask about extension"}).status_code == 200
    assert client.post("/api/item/nope/state", json={"action": "done"}).status_code == 404


def test_manual_task_and_sites(client):
    r = client.post("/api/item", json={"title": "Email advisor", "due": "2030-01-01T09:00", "course_id": "1001"})
    assert r.status_code == 200 and r.get_json()["ok"]
    uid = r.get_json()["uid"]
    assert "Email advisor" in client.get("/").get_data(as_text=True)
    assert client.delete(f"/api/item/{uid}").status_code == 200
    sites = client.get("/sites").get_data(as_text=True)
    assert "LabFlow" in sites and "Gradescope" in sites
    r = client.get("/sites/1/open")
    assert r.status_code == 302
    assert client.post("/sites/1/every", json={"days": 7}).status_code == 200
    assert client.post("/sites/1/hide").status_code == 200
    assert client.post("/api/sync").status_code == 400   # demo mode refuses live sync
    assert client.get("/api/sync/status").status_code == 200
    assert client.post("/visited").status_code == 200


def test_session_expired_callout(client):
    from datetime import datetime, timezone
    from flask import current_app
    store = client.application.config["RADAR_STORE"]
    store.log_sync("canvas", datetime.now(timezone.utc), False, "Canvas browser session expired or missing (401 unauthenticated). Sign in to Canvas in your browser, then run: python -m radar login canvas")
    body = client.get("/").get_data(as_text=True)
    assert "canvas sync failed" in body and "radar login canvas" in body
    store.log_sync("canvas", datetime.now(timezone.utc), True, "", {})
    assert "canvas sync failed" not in client.get("/").get_data(as_text=True)
