"""JSON API used by the native Mac app (and anything else that wants structured data)."""
from __future__ import annotations

import os
import secrets
import threading
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, jsonify, request

from . import __version__
from . import config as cfgmod
from .models import iso, parse_dt, utcnow
from .triage import build_dashboard, humanize_ago, local_tz

api = Blueprint("api", __name__)

ITEM_FIELDS = ["uid", "source", "kind", "kind_label", "title", "course", "course_id", "url", "platform", "status",
               "status_label", "points", "score", "grade", "sender", "priority", "due_text", "ago", "is_new", "is_updated",
               "user_state", "note", "graded_ago"]
EXTRA_FIELDS = ["all_day", "hint", "location", "late", "soft_due", "dup_of", "from", "site_ids", "action_required",
                "relevance", "mentioned_dates", "canvas_notification", "bulk", "external_url", "submission_unknown",
                "requirement", "module", "reasons", "action_words", "focused", "flagged"]
CONFIG_WRITABLE = {
    "canvas": {"base_url", "auth", "browser", "cookie_file", "ics_url", "announcement_feeds", "include_courses",
               "exclude_courses", "lookback_days", "lookahead_days", "fetch_files", "parse_syllabus_files", "token"},
    "outlook": {"enabled", "client_id", "tenant", "lookback_days", "calendar", "folders", "max_messages"},
    "applemail": {"enabled", "account", "lookback_days", "max_messages"},
    "ai": {"enabled", "model", "api_key", "max_items_per_sync"},
    "profile": {"name", "email", "university_domains", "extra_keywords"},
    "server": {"sync_every_minutes", "notify", "port"},
}


def _store():
    return current_app.config["RADAR_STORE"]


def _cfg():
    return current_app.config["RADAR_CFG"]


def item_json(it: dict) -> dict:
    ex = it.get("extra") or {}
    out = {k: it.get(k) for k in ITEM_FIELDS}
    for k in ("due_at", "starts_at", "ends_at", "posted_at", "prev_due_at", "snooze_until", "first_seen", "changed_at"):
        out[k] = iso(it.get(k)) if isinstance(it.get(k), datetime) else it.get(k)
    body = " ".join((it.get("body") or "").split())
    out["body"] = body[:600]
    out["extra"] = {k: ex.get(k) for k in EXTRA_FIELDS if k in ex}
    ai = it.get("ai") or None
    out["ai"] = {k: ai.get(k) for k in ("summary", "action_items", "category", "urgency", "relevant", "action_required")} if ai else None
    return out


def course_json(c: dict, open_counts: dict | None = None) -> dict:
    return {
        "id": c["id"], "name": c.get("name"), "code": c.get("code") or "", "term": c.get("term") or "", "url": c.get("url") or "",
        "teachers": c.get("teachers") or [], "current_score": c.get("current_score"), "current_grade": c.get("current_grade"),
        "has_syllabus": bool(c.get("syllabus_text")), "has_syllabus_ai": bool(c.get("syllabus_ai")),
        "open_count": (open_counts or {}).get(c["id"], 0),
    }


def site_json(s: dict, course_name: str | None = None) -> dict:
    return {
        "id": s["id"], "course_id": s.get("course_id"), "course_name": course_name or s.get("course_name"), "url": s["url"],
        "name": s.get("name"), "platform": s.get("platform"), "category": s.get("category"), "where_found": s.get("where_found"),
        "last_checked": iso(s.get("last_checked_dt")) if s.get("last_checked_dt") else None,
        "check_every_days": s.get("check_every_days") or 0,
    }


def _dash():
    tz = local_tz()
    return build_dashboard(_store(), _cfg(), now=datetime.now(tz), tz=tz)


def _problems():
    from .web import sync_problems
    return sync_problems(_store())


@api.before_request
def _auth():
    token = current_app.config.get("RADAR_API_TOKEN")
    if not token or request.path.endswith("/api/health"):
        return None
    given = request.headers.get("X-Radar-Token") or request.args.get("token")
    if not secrets.compare_digest(given or "", token):
        abort(401)


@api.get("/api/health")
def health():
    return jsonify(ok=True, version=__version__, pid=os.getpid(), demo=bool(current_app.config.get("RADAR_DEMO")),
                   home=str(cfgmod.home()))


@api.get("/api/dashboard")
def dashboard():
    d = _dash()
    state = current_app.config["RADAR_SYNC_STATE"]
    open_counts: dict = {}
    for key in ("overdue", "today", "tomorrow", "week", "later", "nodate"):
        for it in d["buckets"][key]:
            if it.get("course_id"):
                open_counts[it["course_id"]] = open_counts.get(it["course_id"], 0) + 1
    cfg = _cfg()
    configured = bool(cfg["canvas"].get("base_url")) and (cfg["canvas"].get("auth") != "off") or bool(d["courses"])
    return jsonify({
        "generated_at": iso(utcnow()), "last_sync": _store().get_meta("last_sync"), "syncing": state["syncing"],
        "configured": configured, "demo": bool(current_app.config.get("RADAR_DEMO")),
        "stats": d["stats"],
        "buckets": {k: [item_json(i) for i in v] for k, v in d["buckets"].items()},
        "new_info": [item_json(i) for i in d["new_info"]],
        "graded": [item_json(i) for i in d["graded"]],
        "snoozed": [item_json(i) for i in d["snoozed"]],
        "done_recent": [item_json(i) for i in d["done_recent"]],
        "agenda": [{"date": day["date"].isoformat(), "label": day["label"], "sub": day["sub"], "items": [item_json(i) for i in day["items"]]}
                   for day in d["agenda"]],
        "workload": [{"date": w["date"].isoformat(), "label": w["label"], "count": w["count"], "titles": w["titles"]} for w in d["workload"]],
        "courses": [course_json(c, open_counts) for c in d["courses"]],
        "problems": [{"source": p["source"], "message": p["message"], "fix": p.get("fix"), "when": iso(p["when"]) if p.get("when") else None}
                     for p in _problems()],
    })


@api.get("/api/calendar")
def calendar():
    d = _dash()
    tz = d["tz"]
    days = max(1, min(int(request.args.get("days", 21)), 180))
    today = d["today"]
    by_day: dict = {}
    for it in d["items"]:
        ex = it.get("extra") or {}
        if ex.get("dup_of") or it.get("user_state") == "done":
            continue
        when = it.get("due_at") or it.get("starts_at")
        if not when or it["kind"] in ("announcement", "file", "conversation", "email"):
            continue
        if it["kind"] in ("assignment", "quiz", "discussion") and it["status"] in ("submitted", "graded", "excused"):
            continue
        day = when.astimezone(tz).date()
        if today <= day <= today + timedelta(days=days):
            by_day.setdefault(day, []).append(it)
    out = []
    for i in range(days + 1):
        day = today + timedelta(days=i)
        entries = sorted(by_day.get(day, []), key=lambda x: (x.get("starts_at") or x.get("due_at")))
        out.append({"date": day.isoformat(), "is_today": i == 0, "weekend": day.weekday() >= 5, "items": [item_json(x) for x in entries]})
    return jsonify(days=out)


@api.get("/api/inbox")
def inbox():
    d = _dash()
    show_all = request.args.get("all") == "1"
    mail = []
    for it in d["items"]:
        if it["kind"] not in ("email", "conversation", "announcement"):
            continue
        ex = it.get("extra") or {}
        ai = it.get("ai") or {}
        if not show_all:
            if ex.get("dup_of") or ex.get("bulk"):
                continue
            if it["kind"] == "email" and ai and ai.get("relevant") is False:
                continue
            if it["kind"] == "email" and not ai and ex.get("relevance", 0) < 25:
                continue
        mail.append(it)
    mail.sort(key=lambda i: i.get("posted_at") or d["now"], reverse=True)
    return jsonify(mail=[item_json(i) for i in mail[:300]])


@api.get("/api/sites")
def sites():
    d = _dash()
    names = {c["id"]: c["name"] for c in d["courses"]}
    return jsonify(sites=[site_json(s, names.get(s["course_id"])) for s in d["sites"]])


@api.get("/api/course/<course_id>")
def course(course_id):
    from .web import course_groups
    d = _dash()
    c = _store().course(course_id)
    if not c:
        abort(404)
    groups, sites = course_groups(d, c, course_id)
    return jsonify(
        course=course_json(c),
        syllabus_ai=c.get("syllabus_ai"),
        syllabus_text_preview=(c.get("syllabus_text") or "")[:6000],
        groups={k: [item_json(i) for i in v] for k, v in groups.items()},
        sites=[site_json(s) for s in sites],
    )


@api.get("/api/search")
def search():
    d = _dash()
    q = (request.args.get("q") or "").strip().lower()
    kind = request.args.get("kind") or ""
    items = d["items"]
    if q:
        items = [i for i in items if q in (i["title"] or "").lower() or q in (i.get("body") or "").lower() or q in (i.get("course") or "").lower()]
    if kind:
        items = [i for i in items if i["kind"] == kind]
    items = sorted(items, key=lambda i: (i.get("due_at") or i.get("starts_at") or i.get("posted_at") or d["now"]), reverse=True)
    return jsonify(items=[item_json(i) for i in items[:400]], kinds=sorted({i["kind"] for i in d["items"]}))


@api.get("/api/status")
def status():
    store = _store()
    state = current_app.config["RADAR_SYNC_STATE"]
    syncs = [{"source": s["source"], "ok": bool(s["ok"]), "message": s.get("message") or "", "finished": iso(s["finished"]) if s.get("finished") else None,
              "counts": s.get("counts") or {}} for s in store.last_syncs(30)]
    return jsonify(syncs=syncs, log=state["log"][-80:], syncing=state["syncing"], config=config_summary(_cfg(), store),
                   paths={"home": str(cfgmod.home()), "config": str(cfgmod.config_path()), "db": str(store.path)},
                   engine={"version": __version__, "pid": os.getpid()},
                   counts={"items": len(store.items()), "sites": len(store.sites()), "courses": len(store.courses())})


# ------------------------------------------------------------------ config & accounts

def config_summary(cfg: dict, store) -> dict:
    from .sources.canvas_session import load_cookies
    saved = load_cookies(cfgmod.home())
    outlook_account = store.get_meta("outlook_account")
    signed_in = False
    try:
        from .sources.outlook import OutlookSource
        signed_in = bool(OutlookSource(cfg["outlook"], cfgmod.home() / "msal_cache.json").accounts())
    except Exception:
        signed_in = False
    has_key = bool(cfg["ai"].get("api_key") or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    return {
        "canvas": {k: cfg["canvas"].get(k) for k in ("base_url", "auth", "browser", "cookie_file", "ics_url", "announcement_feeds",
                                                     "include_courses", "exclude_courses", "lookback_days", "lookahead_days",
                                                     "fetch_files", "parse_syllabus_files")}
        | {"has_token": bool(cfg["canvas"].get("token")), "has_session": bool(saved), "session_source": (saved or {}).get("source"),
           "session_saved_at": (saved or {}).get("saved_at")},
        "outlook": {k: cfg["outlook"].get(k) for k in ("enabled", "client_id", "tenant", "lookback_days", "calendar", "folders", "max_messages")}
        | {"signed_in": signed_in, "account": outlook_account},
        "applemail": {k: cfg["applemail"].get(k) for k in ("enabled", "account", "lookback_days", "max_messages")},
        "ai": {k: cfg["ai"].get(k) for k in ("enabled", "model", "max_items_per_sync")}
        | {"has_key": has_key, "key_in_config": bool(cfg["ai"].get("api_key"))},
        "profile": {k: cfg["profile"].get(k) for k in ("name", "email", "university_domains", "extra_keywords")},
        "server": {k: cfg["server"].get(k) for k in ("sync_every_minutes", "notify", "port")},
    }


@api.get("/api/config")
def get_config():
    return jsonify(config_summary(_cfg(), _store()))


@api.put("/api/config")
def put_config():
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    changed = []
    for section, values in data.items():
        allowed = CONFIG_WRITABLE.get(section)
        if not allowed or not isinstance(values, dict):
            continue
        for k, v in values.items():
            if k not in allowed:
                continue
            if k == "base_url" and isinstance(v, str):
                v = v.strip().rstrip("/")
                if v and not v.startswith("http"):
                    v = "https://" + v
            if k in ("announcement_feeds", "include_courses", "exclude_courses", "university_domains", "extra_keywords", "folders"):
                if isinstance(v, str):
                    v = [x.strip() for x in v.replace("\n", ",").split(",") if x.strip()]
                v = [str(x) for x in (v or [])]
            cfg[section][k] = v
            changed.append(f"{section}.{k}")
    if changed:
        if not current_app.config.get("RADAR_DEMO"):
            cfgmod.save(cfg)
    return jsonify(ok=True, changed=changed, config=config_summary(cfg, _store()))


@api.post("/api/canvas/login")
def canvas_login():
    from .sources.canvas import CanvasClient, CanvasError
    from .sources.canvas_session import CanvasSessionError, cookies_from_browser, cookies_from_string, has_session, save_cookies
    cfg = _cfg()
    data = request.get_json(silent=True) or {}
    base = (data.get("base_url") or cfg["canvas"].get("base_url") or "").strip().rstrip("/")
    if base and not base.startswith("http"):
        base = "https://" + base
    if not base:
        return jsonify(ok=False, error="Enter your Canvas URL first (for example https://canvas.yourschool.edu)."), 400
    method = data.get("method") or "browser"
    try:
        if method == "token":
            token = (data.get("token") or "").strip()
            if not token:
                return jsonify(ok=False, error="Paste an access token."), 400
            client, source = CanvasClient(base, token), "token"
        else:
            if method == "paste":
                cookies = cookies_from_string(data.get("cookie") or "")
                if not has_session(cookies):
                    return jsonify(ok=False, error="That did not contain a Canvas session cookie (canvas_session or _normandy_session)."), 400
                source = "pasted"
            else:
                cookies, source = cookies_from_browser(base, data.get("browser") or cfg["canvas"].get("browser") or None,
                                                       cfg["canvas"].get("cookie_file") or None)
            client = CanvasClient(base, cookies=cookies)
        me = client.get_json("/users/self")
        prof = client.get_json("/users/self/profile", optional=True) or {}
    except (CanvasError, CanvasSessionError) as e:
        return jsonify(ok=False, error=str(e)), 400
    cfg["canvas"]["base_url"] = base
    if method == "token":
        cfg["canvas"]["token"] = token
        cfg["canvas"]["auth"] = "token"
    else:
        save_cookies(cfgmod.home(), cookies, source)
        cfg["canvas"]["token"] = ""
        cfg["canvas"]["auth"] = "browser"
        if method == "browser" and data.get("browser"):
            cfg["canvas"]["browser"] = data["browser"]
    email = prof.get("primary_email") or ""
    if "@" in email and not cfg["profile"].get("university_domains"):
        cfg["profile"]["university_domains"] = [email.split("@")[-1].lower()]
        cfg["profile"]["email"] = email
    cfg["profile"]["name"] = me.get("name") or cfg["profile"].get("name") or ""
    if not current_app.config.get("RADAR_DEMO"):
        cfgmod.save(cfg)
    return jsonify(ok=True, name=me.get("name"), email=email or me.get("login_id"), source=source)


@api.post("/api/canvas/logout")
def canvas_logout():
    from .sources.canvas_session import clear_cookies
    cfg = _cfg()
    clear_cookies(cfgmod.home())
    cfg["canvas"]["auth"] = "token" if cfg["canvas"].get("token") else "off"
    if not current_app.config.get("RADAR_DEMO"):
        cfgmod.save(cfg)
    return jsonify(ok=True, auth=cfg["canvas"]["auth"])


_outlook_login: dict = {"state": "idle", "flow": None, "account": None, "error": None, "thread": None}


@api.post("/api/outlook/login/start")
def outlook_login_start():
    from .sources.outlook import OutlookError, OutlookSource
    cfg = _cfg()
    cfg["outlook"]["enabled"] = True
    src = OutlookSource(cfg["outlook"], cfgmod.home() / "msal_cache.json")
    try:
        flow = src.start_device_flow()
    except OutlookError as e:
        return jsonify(ok=False, error=str(e)), 400
    _outlook_login.update({"state": "pending", "flow": flow, "account": None, "error": None})

    def finish():
        try:
            src.finish_device_flow(flow)
            accts = src.accounts()
            _outlook_login["account"] = accts[0].get("username") if accts else None
            _outlook_login["state"] = "done"
            if _outlook_login["account"]:
                _store().set_meta("outlook_account", _outlook_login["account"])
            if not current_app.config.get("RADAR_DEMO"):
                cfgmod.save(cfg)
        except Exception as e:
            _outlook_login["error"] = str(e)
            _outlook_login["state"] = "error"

    app_obj = current_app._get_current_object()

    def runner():
        with app_obj.app_context():
            finish()
    t = threading.Thread(target=runner, daemon=True)
    _outlook_login["thread"] = t
    t.start()
    return jsonify(ok=True, user_code=flow.get("user_code"), verification_uri=flow.get("verification_uri"),
                   message=flow.get("message"), expires_in=flow.get("expires_in"))


@api.get("/api/outlook/login/status")
def outlook_login_status():
    return jsonify(state=_outlook_login["state"], account=_outlook_login["account"], error=_outlook_login["error"])


@api.post("/api/outlook/logout")
def outlook_logout():
    from .sources.outlook import OutlookSource
    cfg = _cfg()
    try:
        OutlookSource(cfg["outlook"], cfgmod.home() / "msal_cache.json").logout()
    except Exception:
        pass
    cfg["outlook"]["enabled"] = False
    _store().set_meta("outlook_account", "")
    if not current_app.config.get("RADAR_DEMO"):
        cfgmod.save(cfg)
    return jsonify(ok=True)


@api.post("/api/notifications/claim")
def notifications_claim():
    """Return newly discovered urgent items once (the app shows them as native notifications)."""
    from .sync import pending_notifications
    fresh = pending_notifications(_store(), _cfg(), utcnow())
    _store().mark_notified([f["uid"] for f in fresh])
    return jsonify(items=[item_json(f) for f in fresh])


@api.get("/api/brief")
def brief():
    from .brief import render_brief
    return jsonify(text=render_brief(_dash()))


@api.post("/api/quit")
def quit_engine():
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return jsonify(ok=True)
