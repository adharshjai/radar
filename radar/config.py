"""Configuration: ~/.radar/config.toml plus environment overrides."""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path


CMD = "python3 -m radar"          # how we tell the user to run things (stock macOS has no `python`)


def home() -> Path:
    return Path(os.environ.get("RADAR_HOME", Path.home() / ".radar")).expanduser()


def config_path() -> Path:
    return home() / "config.toml"


def db_path() -> Path:
    return home() / "radar.db"


DEFAULTS: dict = {
    "canvas": {
        "base_url": "",
        "token": "",
        "auth": "auto",             # auto | token | browser | off  (browser = reuse the logged-in session cookies)
        "browser": "",              # chrome | firefox | safari | edge | brave ; empty = try them all
        "cookie_file": "",          # optional: a specific browser profile's Cookies file (multi-profile Chrome)
        "ics_url": "",              # Calendar > Calendar Feed URL (works with no login at all)
        "announcement_feeds": [],   # per-course announcement RSS/Atom URLs
        "lookback_days": 45,
        "lookahead_days": 90,
        "include_courses": [],
        "exclude_courses": [],
        "fetch_files": True,
        "parse_syllabus_files": True,
    },
    "outlook": {
        "enabled": False,
        # Microsoft Graph Command Line Tools - a Microsoft-owned public client that most
        # tenants already allow. Replace with your own app registration if your school blocks it.
        "client_id": "14d82eec-204b-4c2f-b7e8-296a70dab67e",
        "tenant": "organizations",
        "lookback_days": 14,
        "calendar": True,
        "folders": ["inbox"],
        "max_messages": 400,
    },
    "applemail": {
        "enabled": False,
        "account": "",
        "lookback_days": 14,
        "max_messages": 300,
    },
    "ai": {
        "enabled": True,
        "model": "claude-opus-5",
        "api_key": "",              # optional; otherwise ANTHROPIC_API_KEY / `ant auth login` is used
        "max_items_per_sync": 80,
    },
    "profile": {
        "name": "",
        "email": "",
        "university_domains": [],
        "extra_keywords": [],
    },
    "server": {
        "port": 5151,
        "sync_every_minutes": 30,
        "notify": True,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    p = config_path()
    if p.exists():
        with p.open("rb") as f:
            cfg = _merge(cfg, tomllib.load(f))
    env = os.environ
    if env.get("CANVAS_BASE_URL"):
        cfg["canvas"]["base_url"] = env["CANVAS_BASE_URL"]
    if env.get("CANVAS_TOKEN"):
        cfg["canvas"]["token"] = env["CANVAS_TOKEN"]
    if env.get("RADAR_AI") in ("0", "false", "off"):
        cfg["ai"]["enabled"] = False
    cfg["canvas"]["base_url"] = (cfg["canvas"]["base_url"] or "").rstrip("/")
    return cfg


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def dump_toml(cfg: dict) -> str:
    lines = []
    for section, values in cfg.items():
        if not isinstance(values, dict):
            continue
        lines.append(f"[{section}]")
        for k, v in values.items():
            lines.append(f"{k} = {_toml_value(v)}")
        lines.append("")
    return "\n".join(lines)


def write_private(path: Path, text: str) -> Path:
    """Write a file that is 0600 from the moment it exists (no world-readable window)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def save(cfg: dict) -> Path:
    return write_private(config_path(), dump_toml(cfg))
