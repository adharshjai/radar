"""Local dashboard (Flask). Run: python3 -m radar serve"""
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import secrets as _secrets
from urllib.parse import urlencode

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from . import config as cfgmod
from .brief import render_brief
from .models import Item, content_hash, iso, parse_dt, utcnow
from .store import Store
from .sync import run_sync
from .triage import KIND_ICONS, KIND_LABELS, build_dashboard, humanize_ago, humanize_due, local_tz

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]


def sync_problems(store: Store, app_mode: bool = False) -> list[dict]:
    """Recent failed syncs (newer than the last success) with a fix hint."""
    problems = []
    for src in ("canvas", "outlook", "applemail", "canvas-feeds"):
        f = store.last_failure(src)
        if f and f.get("message") and f["message"] != "not configured":
            msg = f["message"]
            fix = None
            if "session expired" in msg.lower() or "login canvas" in msg or "No Canvas login found" in msg:
                fix = ("Sign in to Canvas in your browser, then press Sync (or reconnect in Settings › Canvas)" if app_mode
                       else f"Sign in to Canvas in your browser, then press Sync now (or run:  {cfgmod.CMD} login canvas)")
            elif src == "outlook" and ("Not signed in" in msg or "rejected the token" in msg):
                fix = "Sign in again in Settings › Email" if app_mode else f"Run:  {cfgmod.CMD} login outlook"
            if app_mode:
                msg = msg.replace(f"(or run: {cfgmod.CMD} login canvas)", "").replace(f"Run: {cfgmod.CMD} login outlook", "").strip()
            problems.append({"source": src, "message": msg, "fix": fix, "when": f["finished"]})
    return problems


def sources_configured(cfg: dict) -> bool:
    c = cfg["canvas"]
    return bool((c.get("base_url") and (c.get("auth") or "auto") != "off") or c.get("ics_url") or c.get("announcement_feeds")
                or cfg["outlook"].get("enabled") or cfg["applemail"].get("enabled"))


def course_groups(dash: dict, c: dict, course_id: str):
    """Item groups + grouped sites for one course page (shared by the HTML view and the JSON API)."""
    items = [i for i in dash["items"] if str(i.get("course_id")) == str(course_id)]
    task_ids = {i["uid"] for k in ("overdue", "today", "tomorrow", "week", "later", "nodate") for i in dash["buckets"][k]}
    groups = {
        "tasks": [i for i in items if i["uid"] in task_ids],
        "announcements": sorted([i for i in items if i["kind"] == "announcement"], key=lambda i: i.get("posted_at") or dash["now"], reverse=True),
        "graded": [i for i in items if i["status"] in ("graded", "submitted", "excused")],
        "syllabus": [i for i in items if i["kind"] == "syllabus_event" and not (i.get("extra") or {}).get("dup_of")],
        "files": sorted([i for i in items if i["kind"] == "file"], key=lambda i: i.get("posted_at") or dash["now"], reverse=True)[:20],
        "other": [i for i in items if i["kind"] in ("event", "module", "discussion", "module_item", "page") and i["uid"] not in task_ids],
    }
    groups["tasks"].sort(key=lambda i: (i.get("due_at") or dash["now"] + timedelta(days=999)))
    groups["graded"].sort(key=lambda i: i.get("due_at") or dash["now"], reverse=True)
    groups["syllabus"].sort(key=lambda i: i.get("due_at") or i.get("starts_at") or dash["now"])
    grouped: dict = {}
    for s in dash["sites"]:
        if str(s["course_id"]) != str(course_id):
            continue
        key = s.get("platform") or s["url"]
        if key in grouped:
            grouped[key]["where_found"] += ", " + s["where_found"]
            if s.get("last_checked_dt") and (not grouped[key].get("last_checked_dt") or s["last_checked_dt"] > grouped[key]["last_checked_dt"]):
                grouped[key]["last_checked_dt"] = s["last_checked_dt"]
        else:
            grouped[key] = dict(s, name=s.get("platform") or s["name"])
    sites = sorted(grouped.values(), key=lambda s: (s["category"] != "tool", s["name"].lower()))
    return groups, sites


def create_app(cfg: dict | None = None, store: Store | None = None, demo: bool = False, app_mode: bool = False,
               api_token: str | None = None) -> Flask:
    cfg = cfg or cfgmod.load()
    store = store or Store(cfgmod.db_path())
    app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"),
                static_folder=str(Path(__file__).parent / "static"))
    app.config["RADAR_CFG"] = cfg
    app.config["RADAR_STORE"] = store
    app.config["RADAR_DEMO"] = demo
    app.config["RADAR_APP_MODE"] = app_mode
    app.config["RADAR_API_TOKEN"] = api_token or None
    tz = local_tz()
    state = {"syncing": False, "log": [], "last_result": None, "started": None, "thread": None}
    app.config["RADAR_SYNC_STATE"] = state
    from .api import api as api_blueprint
    app.register_blueprint(api_blueprint)

    def course_colors():
        courses = store.courses()
        return {c["id"]: PALETTE[i % len(PALETTE)] if i < len(PALETTE) else "#898781"
                for i, c in enumerate(sorted(courses, key=lambda c: c["id"]))}

    @app.template_filter("local")
    def _local(dt, fmt="%a %b %-d, %-I:%M %p"):
        if not dt:
            return ""
        return dt.astimezone(tz).strftime(fmt)

    @app.template_filter("ago")
    def _ago(dt):
        return humanize_ago(dt, datetime.now(timezone.utc), tz)

    @app.template_filter("due")
    def _due(dt):
        return humanize_due(dt, datetime.now(timezone.utc), tz)

    @app.template_filter("snippet")
    def _snippet(text, n=220):
        text = " ".join((text or "").split())
        return text if len(text) <= n else text[: n - 1] + "…"

    @app.context_processor
    def inject():
        courses = store.courses()
        problems = []
        for src in ("canvas", "outlook", "applemail", "canvas-feeds"):
            f = store.last_failure(src)
            if f and f.get("message") and f["message"] != "not configured":
                msg = f["message"]
                fix = None
                if "session expired" in msg.lower() or "login canvas" in msg or "No Canvas login found" in msg:
                    fix = f"Sign in to Canvas in your browser, then press Sync now (or run:  {cfgmod.CMD} login canvas)"
                elif src == "outlook" and ("Not signed in" in msg or "rejected the token" in msg):
                    fix = "Run:  python3 -m radar login outlook"
                problems.append({"source": src, "message": msg, "fix": fix, "when": f["finished"]})
        return {
            "problems": problems,
            "courses": courses, "colors": course_colors(), "icons": KIND_ICONS, "kind_labels": KIND_LABELS,
            "sync_state": state, "last_sync": parse_dt(store.get_meta("last_sync")), "demo": demo,
            "cfg": cfg, "now": datetime.now(timezone.utc),
        }

    def dashboard():
        return build_dashboard(store, cfg, now=datetime.now(tz), tz=tz)

    # ------------------------------------------------------------- pages
    @app.route("/")
    def today():
        dash = dashboard()
        course_filter = request.args.get("course")
        if course_filter:
            for k in dash["buckets"]:
                dash["buckets"][k] = [i for i in dash["buckets"][k] if str(i.get("course_id")) == course_filter]
            dash["new_info"] = [i for i in dash["new_info"] if str(i.get("course_id")) == course_filter]
        return render_template("today.html", d=dash, course_filter=course_filter, page="today")

    @app.route("/visited", methods=["POST"])
    def visited():
        store.set_meta("last_visit", iso(utcnow()))
        return jsonify(ok=True)

    @app.route("/calendar")
    def calendar():
        dash = dashboard()
        days = int(request.args.get("days", 21))
        today = dash["today"]
        grid = []
        by_day: dict = {}
        for it in dash["items"]:
            ex = it.get("extra") or {}
            if ex.get("dup_of") or it.get("user_state") == "done":
                continue
            when = it.get("due_at") or it.get("starts_at")
            if not when or it["kind"] in ("announcement", "file", "conversation", "email"):
                continue
            if it["kind"] in ("assignment", "quiz", "discussion") and it["status"] in ("submitted", "graded", "excused"):
                continue
            d = when.astimezone(tz).date()
            if today <= d <= today + timedelta(days=days):
                by_day.setdefault(d, []).append(it)
        for i in range(days + 1):
            d = today + timedelta(days=i)
            entries = sorted(by_day.get(d, []), key=lambda i: (i.get("starts_at") or i.get("due_at")))
            grid.append({"date": d, "items": entries, "is_today": i == 0, "weekend": d.weekday() >= 5})
        return render_template("calendar.html", d=dash, grid=grid, days=days, page="calendar")

    @app.route("/inbox")
    def inbox():
        dash = dashboard()
        show_all = request.args.get("all") == "1"
        mail = []
        for it in dash["items"]:
            if it["kind"] not in ("email", "conversation", "announcement"):
                continue
            ex = it.get("extra") or {}
            if not show_all and (ex.get("dup_of") or ex.get("bulk")):
                continue
            ai = it.get("ai") or {}
            if not show_all and it["kind"] == "email" and ai and ai.get("relevant") is False:
                continue
            if not show_all and it["kind"] == "email" and not ai and ex.get("relevance", 0) < 25:
                continue
            mail.append(it)
        mail.sort(key=lambda i: i.get("posted_at") or dash["now"], reverse=True)
        return render_template("inbox.html", d=dash, mail=mail[:300], show_all=show_all, page="inbox")

    @app.route("/sites")
    def sites():
        dash = dashboard()
        by_course: dict = {}
        for s in dash["sites"]:
            by_course.setdefault(s["course_id"], []).append(s)
        course_map = {c["id"]: c for c in dash["courses"]}
        return render_template("sites.html", d=dash, by_course=by_course, course_map=course_map, page="sites")

    @app.route("/sites/<int:site_id>/open")
    def site_open(site_id):
        s = store.site(site_id)
        if not s:
            abort(404)
        store.mark_site_checked(site_id)
        return redirect(s["url"])

    @app.route("/sites/<int:site_id>/check", methods=["POST"])
    def site_check(site_id):
        store.mark_site_checked(site_id)
        return jsonify(ok=True)

    @app.route("/sites/<int:site_id>/every", methods=["POST"])
    def site_every(site_id):
        store.set_site_check(site_id, int(request.form.get("days", request.json.get("days") if request.is_json else 0) or 0))
        return jsonify(ok=True)

    @app.route("/sites/<int:site_id>/hide", methods=["POST"])
    def site_hide(site_id):
        store.hide_site(site_id, True)
        return jsonify(ok=True)

    @app.route("/course/<course_id>")
    def course(course_id):
        dash = dashboard()
        c = store.course(course_id)
        if not c:
            abort(404)
        groups, sites = course_groups(dash, c, course_id)
        return render_template("course.html", d=dash, c=c, g=groups, sites=sites, page="course")

    @app.route("/all")
    def all_items():
        dash = dashboard()
        q = (request.args.get("q") or "").strip().lower()
        kind = request.args.get("kind") or ""
        items = dash["items"]
        if q:
            items = [i for i in items if q in (i["title"] or "").lower() or q in (i.get("body") or "").lower() or q in (i.get("course") or "").lower()]
        if kind:
            items = [i for i in items if i["kind"] == kind]
        items = sorted(items, key=lambda i: (i.get("due_at") or i.get("starts_at") or i.get("posted_at") or dash["now"]), reverse=True)
        kinds = sorted({i["kind"] for i in dash["items"]})
        return render_template("all.html", d=dash, items=items[:500], q=q, kind=kind, kinds=kinds, page="all")

    @app.route("/status")
    def status_page():
        dash = dashboard()
        return render_template("status.html", d=dash, syncs=store.last_syncs(20), page="status",
                               db_path=str(store.path), cfg_path=str(cfgmod.config_path()))

    @app.route("/brief")
    def brief():
        dash = dashboard()
        text = render_brief(dash)
        return app.response_class(text, mimetype="text/plain")

    # ------------------------------------------------------------- item actions
    @app.route("/api/item/<path:uid>/state", methods=["POST"])
    def item_state(uid):
        data = request.get_json(silent=True) or request.form
        action = data.get("action")
        if uid.startswith("site:") and action == "done":
            for sid in (data.get("site_ids") or [uid.split(":")[1]]):
                store.mark_site_checked(int(sid))
            return jsonify(ok=True)
        it = store.item(uid)
        if not it:
            abort(404)
        if action == "done":
            store.set_user_state(uid, "done")
        elif action == "reopen":
            store.set_user_state(uid, "")
        elif action == "snooze":
            until = data.get("until")
            if until == "due" and it.get("due_at"):
                dt = it["due_at"] - timedelta(hours=24)
            else:
                hours = float(data.get("hours") or 24)
                dt = utcnow() + timedelta(hours=hours)
            store.set_user_state(uid, "snoozed", dt)
        elif action == "note":
            store.set_note(uid, (data.get("note") or "")[:2000])
        else:
            abort(400)
        return jsonify(ok=True)

    @app.route("/api/item", methods=["POST"])
    def add_item():
        data = request.get_json(silent=True) or request.form
        title = (data.get("title") or "").strip()
        if not title:
            abort(400)
        due = None
        if data.get("due"):
            try:
                due = datetime.fromisoformat(data["due"]).replace(tzinfo=tz).astimezone(timezone.utc)
            except ValueError:
                due = None
        cid = data.get("course_id") or None
        course = store.course(cid) if cid else None
        it = Item(uid=f"manual:{content_hash(title, iso(utcnow()))}", source="radar", kind="manual", title=title,
                  course=course["name"] if course else None, course_id=cid, due_at=due, status="open",
                  body=(data.get("note") or "")[:2000])
        store.add_manual_item(it)
        return jsonify(ok=True, uid=it.uid)

    @app.route("/api/item/<path:uid>", methods=["DELETE"])
    def delete_item(uid):
        if not uid.startswith("manual:"):
            abort(400)
        store.delete_item(uid)
        return jsonify(ok=True)

    # ------------------------------------------------------------- sync
    def _sync_worker(sources=None):
        state["syncing"] = True
        state["log"] = []
        state["started"] = utcnow()

        def log(msg):
            state["log"].append(f"{datetime.now(tz).strftime('%H:%M:%S')} {msg}")
            print(msg, flush=True)
        try:
            state["last_result"] = run_sync(cfg, store, sources=sources, log=log,
                                            notify=cfg["server"].get("notify", True) and not app_mode)
        except Exception as e:
            state["last_result"] = {"error": str(e)}
            log(traceback.format_exc())
        finally:
            state["syncing"] = False

    @app.route("/api/sync", methods=["POST"])
    def sync_now():
        if demo:
            return jsonify(ok=False, error="demo mode: no live sources"), 400
        if state["syncing"]:
            return jsonify(ok=True, already=True)
        t = threading.Thread(target=_sync_worker, daemon=True)
        state["thread"] = t
        t.start()
        return jsonify(ok=True)

    @app.route("/api/sync/status")
    def sync_status():
        return jsonify(syncing=state["syncing"], log=state["log"][-40:], result=state["last_result"],
                       last_sync=store.get_meta("last_sync"))

    def scheduler():
        every = int(cfg["server"].get("sync_every_minutes") or 0)
        if every <= 0 or demo:
            return
        last = parse_dt(store.get_meta("last_sync"))
        if sources_configured(cfg) and (not last or utcnow() - last > timedelta(minutes=every)):
            time.sleep(3)
            _sync_worker()
        while True:
            time.sleep(every * 60)
            if not state["syncing"] and sources_configured(cfg):
                _sync_worker()

    @app.before_request
    def _guard():
        # CSRF: state-changing requests must not come from another website open in the browser.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = (request.headers.get("Origin") or "").rstrip("/")
            site = request.headers.get("Sec-Fetch-Site") or ""
            if (origin and origin != request.host_url.rstrip("/")) or site in ("cross-site", "same-site"):
                abort(403)
        token = app.config.get("RADAR_API_TOKEN")
        if not token or request.path.startswith("/static/") or request.path == "/api/health":
            return None
        given = request.headers.get("X-Radar-Token") or request.args.get("token") or request.cookies.get("radar_token")
        if not _secrets.compare_digest(given or "", token):
            abort(401)
        if request.args.get("token") and request.method == "GET":
            # Move the secret out of the address bar into an HttpOnly cookie so the rest of the site works.
            args = request.args.to_dict(flat=False)
            args.pop("token", None)
            resp = redirect(request.path + ("?" + urlencode(args, doseq=True) if args else ""), code=303)
            resp.set_cookie("radar_token", token, httponly=True, samesite="Strict", path="/")
            return resp

    if not app.config.get("TESTING"):
        threading.Thread(target=scheduler, daemon=True).start()
    return app


def _watch_parent(pid: int):
    """Exit with the app that launched us, even if it was force-quit and never sent SIGTERM."""
    import os

    def loop():
        while True:
            time.sleep(2)
            try:
                os.kill(pid, 0)
            except OSError:
                os._exit(0)
            if os.getppid() != pid:
                os._exit(0)
    threading.Thread(target=loop, daemon=True).start()


def serve(cfg: dict | None = None, store: Store | None = None, port: int | None = None, open_browser: bool = True, demo: bool = False,
          app_mode: bool = False):
    cfg = cfg or cfgmod.load()
    port = port or int(cfg["server"].get("port", 5151))
    import os
    token = os.environ.get("RADAR_API_TOKEN") if app_mode else None
    app = create_app(cfg, store, demo=demo, app_mode=app_mode, api_token=token)
    if app_mode:
        open_browser = False
        import logging
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
        parent = os.environ.get("RADAR_PARENT_PID")
        if parent and parent.isdigit():
            _watch_parent(int(parent))
    url = f"http://127.0.0.1:{port}/"
    if open_browser:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Radar dashboard: {url}")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
