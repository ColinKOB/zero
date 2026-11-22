""








from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta

from . import home, store


CLEARED_SEC = 2 * 86400

_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                            "dec"))}
_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
_CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?|\b(noon|midnight)\b", re.I)
_RELATIVE = re.compile(r"\b(today|tonight|tomorrow)\b", re.I)


_CUE = re.compile(r"\b(by|due|expires?|expiring|expired|before|until|deadline|ends?|renew\w*|on)\b\W+(\w+\W+){0,2}$", re.I)
_SIGN_IN = re.compile(r"^sign in to (\w+)", re.I)
_POST = re.compile(r"\bposts?\b", re.I)

_FOLDED_REQUESTS = {"send": "emails", "publish": "posts"}


def _zone():
    return home.zone()


def _end_of_day(day: datetime) -> float:
    return day.replace(hour=23, minute=59, second=0, microsecond=0).timestamp()


def when_in(text: str, base: float, cue: bool = False) -> tuple[float, bool] | None:
    ""


    text = text or ""
    zone = _zone()
    written = datetime.fromtimestamp(base, zone)
    day = None
    for m in _DATE.finditer(text):
        if cue and not _CUE.search(text[:m.start()]):
            continue
        month, date = _MONTHS[m.group(1)[:3].lower()], int(m.group(2))
        for year in (written.year, written.year + 1):
            try:
                candidate = written.replace(year=year, month=month, day=date)
            except ValueError:
                candidate = None
            if candidate and candidate >= written - timedelta(days=183):
                day = candidate
                break
        if day:
            break
    if day is None and not cue:
        if m := _RELATIVE.search(text):
            day = written + timedelta(days=1 if m.group(1).lower() == "tomorrow" else 0)
    if day is None:
        return None
    clocks = list(_CLOCK.finditer(text))
    if len(clocks) == 1:
        c = clocks[0]
        if c.group(4):
            hour, minute = (12 if c.group(4).lower() == "noon" else 0), 0
        else:
            hour, minute = int(c.group(1)) % 12 + (12 if c.group(3).lower() == "p" else 0), int(c.group(2) or 0)
        if hour < 24:
            return day.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp(), True
    return _end_of_day(day), False


def _need(item: dict):
    need = (item.get("reference") or {}).get("need_id")
    if need is None:
        return None
    return store.db().execute("SELECT n.*, t.name AS team_name FROM team_needs n JOIN teams t ON t.id=n.team_id "
                              "WHERE n.id=?", (need,)).fetchone()


def _post(need) -> dict | None:
    try:
        items = json.loads(need["items"] or "[]")
    except ValueError:
        return None
    return next((it for it in items if isinstance(it, dict) and it.get("kind") == "post"), None) \
        if isinstance(items, list) else None


def _due(item: dict, need) -> tuple[float | None, bool]:
    kind, at = item.get("kind"), float(item.get("at") or time.time())
    if need is not None and need["due_at"]:
        return float(need["due_at"]), True
    if kind in ("deadline", "calendar", "meeting", "todo", "reminder") and item.get("date") is not None:
        return float(item["date"]), True
    if kind == "text" and item.get("date") is not None:
        return float(item["date"]), False
    if kind == "mail":
        return None, False
    found = when_in(item.get("ask") or "", at) or \
        when_in(item.get("title") or "", at, cue=kind != "team_need") or \
        when_in(item.get("short_title") or "", at, cue=True)
    return found if found else (None, False)


def due_at(item: dict) -> tuple[float | None, bool]:
    ""

    if pace_at(item) is not None:
        return None, False
    return _due(item, _need(item) if item.get("kind") == "team_need" else None)


def pace_at(item: dict) -> float | None:
    ""






    if item.get("kind") == "deadline" and item.get("date") is not None \
            and str(item.get("summary") or "").startswith("No due date"):
        return float(item["date"])
    return None


def _weekday(ts: float) -> str:
    return datetime.fromtimestamp(ts, _zone()).strftime("%A")


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts, _zone()).strftime("%-I:%M %p").replace("AM", "a.m.").replace("PM", "p.m.")


def _sent(item: dict) -> float | None:
    ""



    feed = (item.get("reference") or {}).get("feed_id")
    row = store.db().execute("SELECT body, created_at FROM feed WHERE id=? AND kind='mail'", (feed,)).fetchone() \
        if feed is not None else None
    if not row:
        return None
    try:
        card = json.loads(row["body"] or "{}")
    except ValueError:
        return None
    people = {p.get("addr", "").lower() for p in card.get("to", []) + card.get("cc", []) if isinstance(p, dict)}
    if card.get("in_reply_to") or not people or not card.get("subject"):
        return None
    for r in store.db().execute("SELECT data, created FROM open_loop_events WHERE kind='mail_sent' AND created>=? "
                                "ORDER BY created", (int(row["created_at"] or 0),)):
        try:
            data = json.loads(r["data"])
        except ValueError:
            continue
        if data.get("subject") == card["subject"] and not data.get("reply_to") and \
                {p.get("addr", "").lower() for p in data.get("to") or [] if isinstance(p, dict)} == people:
            return float(r["created"])
    return None


def _gone(item: dict, need, due: float | None, now: float) -> tuple[str, float] | None:
    ""
    if need is not None and due is not None and due < now:
        post = _post(need)
        if post:
            return f"{_weekday(due)}'s {post.get('where') or 'scheduled'} post", due
        sign_in = _SIGN_IN.match(need["title"] or "")
        if sign_in and _POST.search(need["title"]):

            return f"{_weekday(due)}'s {sign_in.group(1)} sign-in", due
    if item.get("kind") == "mail" and (sent := _sent(item)) is not None:
        to = (item.get("people") or [{}])[0]
        who = to.get("name") or to.get("addr") or "them"
        return f"the email to {who} (sent at {_clock(sent)})", sent
    return None


def _words(item: dict, need, projects: dict) -> dict:
    ""
    kind = item.get("kind")
    project = projects.get(item.get("project_id") or "")
    place = project["title"] if project and project.get("id") != "other" else ""
    if kind in ("email", "text") and item.get("ask"):
        return {"act": item["ask"], "context": item.get("short_title") or item.get("title") or ""}
    if kind == "deadline":
        turn_in = "not turned in" in (item.get("ask") or "")
        return {"act": f"Turn in {item['title']}" if turn_in else item["title"], "context": place}
    if kind == "mail":
        to = (item.get("people") or [{}])[0]
        return {"act": f"Send your email to {to.get('name') or to.get('addr') or 'them'}",
                "context": item.get("title") or ""}
    if need is not None:
        return {"act": item.get("title") or "", "context": need["team_name"] or ""}
    return {"act": "", "context": ""}


def _fold_key(item: dict, need) -> tuple | None:
    if need is not None and _post(need):
        return ("posts", need["team_id"], item.get("bot_id"))
    if item.get("kind") == "choices":


        return ("questions", item.get("bot_id"))
    if item.get("kind") == "approval":
        from . import consent
        rid = (item.get("reference") or {}).get("request_id")
        kind = (consent.get_request(int(rid)) or {}).get("kind") if rid is not None else None
        if kind in _FOLDED_REQUESTS:
            return ("approval", kind, item.get("bot_id"))
    return None


def _post_line(item: dict, need) -> str:
    post = _post(need) or {}
    due = item.get("due_at")
    when = f"{datetime.fromtimestamp(due, _zone()).strftime('%a')} {_clock(due)}" if due else ""
    return " ".join(p for p in (post.get("where") or "", when) if p)


def _fold(rows: list[tuple[dict, object]]) -> list[dict]:
    ""

    groups: dict[tuple, list[tuple[dict, object]]] = {}
    for item, need in rows:
        groups.setdefault(_fold_key(item, need) or ("one", item["id"]), []).append((item, need))
    out = []
    for key, members in groups.items():
        if key[0] == "one" or len(members) < 2:
            out.extend(item for item, _need in members)
            continue
        members.sort(key=lambda m: (m[0].get("due_at") is None, m[0].get("due_at") or 0,
                                     _since(m[0]) if key[0] == "questions" else 0, m[0]["id"]))
        first, need = members[0]
        n = len(members)
        if key[0] == "posts":
            team = re.sub(r"\s+team$", "", need["team_name"] or "", flags=re.I)
            act = f"Approve {n} {team} posts".replace("  ", " ")
            context = ", ".join(_post_line(item, nd) for item, nd in members)
        elif key[0] == "questions":
            act = f"Answer {n} questions"
            context = ", ".join(item.get("title") or "" for item, _nd in members)
        else:
            act = f"Review {n} {_FOLDED_REQUESTS[key[1]]}"
            context = ", ".join(item.get("title") or "" for item, _nd in members)
        out.append({**first, "id": "group:" + ":".join(str(k) for k in key), "title": act, "act": act,
                    "context": context, "members": [{"id": item["id"], "title": item.get("title") or "",
                                                     "due_at": item.get("due_at")} for item, _nd in members]})
    return out


def _since(item: dict) -> float:
    ""
    return float(item.get("since") if item.get("since") is not None else item.get("at") or 0)


def order(item: dict) -> tuple:
    ""

    when = item.get("due_at") if item.get("due_at") is not None else item.get("pace_at")
    return (when is None, when or 0, -_since(item))


def arrange(items: list[dict], projects: list[dict], now: float | None = None) -> tuple[list[dict], list[dict]]:
    ""
    now = time.time() if now is None else now
    by_id = {p["id"]: p for p in projects}
    kept, cleared = [], []
    for item in items:
        need = _need(item) if item.get("kind") == "team_need" else None
        pace = pace_at(item)
        due, timed = (None, False) if pace is not None else _due(item, need)

        carried = item.get("carried_due")
        if carried and (due is None or carried[0] < due):
            due, timed, pace = carried[0], carried[1], None
        if gone := _gone(item, need, due, now):
            if now - gone[1] <= CLEARED_SEC:
                cleared.append({"id": item["id"], "title": gone[0], "at": gone[1]})
            continue
        kept.append(({**item, "due_at": due, "due_timed": timed, **({"pace_at": pace} if pace is not None else {}),
                      **_words(item, need, by_id), "members": []}, need))
    return sorted(_fold(kept), key=order), sorted(cleared, key=lambda c: -c["at"])
