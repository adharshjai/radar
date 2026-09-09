"""Run every configured source, store the results, enrich, and notify."""
from __future__ import annotations

import subprocess
import traceback
from datetime import datetime, timedelta

from . import config as cfgmod
from .mailrules import score_email
from .models import Site, content_hash, utcnow
from .store import Store
from .syllabus import items_from_ai, items_from_text
from .triage import build_dashboard, local_tz


def macos_notify(title: str, message: str, url: str | None = None):
    safe_t = title.replace('"', "'")[:80]
    safe_m = message.replace('"', "'")[:200]
    try:
        subprocess.run(["osascript", "-e", f'display notification "{safe_m}" with title "{safe_t}"'],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def _profile(cfg: dict, store: Store) -> dict:
    profile = dict(cfg.get("profile") or {})
    if not profile.get("university_domains"):
        learned = store.get_meta("learned_domain")
        if learned:
            profile["university_domains"] = [learned]
    return profile


def make_canvas_client(cfg: dict, store: Store, log=print, allow_browser_import: bool = True):
    """Pick token auth or browser-session auth. Returns (client, mode) or raises CanvasError."""
    from .sources.canvas import CanvasClient, CanvasError
    from .sources.canvas_session import cookies_from_browser, load_cookies, save_cookies

    c = cfg["canvas"]
    base = c.get("base_url") or ""
    mode = (c.get("auth") or "auto").lower()
    if not base:
        raise CanvasError("Canvas base_url is not configured (run: python3 -m radar setup)")
    if mode == "off":
        raise CanvasError(f"Canvas API access is switched off (auth = \"off\"). Run: {cfgmod.CMD} login canvas")
    if mode in ("auto", "token") and c.get("token"):
        return CanvasClient(base, c["token"]), "token"
    if mode == "token":
        raise CanvasError("Canvas auth is set to 'token' but no token is configured")
    saved = load_cookies(cfgmod.home())
    if saved:
        return CanvasClient(base, cookies=saved["cookies"]), f"browser session ({saved.get('source', 'saved')})"
    if allow_browser_import:
        cookies, browser = cookies_from_browser(base, c.get("browser") or None, c.get("cookie_file") or None)
        save_cookies(cfgmod.home(), cookies, browser)
        log(f"Imported Canvas session from {browser}")
        return CanvasClient(base, cookies=cookies), f"browser session ({browser})"
    raise CanvasError(f"No Canvas token and no saved browser session. Run: {cfgmod.CMD} login canvas")


def refresh_canvas_session(cfg: dict, log=print):
    """After a session-expired error: re-import cookies from the browser and return a fresh client, or None."""
    from .sources.canvas import CanvasClient
    from .sources.canvas_session import CanvasSessionError, clear_cookies, cookies_from_browser, save_cookies
    clear_cookies(cfgmod.home())
    try:
        cookies, browser = cookies_from_browser(cfg["canvas"]["base_url"], cfg["canvas"].get("browser") or None,
                                                cfg["canvas"].get("cookie_file") or None)
    except CanvasSessionError as e:
        log(str(e))
        return None
    client = CanvasClient(cfg["canvas"]["base_url"], cookies=cookies)
    try:
        client.get_json("/users/self")
    except Exception as e:
        log(f"Re-imported session from {browser} but Canvas still refused it: {e}")
        return None
    save_cookies(cfgmod.home(), cookies, browser)
    log(f"Re-imported Canvas session from {browser}")
    return client


def process_syllabus(store: Store, course: dict, text: str, enricher, now, tz, log) -> int:
    h = content_hash(text)
    ai = None
    if course.get("syllabus_hash") == h and course.get("syllabus_ai"):
        ai = course["syllabus_ai"]
    elif enricher is not None:
        try:
            ai = enricher.syllabus(course, text, now)
        except Exception as e:  # AIUnavailable or network trouble - fall back to regex
            log(f"  syllabus AI failed for {course['name']}: {e}")
            ai = None
    store.set_course_syllabus(course["id"], text, h, ai)
    course["syllabus_ai"] = ai
    items = items_from_ai(course, ai, now, tz) if ai else items_from_text(course, text, now, tz)
    if ai:
        sites = []
        for w in ai.get("websites") or []:
            if w.get("url") and str(w["url"]).startswith("http"):
                from .sources.platforms import detect_platform
                platform, cat = detect_platform(w["url"], w.get("name"))
                sites.append(Site(course_id=course["id"], url=w["url"], name=w.get("name") or w["url"], platform=platform,
                                  category=cat if platform else "link", where_found="syllabus (AI)"))
        if sites:
            store.upsert_sites(sites)
    store.upsert_items(items)
    store.prune("syllabus", course["id"], {i.uid for i in items})
    return len(items)


def run_sync(cfg: dict | None = None, store: Store | None = None, sources: list[str] | None = None,
             log=print, use_ai: bool = True, now: datetime | None = None, notify: bool = False,
             canvas_client=None, outlook_source=None, applemail_source=None, feeds_source=None) -> dict:
    cfg = cfg or cfgmod.load()
    store = store or Store(cfgmod.db_path())
    now = now or utcnow()
    tz = local_tz()
    results: dict = {}
    sources = sources or ["canvas", "outlook", "applemail"]

    enricher = None
    if use_ai and cfg.get("ai", {}).get("enabled", True):
        try:
            from .enrich.ai import Enricher
            e = Enricher(cfg["ai"], log=log)
            if e.available():
                enricher = e
            else:
                log(f"AI enrichment off: {e.disabled_reason}")
        except Exception as ex:
            log(f"AI enrichment off: {ex}")

    # ---------------- Canvas API (token or browser session)
    canvas_configured = bool(cfg["canvas"].get("base_url")) and (
        bool(cfg["canvas"].get("token")) or (cfg["canvas"].get("auth") or "auto") in ("auto", "browser"))
    if "canvas" in sources and (canvas_configured or canvas_client is not None):
        started = utcnow()
        try:
            from .sources.canvas import CanvasSessionExpired, CanvasSource
            from .sources.canvas_session import CanvasSessionError
            if canvas_client is not None:
                client, mode = canvas_client, "test"
            else:
                try:
                    client, mode = make_canvas_client(cfg, store, log=log)
                except CanvasSessionError as e:
                    raise CanvasSessionExpired(str(e)) from e
            log(f"Canvas auth: {mode}")
            try:
                snap = CanvasSource(client, cfg["canvas"], now=now, log=log).fetch()
            except CanvasSessionExpired as e:
                if canvas_client is not None or (cfg["canvas"].get("auth") or "auto") == "token":
                    raise
                log(f"{e}\nTrying to re-import the session from your browser…")
                client = refresh_canvas_session(cfg, log=log)
                if client is None:
                    raise
                snap = CanvasSource(client, cfg["canvas"], now=now, log=log).fetch()
            if getattr(client, "cookie_mode", False) and canvas_client is None:
                from .sources.canvas_session import load_cookies, save_cookies
                saved = load_cookies(cfgmod.home()) or {}
                fresh = client.fresh_cookies()
                if fresh and fresh != saved.get("cookies"):
                    save_cookies(cfgmod.home(), fresh, saved.get("source") or mode)
            store.upsert_courses(snap.courses)
            uniq: dict = {}
            for s in snap.sites:
                uniq.setdefault((s.course_id, s.url), s)
            store.upsert_sites(list(uniq.values()))
            counts = store.upsert_items(snap.items)
            email = (snap.me or {}).get("primary_email") or ""
            if "@" in email:
                store.set_meta("learned_domain", email.split("@")[-1].lower())
                store.set_meta("me_name", (snap.me or {}).get("name") or "")
            syl = 0
            for cid, text in snap.syllabi.items():
                course = store.course(cid)
                if course:
                    syl += process_syllabus(store, course, text, enricher, now, tz, log)
            counts["syllabus_items"] = syl
            counts["sites"] = len(uniq)
            counts["api_calls"] = snap.api_calls
            store.log_sync("canvas", started, True, counts=counts)
            results["canvas"] = {"ok": True, **counts}
            log(f"Canvas: {counts}")
        except Exception as e:
            msg = str(e) or e.__class__.__name__
            store.log_sync("canvas", started, False, msg)
            results["canvas"] = {"ok": False, "error": msg}
            log(f"Canvas failed: {msg}")
            if not isinstance(e, RuntimeError):
                log(traceback.format_exc())
    elif "canvas" in sources:
        results["canvas"] = {"ok": False, "error": "not configured"}

    # ---------------- Canvas feeds (.ics calendar + announcement .atom) - no login needed, so always worth running
    feeds_cfg = cfg["canvas"]
    if "canvas" in sources and (feeds_cfg.get("ics_url") or feeds_cfg.get("announcement_feeds") or feeds_source is not None):
        started = utcnow()
        try:
            from .sources.canvas_feeds import CanvasFeedsSource
            known = {c["id"]: c["name"] for c in store.courses(include_hidden=True)}
            src = feeds_source or CanvasFeedsSource(feeds_cfg, tz, known_courses=known, log=log)
            fs = src.fetch(now=now)
            include = {str(x) for x in feeds_cfg.get("include_courses") or []}
            exclude = {str(x) for x in feeds_cfg.get("exclude_courses") or []}
            if include or exclude:
                keep = lambda cid: (not include or cid in include) and cid not in exclude  # noqa: E731
                fs.courses = [c for c in fs.courses if keep(str(c.id))]
                fs.items = [i for i in fs.items if not i.course_id or keep(str(i.course_id))]
            fs.warnings = [w for w in fs.warnings]
            if fs.courses:
                store.upsert_courses(fs.courses)
            # API data is richer (submission state, grades): never overwrite it with a feed entry, even on runs
            # where the API failed. Items the feeds themselves created earlier are refreshed normally.
            existing = {i["uid"] for i in store.items(source="canvas") if (i.get("extra") or {}).get("from") not in ("ics", "atom")}
            fresh = [i for i in fs.items if i.uid not in existing]
            counts = store.upsert_items(fresh)
            counts["skipped_dupes"] = len(fs.items) - len(fresh)
            ok = not fs.warnings or bool(fs.items)
            store.log_sync("canvas-feeds", started, ok, "; ".join(fs.warnings), counts)
            results["canvas-feeds"] = {"ok": ok, **counts, "warnings": fs.warnings}
            log(f"Canvas feeds: {counts}")
        except Exception as e:
            from .sources.canvas_feeds import redact
            msg = redact(str(e) or e.__class__.__name__)
            store.log_sync("canvas-feeds", started, False, msg)
            results["canvas-feeds"] = {"ok": False, "error": msg}
            log(f"Canvas feeds failed: {msg}")

    # ---------------- Mail (Outlook via Graph, or Apple Mail)
    courses = store.courses()
    profile = _profile(cfg, store)
    mail_items = []
    if "outlook" in sources and (cfg["outlook"].get("enabled") or outlook_source is not None):
        started = utcnow()
        try:
            from .sources.outlook import OutlookSource
            src = outlook_source or OutlookSource(cfg["outlook"], cfgmod.home() / "msal_cache.json", log=log)
            me, items = src.fetch(now=now)
            mail_items.extend(items)
            if me.get("mail") or me.get("userPrincipalName"):
                store.set_meta("outlook_account", me.get("mail") or me.get("userPrincipalName"))
            store.log_sync("outlook", started, True, counts={"items": len(items)})
            results["outlook"] = {"ok": True, "items": len(items)}
        except Exception as e:
            msg = str(e) or e.__class__.__name__
            store.log_sync("outlook", started, False, msg)
            results["outlook"] = {"ok": False, "error": msg}
            log(f"Outlook failed: {msg}")
    if "applemail" in sources and (cfg["applemail"].get("enabled") or applemail_source is not None):
        started = utcnow()
        try:
            from .sources.applemail import AppleMailSource
            src = applemail_source or AppleMailSource(cfg["applemail"], log=log)
            items = src.fetch(now=now)
            mail_items.extend(items)
            store.log_sync("applemail", started, True, counts={"items": len(items)})
            results["applemail"] = {"ok": True, "items": len(items)}
        except Exception as e:
            msg = str(e) or e.__class__.__name__
            store.log_sync("applemail", started, False, msg)
            results["applemail"] = {"ok": False, "error": msg}
            log(f"Apple Mail failed: {msg}")
    if mail_items:
        for it in mail_items:
            if it.kind == "email":
                score_email(it, courses, profile, now, tz)
        counts = store.upsert_items(mail_items)
        log(f"Mail: {counts}")

    # ---------------- AI triage of new mail / announcements / inbox
    if enricher is not None:
        try:
            limit = int(cfg["ai"].get("max_items_per_sync", 80))
            rows = store.items_needing_ai(["email", "announcement", "conversation"], limit * 3)
            todo = []
            for r in rows:
                ex = r.get("extra") or {}
                if r["kind"] == "email" and (ex.get("bulk") or ex.get("relevance", 0) < 25) and r["status"] == "read":
                    store.set_ai(r["uid"], {"relevant": False, "skipped": True}, r["fingerprint"])
                    continue
                todo.append(r)
                if len(todo) >= limit:
                    break
            if todo:
                out = enricher.triage(todo, courses)
                for r in todo:
                    if r["uid"] in out:
                        store.set_ai(r["uid"], out[r["uid"]], r["fingerprint"])
                results["ai"] = {"ok": True, "triaged": len(out)}
        except Exception as e:
            results["ai"] = {"ok": False, "error": str(e)}
            log(f"AI triage failed: {e}")

    store.set_meta("last_sync", now.isoformat())
    if notify:
        notify_new(store, cfg, now)
    return results


def notify_new(store: Store, cfg: dict, now: datetime) -> list[dict]:
    """Send one macOS notification about newly discovered urgent items."""
    dash = build_dashboard(store, cfg, now=now.astimezone(local_tz()))
    fresh = []
    for bucket in ("overdue", "today", "tomorrow"):
        for it in dash["buckets"][bucket]:
            if it.get("first_seen") and it["first_seen"] >= now - timedelta(hours=2) and not it.get("notified"):
                fresh.append(it)
    for it in dash["new_info"][:10]:
        if it["kind"] in ("announcement", "email") and it.get("first_seen") and it["first_seen"] >= now - timedelta(hours=2) and not it.get("notified"):
            if it["kind"] == "email" and not ((it.get("ai") or {}).get("action_required") or (it.get("extra") or {}).get("action_required")):
                continue
            fresh.append(it)
    if fresh:
        first = fresh[0]
        more = f" (+{len(fresh) - 1} more)" if len(fresh) > 1 else ""
        macos_notify(f"Radar: {len(fresh)} new", f"{first['title']} — {first.get('due_text') or first['kind_label']}{more}")
        store.mark_notified([f["uid"] for f in fresh])
    return fresh
