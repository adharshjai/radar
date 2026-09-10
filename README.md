# Radar

One local dashboard for **everything you could miss** as a student:

- **Canvas** – every active course: assignments, quizzes, graded discussions, missing work, announcements,
  calendar events, planner notes, page to-dos, module unlock dates, module requirements you haven't completed,
  new files, and the Canvas inbox.
- **LabFlow, Gradescope, WebAssign, Piazza, Pearson, zyBooks…** – every external tool and website linked
  from your courses (navigation tabs, installed LTI tools, module links, syllabus/assignment/announcement links).
  Assignments that live on those platforms are tagged, and Radar reminds you to open platforms it can't see inside.
- **Syllabi** – the Canvas syllabus page, the course front page, schedule-like pages, and any syllabus
  PDF/Word file in course Files are read; exam dates and deadlines become tasks (Claude extracts them
  precisely when AI is on; a regex fallback still catches dated lines when it's off).
- **Outlook** – mail and calendar through Microsoft Graph (or the Outlook account already in Apple Mail).
  Emails from instructors, TAs, the registrar, financial aid, etc. are scored, action items and dates are
  pulled out, and Canvas notification emails are collapsed into the Canvas item they duplicate.

Everything lands in a **Today** view (overdue → today → tomorrow → this week → later), a **Next 7 days** agenda,
a **New since your last look** feed, per-course pages with a syllabus digest, a **Sites** page for the
external platforms, and a plain-text daily brief. Mark things done, snooze them, add notes, add your own tasks.
A macOS notification fires when a sync discovers something new and urgent.

## The Mac app

`Radar.app` is a native SwiftUI app (macOS 26+) with a sidebar, menu bar item, Dock badge, native notifications,
a Settings window that connects Canvas and Outlook without touching a terminal, and launch-at-login. The Python
engine below ships inside the bundle (`Contents/Resources/engine`), so the app needs no Python install.

```bash
./scripts/build_app.sh          # PyInstaller engine + swift build -> dist/Radar.app  (~3 min first time)
open dist/Radar.app             # or: RADAR_DEMO=1 open dist/Radar.app  for sample data
```

Requirements to build: macOS 26, Xcode Command Line Tools (Swift 6.2), Python 3.11+ with `pip install -r requirements.txt pyinstaller`.
Drag `dist/Radar.app` to /Applications. The app is ad-hoc signed; on first launch macOS may ask you to confirm opening it.
To hack on the app itself, `cd RadarApp && swift build && .build/debug/Radar` runs it against the checked-out
engine (`python3 -m radar`) instead of the bundled one.

## Quick start (engine on its own)

```bash
cd ~/Claude/radar
pip install -r requirements.txt        # requests, flask, msal, pypdf (+ anthropic for AI, pytest)
python3 -m radar setup                 # Canvas token → Outlook sign-in → first sync
python3 -m radar serve                 # opens http://127.0.0.1:5151 and auto-syncs every 30 min
```

Want to see it before connecting anything?

```bash
python3 -m radar demo                  # sample courses/mail on http://127.0.0.1:5152
```

### Connecting Canvas (three ways)

The Canvas REST API always needs proof that you are you; there is no anonymous mode. Pick whichever your school allows:

1. **Reuse your browser login (default).** Since 2025 schools can tick *Restrict students from creating personal
   access tokens*, which removes *+ New Access Token*. The Canvas web UI talks to the very same `/api/v1` with your
   session cookie (`canvas_session` on Instructure-hosted Canvas, `_normandy_session` on self-hosted), so Radar
   can too. `setup` (or `python3 -m radar login canvas`) reads that cookie from Chrome, Edge, Brave, Arc, Firefox
   or Safari, verifies it against `/api/v1/users/self`, and stores it in `~/.radar/canvas_cookies.json` (mode 600).
   Radar only ever sends it to your Canvas host, over https, for GET requests. When the session expires (Canvas
   sessions are typically about a day; logging out or a password change ends them too), the next sync re-imports it
   from the browser, so all you do is stay logged in to Canvas in your browser.
   `--browser chrome` pins one browser; `--paste` lets you paste the cookie from DevTools instead (empty answer =
   read it from the clipboard). Chrome-family browsers trigger a macOS Keychain prompt ("security wants to use
   Chrome Safe Storage") - click *Always Allow* (if you ever dismissed it, macOS remembers a *deny*: fix it in
   Keychain Access → "Chrome Safe Storage" → Access Control). Safari needs *Full Disk Access* for your terminal
   app. Firefox needs nothing. Arc and Dia keep their cookie key in Apple Passwords, which no tool can read - use
   `--paste` there. Several Chrome profiles? Point `[canvas] cookie_file` at that profile's `Cookies` file.
   Radar saves the refreshed cookie after every sync, so its copy stays alive as long as syncs keep running.
2. **Personal access token.** If *Account → Settings → Approved Integrations → + New Access Token* still exists
   for you, it works too - but since mid-2026 student tokens expire after 30 days, so you will re-paste one monthly.
   Schools can also mint a token *for* you (an admin with *Users - Manage Access Tokens* creates it, you activate
   it); some universities have a form for instructor-sponsored requests. Stored in `~/.radar/config.toml` (mode 600).
3. **Feeds only (no login at all).** Canvas → *Calendar → Calendar Feed* gives a private `.ics` URL
   (`/feeds/calendars/user_….ics`) with every dated assignment and event across your courses (30 days back, a year
   ahead). Each course's *Announcements* page → *External Feeds* → *RSS Feed* gives a
   `/feeds/announcements/enrollment_….atom` URL (newest 15). Put them in `[canvas] ics_url` /
   `announcement_feeds`; Radar also derives the per-user activity feed (`/feeds/users/…`, last 7 days of
   announcements, discussions, assignments and pages in every course) from the calendar feed's code, and the
   per-course feed from each announcement feed. Feeds can't see submission status, grades, modules, files, syllabi,
   planner notes or the inbox, so use them as a backup alongside 1 or 2: Radar always fetches them if set, and never
   lets a feed entry overwrite richer API data. Treat the URLs as passwords - they can't be rotated by a student.

None of these need your school's approval. Radar only ever reads. `python3 -m radar login canvas --logout` forgets
the saved session and switches Canvas API access off until you log in again.

### Outlook

`setup` (or `python3 -m radar login outlook`) starts a Microsoft **device-code sign-in**: you open the printed
URL, type the code, approve. Radar asks for `Mail.Read`, `Calendars.Read`, `User.Read` only. The refresh token is cached
in `~/.radar/msal_cache.json`.

It uses Microsoft's own public *Graph Command Line Tools* client ID, which most university tenants allow.
If your school blocks it you'll see an `AADSTS…` error; then either:

- ask IT to allow the Microsoft Graph Command Line Tools app, or register your own app (Azure Portal →
  App registrations → New → public client, redirect `https://login.microsoftonline.com/common/oauth2/nativeclient`,
  permissions Mail.Read / Calendars.Read / User.Read) and paste its client ID into `[outlook] client_id`, **or**
- set `[applemail] enabled = true` – Radar then reads the Outlook account you already added to Apple Mail
  (Mail.app has to be running and synced).

### AI enrichment (optional, recommended)

If the `anthropic` package is installed and credentials resolve (`ANTHROPIC_API_KEY`, or `ant auth login`),
each sync sends *new* emails, announcements, inbox messages and changed syllabi to Claude (`claude-opus-5` by
default) to extract action items, deadlines, exams, weekly schedules and policies. Results are cached per item,
so re-syncs cost nothing. Turn it off with `[ai] enabled = false` or `RADAR_AI=0`; heuristics still run.

## Commands

| Command | What it does |
|---|---|
| `python3 -m radar setup` | Interactive first-time setup |
| `python3 -m radar sync [--source canvas\|outlook\|applemail] [--no-ai] [--notify]` | Pull everything now |
| `python3 -m radar today [--ai]` | Print the daily brief (add `--ai` for a short narrative) |
| `python3 -m radar serve [--port N] [--no-open]` | Dashboard + background auto-sync |
| `python3 -m radar demo` | Dashboard with sample data |
| `python3 -m radar login canvas [--browser chrome] [--paste] [--logout]` | Import (or forget) the Canvas session from your browser |
| `python3 -m radar login outlook [--logout]` | (Re)authenticate Microsoft |
| `python3 -m radar install-schedule` | launchd agent: start at login, keep syncing (`uninstall-schedule` removes it) |

`./radar.sh <cmd>` is a shortcut for `python3 -m radar <cmd>`. `pip install -e .` gives you a `radar` command.

## Config (`~/.radar/config.toml`)

```toml
[canvas]
base_url = "https://canvas.youruniversity.edu"
auth = "auto"               # auto | token | browser  (auto = token if set, else the browser session)
token = ""                  # personal access token, if your school allows one
browser = ""                # chrome | safari | firefox | edge | brave | arc ; empty = try them all
ics_url = ""                # Calendar > Calendar Feed (.ics) - optional, login-free backup
announcement_feeds = []     # per-course announcement .atom URLs - optional
lookback_days = 45          # announcements / files / mail window
lookahead_days = 90
include_courses = []        # Canvas course ids; empty = all active
exclude_courses = []
fetch_files = true          # list recent course files
parse_syllabus_files = true # download + read syllabus PDF/DOCX

[outlook]
enabled = true
client_id = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
tenant = "organizations"
lookback_days = 14
calendar = true
folders = ["inbox"]

[applemail]
enabled = false
account = ""                # Mail.app account name; blank = unified inbox

[ai]
enabled = true
model = "claude-opus-5"

[profile]
university_domains = ["youruniversity.edu"]   # learned from Canvas automatically if empty
extra_keywords = []                            # e.g. ["scholarship", "Honors"] to boost matching mail

[server]
port = 5151
sync_every_minutes = 30
notify = true
```

Environment overrides: `CANVAS_BASE_URL`, `CANVAS_TOKEN`, `RADAR_HOME` (data directory), `RADAR_AI=0`.

## How "don't miss anything" works

| Signal | Where it comes from | What Radar does |
|---|---|---|
| Missing / past-due work | Canvas `missing_submissions` + submission state | red **Overdue** bucket; external-tool assignments with no grade passback are shown as *Past due – verify on LabFlow* |
| Due dates | assignments, quizzes, discussions, planner notes, page to-dos, module requirements | today / tomorrow / week / later buckets, 14-day workload bars, 7-day agenda |
| Exams and deadlines that are **only in the syllabus** | syllabus page, front page, schedule pages, syllabus PDF/DOCX | dated tasks (deduped against real Canvas assignments on the same day) |
| Things happening on other websites | LTI tools, nav tabs, module links, links in syllabus/announcements/assignments | Sites page + "Check LabFlow" reminders every N days (opening a site from Radar records the visit) |
| Instructor / school mail that needs action | Outlook, scored by sender domain, instructor name, course code, action words, dates; AI when enabled | tasks with a *mentions Fri* soft date, action items listed on the card |
| Announcements, inbox, new files, grades | Canvas | **New since your last look** feed and Recently graded |
| Changes | fingerprint per item | *updated* badge, previous due date kept |

## Files

```
RadarApp/         native SwiftUI Mac app (Package.swift; Sources/Radar)
scripts/          build_app.sh · radar-engine.spec (PyInstaller) · make_icon.py · Info.plist
radar/            engine package
  cli.py          commands
  sync.py         orchestration: sources → store → enrichment → notify
  sources/        canvas.py · outlook.py · applemail.py · platforms.py
  syllabus.py     syllabus text → dated items          dates.py  date/time extraction
  mailrules.py    email relevance/action heuristics    enrich/ai.py  Claude calls (structured outputs)
  triage.py       buckets, priority, dedupe            brief.py  text brief
  web.py          Flask app · templates/ · static/   api.py  JSON API used by the Mac app
demo/fixtures.py  realistic sample Canvas + Outlook payloads (also used by the tests)
tests/            pytest suite (python3 -m pytest)
```

Data lives in `~/.radar/radar.db` (SQLite). Delete it to start fresh; your token/config stay.

## Limits worth knowing

- Browser-session mode depends on the cookie your browser holds: if you log out of Canvas, or the school's
  session limit hits, the next sync re-imports it (and tells you to log in again if the browser has no session).
  Schools using Canvas's own MFA (not Duo/SSO) bind sessions to your IP since Aug 2026, so a VPN toggle can end
  the session; just open Canvas again. Cookie-mode API calls share your own browser's rate-limit bucket, so Radar
  runs requests one at a time and slows down when Canvas says the bucket is low.
- Radar sees what the Canvas API exposes to you. It cannot read *inside* LabFlow or other LTI tools –
  LabFlow work that posts to the Canvas gradebook shows up as an assignment; anything else is covered by the
  site-check reminder. (A browser-session scraper for LabFlow could be added later.)
- Canvas tokens do not expire unless you set an expiry; Microsoft refresh tokens last ~90 days of inactivity.
- Everything runs on your Mac and talks only to your Canvas, Microsoft Graph, and (optionally) Anthropic.
