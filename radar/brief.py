"""Plain-text daily brief (also the input for the optional AI narrative)."""
from __future__ import annotations


def _line(it: dict) -> str:
    bits = [it["title"]]
    if it.get("course"):
        bits.append(f"[{it['course']}]")
    if it.get("due_text"):
        bits.append(it["due_text"])
    if it.get("platform"):
        bits.append(f"on {it['platform']}")
    if it.get("status_label") and it["status_label"] not in ("", "Unread"):
        bits.append(f"({it['status_label']})")
    return " · ".join(bits)


def render_brief(dash: dict, width: int = 160) -> str:
    b = dash["buckets"]
    s = dash["stats"]
    out = [f"RADAR — {dash['now'].strftime('%A %b %-d, %Y %-I:%M %p')}",
           f"{s['overdue']} overdue · {s['today']} due today · {s['tomorrow']} tomorrow · {s['week']} this week · {s['new']} new · {s['unread_mail']} unread mail",
           ""]

    def section(title, items, limit=25):
        if not items:
            return
        out.append(title.upper())
        for it in items[:limit]:
            out.append("  - " + _line(it)[:width])
            ai = it.get("ai") or {}
            for a in (ai.get("action_items") or [])[:3]:
                out.append(f"      → {a.get('text')}" + (f" (by {a['due']})" if a.get("due") else ""))
        if len(items) > limit:
            out.append(f"  … and {len(items) - limit} more")
        out.append("")

    section("Overdue / missing", b["overdue"])
    section("Due today", b["today"])
    section("Due tomorrow", b["tomorrow"])
    section("This week", b["week"])
    section("No date yet (act on these)", b["nodate"], 15)
    section("New since last look", dash["new_info"], 15)
    if dash["graded"]:
        out.append("RECENTLY GRADED")
        for it in dash["graded"][:10]:
            score = f"{it['score']:g}/{it['points']:g}" if it.get("score") is not None and it.get("points") else (it.get("grade") or "graded")
            out.append(f"  - {it['title']} [{it.get('course')}] — {score}")
        out.append("")
    agenda = [d for d in dash["agenda"][:3] if d["items"]]
    if agenda:
        out.append("NEXT 3 DAYS")
        for d in agenda:
            out.append(f"  {d['label']} ({d['sub']})")
            for it in d["items"][:10]:
                when = it.get("starts_at") or it.get("due_at")
                t = when.astimezone(dash["tz"]).strftime("%-I:%M %p").lower() if when and not (it.get("extra") or {}).get("all_day") else "all day"
                out.append(f"    {t:>9}  {it['title']}" + (f" [{it['course']}]" if it.get("course") else ""))
        out.append("")
    section("Later", b["later"], 20)
    return "\n".join(out).rstrip() + "\n"
