"""Command-line entry points: setup, login, sync, today, serve, demo, install-schedule."""
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import config as cfgmod


def _store():
    from .store import Store
    return Store(cfgmod.db_path())


def cmd_setup(args):
    cfg = cfgmod.load()
    print("Radar setup — everything is stored under", cfgmod.home())
    print()
    print("1) Canvas")
    base = input(f"   Canvas URL [{cfg['canvas']['base_url'] or 'https://canvas.youruniversity.edu'}]: ").strip() or cfg["canvas"]["base_url"]
    if not base.startswith("http"):
        base = "https://" + base
    cfg["canvas"]["base_url"] = base.rstrip("/")
    has_token = bool(cfg["canvas"].get("token"))
    default = "2" if has_token else "1"
    print("   How should Radar sign in?")
    print("     [1] Reuse the Canvas login from your browser (works even when your school hides 'New Access Token')")
    print(f"     [2] Personal access token (Account > Settings > Approved Integrations > + New Access Token){' - currently set' if has_token else ''}")
    print("     [3] Paste the session cookie by hand")
    how = input(f"   Choice [{default}]: ").strip() or default
    client = None
    if how == "2":
        token = getpass.getpass("   Paste the access token (hidden; Enter keeps the current one): ").strip() or cfg["canvas"]["token"]
        if token:
            from .sources.canvas import CanvasClient
            client = CanvasClient(base, token)
            if _canvas_hello(cfg, client):
                cfg["canvas"]["token"] = token
                cfg["canvas"]["auth"] = "token"
    else:
        client = _canvas_browser_login(cfg, paste=(how == "3"))
        if client is not None and _canvas_hello(cfg, client):
            cfg["canvas"]["token"] = ""           # only now: the browser session is proven to work
            cfg["canvas"]["auth"] = "browser"
        elif has_token:
            print("   Keeping your existing token setup.")
    print()
    print("   Optional, token-free backup: Canvas > Calendar > 'Calendar Feed' gives a private .ics link.")
    ics = input(f"   Paste it here (or Enter to skip) [{'set' if cfg['canvas'].get('ics_url') else 'skip'}]: ").strip()
    if ics.startswith("http"):
        cfg["canvas"]["ics_url"] = ics
    print()
    print("2) Email — pick one: [1] Outlook via Microsoft sign-in (recommended)  [2] Apple Mail app  [3] skip")
    choice = (input("   Choice [1]: ").strip() or "1")
    if choice == "1":
        cfg["outlook"]["enabled"] = True
        cfg["applemail"]["enabled"] = False
        cfgmod.save(cfg)
        print("   Signing in to Microsoft…")
        try:
            from .sources.outlook import OutlookSource
            src = OutlookSource(cfg["outlook"], cfgmod.home() / "msal_cache.json")
            src.token(interactive=True)
            print("   ✓ Outlook connected")
        except Exception as e:
            print(f"   ✗ {e}\n   You can retry later with: python3 -m radar login outlook")
    elif choice == "2":
        cfg["applemail"]["enabled"] = True
        cfg["outlook"]["enabled"] = False
        acct = input("   Account name as shown in Mail.app (blank = unified inbox): ").strip()
        cfg["applemail"]["account"] = acct
    print()
    print("3) AI enrichment (reads your mail/syllabi with Claude to extract action items and exam dates)")
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    ans = input(f"   Enable? [{'Y' if has_key else 'y'}/n]: ").strip().lower()
    cfg["ai"]["enabled"] = ans != "n"
    if cfg["ai"]["enabled"] and not has_key:
        print("   Note: set ANTHROPIC_API_KEY (or run `ant auth login`) before syncing, otherwise Radar falls back to regex heuristics.")
    p = cfgmod.save(cfg)
    print(f"\nSaved {p}")
    if input("Run the first sync now? [Y/n]: ").strip().lower() != "n":
        cmd_sync(argparse.Namespace(source=None, no_ai=not cfg["ai"]["enabled"], notify=False, quiet=False))
        print("\nStart the dashboard with:  python3 -m radar serve")


def _canvas_hello(cfg: dict, client) -> bool:
    from .sources.canvas import CanvasError
    try:
        me = client.get_json("/users/self")
        print(f"   ✓ Connected to Canvas as {me.get('name')} ({me.get('primary_email') or me.get('login_id') or ''})")
        prof = client.get_json("/users/self/profile", optional=True) or {}
        email = prof.get("primary_email") or ""
        if "@" in email and not cfg["profile"].get("university_domains"):
            cfg["profile"]["university_domains"] = [email.split("@")[-1].lower()]
            cfg["profile"]["email"] = email
        cfg["profile"]["name"] = me.get("name") or ""
        return True
    except CanvasError as e:
        print(f"   ✗ {e}")
        return False


def _canvas_browser_login(cfg: dict, paste: bool = False, browser: str | None = None):
    """Import the Canvas session from a browser (or a pasted Cookie header) and build a client with it."""
    from .sources.canvas import CanvasClient
    from .sources.canvas_session import CanvasSessionError, cookies_from_browser, cookies_from_string, save_cookies
    base = cfg["canvas"]["base_url"]
    cookies, source = None, None
    if not paste:
        print(f"   Looking for a Canvas login for {base} in your browsers (macOS may ask to allow Keychain access - click Always Allow)…")
        try:
            cookies, source = cookies_from_browser(base, browser or cfg["canvas"].get("browser") or None)
            print(f"   Found a session in {source}.")
        except CanvasSessionError as e:
            print(f"   ✗ {e}")
            paste = True
    if paste:
        from .sources.canvas_session import has_session
        print("   Chrome/Edge/Brave: open Canvas, press ⌥⌘I > Application > Cookies > your Canvas site, copy the 'canvas_session' value.")
        print("   Safari: Develop menu > Show Web Inspector > Storage > Cookies.  Firefox: ⌥⌘I > Storage > Cookies.")
        print("   (Self-hosted Canvas may call it '_normandy_session'; copying the whole 'Cookie:' request header also works.)")
        raw = _read_secret("   Copy it, then press Enter here to read it from the clipboard (or paste it, hidden): ")
        cookies = cookies_from_string(raw)
        source = "pasted"
        if not has_session(cookies):
            print("   ✗ That did not contain a Canvas session cookie.")
            return None
    save_cookies(cfgmod.home(), cookies, source)
    if browser:
        cfg["canvas"]["browser"] = browser
    return CanvasClient(base, cookies=cookies)


def _read_secret(prompt: str) -> str:
    """Read a possibly very long secret without echo. macOS terminals drop input past 1024 bytes in
    canonical mode (which is what getpass uses), so read raw; an empty answer means 'use the clipboard'."""
    import subprocess
    import sys
    import termios
    import tty
    print(prompt, end="", flush=True)
    chars: list[str] = []
    if sys.stdin.isatty():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            new = termios.tcgetattr(fd)
            new[3] &= ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSANOW, new)
            while True:
                ch = sys.stdin.read(1)
                if ch in ("\n", "\r", ""):
                    break
                if ch == "\x03":
                    raise KeyboardInterrupt
                if ch in ("\x7f", "\b"):
                    if chars:
                        chars.pop()
                    continue
                chars.append(ch)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            print()
    else:
        chars = list(sys.stdin.readline().rstrip("\n"))
    value = "".join(chars).strip()
    if not value:
        try:
            value = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout.strip()
            if value:
                print("   (read from clipboard)")
        except Exception:
            value = ""
    return value


def cmd_login(args):
    cfg = cfgmod.load()
    if args.what == "canvas":
        if args.logout:
            from .sources.canvas_session import clear_cookies
            clear_cookies(cfgmod.home())
            cfg["canvas"]["auth"] = "token" if cfg["canvas"].get("token") else "off"
            cfgmod.save(cfg)
            if cfg["canvas"]["auth"] == "token":
                print("Forgot the saved Canvas session; Radar will use your access token from now on.")
            else:
                print(f"Forgot the saved Canvas session. Radar will NOT read Canvas again until you run: {cfgmod.CMD} login canvas")
            return
        if not cfg["canvas"]["base_url"]:
            base = input("Canvas URL: ").strip()
            cfg["canvas"]["base_url"] = ("https://" + base if not base.startswith("http") else base).rstrip("/")
        client = _canvas_browser_login(cfg, paste=args.paste, browser=args.browser)
        if client is not None and _canvas_hello(cfg, client):
            cfg["canvas"]["auth"] = "browser"
            cfg["canvas"]["token"] = ""
            cfgmod.save(cfg)
            if args.paste:
                print(f"Saved. A pasted session stops working when Canvas logs you out; then run  {cfgmod.CMD} login canvas --paste  again.")
            else:
                print("Saved. When the session expires, Radar re-imports it from your browser as long as you stay logged in to Canvas there.")
        else:
            sys.exit(1)
        return
    if args.what == "outlook":
        from .sources.outlook import OutlookSource
        cfg["outlook"]["enabled"] = True
        cfgmod.save(cfg)
        src = OutlookSource(cfg["outlook"], cfgmod.home() / "msal_cache.json")
        if args.logout:
            src.logout()
            print("Signed out of Outlook.")
            return
        src.token(interactive=True)
        print("✓ Outlook connected")


def cmd_sync(args):
    from .sync import run_sync
    cfg = cfgmod.load()
    sources = [args.source] if getattr(args, "source", None) else None
    log = (lambda *a: None) if getattr(args, "quiet", False) else print
    res = run_sync(cfg, _store(), sources=sources, log=log, use_ai=not getattr(args, "no_ai", False),
                   notify=getattr(args, "notify", False))
    for k, v in res.items():
        print(f"{k}: {'ok' if v.get('ok') else 'FAILED'} {v}")


def cmd_today(args):
    from .brief import render_brief
    from .triage import build_dashboard, local_tz
    cfg = cfgmod.load()
    dash = build_dashboard(_store(), cfg, now=datetime.now(local_tz()))
    text = render_brief(dash)
    if getattr(args, "ai", False):
        try:
            from .enrich.ai import Enricher
            e = Enricher(cfg["ai"])
            if e.available():
                narrative = e.brief(text)
                if narrative:
                    print(narrative)
                    print("-" * 60)
        except Exception as ex:
            print(f"(AI brief unavailable: {ex})")
    print(text)


def cmd_serve(args):
    from .web import serve
    cfg = cfgmod.load()
    serve(cfg, _store(), port=args.port, open_browser=not args.no_open)


def cmd_demo(args):
    """Load realistic sample data into a throwaway database and open the dashboard."""
    from demo.fixtures import load_demo
    demo_home = Path(os.environ.get("RADAR_DEMO_HOME", Path(__file__).resolve().parent.parent / "demo" / "home"))
    os.environ["RADAR_HOME"] = str(demo_home)
    if demo_home.exists() and not args.keep:
        shutil.rmtree(demo_home)
    demo_home.mkdir(parents=True, exist_ok=True)
    cfg = cfgmod.load()
    cfg["ai"]["enabled"] = False
    cfg["server"]["sync_every_minutes"] = 0
    store = _store()
    load_demo(store, cfg)
    print(f"Demo data loaded into {demo_home}")
    if args.brief:
        cmd_today(argparse.Namespace(ai=False))
        return
    from .web import serve
    serve(cfg, store, port=args.port, open_browser=not args.no_open, demo=True)


def cmd_install_schedule(args):
    """Write a launchd agent that keeps `radar serve` (and its auto-sync) running at login."""
    label = "com.radar.serve"
    home = cfgmod.home()
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parent.parent
    python = sys.executable
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    <string>{python}</string><string>-m</string><string>radar</string><string>serve</string><string>--no-open</string>
  </array>
  <key>WorkingDirectory</key><string>{repo}</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>{os.environ.get('PATH', '/usr/bin:/bin')}</string>
    {'<key>ANTHROPIC_API_KEY</key><string>' + os.environ['ANTHROPIC_API_KEY'] + '</string>' if os.environ.get('ANTHROPIC_API_KEY') and args.with_key else ''}
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{logs}/serve.log</string>
  <key>StandardErrorPath</key><string>{logs}/serve.err.log</string>
</dict></plist>
"""
    dest = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(plist)
    print(f"Wrote {dest}")
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(dest)], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        r = subprocess.run(["launchctl", "load", "-w", str(dest)], capture_output=True, text=True)
    if r.returncode == 0:
        print("Radar now starts at login and syncs every", cfgmod.load()["server"]["sync_every_minutes"], "minutes.")
        print(f"Dashboard: http://127.0.0.1:{cfgmod.load()['server']['port']}/   Logs: {logs}")
    else:
        print("launchctl failed:", r.stderr.strip(), "\nLoad it manually with: launchctl load -w", dest)


def cmd_uninstall_schedule(args):
    dest = Path.home() / "Library" / "LaunchAgents" / "com.radar.serve.plist"
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(dest)], capture_output=True)
    if dest.exists():
        dest.unlink()
    print("Removed launch agent.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="radar", description="Everything due, from Canvas + syllabi + Outlook, in one place.")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("setup", help="interactive first-time setup").set_defaults(fn=cmd_setup)
    l = sub.add_parser("login", help="sign in to a source (canvas via your browser's session, or outlook)")
    l.add_argument("what", choices=["canvas", "outlook"])
    l.add_argument("--logout", action="store_true")
    l.add_argument("--browser", choices=["chrome", "safari", "firefox", "edge", "brave", "arc", "chromium", "vivaldi", "opera"],
                   help="canvas: which browser holds your Canvas login (default: try them all)")
    l.add_argument("--paste", action="store_true", help="canvas: paste the canvas_session cookie instead of reading the browser")
    l.set_defaults(fn=cmd_login)
    s = sub.add_parser("sync", help="pull everything now")
    s.add_argument("--source", choices=["canvas", "outlook", "applemail"])
    s.add_argument("--no-ai", action="store_true", help="skip Claude enrichment")
    s.add_argument("--notify", action="store_true", help="macOS notification for new urgent items")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_sync)
    t = sub.add_parser("today", help="print the daily brief")
    t.add_argument("--ai", action="store_true", help="add a short Claude-written narrative on top")
    t.set_defaults(fn=cmd_today)
    v = sub.add_parser("serve", help="run the dashboard (auto-syncs in the background)")
    v.add_argument("--port", type=int)
    v.add_argument("--no-open", action="store_true")
    v.set_defaults(fn=cmd_serve)
    d = sub.add_parser("demo", help="dashboard with sample data (no accounts needed)")
    d.add_argument("--port", type=int, default=5152)
    d.add_argument("--no-open", action="store_true")
    d.add_argument("--keep", action="store_true", help="keep existing demo state")
    d.add_argument("--brief", action="store_true", help="print the text brief instead of serving")
    d.set_defaults(fn=cmd_demo)
    i = sub.add_parser("install-schedule", help="start at login + auto-sync (launchd)")
    i.add_argument("--with-key", action="store_true", help="bake the current ANTHROPIC_API_KEY into the launch agent")
    i.set_defaults(fn=cmd_install_schedule)
    sub.add_parser("uninstall-schedule").set_defaults(fn=cmd_uninstall_schedule)
    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 0
    try:
        return args.fn(args) or 0
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
