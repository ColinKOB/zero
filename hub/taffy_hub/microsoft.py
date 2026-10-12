"""Outlook mail and calendar through Microsoft Graph, with the sign-in in oauth.py and no model in between.

Read only: the inbox's latest mail, a search, one message's text, and the calendar's events in a window. Zero asks for
Mail.Read and Calendars.Read (and Files.Read, User.Read, offline_access) and nothing that sends or changes. Mail and
invitations come from other people: the plugin tells the Zip never to follow instructions in them.

Tests point GRAPH at a local fake.
"""

import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import home, oauth

GRAPH = "https://graph.microsoft.com/v1.0"
MAIL_FIELDS = "id,subject,from,receivedDateTime,bodyPreview,isRead,webLink"
EVENT_FIELDS = "id,subject,start,end,location,organizer,isAllDay,webLink,isCancelled"
LIMIT_MAX = 50
TEXT_MAX = 30000
ID = re.compile(r"^[A-Za-z0-9=_+/-]{10,400}$")


def _clean(v, limit: int = 300) -> str:
    return " ".join(str(v or "").split())[:limit]


def _get(path: str, params: dict | None = None, headers: dict | None = None, token: str | None = None) -> dict:
    return oauth.request("outlook", "GET", f"{GRAPH}{path}", token=token, params=params, headers=headers).json()


def me(token: str | None = None) -> str:
    m = _get("/me", {"$select": "displayName,mail,userPrincipalName"}, token=token)
    return _clean(m.get("mail") or m.get("userPrincipalName") or m.get("displayName"), 120)


def _who(r: dict | None) -> str:
    a = (r or {}).get("emailAddress") or {}
    n, addr = _clean(a.get("name"), 80), _clean(a.get("address"), 120)
    return f"{n} <{addr}>" if n and addr and n != addr else n or addr


def _message(m: dict) -> dict:
    return {"id": m.get("id"), "subject": _clean(m.get("subject"), 300), "from": _who(m.get("from")),
            "received": m.get("receivedDateTime"), "unread": m.get("isRead") is False,
            "preview": _clean(m.get("bodyPreview"), 300), "link": m.get("webLink")}


def _limit(n, default: int = 20) -> int:
    try:
        return max(1, min(int(n or default), LIMIT_MAX))
    except (TypeError, ValueError):
        return default


def mail(query: str = "", limit: int = 20) -> list[dict]:
    """The inbox's latest mail, or every folder's mail matching `query` (Outlook's own search)."""
    q = _clean(query, 200).replace('"', "")
    if q:
        rows = _get("/me/messages", {"$search": f'"{q}"', "$top": _limit(limit), "$select": MAIL_FIELDS})
    else:
        rows = _get("/me/mailFolders/inbox/messages", {"$top": _limit(limit), "$select": MAIL_FIELDS,
                                                       "$orderby": "receivedDateTime desc"})
    return [_message(m) for m in rows.get("value") or []]


def message(ref: str, max_chars: int = TEXT_MAX) -> dict:
    """One message with its text."""
    mid = str(ref or "").strip()
    if not ID.match(mid):
        raise oauth.OAuthError("Give the message's id from mail.")
    m = _get(f"/me/messages/{quote(mid, safe='')}",
             {"$select": MAIL_FIELDS + ",toRecipients,ccRecipients,body"},
             headers={"Prefer": 'outlook.body-content-type="text"'})
    cap = max(500, min(int(max_chars or TEXT_MAX), TEXT_MAX))
    text = str((m.get("body") or {}).get("content") or "")
    out = _message(m)
    out.pop("preview", None)
    out.update(to=[_who(r) for r in m.get("toRecipients") or []][:20],
               cc=[_who(r) for r in m.get("ccRecipients") or []][:20], text=text[:cap], truncated=len(text) > cap)
    return out


def _iso(v: str, default: datetime) -> str:
    s = str(v or "").strip()
    if not s:
        return default.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise oauth.OAuthError("Give start and end as dates or times in ISO 8601, for example 2026-10-14.") from None
    if d.tzinfo is None:
        d = d.replace(tzinfo=home.zone())
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def events(start: str = "", end: str = "", limit: int = 50) -> list[dict]:
    """The calendar's events from `start` to `end` (default: now to seven days on), in the home's time zone."""
    now = datetime.fromtimestamp(time.time(), timezone.utc)
    s, e = _iso(start, now), _iso(end, now + timedelta(days=7))
    rows = _get("/me/calendarView", {"startDateTime": s, "endDateTime": e, "$top": _limit(limit, 50),
                                     "$orderby": "start/dateTime", "$select": EVENT_FIELDS},
                headers={"Prefer": f'outlook.timezone="{home.timezone_name()}"'})
    out = []
    for ev in rows.get("value") or []:
        if ev.get("isCancelled"):
            continue
        out.append({"id": ev.get("id"), "title": _clean(ev.get("subject"), 300),
                    "start": (ev.get("start") or {}).get("dateTime"), "end": (ev.get("end") or {}).get("dateTime"),
                    "all_day": bool(ev.get("isAllDay")), "where": _clean((ev.get("location") or {}).get("displayName"), 200),
                    "organizer": _who(ev.get("organizer")), "link": ev.get("webLink")})
    return out
