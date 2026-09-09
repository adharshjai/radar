"""Optional Claude enrichment: email/announcement triage and structured syllabus extraction.

Runs only when the `anthropic` package is installed and credentials resolve
(ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile).
"""
from __future__ import annotations

import json
from datetime import datetime

DEFAULT_MODEL = "claude-opus-5"

TRIAGE_SYSTEM = """You triage a university student's incoming mail and course announcements so nothing important is missed.
For each item decide whether it matters to the student, whether it needs an action, extract every concrete action item with its
deadline, and write a one-line summary. Dates: resolve relative phrases ("this Friday", "tomorrow") using the item's own date
and output ISO dates (YYYY-MM-DD, add THH:MM if a time is given). Treat pure marketing, newsletters and mass campus spam as not
relevant. Course-related mail (instructors, TAs, lab platforms such as LabFlow or Gradescope, registrar, financial aid, advising)
is relevant. Be terse. The student's courses and instructors are listed below."""

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "relevant": {"type": "boolean"},
                    "category": {"type": "string", "enum": ["course", "lab", "admin", "financial", "career", "club", "personal", "newsletter", "spam", "other"]},
                    "action_required": {"type": "boolean"},
                    "urgency": {"type": "integer", "minimum": 0, "maximum": 3},
                    "summary": {"type": "string"},
                    "action_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}, "due": {"type": ["string", "null"]}},
                            "required": ["text", "due"], "additionalProperties": False,
                        },
                    },
                    "course": {"type": ["string", "null"]},
                },
                "required": ["id", "relevant", "category", "action_required", "urgency", "summary", "action_items", "course"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

SYLLABUS_SYSTEM = """You read a university course syllabus (and related course pages) and extract every dated commitment so the
student can put them on a task list. Output exams (including finals, midterms, in-class tests), deadlines (projects, papers,
problem sets, lab reports, registration/drop deadlines that affect the student), other dated events (guest lectures, field trips,
no-class days, review sessions), the weekly recurring schedule (lectures, labs, discussion sections, office hours), a short
policy digest, and any external websites the course uses. Dates must be ISO YYYY-MM-DD; if the syllabus omits the year, infer it
from the term given. Use null when a date is not stated. Do not invent dates that are not in the text. Keep every string short."""

SYLLABUS_SCHEMA = {
    "type": "object",
    "properties": {
        "exams": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "date": {"type": ["string", "null"]}, "time": {"type": ["string", "null"]},
            "location": {"type": ["string", "null"]}, "notes": {"type": ["string", "null"]}},
            "required": ["name", "date", "time", "location", "notes"], "additionalProperties": False}},
        "deadlines": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "date": {"type": ["string", "null"]}, "time": {"type": ["string", "null"]},
            "notes": {"type": ["string", "null"]}},
            "required": ["title", "date", "time", "notes"], "additionalProperties": False}},
        "other_dates": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "date": {"type": ["string", "null"]}, "time": {"type": ["string", "null"]},
            "kind": {"type": "string", "enum": ["no_class", "review", "lab", "event", "admin", "date"]},
            "notes": {"type": ["string", "null"]}},
            "required": ["title", "date", "time", "kind", "notes"], "additionalProperties": False}},
        "weekly_schedule": {"type": "array", "items": {"type": "object", "properties": {
            "what": {"type": "string"}, "days": {"type": "string"}, "time": {"type": ["string", "null"]},
            "location": {"type": ["string", "null"]}},
            "required": ["what", "days", "time", "location"], "additionalProperties": False}},
        "policies": {"type": "object", "properties": {
            "late_work": {"type": ["string", "null"]}, "attendance": {"type": ["string", "null"]},
            "grading": {"type": ["string", "null"]}, "contact": {"type": ["string", "null"]}},
            "required": ["late_work", "attendance", "grading", "contact"], "additionalProperties": False},
        "websites": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "url": {"type": ["string", "null"]}, "purpose": {"type": ["string", "null"]}},
            "required": ["name", "url", "purpose"], "additionalProperties": False}},
        "key_facts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["exams", "deadlines", "other_dates", "weekly_schedule", "policies", "websites", "key_facts"],
    "additionalProperties": False,
}


class AIUnavailable(RuntimeError):
    pass


class Enricher:
    def __init__(self, cfg: dict, log=None):
        self.cfg = cfg
        self.model = cfg.get("model") or DEFAULT_MODEL
        self.log = log or (lambda *a: None)
        self._client = None
        self.disabled_reason: str | None = None

    def available(self) -> bool:
        if not self.cfg.get("enabled", True):
            self.disabled_reason = "disabled in config"
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            self.disabled_reason = "anthropic package not installed (pip install anthropic)"
            return False
        return self.disabled_reason is None

    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def _call(self, system: str, user: str, schema: dict, max_tokens: int = 16000) -> dict | None:
        import anthropic
        try:
            resp = self.client().beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.AuthenticationError:
            self.disabled_reason = "no Anthropic credentials (set ANTHROPIC_API_KEY or run `ant auth login`)"
            raise AIUnavailable(self.disabled_reason)
        except anthropic.BadRequestError as e:
            # Older API surfaces may not accept the fallback parameter; retry once without it.
            if "fallback" in str(e).lower():
                resp = self.client().messages.create(
                    model=self.model, max_tokens=max_tokens,
                    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": user}],
                    output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                )
            else:
                raise
        except (anthropic.RateLimitError, anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            self.log(f"AI call failed: {e}")
            return None
        if resp.stop_reason == "refusal":
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    # -- email / announcement triage ------------------------------------------------
    def triage(self, items: list[dict], courses: list[dict], batch_size: int = 15) -> dict[str, dict]:
        """items: store rows. Returns uid -> ai dict."""
        if not items:
            return {}
        course_lines = "\n".join(
            f"- {c.get('code') or ''} {c.get('name')} (instructors: {', '.join(c.get('teachers') or []) or 'unknown'})" for c in courses)
        system = TRIAGE_SYSTEM + "\n\nCourses:\n" + course_lines
        results: dict[str, dict] = {}
        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            parts = []
            for it in batch:
                when = it["posted_at"].strftime("%a %Y-%m-%d %H:%M UTC") if it.get("posted_at") else "unknown date"
                parts.append(
                    f"### id={it['uid']}\nkind: {it['kind']} | course: {it.get('course') or '-'} | from: {it.get('sender') or '-'} | date: {when}\n"
                    f"subject: {it['title']}\n{(it.get('body') or '')[:3500]}\n")
            data = self._call(system, "Triage these items:\n\n" + "\n".join(parts), TRIAGE_SCHEMA)
            if not data:
                continue
            for r in data.get("results", []):
                results[r["id"]] = r
            self.log(f"AI triaged {len(batch)} items")
        return results

    # -- syllabus extraction ----------------------------------------------------------
    def syllabus(self, course: dict, text: str, now: datetime) -> dict | None:
        ex = course.get("extra") or {}
        term = f"Term: {course.get('term') or 'unknown'} (starts {ex.get('term_start') or '?'}, ends {ex.get('term_end') or '?'}); today is {now.date().isoformat()}."
        user = f"Course: {course.get('code')} {course.get('name')}\n{term}\n\n{text[:120000]}"
        return self._call(SYLLABUS_SYSTEM, user, SYLLABUS_SCHEMA, max_tokens=16000)

    # -- daily narrative ---------------------------------------------------------------
    def brief(self, brief_text: str) -> str | None:
        import anthropic
        try:
            resp = self.client().messages.create(
                model=self.model, max_tokens=2000,
                system="You write a student's morning briefing from a structured task dump. Plain text, at most 12 short lines. "
                       "Lead with anything overdue or due today, then tomorrow, then the one or two things they might forget "
                       "(external lab platforms, unread instructor mail). No preamble.",
                messages=[{"role": "user", "content": brief_text}],
            )
        except anthropic.APIError as e:
            self.log(f"AI brief failed: {e}")
            return None
        return next((b.text for b in resp.content if b.type == "text"), None)
