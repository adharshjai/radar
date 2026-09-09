"""Fallback mail source: read the Outlook account already configured in Apple Mail (via JXA)."""
from __future__ import annotations

import json
import subprocess
from datetime import timedelta

from ..models import Item, parse_dt, utcnow

JXA = r"""
function run(argv) {
  const account = argv[0] || "";
  const sinceMs = parseInt(argv[1], 10);
  const maxN = parseInt(argv[2], 10);
  const Mail = Application("Mail");
  let mailbox;
  if (account) {
    const acct = Mail.accounts.byName(account);
    mailbox = acct.mailboxes.byName("INBOX");
  } else {
    mailbox = Mail.inbox();
  }
  const since = new Date(sinceMs);
  const msgs = mailbox.messages.whose({dateReceived: {_greaterThan: since}});
  const ids = msgs.id();
  const subjects = msgs.subject();
  const senders = msgs.sender();
  const dates = msgs.dateReceived();
  const reads = msgs.readStatus();
  const flagged = msgs.flaggedStatus();
  const mids = msgs.messageId();
  const out = [];
  const n = Math.min(ids.length, maxN);
  for (let i = 0; i < n; i++) {
    let content = "";
    try { content = msgs[i].content().slice(0, 6000); } catch (e) {}
    out.push({id: ids[i], message_id: mids[i], subject: subjects[i], sender: senders[i],
              date: dates[i] ? dates[i].toISOString() : null, read: reads[i], flagged: flagged[i], content: content});
  }
  return JSON.stringify(out);
}
"""


class AppleMailError(RuntimeError):
    pass


class AppleMailSource:
    def __init__(self, cfg: dict, log=None):
        self.cfg = cfg
        self.log = log or (lambda *a: None)

    def fetch(self, now=None) -> list[Item]:
        now = now or utcnow()
        since = now - timedelta(days=int(self.cfg.get("lookback_days", 14)))
        args = ["osascript", "-l", "JavaScript", "-e", JXA, "--", self.cfg.get("account") or "",
                str(int(since.timestamp() * 1000)), str(int(self.cfg.get("max_messages", 300)))]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
        except FileNotFoundError as e:
            raise AppleMailError("osascript not found; Apple Mail source only works on macOS") from e
        except subprocess.TimeoutExpired as e:
            raise AppleMailError("Apple Mail took too long to answer (is Mail.app running with the account synced?)") from e
        if proc.returncode != 0:
            raise AppleMailError(f"Apple Mail error: {proc.stderr.strip()[:400]}")
        try:
            rows = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError as e:
            raise AppleMailError("Could not parse Apple Mail output") from e
        items = [map_apple_message(r) for r in rows]
        self.log(f"{len(items)} Apple Mail messages")
        return items


def map_apple_message(r: dict) -> Item:
    sender = r.get("sender") or ""
    name, addr = sender, ""
    if "<" in sender and sender.endswith(">"):
        name, addr = sender.rsplit("<", 1)
        name, addr = name.strip().strip('"'), addr[:-1].strip()
    elif "@" in sender:
        addr = sender.strip()
        name = addr.split("@")[0]
    mid = r.get("message_id") or r.get("id")
    return Item(
        uid=f"applemail:msg:{mid}", source="applemail", kind="email", title=r.get("subject") or "(no subject)",
        url=f"message://%3C{mid}%3E" if r.get("message_id") else None,
        status="read" if r.get("read") else "unread", body=(r.get("content") or "")[:6000],
        sender=f"{name} <{addr}>".strip(), posted_at=parse_dt(r.get("date")),
        extra={"from_name": name, "from_addr": addr, "flagged": bool(r.get("flagged")), "folder": "inbox"},
    )
