"""Use the browser's logged-in Canvas session instead of a personal access token.

Schools can disable student token creation ("Approved Integrations" shows no "+ New Access Token").
The Canvas web UI itself talks to /api/v1 with the `canvas_session` cookie, so we can do the same:
either import the cookie straight out of Chrome/Safari/Firefox (browser_cookie3) or paste it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .. import config as cfgmod

SESSION_COOKIE = "canvas_session"                     # Instructure-hosted Canvas
SESSION_COOKIE_NAMES = ("canvas_session", "_normandy_session", "_legacy_normandy_session")   # self-hosted uses _normandy_session
# pseudonym_credentials is the 2-week "Stay signed in" cookie: with it Canvas silently re-creates a session
KEEP_COOKIES = set(SESSION_COOKIE_NAMES) | {"_csrf_token", "log_session_id", "pseudonym_credentials"}
# Arc/Dia keep their cookie key in Apple Passwords, which nothing on the command line can read - not tried by default
BROWSERS = ["chrome", "firefox", "safari", "edge", "brave", "chromium", "vivaldi", "opera"]
SAFARI_HINT = ("Safari cookies are protected by macOS. Give your terminal app Full Disk Access "
               "(System Settings > Privacy & Security > Full Disk Access), then try again - or use Chrome/Firefox.")
KEYCHAIN_HINT = ("macOS refused access to the browser's cookie key (a dismissed Keychain prompt is remembered as 'deny'). "
                 "Open Keychain Access, search 'Chrome Safe Storage' (or 'Brave/Edge Safe Storage'), Access Control tab, "
                 "remove the denied entry, then try again and click Always Allow.")
PROFILE_HINT = "If you use several browser profiles, set [canvas] cookie_file to that profile's Cookies file in the config."


def filter_cookies(cookies: dict) -> dict:
    return {k: v for k, v in (cookies or {}).items() if k in KEEP_COOKIES and v}


def has_session(cookies: dict | None) -> bool:
    return bool(cookies) and any(cookies.get(n) for n in SESSION_COOKIE_NAMES)


class CanvasSessionError(RuntimeError):
    pass


def host_of(base_url: str) -> str:
    return (urlparse(base_url).hostname or base_url).lower()


def _domain_matches(cookie_domain: str, host: str) -> bool:
    """RFC 6265 domain matching: the cookie's domain must equal the host or be a parent of it."""
    d = (cookie_domain or "").lower().lstrip(".")
    return bool(d) and (host == d or host.endswith("." + d))


def cookies_from_browser(base_url: str, browser: str | None = None, cookie_file: str | None = None) -> tuple[dict, str]:
    """Return ({cookie: value}, browser_name) for the Canvas host from the first browser that has a login.

    cookie_file points browser_cookie3 at one specific profile's cookie database (multi-profile Chrome)."""
    try:
        import browser_cookie3 as bc
    except ImportError as e:
        raise CanvasSessionError("browser-cookie3 is not installed: pip install browser-cookie3") from e
    host = host_of(base_url)
    order = [browser] if browser else BROWSERS
    errors = []
    for name in order:
        loader = getattr(bc, name, None)
        if loader is None:
            errors.append(f"{name}: unknown browser")
            continue
        try:
            jar = loader(domain_name=host, cookie_file=cookie_file) if (cookie_file and browser) else loader(domain_name=host)
        except Exception as e:  # BrowserCookieError, PermissionError, sqlite errors, keychain denial
            msg = str(e).strip()[:120] or e.__class__.__name__
            low = msg.lower()
            if name == "safari" and ("find" in low or "permission" in low or "denied" in low):
                msg += f" - {SAFARI_HINT}"
            elif name in ("chrome", "chromium", "edge", "brave", "vivaldi", "opera") and ("decrypt" in low or "key" in low or "keychain" in low or "security" in low):
                msg += f" - {KEYCHAIN_HINT}"
            errors.append(f"{name}: {msg}")
            continue
        # most specific domain wins when two cookies share a name (canvas.school.edu beats .school.edu)
        found: dict = {}
        for c in sorted(jar, key=lambda c: len((c.domain or "").lstrip("."))):
            if _domain_matches(c.domain, host):
                found[c.name] = c.value
        found = filter_cookies(found)
        if has_session(found):
            return found, name
        errors.append(f"{name}: no Canvas session cookie for {host} ({PROFILE_HINT})")
    raise CanvasSessionError(
        f"No Canvas login found for {host}. Sign in to Canvas in your browser, then sync again.\n  "
        + "\n  ".join(errors))


def cookies_from_string(raw: str) -> dict:
    """Parse a pasted Cookie header ('canvas_session=...; _csrf_token=...') or a bare canvas_session value."""
    raw = (raw or "").strip()
    if raw.lower().startswith("cookie:"):
        raw = raw[7:].strip()
    if not raw:
        return {}
    if "=" not in raw:
        return {SESSION_COOKIE: raw}
    out = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return filter_cookies(out)


def cookie_path(home: Path) -> Path:
    return Path(home) / "canvas_cookies.json"


def save_cookies(home: Path, cookies: dict, source: str) -> Path:
    payload = {"cookies": filter_cookies(cookies), "source": source, "saved_at": datetime.now(timezone.utc).isoformat()}
    return cfgmod.write_private(cookie_path(home), json.dumps(payload))


def load_cookies(home: Path) -> dict | None:
    p = cookie_path(home)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if has_session(data.get("cookies")) else None


def clear_cookies(home: Path):
    p = cookie_path(home)
    if p.exists():
        p.unlink()
