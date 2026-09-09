"""SQLite persistence: items, courses, sites, sync log, and per-item user state."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Course, Item, Site, iso, parse_dt, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    uid TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    course TEXT, course_id TEXT,
    due_at TEXT, starts_at TEXT, ends_at TEXT,
    url TEXT, platform TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    points REAL, score REAL, grade TEXT,
    body TEXT NOT NULL DEFAULT '',
    sender TEXT, posted_at TEXT,
    extra TEXT NOT NULL DEFAULT '{}',
    fingerprint TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, changed_at TEXT,
    prev_due_at TEXT,
    user_state TEXT NOT NULL DEFAULT '',
    snooze_until TEXT,
    note TEXT NOT NULL DEFAULT '',
    ai TEXT NOT NULL DEFAULT '',
    ai_fingerprint TEXT NOT NULL DEFAULT '',
    notified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_due ON items(due_at);
CREATE INDEX IF NOT EXISTS idx_items_course ON items(course_id);
CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY, name TEXT, code TEXT, term TEXT, url TEXT,
    teachers TEXT NOT NULL DEFAULT '[]', current_score REAL, current_grade TEXT,
    syllabus_html TEXT NOT NULL DEFAULT '', syllabus_text TEXT NOT NULL DEFAULT '',
    syllabus_hash TEXT NOT NULL DEFAULT '', syllabus_ai TEXT NOT NULL DEFAULT '',
    extra TEXT NOT NULL DEFAULT '{}', first_seen TEXT, last_seen TEXT, hidden INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id TEXT NOT NULL, url TEXT NOT NULL, name TEXT, platform TEXT,
    category TEXT NOT NULL DEFAULT 'link', where_found TEXT,
    first_seen TEXT, last_seen TEXT, check_every_days INTEGER NOT NULL DEFAULT 0,
    last_checked TEXT, hidden INTEGER NOT NULL DEFAULT 0,
    UNIQUE(course_id, url)
);
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT, finished TEXT, source TEXT, ok INTEGER, message TEXT, counts TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

ITEM_COLS = ["uid", "source", "kind", "title", "course", "course_id", "due_at", "starts_at", "ends_at",
             "url", "platform", "status", "points", "score", "grade", "body", "sender", "posted_at", "extra"]


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    # -- helpers ---------------------------------------------------------
    def _q(self, sql, params=()):
        with self._lock:
            return self._conn.execute(sql, params)

    def commit(self):
        with self._lock:
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # -- meta --------------------------------------------------------------
    def get_meta(self, key: str, default=None):
        row = self._q("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value):
        self._q("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, "" if value is None else str(value)))
        self.commit()

    # -- courses -----------------------------------------------------------
    def upsert_courses(self, courses: list[Course]):
        now = iso(utcnow())
        for c in courses:
            self._q("""INSERT INTO courses(id,name,code,term,url,teachers,current_score,current_grade,syllabus_html,extra,first_seen,last_seen)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET name=excluded.name, code=excluded.code, term=excluded.term, url=excluded.url,
                       teachers=excluded.teachers, current_score=excluded.current_score, current_grade=excluded.current_grade,
                       syllabus_html=excluded.syllabus_html, extra=excluded.extra, last_seen=excluded.last_seen""",
                    (c.id, c.name, c.code, c.term, c.url, json.dumps(c.teachers), c.current_score, c.current_grade,
                     c.syllabus_html, json.dumps(c.extra), now, now))
        self.commit()

    def courses(self, include_hidden=False) -> list[dict]:
        rows = self._q("SELECT * FROM courses WHERE hidden=0 OR ? ORDER BY name", (1 if include_hidden else 0,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["teachers"] = json.loads(d.get("teachers") or "[]")
            d["extra"] = json.loads(d.get("extra") or "{}")
            d["syllabus_ai"] = json.loads(d["syllabus_ai"]) if d.get("syllabus_ai") else None
            out.append(d)
        return out

    def course(self, course_id: str) -> dict | None:
        for c in self.courses(include_hidden=True):
            if c["id"] == str(course_id):
                return c
        return None

    def set_course_syllabus(self, course_id: str, text: str, h: str, ai: dict | None = None):
        if ai is None:
            self._q("UPDATE courses SET syllabus_text=?, syllabus_hash=? WHERE id=?", (text, h, course_id))
        else:
            self._q("UPDATE courses SET syllabus_text=?, syllabus_hash=?, syllabus_ai=? WHERE id=?",
                    (text, h, json.dumps(ai), course_id))
        self.commit()

    # -- sites -------------------------------------------------------------
    def upsert_sites(self, sites: list[Site]):
        now = iso(utcnow())
        for s in sites:
            primary = s.where_found.startswith(("course navigation", "installed tool", "module:"))
            default_check = 3 if (s.category == "tool" and primary and s.platform) else 0
            self._q("""INSERT INTO sites(course_id,url,name,platform,category,where_found,first_seen,last_seen,check_every_days)
                       VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(course_id,url) DO UPDATE SET name=excluded.name, platform=excluded.platform,
                       category=excluded.category, where_found=excluded.where_found, last_seen=excluded.last_seen""",
                    (s.course_id, s.url, s.name, s.platform, s.category, s.where_found, now, now, default_check))
        self.commit()

    def sites(self, course_id: str | None = None, include_hidden=False) -> list[dict]:
        sql = "SELECT * FROM sites WHERE (hidden=0 OR ?)"
        params: list = [1 if include_hidden else 0]
        if course_id:
            sql += " AND course_id=?"
            params.append(str(course_id))
        sql += " ORDER BY category, platform IS NULL, name"
        out = []
        for r in self._q(sql, params).fetchall():
            d = dict(r)
            d["last_checked_dt"] = parse_dt(d.get("last_checked"))
            out.append(d)
        return out

    def site(self, site_id: int) -> dict | None:
        r = self._q("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
        return dict(r) if r else None

    def mark_site_checked(self, site_id: int):
        self._q("UPDATE sites SET last_checked=? WHERE id=?", (iso(utcnow()), site_id))
        self.commit()

    def set_site_check(self, site_id: int, days: int):
        self._q("UPDATE sites SET check_every_days=? WHERE id=?", (int(days), site_id))
        self.commit()

    def hide_site(self, site_id: int, hidden=True):
        self._q("UPDATE sites SET hidden=? WHERE id=?", (1 if hidden else 0, site_id))
        self.commit()

    # -- items -------------------------------------------------------------
    def upsert_items(self, items: list[Item]) -> dict:
        """Insert or update items. Preserves user state. Returns counts of new/changed."""
        now = iso(utcnow())
        new = changed = 0
        with self._lock:
            for it in items:
                fp = it.fingerprint()
                row = self._conn.execute("SELECT fingerprint, due_at FROM items WHERE uid=?", (it.uid,)).fetchone()
                vals = {
                    "uid": it.uid, "source": it.source, "kind": it.kind, "title": it.title, "course": it.course,
                    "course_id": it.course_id, "due_at": iso(it.due_at), "starts_at": iso(it.starts_at),
                    "ends_at": iso(it.ends_at), "url": it.url, "platform": it.platform, "status": it.status,
                    "points": it.points, "score": it.score, "grade": it.grade, "body": it.body or "",
                    "sender": it.sender, "posted_at": iso(it.posted_at), "extra": json.dumps(it.extra, default=str),
                }
                if row is None:
                    new += 1
                    cols = ITEM_COLS + ["fingerprint", "first_seen", "last_seen"]
                    self._conn.execute(
                        f"INSERT INTO items({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
                        [vals[c] for c in ITEM_COLS] + [fp, now, now])
                else:
                    sets = ", ".join(f"{c}=?" for c in ITEM_COLS if c != "uid")
                    params = [vals[c] for c in ITEM_COLS if c != "uid"]
                    if row["fingerprint"] != fp:
                        changed += 1
                        sets += ", fingerprint=?, changed_at=?"
                        params += [fp, now]
                        if row["due_at"] != vals["due_at"] and row["due_at"]:
                            sets += ", prev_due_at=?"
                            params.append(row["due_at"])
                    sets += ", last_seen=?"
                    params.append(now)
                    params.append(it.uid)
                    self._conn.execute(f"UPDATE items SET {sets} WHERE uid=?", params)
            self._conn.commit()
        return {"new": new, "changed": changed, "total": len(items)}

    def _row_to_item(self, r) -> dict:
        d = dict(r)
        for k in ("due_at", "starts_at", "ends_at", "posted_at", "first_seen", "last_seen", "changed_at",
                  "snooze_until", "prev_due_at"):
            d[k] = parse_dt(d.get(k))
        d["extra"] = json.loads(d.get("extra") or "{}")
        d["ai"] = json.loads(d["ai"]) if d.get("ai") else None
        return d

    def items(self, source: str | None = None, kinds: list[str] | None = None, course_id: str | None = None,
              since: datetime | None = None) -> list[dict]:
        sql = "SELECT * FROM items WHERE 1=1"
        params: list = []
        if source:
            sql += " AND source=?"
            params.append(source)
        if kinds:
            sql += f" AND kind IN ({','.join('?' for _ in kinds)})"
            params += kinds
        if course_id:
            sql += " AND course_id=?"
            params.append(str(course_id))
        if since:
            sql += " AND (due_at >= ? OR starts_at >= ? OR posted_at >= ? OR (due_at IS NULL AND starts_at IS NULL AND posted_at IS NULL))"
            params += [iso(since)] * 3
        sql += " ORDER BY COALESCE(due_at, starts_at, posted_at) ASC"
        return [self._row_to_item(r) for r in self._q(sql, params).fetchall()]

    def item(self, uid: str) -> dict | None:
        r = self._q("SELECT * FROM items WHERE uid=?", (uid,)).fetchone()
        return self._row_to_item(r) if r else None

    def set_user_state(self, uid: str, state: str, snooze_until: datetime | None = None):
        self._q("UPDATE items SET user_state=?, snooze_until=? WHERE uid=?", (state, iso(snooze_until), uid))
        self.commit()

    def set_note(self, uid: str, note: str):
        self._q("UPDATE items SET note=? WHERE uid=?", (note, uid))
        self.commit()

    def set_ai(self, uid: str, ai: dict, fingerprint: str):
        self._q("UPDATE items SET ai=?, ai_fingerprint=? WHERE uid=?", (json.dumps(ai), fingerprint, uid))
        self.commit()

    def items_needing_ai(self, kinds: list[str], limit: int) -> list[dict]:
        sql = f"""SELECT * FROM items WHERE kind IN ({','.join('?' for _ in kinds)})
                  AND ai_fingerprint != fingerprint ORDER BY COALESCE(posted_at, due_at) DESC LIMIT ?"""
        return [self._row_to_item(r) for r in self._q(sql, kinds + [limit]).fetchall()]

    def mark_notified(self, uids: list[str]):
        for u in uids:
            self._q("UPDATE items SET notified=1 WHERE uid=?", (u,))
        self.commit()

    def add_manual_item(self, it: Item):
        self.upsert_items([it])

    def delete_item(self, uid: str):
        self._q("DELETE FROM items WHERE uid=?", (uid,))
        self.commit()

    def prune(self, source: str, course_id: str | None, keep_uids: set[str]):
        """Delete items of one source (optionally one course) that are not in keep_uids."""
        sql = "SELECT uid FROM items WHERE source=?"
        params: list = [source]
        if course_id:
            sql += " AND course_id=?"
            params.append(str(course_id))
        for r in self._q(sql, params).fetchall():
            if r["uid"] not in keep_uids:
                self._q("DELETE FROM items WHERE uid=?", (r["uid"],))
        self.commit()

    # -- sync log ------------------------------------------------------------
    def log_sync(self, source: str, started: datetime, ok: bool, message: str = "", counts: dict | None = None):
        self._q("INSERT INTO sync_log(started,finished,source,ok,message,counts) VALUES(?,?,?,?,?,?)",
                (iso(started), iso(utcnow()), source, 1 if ok else 0, message, json.dumps(counts or {})))
        self.commit()

    def last_syncs(self, n=10) -> list[dict]:
        rows = self._q("SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["started"] = parse_dt(d["started"])
            d["finished"] = parse_dt(d["finished"])
            d["counts"] = json.loads(d.get("counts") or "{}")
            out.append(d)
        return out

    def last_failure(self, source: str) -> dict | None:
        """The most recent failed sync for a source, if it is newer than the last success."""
        r = self._q("SELECT * FROM sync_log WHERE source=? ORDER BY id DESC LIMIT 1", (source,)).fetchone()
        if not r or r["ok"]:
            return None
        d = dict(r)
        d["finished"] = parse_dt(d["finished"])
        return d

    def last_ok_sync(self, source: str | None = None) -> datetime | None:
        sql = "SELECT finished FROM sync_log WHERE ok=1"
        params: list = []
        if source:
            sql += " AND source=?"
            params.append(source)
        sql += " ORDER BY id DESC LIMIT 1"
        r = self._q(sql, params).fetchone()
        return parse_dt(r["finished"]) if r else None
