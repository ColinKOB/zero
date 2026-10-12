"""Before the lead asks "is it done?" about schoolwork due within hours (calendar_noticers.due_soon), the Zip that
follows schoolwork looks at the course site for a submission, so the question rests on what it actually saw (Colin,
Oct 10: "you have something due in an hour and I couldn't find anything on your school website ... that showed that
you had uploaded anything. Is the assignment done?").

The hub gives that Zip (School, or the lead in a home without one) one turn in a session apart from the person's
chat: hermes lane "quiet", the same read-only lane as the lead's quiet look, where the gate lets it read and change
nothing and no sign-in card goes to the person. It answers with JSON: for each item, submitted yes, no or unknown,
what the page showed, and the assignment page's address. The answer is kept for the noticer's next pass. At most
four checks a day (the `school_checks_daily` setting), in waking hours, never while the Claude plan is at its
limit, and never while that Zip is busy in a chat turn or on the board (its browser is in use; the next pass tries
again). A check that fails or never answers counts as unknown, and the card says plainly that it couldn't check.
"""

import asyncio
import logging
import re

from . import config, notices, store, who

log = logging.getLogger("taffy.schoolcheck")

KEY = "proactivity_school_checks"
WAIT_SEC = 20 * 60        # a check not back by then counts as unknown
KEEP_SEC = 2 * 86400
ANSWERS = ("yes", "no", "unknown")

NOTE = """[A check from the hub, not a message from {first}. {first} won't see this conversation.]
{first} has schoolwork due {when}:
{items}

Before {lead} asks {first} whether it is done, look on the course site where {first} turns in work (your notes say \
which one) with your browser, and see whether each item shows as submitted. Only read: don't submit, upload, post, \
mark or change anything, and don't message anyone. Keep it to a few minutes. If you can't get in or can't find an \
item, say so for that item; never guess.

Reply with one JSON object and nothing else:
{{"site": "the site's short name, like eLearning", "items": [{{"name": "the item as written above", "submitted": \
"yes, no or unknown", "seen": "one short sentence on what the page showed", "link": "the assignment page's address, \
or empty"}}]}}"""

_tasks: set = set()


async def run_turn(bot_id: str, text: str) -> str | None:
    """The check, in the Zip's read-only session. Tests replace this."""
    from . import hermes
    return await hermes.run_turn(bot_id, text, lane="quiet")


def checker() -> str:
    """The Zip that follows schoolwork: School when the home has one, else the lead."""
    return "school" if "school" in config.BOTS_BY_ID else "default"


def _all() -> dict:
    d = store.get_setting(KEY, {}) or {}
    return d if isinstance(d, dict) else {}


def _put(subject: str, entry: dict) -> None:
    t = notices.now()
    d = {k: v for k, v in _all().items() if isinstance(v, dict) and t - float(v.get("at") or 0) < KEEP_SEC}
    d[subject] = entry
    store.set_setting(KEY, d)


def result(subject: str) -> dict | None:
    """The check for this subject: None when none was asked for, {"state": "pending"} while it runs, else
    {"state": "done", "site", "items": [{"name", "submitted", "seen", "link"}]}. One that never answered is done with
    no items, which reads as unknown."""
    r = _all().get(subject)
    if not isinstance(r, dict):
        return None
    if r.get("state") == "pending" and notices.now() - float(r.get("at") or 0) > WAIT_SEC:
        return {"state": "done", "site": "", "items": [], "at": r.get("at")}
    return r


def allowed(busy: bool = False) -> bool:
    from . import turns
    if busy or not notices.waking() or turns.is_busy(checker()):
        return False
    try:
        from . import health
        if not health.status.get("claude", True):
            return False
    except Exception:
        pass
    return notices._count("school_check", notices.day_start()) < int(notices.settings()["school_checks_daily"])


def prompt(names: list[str], due: float) -> str:
    return NOTE.format(first=who.first(), lead=notices._lead(), when=f"{notices.day(due)} at {notices.clock(due)}",
                       items="\n".join(f"- {notices.plain(n, 120)}" for n in names))


def request(subject: str, names: list[str], due: float, busy: bool = False) -> bool:
    """Starts the check once per subject. Returns whether one started. `busy`: the Zip is working on the board."""
    if subject in _all() or not allowed(busy):
        return False
    _put(subject, {"state": "pending", "at": notices.now(), "names": list(names), "due": due})
    notices._log("school_check", "due_soon", subject)
    task = asyncio.get_running_loop().create_task(check(subject, list(names), due))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


def _answer(v) -> str:
    v = str(v or "").strip().lower()
    return v if v in ANSWERS else "unknown"


def parse(reply: str, names: list[str]) -> dict:
    """The Zip's answer as {"site", "items"}, one item per name asked about, in that order. An item it didn't answer
    is unknown."""
    data = notices._parse(reply or "") or {}
    got = [i for i in (data.get("items") or []) if isinstance(i, dict)] if isinstance(data.get("items"), list) else []
    by_name = {" ".join(str(i.get("name") or "").lower().split()): i for i in got}
    items = []
    for n, name in enumerate(names):
        i = by_name.get(" ".join(name.lower().split())) or (got[n] if n < len(got) and len(got) == len(names) else {})
        link = str(i.get("link") or "").strip()
        items.append({"name": name, "submitted": _answer(i.get("submitted")),
                      "seen": notices.clean(i.get("seen") or "", 200),
                      "link": link if re.fullmatch(r"https?://[^\s\"'<>]{3,300}", link) else ""})
    site = notices.clean(data.get("site") or "", 40)
    return {"site": site, "items": items}


async def check(subject: str, names: list[str], due: float) -> None:
    try:
        reply = await run_turn(checker(), prompt(names, due))
    except Exception:
        log.warning("the course site check for %s failed", subject, exc_info=True)
        reply = None
    found = parse(reply or "", names)
    _put(subject, {"state": "done", "at": notices.now(), "due": due, **found})
    log.info("course site check for %s: %s", subject, [i["submitted"] for i in found["items"]])


async def drain() -> None:
    """Waits for the checks running on this loop (tests)."""
    loop = asyncio.get_running_loop()
    mine = [t for t in list(_tasks) if t.get_loop() is loop]
    if mine:
        await asyncio.gather(*mine, return_exceptions=True)
