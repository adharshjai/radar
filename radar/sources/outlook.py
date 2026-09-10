"""Outlook via Microsoft Graph (device-code sign-in, token cached under ~/.radar)."""
from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import requests

from ..models import Item, html_to_text, parse_dt, utcnow

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Read", "Calendars.Read", "User.Read"]


class OutlookError(RuntimeError):
    pass


class OutlookSource:
    def __init__(self, cfg: dict, cache_path: Path, log=None):
        self.cfg = cfg
        self.cache_path = Path(cache_path)
        self.log = log or (lambda *a: None)
        self._app = None
        self._cache = None

    # -- auth ------------------------------------------------------------------
    def app(self):
        if self._app is None:
            import msal
            self._cache = msal.SerializableTokenCache()
            if self.cache_path.exists():
                try:
                    self._cache.deserialize(self.cache_path.read_text())
                except Exception:
                    pass
            authority = f"https://login.microsoftonline.com/{self.cfg.get('tenant') or 'organizations'}"
            self._app = msal.PublicClientApplication(self.cfg["client_id"], authority=authority, token_cache=self._cache)
        return self._app

    def _persist(self):
        if self._cache is not None and self._cache.has_state_changed:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(self._cache.serialize())
            try:
                os.chmod(self.cache_path, 0o600)
            except OSError:
                pass

    def accounts(self):
        return self.app().get_accounts()

    def token(self, interactive: bool = False, print_fn=print) -> str | None:
        app = self.app()
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(SCOPES, account=accounts[0])
            if result and "access_token" in result:
                self._persist()
                return result["access_token"]
        if not interactive:
            return None
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise OutlookError(f"Could not start device sign-in: {json.dumps(flow, indent=2)}")
        print_fn("\n" + flow["message"] + "\n")
        result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            err = result.get("error_description") or result.get("error") or "unknown error"
            hint = ""
            if "AADSTS65001" in err or "consent" in err.lower() or "AADSTS7000218" in err or "AADSTS700016" in err:
                hint = ("\nYour school tenant did not accept this client_id. Ask IT, or register your own app "
                        "(Azure Portal > App registrations > New; public client; redirect https://login.microsoftonline.com/common/oauth2/nativeclient; "
                        "API permissions Mail.Read, Calendars.Read, User.Read) and put its Application (client) ID in ~/.radar/config.toml under [outlook].")
            raise OutlookError(f"Sign-in failed: {err}{hint}")
        self._persist()
        return result["access_token"]

    def start_device_flow(self) -> dict:
        flow = self.app().initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise OutlookError(f"Could not start device sign-in: {json.dumps(flow, indent=2)}")
        return flow

    def finish_device_flow(self, flow: dict) -> str:
        result = self.app().acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            err = result.get("error_description") or result.get("error") or "unknown error"
            hint = ""
            if "AADSTS65001" in err or "consent" in err.lower() or "AADSTS7000218" in err or "AADSTS700016" in err:
                hint = (" Your school tenant did not accept this client_id; ask IT to allow 'Microsoft Graph Command Line Tools' "
                        "or register your own app and put its client ID in Settings.")
            raise OutlookError(f"Sign-in failed: {err}{hint}")
        self._persist()
        return result["access_token"]

    def logout(self):
        app = self.app()
        for a in app.get_accounts():
            app.remove_account(a)
        self._persist()

    # -- fetch -------------------------------------------------------------------
    def _get(self, token: str, url: str, params=None, headers=None):
        h = {"Authorization": f"Bearer {token}"}
        h.update(headers or {})
        r = requests.get(url, params=params, headers=h, timeout=40)
        if r.status_code == 401:
            raise OutlookError("Microsoft rejected the token; run: python3 -m radar login outlook")
        if r.status_code >= 400:
            raise OutlookError(f"Graph {r.status_code}: {r.text[:300]}")
        return r.json()

    def _paged(self, token: str, url: str, params=None, headers=None, limit=400):
        out = []
        while url and len(out) < limit:
            data = self._get(token, url, params, headers)
            out.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            params = None
        return out[:limit]

    def fetch(self, now=None) -> tuple[dict, list[Item]]:
        now = now or utcnow()
        token = self.token(interactive=False)
        if not token:
            raise OutlookError("Not signed in to Outlook. Run: python3 -m radar login outlook")
        me = self._get(token, f"{GRAPH}/me", params={"$select": "displayName,mail,userPrincipalName"})
        items: list[Item] = []
        since = now - timedelta(days=int(self.cfg.get("lookback_days", 14)))
        folders = self.cfg.get("folders") or ["inbox"]
        max_messages = int(self.cfg.get("max_messages", 400))
        select = ("id,subject,from,receivedDateTime,bodyPreview,body,isRead,webLink,importance,flag,"
                  "hasAttachments,categories,conversationId,inferenceClassification,toRecipients")
        for folder in folders:
            msgs = self._paged(token, f"{GRAPH}/me/mailFolders/{folder}/messages", params={
                "$filter": f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}",
                "$orderby": "receivedDateTime desc", "$top": 50, "$select": select,
            }, headers={"Prefer": 'outlook.body-content-type="text"'}, limit=max_messages)
            for m in msgs:
                items.append(map_message(m, folder))
        self.log(f"{len(items)} Outlook messages")

        if self.cfg.get("calendar", True):
            start = now - timedelta(days=1)
            end = now + timedelta(days=45)
            events = self._paged(token, f"{GRAPH}/me/calendarView", params={
                "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "$top": 100, "$orderby": "start/dateTime",
                "$select": "id,subject,start,end,location,webLink,isAllDay,bodyPreview,organizer,showAs,isCancelled,seriesMasterId",
            }, headers={"Prefer": 'outlook.timezone="UTC"'}, limit=300)
            for e in events:
                if e.get("isCancelled"):
                    continue
                items.append(map_calendar(e))
            self.log(f"{len(events)} Outlook calendar entries")
        return me, items


def map_message(m: dict, folder: str = "inbox") -> Item:
    frm = (m.get("from") or {}).get("emailAddress") or {}
    body = m.get("body") or {}
    text = body.get("content") or m.get("bodyPreview") or ""
    if (body.get("contentType") or "").lower() == "html":
        text = html_to_text(text)
    flag = (m.get("flag") or {}).get("flagStatus")
    return Item(
        uid=f"outlook:msg:{m['id']}", source="outlook", kind="email", title=m.get("subject") or "(no subject)",
        url=m.get("webLink"), status="unread" if not m.get("isRead") else "read",
        body=text[:6000], sender=f"{frm.get('name') or ''} <{frm.get('address') or ''}>".strip(),
        posted_at=parse_dt(m.get("receivedDateTime")),
        extra={
            "from_name": frm.get("name"), "from_addr": frm.get("address"), "flagged": flag == "flagged",
            "importance": m.get("importance"), "focused": (m.get("inferenceClassification") or "") == "focused",
            "has_attachments": bool(m.get("hasAttachments")), "categories": m.get("categories") or [],
            "conversation_id": m.get("conversationId"), "folder": folder,
        },
    )


def map_calendar(e: dict) -> Item:
    loc = (e.get("location") or {}).get("displayName")
    org = ((e.get("organizer") or {}).get("emailAddress") or {}).get("name")
    return Item(
        uid=f"outlook:cal:{e['id']}", source="outlook", kind="calendar", title=e.get("subject") or "(untitled event)",
        starts_at=parse_dt((e.get("start") or {}).get("dateTime")), ends_at=parse_dt((e.get("end") or {}).get("dateTime")),
        url=e.get("webLink"), status="info", body=(e.get("bodyPreview") or "")[:1000], sender=org,
        extra={"location": loc, "all_day": bool(e.get("isAllDay")), "show_as": e.get("showAs")},
    )
