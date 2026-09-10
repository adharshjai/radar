"""Decide what is a task, how urgent it is, and which bucket it lands in."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from .models import parse_dt

TASK_STATUSES = {"open", "missing", "pastdue", "locked"}
INFO_KINDS = {"announcement", "event", "calendar", "conversation", "file", "module"}
KIND_ICONS = {
    "assignment": "📝", "quiz": "❓", "discussion": "💬", "announcement": "📣", "event": "📅", "calendar": "📅",
    "module": "🔓", "module_item": "☑️", "planner_note": "🗒️", "page": "📄", "conversation": "✉️", "email": "📧",
    "file": "📎", "syllabus_event": "📘", "site_check": "🌐", "manual": "⭐",
}
KIND_LABELS = {
    "assignment": "Assignment", "quiz": "Quiz", "discussion": "Discussion", "announcement": "Announcement",
    "event": "Canvas event", "calendar": "Outlook calendar", "module": "Module", "module_item": "Module requirement",
    "planner_note": "Planner note", "page": "Page to-do", "conversation": "Canvas inbox", "email": "Email",
    "file": "New file", "syllabus_event": "From syllabus", "site_check": "Check site", "manual": "My task",
}
STATUS_LABELS = {
    "open": "", "submitted": "Submitted", "graded": "Graded", "missing": "Missing", "pastdue": "Past due",
    "excused": "Excused", "locked": "Locked", "unread": "Unread", "read": "", "info": "", "complete": "Done",
}


_TZ = None


def local_tz():
    """The machine's zone as a real ZoneInfo (so November dates get November's offset), else a fixed offset."""
    global _TZ
    if _TZ is not None:
        return _TZ
    import os
    from zoneinfo import ZoneInfo
    name = os.environ.get("TZ")
    if not name:
        try:
            target = os.path.realpath("/etc/localtime")
            if "zoneinfo/" in target:
                name = target.split("zoneinfo/", 1)[1]
        except OSError:
            name = None
    try:
        _TZ = ZoneInfo(name) if name else datetime.now().astimezone().tzinfo
    except Exception:
        _TZ = datetime.now().astimezone().tzinfo
    return _TZ


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 2 and t not in {"the", "and", "for", "due"}}


def similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    a1, b1 = a.lower().strip(), b.lower().strip()
    if a1 in b1 or b1 in a1:
        return True
    ta, tb = _tokens(a), _tokens(b)
    if ta and tb and len(ta & tb) / max(1, min(len(ta), len(tb))) >= 0.6:
        return True
    return SequenceMatcher(None, a1, b1).ratio() > 0.72


def _first_clause(title: str) -> str:
    return re.split(r"[:\-–—(,]", title, maxsplit=1)[0].strip() or title


def is_task(item: dict, now: datetime) -> bool:
    kind = item["kind"]
    ex = item.get("extra") or {}
    if item.get("user_state") == "done":
        return False
    if ex.get("dup_of"):
        return False
    if kind in ("assignment", "quiz") or (kind == "discussion" and item["uid"].startswith("canvas:assignment:")):
        return item["status"] in TASK_STATUSES and not ex.get("canvas_marked_complete")
    if kind == "discussion":
        return bool(item.get("due_at")) and item["status"] != "complete"
    if kind in ("planner_note", "page", "module_item", "manual"):
        return item["status"] not in ("complete", "info")
    if kind == "syllabus_event":
        return item["status"] == "open" and bool(item.get("due_at"))
    if kind == "email":
        ai = item.get("ai") or {}
        if ai:
            return bool(ai.get("relevant")) and bool(ai.get("action_required"))
        return bool(ex.get("action_required")) and ex.get("relevance", 0) >= 40 and not ex.get("bulk") and not ex.get("canvas_notification")
    return False


def priority(item: dict, now: datetime, tz) -> int:
    due = item.get("due_at")
    ex = item.get("extra") or {}
    if due:
        days = (due.astimezone(tz).date() - now.astimezone(tz).date()).days
        if due < now and not (ex.get("all_day") and days == 0):
            p = 100
        elif days <= 0:
            p = 90
        elif days == 1:
            p = 80
        elif days <= 3:
            p = 60
        elif days <= 7:
            p = 45
        else:
            p = 20
    else:
        p = 15
    if item["status"] == "missing":
        p += 5
    if ex.get("hint") == "exam":
        p += 10
    if (item.get("points") or 0) >= 50:
        p += 5
    ai = item.get("ai") or {}
    if ai:
        p += int(ai.get("urgency") or 0) * 8
    elif item["kind"] == "email":
        p += min(20, ex.get("relevance", 0) // 5)
    return p


def humanize_due(dt: datetime | None, now: datetime, tz, all_day: bool = False, prefix: str = "due") -> str:
    if not dt:
        return "no date"
    loc = dt.astimezone(tz)
    today = now.astimezone(tz).date()
    d = loc.date()
    days = (d - today).days
    t = "" if all_day else " " + loc.strftime("%-I:%M %p").lower()
    if dt < now and not (all_day and days == 0):
        delta = now - dt
        if delta < timedelta(hours=1):
            return f"{prefix} {int(delta.total_seconds() // 60)} min ago"
        if days == 0:
            return f"{prefix} {int(delta.total_seconds() // 3600)}h ago"
        if days == -1:
            return f"{prefix} yesterday{t}"
        return f"{-days} days overdue"
    if days == 0:
        return f"{prefix} today{t}"
    if days == 1:
        return f"{prefix} tomorrow{t}"
    if days < 7:
        return f"{prefix} {loc.strftime('%a')}{t}"
    return f"{prefix} {loc.strftime('%b %-d')}{t}"


def humanize_ago(dt: datetime | None, now: datetime, tz) -> str:
    if not dt:
        return ""
    delta = now - dt
    if delta < timedelta(minutes=1):
        return "just now"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)}m ago"
    if delta < timedelta(hours=24):
        return f"{int(delta.total_seconds() // 3600)}h ago"
    days = (now.astimezone(tz).date() - dt.astimezone(tz).date()).days
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days}d ago"
    return dt.astimezone(tz).strftime("%b %-d")


def dedupe(items: list[dict], tz) -> None:
    """Mark syllabus lines and Canvas-notification emails that duplicate real Canvas items."""
    canvas = [i for i in items if i["source"] == "canvas" and i["kind"] in ("assignment", "quiz", "discussion", "event", "announcement")]
    by_course_day: dict[tuple, list[dict]] = {}
    titles = []
    for c in canvas:
        when = c.get("due_at") or c.get("starts_at")
        if when:
            by_course_day.setdefault((c.get("course_id"), when.astimezone(tz).date()), []).append(c)
        if len(c["title"]) >= 8:
            titles.append((c["title"].lower(), c["uid"]))
    latest_in_thread: dict[str, dict] = {}
    pages = [i for i in items if i["kind"] == "page"]
    seen_syllabus: dict[tuple, list[dict]] = {}
    for it in items:
        ex = it.get("extra") or {}
        ex.pop("dup_of", None)
        if it["kind"] == "module_item":
            base_title = re.sub(r"\s*\([^)]*\)\s*$", "", it["title"])
            for pg in pages:
                if pg.get("course_id") == it.get("course_id") and similar(base_title, pg["title"]):
                    ex["dup_of"] = pg["uid"]
                    break
        elif it["kind"] == "syllabus_event":
            when = it.get("due_at") or it.get("starts_at")
            if not when:
                continue
            day = when.astimezone(tz).date()
            for c in by_course_day.get((it.get("course_id"), day), []):
                if similar(it["title"], c["title"]) or (c["kind"] in ("assignment", "quiz") and c["title"].lower() in it["title"].lower()):
                    ex["dup_of"] = c["uid"]
                    break
            if not ex.get("dup_of"):
                # the same exam often appears on the syllabus page AND in the syllabus PDF/DOCX
                bucket = seen_syllabus.setdefault((it.get("course_id"), day), [])
                for prev in bucket:
                    if similar(_first_clause(it["title"]), _first_clause(prev["title"])):
                        ex["dup_of"] = prev["uid"]
                        break
                else:
                    bucket.append(it)
        elif it["kind"] == "email":
            if ex.get("canvas_notification"):
                subj = it["title"].lower()
                for t, uid in titles:
                    if t in subj:
                        ex["dup_of"] = uid
                        break
            cid = ex.get("conversation_id")
            if cid:
                prev = latest_in_thread.get(cid)
                if prev and (prev.get("posted_at") or datetime.min) >= (it.get("posted_at") or datetime.min):
                    ex["dup_of"] = prev["uid"]
                elif prev:
                    (prev.get("extra") or {})["dup_of"] = it["uid"]
                    latest_in_thread[cid] = it
                else:
                    latest_in_thread[cid] = it
        it["extra"] = ex


RANK = ("course navigation", "installed tool", "module:")


def _site_rank(s: dict) -> int:
    w = s.get("where_found") or ""
    for i, r in enumerate(RANK):
        if w.startswith(r):
            return i
    return len(RANK)


def site_checks(sites: list[dict], now: datetime, tz) -> list[dict]:
    """One reminder per course+platform, using the most recent visit to any of that platform's links."""
    groups: dict[tuple, list[dict]] = {}
    for s in sites:
        if int(s.get("check_every_days") or 0) <= 0:
            continue
        groups.setdefault((s["course_id"], s.get("platform") or s["url"]), []).append(s)
    out = []
    for group in groups.values():
        group.sort(key=_site_rank)
        s = dict(group[0])
        visits = [g["last_checked_dt"] for g in group if g.get("last_checked_dt")]
        s["last_checked_dt"] = max(visits) if visits else None
        s["name"] = s.get("platform") or s.get("name")
        every = min(int(g.get("check_every_days") or 99) for g in group)
        last = s.get("last_checked_dt")
        overdue_days = None if last is None else (now - last).days
        if last is None or overdue_days >= every:
            out.append({
                "uid": f"site:{s['id']}", "kind": "site_check", "source": "radar", "status": "open",
                "title": f"Check {s.get('name') or s.get('platform') or s['url']}",
                "course": s.get("course_name"), "course_id": s.get("course_id"), "platform": s.get("platform"),
                "url": f"/sites/{s['id']}/open", "due_at": None, "posted_at": None, "user_state": "", "note": "",
                "body": ("never opened from Radar" if last is None else f"last opened {overdue_days} days ago") + f" · reminder every {every} days",
                "extra": {"site_id": s["id"], "external_url": s["url"], "site_ids": [g["id"] for g in group]},
                "ai": None, "first_seen": None, "changed_at": None,
            })
    return out


def soft_due(item: dict, now: datetime, tz) -> datetime | None:
    """For action emails without a real due date, use the earliest future date the mail (or AI) mentions."""
    if item["kind"] != "email" or item.get("due_at"):
        return None
    candidates = []
    ai = item.get("ai") or {}
    for a in ai.get("action_items") or []:
        d = parse_dt(a.get("due")) if a.get("due") and "T" in str(a.get("due")) else None
        if not d and a.get("due"):
            try:
                d = datetime.fromisoformat(str(a["due"])[:10] + "T23:59").replace(tzinfo=tz)
            except ValueError:
                d = None
        if d:
            candidates.append(d)
    if not candidates:
        for ds in (item.get("extra") or {}).get("mentioned_dates") or []:
            try:
                candidates.append(datetime.fromisoformat(ds + "T23:59").replace(tzinfo=tz))
            except ValueError:
                continue
    future = sorted(d for d in candidates if d >= now - timedelta(hours=12) and d <= now + timedelta(days=60))
    return future[0] if future else None


def build_dashboard(store, cfg: dict, now: datetime | None = None, tz=None) -> dict:
    now = now or datetime.now().astimezone()
    tz = tz or local_tz()
    today = now.astimezone(tz).date()
    items = store.items()
    courses = store.courses()
    course_by_id = {c["id"]: c for c in courses}
    sites = store.sites()
    for s in sites:
        s["course_name"] = (course_by_id.get(s["course_id"]) or {}).get("name")
    last_visit = parse_dt(store.get_meta("last_visit")) or (now - timedelta(days=3))
    dedupe(items, tz)

    def decorate(it: dict):
        ex = it.get("extra") or {}
        it["icon"] = KIND_ICONS.get(it["kind"], "•")
        it["kind_label"] = KIND_LABELS.get(it["kind"], it["kind"])
        it["status_label"] = STATUS_LABELS.get(it["status"], it["status"])
        it["is_new"] = bool(it.get("first_seen") and it["first_seen"] > last_visit)
        it["is_updated"] = bool(it.get("changed_at") and it["changed_at"] > last_visit and not it["is_new"])
        it["due_text"] = humanize_due(it.get("due_at"), now, tz, ex.get("all_day", False)) if it.get("due_at") else ""
        if it.get("due_at") and it["status"] in ("graded", "submitted", "excused", "complete", "info") and it["due_at"] < now:
            it["due_text"] = "was due " + it["due_at"].astimezone(tz).strftime("%b %-d")
        if it.get("starts_at") and not it.get("due_at"):
            it["due_text"] = humanize_due(it["starts_at"], now, tz, ex.get("all_day", False), prefix="")
        it["ago"] = humanize_ago(it.get("posted_at"), now, tz)
        it["priority"] = priority(it, now, tz)
        return it

    for it in items:
        decorate(it)

    tasks = [i for i in items if is_task(i, now)]
    for t in tasks:
        sd = soft_due(t, now, tz)
        if sd:
            t["due_at"] = sd
            t["extra"]["soft_due"] = True
            t["due_text"] = "mentions " + humanize_due(sd, now, tz, True, prefix="").strip()
            t["priority"] = priority(t, now, tz)
    snoozed_now = []
    active = []
    for t in tasks:
        if t.get("user_state") == "snoozed" and t.get("snooze_until") and t["snooze_until"] > now:
            snoozed_now.append(t)
        else:
            active.append(t)
    checks = [decorate(c) for c in site_checks(sites, now, tz)]

    buckets = {"overdue": [], "today": [], "tomorrow": [], "week": [], "later": [], "nodate": []}
    for t in active:
        due = t.get("due_at")
        if not due:
            buckets["nodate"].append(t)
            continue
        days = (due.astimezone(tz).date() - today).days
        all_day = (t.get("extra") or {}).get("all_day")
        if due < now and not (all_day and days == 0):
            buckets["overdue"].append(t)
        elif days <= 0:
            buckets["today"].append(t)
        elif days == 1:
            buckets["tomorrow"].append(t)
        elif days <= 7:
            buckets["week"].append(t)
        else:
            buckets["later"].append(t)
    buckets["nodate"].extend(checks)
    for k, v in buckets.items():
        v.sort(key=lambda i: (-(i["priority"]), i.get("due_at") or now, i["title"]))
    buckets["nodate"].sort(key=lambda i: (-(i["priority"]), i["title"]))

    def recent(it, days=14):
        p = it.get("posted_at")
        return bool(p) and p >= now - timedelta(days=days)

    new_info = []
    for it in items:
        ex = it.get("extra") or {}
        if ex.get("dup_of") or it.get("user_state") == "done":
            continue
        if it["kind"] in ("announcement", "conversation", "file", "discussion") and recent(it) and (it["is_new"] or it["status"] == "unread"):
            if it["kind"] == "discussion" and it in active:
                continue
            new_info.append(it)
        elif it["kind"] == "email" and recent(it) and it not in active:
            ai = it.get("ai") or {}
            relevant = ai.get("relevant") if ai else (ex.get("relevance", 0) >= 40 and not ex.get("bulk"))
            if relevant and (it["is_new"] or it["status"] == "unread") and not ex.get("canvas_notification"):
                new_info.append(it)
    new_info.sort(key=lambda i: i.get("posted_at") or now, reverse=True)

    graded = []
    for it in items:
        if it["status"] == "graded" and it["kind"] in ("assignment", "quiz", "discussion"):
            g = parse_dt((it.get("extra") or {}).get("graded_at"))
            if g and g >= now - timedelta(days=10):
                it["graded_ago"] = humanize_ago(g, now, tz)
                graded.append((g, it))
    graded.sort(key=lambda x: x[0], reverse=True)
    graded = [g[1] for g in graded[:15]]

    agenda: dict = {}
    agenda_seen: set = set()
    horizon = today + timedelta(days=7)
    for it in items:
        ex = it.get("extra") or {}
        if ex.get("dup_of"):
            continue
        when = it.get("starts_at") if it["kind"] in ("event", "calendar", "module", "syllabus_event") else None
        if it["kind"] == "syllabus_event" and it.get("due_at"):
            when = it["due_at"]
        if not when:
            continue
        d = when.astimezone(tz).date()
        if today <= d <= horizon and (d, it["uid"]) not in agenda_seen:
            agenda.setdefault(d, []).append(it)
            agenda_seen.add((d, it["uid"]))
    for t in active:
        if t.get("due_at"):
            d = t["due_at"].astimezone(tz).date()
            if today <= d <= horizon and (d, t["uid"]) not in agenda_seen:
                agenda.setdefault(d, []).append(t)
                agenda_seen.add((d, t["uid"]))
    agenda_days = []
    for i in range(8):
        d = today + timedelta(days=i)
        entries = sorted(agenda.get(d, []), key=lambda i: (i.get("starts_at") or i.get("due_at") or now))
        agenda_days.append({"date": d, "label": "Today" if i == 0 else "Tomorrow" if i == 1 else d.strftime("%A"),
                            "sub": d.strftime("%b %-d"), "items": entries})

    workload = []
    for i in range(14):
        d = today + timedelta(days=i)
        due = [t for t in active if t.get("due_at") and t["due_at"].astimezone(tz).date() == d]
        workload.append({"date": d, "label": d.strftime("%a")[0], "count": len(due), "titles": [t["title"] for t in due][:6]})

    done_recent = [i for i in items if i.get("user_state") == "done"]
    done_recent.sort(key=lambda i: i.get("last_seen") or now, reverse=True)

    unread_mail = sum(1 for i in items if i["kind"] == "email" and i["status"] == "unread" and not (i.get("extra") or {}).get("bulk"))
    stats = {
        "overdue": len(buckets["overdue"]), "today": len(buckets["today"]), "tomorrow": len(buckets["tomorrow"]),
        "week": len(buckets["week"]), "new": len(new_info), "unread_mail": unread_mail, "checks": len(checks),
        "tasks": len(active),
    }
    return {
        "now": now, "tz": tz, "today": today, "items": items, "courses": courses, "sites": sites, "buckets": buckets,
        "new_info": new_info, "graded": graded, "agenda": agenda_days, "workload": workload, "stats": stats,
        "snoozed": snoozed_now, "done_recent": done_recent[:30], "last_visit": last_visit, "checks": checks,
    }
