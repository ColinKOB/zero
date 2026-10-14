"""What Taffy already knows about Colin and what is already set up, for the welcome flow in the app."""

import json
import re
from datetime import datetime

from . import accounts, apns, config, logins, onboarding

SECTION_TITLES = ["About You", "How You Like Answers", "What Needs Your Okay", "Your Accounts"]


def _clock(h: int, m: int) -> str:
    return datetime(2000, 1, 1, h, m).strftime("%-I:%M %p")


def describe_schedule(s: str) -> str:
    """Plain words for the cron patterns the bots use; anything unusual is shown as is."""
    s = (s or "").strip()
    m = re.fullmatch(r"once at (\d{4}-\d{2}-\d{2})T?\s?(\d{2}):(\d{2})", s)
    if m:
        d = datetime.strptime(m.group(1), "%Y-%m-%d")
        return f"Once on {d.strftime('%b %-d')} at {_clock(int(m.group(2)), int(m.group(3)))}"
    parts = s.split()
    if len(parts) != 5:
        return s
    mi, hr, dom, mon, dow = parts
    days = {"0": "Sundays", "1": "Mondays", "2": "Tuesdays", "3": "Wednesdays", "4": "Thursdays",
            "5": "Fridays", "6": "Saturdays", "7": "Sundays"}
    if mi.isdigit() and hr.isdigit():
        at = _clock(int(hr), int(mi))
        if dom == "*" and mon == "*" and dow == "*":
            return f"Every day at {at}"
        if dom == "*" and mon == "*" and dow in days:
            return f"{days[dow]} at {at}"
        if dom == "*/2" and mon == "*" and dow == "*":
            return f"Every other day at {at}"
    m = re.fullmatch(r"\*/(\d+)", mi)
    r = re.fullmatch(r"(\d+)-(\d+)", hr)
    if m and r and dom == mon == dow == "*":
        return (f"Every {m.group(1)} minutes, {_clock(int(r.group(1)), 0)} to "
                f"{_clock(int(r.group(2)), 0)}")
    return s


def notes() -> list[dict]:
    """Taffy's shared notes about Colin (USER.md), one card per section."""
    try:
        text = (config.HERMES_HOME / "memories" / "USER.md").read_text()
    except OSError:
        return []
    out = []
    for i, block in enumerate(b.strip() for b in text.split("§")):
        if not block:
            continue
        if block.startswith(onboarding.ABOUT_START):
            body = block.replace(onboarding.ABOUT_START, "").replace(onboarding.ABOUT_END, "").strip()
            out.append({"title": "What You Told Me", "text": body})
            continue
        body = re.sub(r"^(Style|Boundaries|Accounts):\s*", "", block)
        body = body[:1].upper() + body[1:]
        out.append({"title": SECTION_TITLES[i] if i < len(SECTION_TITLES) else "More", "text": body})
    return out


def checkins() -> list[dict]:
    out = []
    homes = [("default", config.HERMES_HOME)] + [(b.id, config.HERMES_HOME / "profiles" / b.id)
                                                for b in config.BOTS if b.id != "default"]
    for bot_id, home in homes:
        try:
            data = json.loads((home / "cron" / "jobs.json").read_text())
        except (OSError, ValueError):
            continue
        jobs = data.get("jobs", []) if isinstance(data, dict) else data
        for j in jobs:
            if j.get("enabled") is False:
                continue
            sched = j.get("schedule")
            sched = sched.get("display") if isinstance(sched, dict) else sched
            out.append({"bot_id": bot_id, "name": j.get("name") or "Check-in",
                        "when": describe_schedule(str(sched or ""))})
    return out


def summary(server: str | None) -> dict:
    saved = []
    for l in logins.list_all():
        if (l.get("label") or "").endswith("(sign-in page)"):
            continue
        site = (l.get("origin") or "").removeprefix("https://").removeprefix("http://")
        saved.append({"bot_id": l["bot"], "site": site})
    return {
        "name": onboarding.person_name(full=False),
        "notes": notes(),
        "server": server or "",
        "connectors": accounts.connected_names(),
        "alerts": "your iPhone and Mac as notifications" if apns.ready() else "",
        "logins": saved,
        "checkins": checkins(),
    }
