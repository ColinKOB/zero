"""The noticers (proactivity; the rules and the card are in notices.py).

Each noticer is plain code over data the hub can already read for free, and returns candidates with code's own
wording, a fixed set of options and a subject key. Phase 1:

  unreplied       an email Colin sent to a person that asks something, with no reply after five business days (the
                  Theo watch, generalized)
  reply_waiting   a person's email over two days old that asks him something, with no reply from him
  waiting_in_app  a review, a blocked task or a permission card that has waited on him for more than a day
  renewal         a renewal, trial or expiry with a date in the next week, from mail or the Zips' notes
  promise         something he told a Zip he would do, checked on the day he named

Phase 2 adds `form` here (a signature request, form or invitation with an expiry), the calendar and Trello ones in
calendar_noticers.py (school deadlines, clashes, prep before a meeting, follow-ups after a meeting or call, windows)
and the money ones in money_noticers.py (charges Money hasn't seen, bills with nothing showing paid). For an email
Colin sent and a reply waiting on him, the owning Zip prepares the send first when it is to someone he wrote to
(prepared.py).

Mail is read through mail.py (envelopes in bulk, a message's text only for a candidate). The sources sit behind
`Sources`, which tests replace with fixtures.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta

from . import config, home, mail, notices, who
from .notices import Candidate, Option

log = logging.getLogger("taffy.noticers")
P = notices.plain   # outside text in the person's words: one plain line, no quotes (QA round 10, L5)


class _AllMail:
    """account -> its All Mail folder, from the home's mailboxes."""

    def get(self, account, default=None):
        return mail.all_mail_folder(account) or default

    def __contains__(self, account):
        return bool(mail.all_mail_folder(account))

    def __getitem__(self, account):
        f = mail.all_mail_folder(account)
        if not f:
            raise KeyError(account)
        return f


ACCOUNTS = mail.ACCOUNTS     # live: a mailbox added in the app is read on the next pass
ALL_MAIL = _AllMail()
MAIL_FRESH_SEC = 60 * 60
PER_NOTICER = 3          # candidates each noticer hands on per pass, so a pass stays inside the Haiku budget
BODY_READS_PER_PASS = 6

# Who follows through, by the account or the other person's domain (home.json: each mailbox's `owner_zip` and
# `mail.owner_by_domain`). Anything else is the lead's.


def owner_by_account() -> dict:
    return {a["id"]: str(a.get("owner_zip") or "default") for a in home.mail_accounts()}


def owner_by_domain() -> dict:
    d = home.get("mail.owner_by_domain") or {}
    return {str(k).lower(): str(v) for k, v in d.items()} if isinstance(d, dict) else {}


def owner_for(account: str, addr: str = "") -> str:
    dom = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    for d, bot in owner_by_domain().items():
        if dom == d or dom.endswith("." + d):
            return bot if bot in config.BOTS_BY_ID else "default"
    bot = owner_by_account().get(account, "default")
    return bot if bot in config.BOTS_BY_ID else "default"


def _first(name: str, addr: str) -> str:
    return (name or "").strip() or addr


def _now() -> float:
    return notices.now()


def business_days_after(ts: float, n: int) -> float:
    d = datetime.fromtimestamp(ts, notices.ET)
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d.timestamp()


# MARK: Sources

class Sources:
    """What the noticers read, cached for one pass (mail envelopes for an hour). Tests replace the methods."""

    def __init__(self):
        self._mail: dict[tuple, tuple[float, list]] = {}
        self._bodies: dict[tuple, str] = {}
        self._replied: dict[tuple, tuple[float, bool | None]] = {}
        self.body_reads = 0

    async def envelopes(self, account: str, folder: str, fresh: bool = False) -> list[dict]:
        key = (account, folder)
        hit = self._mail.get(key)
        if hit and not fresh and _now() - hit[0] < MAIL_FRESH_SEC:
            return hit[1]
        try:
            items = await asyncio.to_thread(mail.envelopes, account, folder, 50)
        except mail.MailError as e:
            log.info("mail not read for noticing: %s", e)
            items = hit[1] if hit else []
        self._mail[key] = (_now(), items)
        return items

    def stale(self) -> None:
        self._mail.clear()
        self._replied.clear()

    async def body(self, e: dict) -> str | None:
        """A candidate's text, read once. None when this pass has read its share already."""
        key = (e["account"], e["folder"], e["id"], e["date"])
        if key in self._bodies:
            return self._bodies[key]
        if self.body_reads >= BODY_READS_PER_PASS:
            return None
        self.body_reads += 1
        try:
            text = await asyncio.to_thread(mail.read, e["account"], e["folder"], e["id"])
        except mail.MailError as err:
            log.info("message not read for noticing: %s", err)
            return None
        if len(self._bodies) > 400:
            self._bodies.clear()
        self._bodies[key] = text
        return text

    async def replied(self, account: str, addr: str, after: float) -> bool | None:
        """Did this person write to Colin after `after`? None when the mailbox can't tell."""
        key = (account, addr, after)
        hit = self._replied.get(key)
        if hit and _now() - hit[0] < MAIL_FRESH_SEC:
            return hit[1]
        answer = None
        for folder in (ALL_MAIL.get(account), "INBOX"):
            if not folder:
                continue
            try:
                found = await asyncio.to_thread(mail.envelopes, account, folder, 20, ["from", addr])
            except mail.MailError:
                continue
            answer = any(x["from_addr"] == addr and x["date"] > after for x in found)
            break
        self._replied[key] = (_now(), answer)
        return answer

    async def from_sender(self, account: str, addr: str) -> list[dict] | None:
        """The newest messages this address sent to the account (up to 20), or None when the mailbox can't tell."""
        key = ("from", account, addr)
        hit = self._replied.get(key)
        if hit and _now() - hit[0] < MAIL_FRESH_SEC:
            return hit[1]
        found = None
        for folder in (ALL_MAIL.get(account), "INBOX"):
            if not folder:
                continue
            try:
                found = [x for x in await asyncio.to_thread(mail.envelopes, account, folder, 20, ["from", addr])
                         if x["from_addr"] == addr]
            except mail.MailError:
                continue
            break
        self._replied[key] = (_now(), found)
        return found

    async def board(self) -> dict:
        from . import board
        return await asyncio.to_thread(board.grouped)

    async def status_since(self, task: dict) -> float:
        """When a task started waiting on Colin: its last blocked or review event."""
        from . import board
        full = await asyncio.to_thread(board.get_task, task["id"])
        for ev in reversed((full or {}).get("events") or []):
            if ev.get("kind") in ("blocked", "block_loop_detected", "review_requested", "review"):
                return float(ev.get("created_at") or 0)
        return float(task.get("completed_at") or task.get("started_at") or task.get("created_at") or 0)

    async def pending_cards(self) -> list[dict]:
        from . import consent
        return await asyncio.to_thread(consent.pending)

    async def chat(self, bot_id: str) -> list[dict]:
        """The chat as Colin sees it, oldest first, each with role, ts and display."""
        from . import hermes, history
        try:
            return history.visible(await hermes.messages(bot_id, limit=120))
        except Exception:
            log.info("chat with %s not read for noticing", bot_id, exc_info=True)
            return []

    def notes_of(self, bot_id: str) -> list[str]:
        """One Zip's saved notes, one entry each."""
        home = config.HERMES_HOME if bot_id == "default" else config.HERMES_HOME / "profiles" / bot_id
        out = []
        for name in ("MEMORY.md", "USER.md"):
            try:
                text = (home / "memories" / name).read_text()[:20000]
            except OSError:
                continue
            out.extend(e.strip() for e in re.split(r"\n?§\n?", text) if e.strip())
        return out

    def calendar(self) -> list[dict]:
        """The hourly calendar copy (calcache.py)."""
        return notices.calendar_events()

    def trello(self) -> list[dict]:
        from . import calcache
        return calcache.trello_cards()

    def work_files(self, days: int = 3) -> list[tuple[str, str, float]]:
        """(Zip, file name, modified) for files the Zips saved in their work folders in the last few days."""
        root = config.HOME / "taffy" / "work"
        cutoff = _now() - days * 86400
        out = []
        try:
            folders = [d for d in root.iterdir() if d.is_dir()]
        except OSError:
            return []
        for d in folders:
            try:
                for f in d.iterdir():
                    try:
                        m = f.stat().st_mtime
                    except OSError:
                        continue
                    if m >= cutoff:
                        out.append((d.name, f.name, m))
            except OSError:
                continue
        return out

    def calls(self, since: float) -> list[dict]:
        """Voice calls that ended since then, with their summaries (voice.py's call entry in the chat)."""
        from . import store
        out = []
        for r in store.db().execute("SELECT * FROM feed WHERE kind='call' AND created_at>=? ORDER BY id",
                                    (int(since),)):
            try:
                summary = json.loads(r["body"] or "{}").get("summary") or ""
            except ValueError:
                summary = ""
            out.append({"id": r["id"], "bot_id": r["bot_id"] or "default", "at": r["created_at"], "summary": summary})
        return out

    async def wrote_to(self, account: str, addr: str) -> bool:
        """Has the person ever written to this address from this account (its Sent folder)? Only then may a
        prepared send go to them."""
        key = ("wrote", account, addr)
        hit = self._replied.get(key)
        if hit and _now() - hit[0] < MAIL_FRESH_SEC:
            return bool(hit[1])
        try:
            found = await asyncio.to_thread(mail.envelopes, account, "Sent", 5, ["to", addr])
            answer = any(any(p[1] == addr for p in x["to"]) for x in found)
        except Exception:
            answer = False   # can't tell: no prepared send, the question still goes up
        self._replied[key] = (_now(), answer)
        return answer

    async def purchases(self) -> list[dict]:
        """Tally's purchases, newest first, read the way Money's `tally` tool reads them (its code, loaded by path).
        Tally keeps transactions only, never balances. Only a home with Tally (home.json `integrations.tally`)."""
        if not home.tally():
            return []
        tools = _plugin_tools()
        if tools is None:
            return []
        start = datetime.fromtimestamp(_now() - 200 * 86400, notices.ET).strftime("%Y-%m-%d")
        try:
            out = json.loads(await asyncio.to_thread(tools.tally, {"action": "purchases", "start_date": start}))
        except Exception:
            log.info("Tally not read for noticing", exc_info=True)
            return []
        rows = out.get("purchases") if isinstance(out, dict) else None
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def notes(self) -> list[str]:
        """Money's and Zero's saved notes and the team notes, one entry each."""
        out = []
        homes = [config.HERMES_HOME, config.HERMES_HOME / "profiles" / "money"]
        for home in homes:
            for name in ("MEMORY.md", "USER.md"):
                try:
                    text = (home / "memories" / name).read_text()[:20000]
                except OSError:
                    continue
                out.extend(e.strip() for e in re.split(r"\n?§\n?", text) if e.strip())
        try:
            from . import team
            out.extend(n["text"] for n in team.notes())
        except Exception:
            pass
        return out


_TOOLS = None


def _plugin_tools():
    """The taffy-tools plugin's tools.py, loaded by path the way consent.py is, for its read-only `tally` code."""
    global _TOOLS
    if _TOOLS is not None:
        return _TOOLS
    import importlib.util
    from pathlib import Path
    path = config.PLUGIN_DIR / "tools.py"
    repo = Path(__file__).resolve().parents[2] / "alien" / "plugins" / "taffy-tools" / "tools.py"
    if not path.exists() and repo.exists():
        path = repo
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("taffy_hub_plugin_tools", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _TOOLS = mod
    return mod


SOURCES = Sources()

# Which noticers each kind of wake runs. A timer pass runs everything but the chat read, which runs hourly and on a
# quiet wake.
WAKES = {
    "timer": ("unreplied", "reply_waiting", "waiting_in_app", "renewal", "form", "school_deadline", "due_soon",
              "conflict", "prep", "followup", "window", "charge", "bill", "running_low"),
    "mail": ("unreplied", "reply_waiting", "renewal", "form"),
    "board": ("waiting_in_app",),
    "calendar": ("conflict", "prep", "followup", "school_deadline", "due_soon"),
    "trello": ("school_deadline", "due_soon"),
    "call": ("followup",),
    "quiet": ("unreplied", "reply_waiting", "waiting_in_app", "renewal", "promise", "form", "school_deadline",
              "due_soon", "conflict", "prep", "followup", "window", "charge", "bill", "running_low"),
    "promise": ("promise",),
}


async def find(wakes: set[str], src: Sources | None = None) -> list[Candidate]:
    src = src or SOURCES
    src.body_reads = 0
    if "mail" in wakes:
        src.stale()
    names = []
    for w in wakes:
        names.extend(n for n in WAKES.get(w, ()) if n not in names)
    out: list[Candidate] = []
    for name in names:
        if notices.mode(name) == "off" or not notices.fits(name):
            continue
        try:
            found = await finders()[name](src)
        except Exception:
            log.exception("noticer %s failed", name)
            found = []
        # Each noticer's share goes to what can still become a question, soonest deadline first and then newest
        # (QA round 10, M1: the same three answered items took the share every pass).
        fresh = [c for c in found if not notices.settled(c)]
        fresh.sort(key=lambda c: (c.deadline is None, c.deadline or 0, -float(c.facts.get("_at") or 0)))
        out.extend(fresh[:PER_NOTICER])
    return out


async def check_new_mail(src: Sources | None = None) -> bool:
    """The new-mail wake: the newest few envelopes in each inbox and Sent folder. A change runs the mail noticers."""
    src = src or SOURCES
    sig = []
    for account in ACCOUNTS:
        for folder in ("INBOX", "Sent"):
            try:
                items = await asyncio.to_thread(mail.envelopes, account, folder, 5)
            except mail.MailError:
                continue
            sig.extend(f"{account}/{folder}/{e['id']}/{e['date']}" for e in items)
    digest = hashlib.sha1("|".join(sig).encode()).hexdigest()
    from . import store
    before = store.get_setting("proactivity_mail_sig")
    store.set_setting("proactivity_mail_sig", digest)
    if before is not None and before != digest:
        notices.wake("mail")
        return True
    return False


# MARK: 1. An email Colin sent with no reply

WAIT_BUSINESS_DAYS = 5
SENT_WINDOW_DAYS = 21   # older than three weeks, a sharp assistant would have asked already; it is moot now


async def unreplied(src: Sources) -> list[Candidate]:
    t = _now()
    found = []
    for account in ACCOUNTS:
        latest: dict[tuple, dict] = {}
        for e in await src.envelopes(account, "Sent"):
            people = [p for p in e["to"] if mail.is_person(p[1])]
            if t - e["date"] > SENT_WINDOW_DAYS * 86400 or len(e["to"]) != 1 or len(people) != 1:
                continue
            key = (people[0][1], mail.norm_subject(e["subject"]))
            if key not in latest or e["date"] > latest[key]["date"]:
                latest[key] = {**e, "who": people[0]}
        for (addr, ns), e in sorted(latest.items(), key=lambda kv: -kv[1]["date"]):   # newest first
            if t < business_days_after(e["date"], WAIT_BUSINESS_DAYS):
                continue
            if notices.settled(_unreplied(account, e, addr, ns)):
                continue   # already asked: its text need not be read again
            replied = await src.replied(account, addr, e["date"])
            if replied is not False:
                continue
            body = await src.body(e)
            if body is None or not mail.asks_something(body):
                continue   # nothing is owed back unless he asked something (Oct 9: files sent to a print shop)
            c = _unreplied(account, e, addr, ns)
            c.facts["_at"] = e["date"]
            c.prepare = {"bot": c.owner}   # Colin wrote to them, so the Zip may prepare the nudge
            found.append(c)
    return found


def _unreplied(account: str, e: dict, addr: str, ns: str) -> Candidate:
    name = _first(e["who"][0], addr)
    first = name.split()[0] if " " not in addr and name != addr else name
    days = int((_now() - e["date"]) // 86400)
    when = notices.date_short(e["date"])
    subject = e["subject"] or "(no subject)"
    re_subject = subject if re.match(r"\s*re\s*:", subject, re.I) else f"Re: {subject}"
    ask_nudge = (f"Please draft a short, friendly nudge to {P(name)} <{addr}> about my {when} email \"{P(subject)}\" "
                 f"from my {account} account, and show it to me before anything is sent.")
    return Candidate(
        noticer="unreplied", subject=f"mail-sent:{account}:{addr}:{ns}", owner=owner_for(account, addr),
        evidence=f"{name} hasn't answered your {when} email about \"{subject}\". It has been {days} days.",
        question="Want me to send a short nudge, or keep waiting?",
        lock=f"No reply from {first} since {when}. Nudge?",
        facts={"who": f"{name} <{addr}>", "what": f"your email \"{subject}\" from your {account} account",
               "when": f"sent {when}, {days} days ago, no reply since"},
        consequence="The thread is stalled until someone writes.",
        options=[Option("Draft a nudge", "act", say=ask_nudge),
                 Option("Wait another week", "wait", wait_days=7),
                 Option("I already heard from them", "close")],
        draft={"account": account, "from_addr": mail.ACCOUNTS[account], "to_name": name, "to_addr": addr,
               "subject": re_subject,
               "purpose": f"kindly checks in on {who.possessive()} earlier email and asks where things stand"},
        draft_options=[Option("Send this nudge", "send"),
                       Option("Wait another week", "wait", wait_days=7),
                       Option("I already heard from them", "close")],
    )


# MARK: 2. A reply waiting on Colin that asks something

REPLY_AFTER_SEC = 48 * 3600
REPLY_WINDOW_DAYS = 14
# The same sender on a regular cadence with a new subject each time, to someone who never writes back: a newsletter
# written in a person's name (Oct 10: a weekly creator letter for four months). Three in four months is enough.
REGULAR_SENDS = 3
REGULAR_DAYS = 120


async def regular_sender(src: Sources, account: str, addr: str) -> bool:
    """True when this address writes to the account on a cadence, each time about something new, and the person has
    never written to it: a newsletter or a list, however personal it sounds. No reply is owed to one."""
    if await src.wrote_to(account, addr):
        return False
    sent = await src.from_sender(account, addr)
    if not sent:
        return False
    recent = [x for x in sent if _now() - x["date"] <= REGULAR_DAYS * 86400]
    return len({mail.norm_subject(x["subject"]) for x in recent}) >= REGULAR_SENDS


async def reply_waiting(src: Sources) -> list[Candidate]:
    t = _now()
    found = []
    for account in ACCOUNTS:
        inbox = await src.envelopes(account, "INBOX")
        sent = None
        for e in sorted(inbox, key=lambda x: -x["date"]):   # newest first
            age = t - e["date"]
            if age < REPLY_AFTER_SEC or age > REPLY_WINDOW_DAYS * 86400:
                continue
            if not mail.is_person(e["from_addr"], e["from_name"]) or "answered" in e["flags"]:
                continue
            if mail.is_notification(e["subject"]):
                continue   # a course site's notice in the teacher's name is not a reply owed (Oct 9)
            ns = mail.norm_subject(e["subject"])
            if sent is None:
                sent = await src.envelopes(account, "Sent")
            if any(s["date"] > e["date"] and mail.norm_subject(s["subject"]) == ns
                   and any(p[1] == e["from_addr"] for p in s["to"]) for s in sent):
                continue   # he answered (the Sent folder is the source of truth, as the email skill says)
            old = notices.row(f"mail-in:{account}:{e['from_addr']}:{ns}")
            if old and old["state"] in ("picked", "dismissed", "stopped", "handled", "preparing", "open"):
                continue   # already asked: its body need not be read again (QA round 10, M1)
            if await regular_sender(src, account, e["from_addr"]):
                continue   # a newsletter in a person's name (Oct 10)
            body = await src.body(e)
            if body is None or not mail.asks_something(body) or mail.is_notification("", body) or mail.is_bulk(body):
                continue   # a list or bulk send asks its readers, not the person
            c = _reply_waiting(account, e, ns, mail.own_text(body))
            c.facts["_at"] = e["date"]
            if await src.wrote_to(account, e["from_addr"]):
                c.prepare = {"bot": c.owner}   # only someone the person has written to may be a prepared send's target
            found.append(c)
    return found


def _reply_waiting(account: str, e: dict, ns: str, text: str) -> Candidate:
    name, addr = _first(e["from_name"], e["from_addr"]), e["from_addr"]
    first = name.split()[0]
    when = notices.day(e["date"]) if _now() - e["date"] < 6 * 86400 else notices.date_short(e["date"])
    subject = e["subject"] or "(no subject)"
    re_subject = subject if re.match(r"\s*re\s*:", subject, re.I) else f"Re: {subject}"
    ask_draft = (f"Please draft a reply to {P(name)} <{addr}> about \"{P(subject)}\" (their {notices.date_short(e['date'])} "
                 f"email) from my {account} account, and show it to me before anything is sent.")
    # Said to the person, as a teammate would ("I don't see a reply from you"), never about them (Oct 10). The facts
    # are in the same voice, so the model's wording starts from it.
    return Candidate(
        noticer="reply_waiting", subject=f"mail-in:{account}:{addr}:{ns}", owner=owner_for(account, addr),
        evidence=f"{name} wrote to you {when} about \"{subject}\" and asked you something. I don't see a reply "
                 "from you yet.",
        question="Want me to draft a reply for you to check?",
        lock=f"{first} asked you something. Draft a reply?",
        facts={"who": f"{name} <{addr}>", "what": f"their email \"{subject}\" to your {account} account",
               "when": f"received {notices.date_short(e['date'])}, no reply from you since"},
        consequence="They are waiting on your answer.",
        excerpt=text[:1500],
        options=[Option("Draft a reply for me to check", "act", say=ask_draft),
                 Option("I already answered", "close"),
                 Option("Remind me in a few days", "wait", wait_days=3)],
        draft={"account": account, "from_addr": mail.ACCOUNTS[account], "to_name": name, "to_addr": addr,
               "subject": re_subject,
               "purpose": "answers their question, but only if the answer is plainly in the material"},
        draft_options=[Option("Send this answer", "send"),
                       Option("I already answered", "close"),
                       Option("Remind me in a few days", "wait", wait_days=3)],
    )


# MARK: 4. Something waiting on Colin in the app for over a day

WAITING_SEC = 24 * 3600


async def waiting_in_app(src: Sources) -> list[Candidate]:
    t = _now()
    found = []
    cards = await src.pending_cards()
    card_tasks = {c.get("task_id") for c in cards if c.get("task_id")}
    groups = await src.board()
    for task in groups.get("needs_you", []):
        if task.get("status") not in ("review", "blocked") or task["id"] in card_tasks:
            continue
        since = await src.status_since(task)
        if not since or t - since < WAITING_SEC:
            continue
        found.append(_task_waiting(task, since))
    for c in cards:
        if not c.get("created") or t - float(c["created"]) < WAITING_SEC:
            continue
        found.append(_card_waiting(c))
    return found


def _task_waiting(task: dict, since: float) -> Candidate:
    owner = task.get("assignee") or "default"
    who_name = notices._bot_name(owner)
    title = (task.get("title") or "A task")[:100]
    when = notices.day(since)
    ref = {"task_id": task["id"]}
    if task["status"] == "review":
        summary = (task.get("summary") or "").strip()
        return Candidate(
            noticer="waiting_in_app", subject=f"task:{task['id']}:review", owner=owner,
            evidence=f"{who_name}'s \"{title}\" has waited for your review since {when}.",
            question="Approve it, change it, or drop it?", lock=f"{who_name} is waiting for your review",
            facts={"who": who_name, "what": title, "when": f"waiting since {notices.date_short(since)}"},
            blocking=True, judge=False, ref=ref,
            detail={"label": "what you are approving", "text": summary[:4000]} if summary else None,
            options=[Option("Approve it", "hub", hub="approve_task"),
                     Option("I'll change it", "decide", follow="Write your changes on the task in Work."),
                     Option("Drop it", "hub", hub="drop_task")])
    reason = " ".join((task.get("block_reason") or "").split())[:200]
    return Candidate(
        noticer="waiting_in_app", subject=f"task:{task['id']}:blocked", owner=owner,
        evidence=f"{who_name} has waited on you since {when} for \"{title}\"" + (f": {reason}" if reason else "."),
        question="Want help sorting it out? You can also type your answer below.",
        lock=f"{who_name} has been waiting on you since {when}",
        facts={"who": who_name, "what": title, "when": f"blocked since {notices.date_short(since)}"},
        blocking=True, judge=False, ref=ref,
        options=[Option("Help me sort it out", "act",
                        say=f"{who_name} is blocked on \"{P(title)}\" (task {task['id']})"
                            + (f": {P(reason, 200)}" if reason else "") + ". Help me sort it out."),
                 Option("Drop the task", "hub", hub="drop_task"),
                 Option("Leave it for now", "wait", wait_days=1)])


def _card_waiting(c: dict) -> Candidate:
    owner = c.get("bot_id") or "default"
    who_name = notices._bot_name(owner)
    since = float(c["created"])
    what = " ".join((c.get("reason") or "").split())[:160] or (
        f"sign in to {c.get('site')}" if c.get("site") else "go ahead")
    what = what[:1].lower() + what[1:]
    return Candidate(
        noticer="waiting_in_app", subject=f"card:{c['id']}", owner=owner,
        evidence=f"{who_name} has waited since {notices.day(since)} for your okay to {what.rstrip('.')}. The card is in Work.",
        question="Say not now to it, or keep it open?", lock=f"{who_name} is waiting for your okay",
        facts={"who": who_name, "what": what, "when": f"waiting since {notices.date_short(since)}"},
        blocking=True, judge=False, ref={"request_id": c["id"], "task_id": c.get("task_id")},
        options=[Option("Say not now", "hub", hub="deny_card"),
                 Option("Keep it open", "wait", wait_days=1)])


# MARK: 5. A renewal, trial or expiry with a date

RENEW = re.compile(r"\b(renew(s|al|ing|ed)?|auto-?renew\w*|expir(e|es|ing|ation|y)|trial (ends|ending|expires)|"
                   r"subscription|domain|certificate|ssl|your plan|billing date|will be charged)\b", re.I)
RENEW_WINDOW_DAYS = 7
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                          "dec"])}
_DATE = re.compile(
    r"\b(?P<iso>(?P<y1>20\d\d)-(?P<m1>\d\d)-(?P<d1>\d\d))\b"
    r"|\b(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?P<d2>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:,?\s+(?P<y2>20\d\d))?\b"
    r"|\b(?P<m3>\d{1,2})/(?P<d3>\d{1,2})(?:/(?P<y3>(?:20)?\d\d))?\b", re.I)
_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d\d)?")
HOSTING = re.compile(r"\b(domain|certificate|ssl|hosting|netlify|railway|cloudflare|vercel|dns|namecheap|porkbun)\b",
                     re.I)


def dates_in(text: str, ref_ts: float) -> list[float]:
    """Dates written in text, as the start of that day Eastern. A date without a year is the next one on or after
    the day the text was written."""
    ref = datetime.fromtimestamp(ref_ts, notices.ET)
    out = []
    for m in _DATE.finditer(text or ""):
        try:
            if m.group("iso"):
                y, mo, d = int(m.group("y1")), int(m.group("m1")), int(m.group("d1"))
            elif m.group("mon"):
                mo, d = MONTHS[m.group("mon").lower()[:3]], int(m.group("d2"))
                y = int(m.group("y2")) if m.group("y2") else None
            else:
                mo, d = int(m.group("m3")), int(m.group("d3"))
                y3 = m.group("y3")
                y = (int(y3) + (2000 if len(y3) == 2 else 0)) if y3 else None
            if y is None:
                y = ref.year
                if datetime(y, mo, d, tzinfo=notices.ET).date() < ref.date():
                    y += 1
            out.append(datetime(y, mo, d, tzinfo=notices.ET).timestamp())
        except (ValueError, KeyError):
            continue
    return out


def _slug(text: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", (text or "").lower()))[:40] or "item"


def _in_window(ts: float) -> bool:
    today = notices.day_start()
    return today <= ts <= today + RENEW_WINDOW_DAYS * 86400


async def renewal(src: Sources) -> list[Candidate]:
    t = _now()
    found: dict[str, Candidate] = {}
    for account in ACCOUNTS:
        for e in sorted(await src.envelopes(account, "INBOX"), key=lambda x: -x["date"]):
            if t - e["date"] > 45 * 86400 or not RENEW.search(e["subject"]):
                continue
            body = await src.body(e)
            text = f"{e['subject']}\n{mail.own_text(body or '')}"
            dates = [d for d in dates_in(text, e["date"]) if _in_window(d)]
            if not dates:
                continue
            thing = e["from_name"] or e["from_addr"].split("@")[-1]
            m = _MONEY.search(text)
            c = _renewal(thing, min(dates), m.group(0) if m else "",
                         f"an email from {thing} on {notices.date_short(e['date'])}", text,
                         "builder" if HOSTING.search(text) else "money")
            found.setdefault(c.subject, c)
    for note in src.notes():
        if not RENEW.search(note):
            continue
        dates = [d for d in dates_in(note, t) if _in_window(d)]
        if not dates:
            continue
        m = re.search(r"\b([a-z0-9-]+\.(?:app|com|net|org|io|dev|tech|co|ai))\b", note, re.I)
        thing = m.group(1) if m else " ".join(note.split()[:4])
        money = _MONEY.search(note)
        c = _renewal(thing, min(dates), money.group(0) if money else "", "your notes", note,
                     "builder" if HOSTING.search(note) else "money")
        found.setdefault(c.subject, c)
    return sorted(found.values(), key=lambda c: c.deadline or 0)


def _renewal(thing: str, when: float, amount: str, source: str, text: str, owner: str) -> Candidate:
    owner = owner if owner in config.BOTS_BY_ID else "default"
    on = notices.day(when)
    on_full = datetime.fromtimestamp(when, notices.ET).strftime("%A, %b %-d")
    return Candidate(
        noticer="renewal", subject=f"renewal:{_slug(thing)}", owner=owner, version=str(int(when)),
        evidence=f"{thing} renews or expires {on_full}{f' for {amount}' if amount else ''}, according to {source}.",
        question="Keep it, or should I find the cancel page for you?",
        lock=f"{thing} renews {on}. Keep it?",
        facts={"what": thing, "when": on_full, "amount": amount, "source": source},
        deadline=when, sensitive=True, excerpt=text[:1200], expires=when + 86400 - 60,
        options=[Option("Keep it", "decide", say=f"Keep {P(thing)}. It renews {on_full}, and I don't need to cancel it.",
                        follow="Got it. I'll leave it alone."),
                 Option("Find the cancel page", "act",
                        say=f"Please find the page where I can cancel {P(thing)} before it renews {on_full}. "
                            "Don't cancel anything; just show me the page."),
                 Option("Already canceled", "close")])


# MARK: 13. A promise Colin made in chat

PROMISE = re.compile(
    r"\b(?P<lead>i'll|i will|i'm going to|i am going to|i need to|i have to|i gotta|remind me to|remind me)\b"
    r"(?P<what>[^.?!\n]{3,120}?)\s*\b(?P<when>tomorrow|tonight|later today|this afternoon|this evening|today|"
    r"this weekend|next week|on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|monday|tuesday|"
    r"wednesday|thursday|friday|saturday|sunday)\b", re.I)
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def promises_in(text: str, ts: float) -> list[dict]:
    """[{what, when, due (day start), remind}] for each promise in one message of Colin's."""
    out = []
    said = datetime.fromtimestamp(ts, notices.ET)
    for m in PROMISE.finditer(text or ""):
        what = " ".join(m.group("what").split()).strip(" ,").removeprefix("to ").strip()
        if len(what) < 3:
            continue
        when = m.group("when").lower().removeprefix("on ").strip()
        base = said.replace(hour=0, minute=0, second=0, microsecond=0)
        if when in ("today", "tonight", "later today", "this afternoon", "this evening"):
            due = base
        elif when == "tomorrow":
            due = base + timedelta(days=1)
        elif when == "this weekend":
            due = base + timedelta(days=(5 - base.weekday()) % 7 or 7)
        elif when == "next week":
            due = base + timedelta(days=7 - base.weekday())
        else:
            due = base + timedelta(days=(WEEKDAYS.index(when) - base.weekday()) % 7 or 7)
        out.append({"what": what, "when": m.group("when"), "due": due.timestamp(),
                    "remind": m.group("lead").lower().startswith("remind")})
    return out


async def promise(src: Sources) -> list[Candidate]:
    t = _now()
    found = []
    groups = await src.board()
    tasks = [x for g in groups.values() for x in g]
    for bot in list(config.BOTS):
        msgs = await src.chat(bot.id)
        for i, m in enumerate(msgs):
            ts = m.get("ts")
            if m.get("class") != "colin" or not isinstance(ts, (int, float)) or t - ts > 7 * 86400:
                continue
            for p in promises_in(m.get("display") or m.get("text") or "", ts):
                ask_at = p["due"] + (9 if p["remind"] else 12) * 3600
                ask_at = max(ask_at, ts + 2 * 3600)
                end = p["due"] + 23 * 3600
                if not ask_at <= t < end:
                    continue
                words = {w for w in re.findall(r"[a-z]{4,}", p["what"].lower())}
                if any(len(words & set(re.findall(r"[a-z]{4,}", (x.get("title") or "").lower()))) >= 2
                       and float(x.get("created_at") or 0) >= ts for x in tasks):
                    continue   # a board task already covers it
                later = "\n".join(f"{who.first() if n.get('role') == 'user' else bot.name}: "
                                  f"{' '.join((n.get('display') or '').split())[:300]}" for n in msgs[i + 1:i + 9])
                found.append(_promise(bot.id, m, p, later, end))
    return found


def _promise(bot_id: str, m: dict, p: dict, later: str, end: float) -> Candidate:
    zip_name = notices._bot_name(bot_id)
    said = notices.day(m["ts"])
    key = hashlib.sha1(f"{p['what']}|{int(p['due'])}".encode()).hexdigest()[:12]
    if p["remind"]:
        evidence = f"On {said} you asked {zip_name} to remind you to {p['what']} {p['when']}."
        question = "Want me to block 20 minutes for it today, or is it done?"
    else:
        evidence = f"On {said} you told {zip_name} you would {p['what']} {p['when']}."
        question = "Did it happen, or should I block 20 minutes for it today?"
    return Candidate(
        noticer="promise", subject=f"promise:{bot_id}:{key}", owner="default",
        evidence=evidence.replace("On today", "Earlier today").replace("On yesterday", "Yesterday"),
        question=question, lock=f"Did you {p['what']}?"[:60],
        facts={"who": who.first(), "what": p["what"], "when": f"said {notices.date_short(m['ts'])} for {p['when']}"},
        deadline=end, excerpt=f"{who.first()}: {m.get('display') or ''}\n{later}"[:1500],
        options=[Option("Block 20 minutes for it", "act",
                        say=f"I haven't done it yet: {p['what']}. Please put a 20-minute block on my calendar for "
                            "it today."),
                 Option("It's done", "done", follow="Good. Closed."),
                 Option("Drop it", "done", follow="Dropped. I won't ask about it again.")])


# MARK: 8. A form, signature request or invitation with an expiry

SIGN = re.compile(r"\b(docusign|hellosign|dropbox sign|adobe sign|adobe acrobat sign|pandadoc|signnow|e-?sign\w*|"
                  r"please sign|signature (request|requested|needed|required)|sign (the|your|this)|review and sign|"
                  r"waiting for your signature|form|rsvp|invitation|you're invited|you are invited|please complete|"
                  r"action required)\b", re.I)
SIGN_SENDERS = re.compile(r"@(.+\.)?(docusign\.net|docusign\.com|hellosign\.com|dropboxsign\.com|adobesign\.com|"
                          r"echosign\.com|pandadoc\.net|signnow\.com|jotform\.com|typeform\.com)$", re.I)
DONE_WORDS = re.compile(r"\b(completed|you signed|has been signed|was signed|all parties|thank you for (signing|"
                        r"submitting|your response)|response recorded|we received your)\b", re.I)
EXPIRES = re.compile(r"\b(expires?|expiring|expiration|due|deadline|by|before|until|closes|respond by|rsvp by)\b",
                     re.I)
FORM_WINDOW_DAYS = 7


async def form(src: Sources) -> list[Candidate]:
    t = _now()
    found: dict[str, Candidate] = {}
    for account in ACCOUNTS:
        inbox = await src.envelopes(account, "INBOX")
        for e in sorted(inbox, key=lambda x: -x["date"]):
            if t - e["date"] > 21 * 86400 or "answered" in e["flags"]:
                continue
            service = bool(SIGN_SENDERS.search(e["from_addr"] or ""))
            if not (service or (SIGN.search(e["subject"]) and mail.is_person(e["from_addr"], e["from_name"]))):
                continue   # a signing service, or a person; never a newsletter's "invitation"
            if DONE_WORDS.search(e["subject"]):
                continue
            ns = mail.norm_subject(e["subject"])
            if any(x["date"] > e["date"] and mail.norm_subject(x["subject"]).endswith(ns[-40:]) and
                   DONE_WORDS.search(x["subject"]) for x in inbox):
                continue   # a "completed" or "thank you for signing" notice came after it
            body = await src.body(e)
            if body is None:
                continue
            text = f"{e['subject']}\n{mail.own_text(body)}"
            if not EXPIRES.search(text):
                continue
            dates = [d for d in dates_in(text, e["date"]) if 0 <= notices.days_ahead(d) <= FORM_WINDOW_DAYS]
            if not dates:
                continue
            c = _form(account, e, min(dates), text)
            found.setdefault(c.subject, c)
    return sorted(found.values(), key=lambda c: c.deadline or 0)


def _form(account: str, e: dict, day_ts: float, text: str) -> Candidate:
    who_name = e["from_name"] or e["from_addr"].split("@")[-1]
    subject = e["subject"] or "(no subject)"
    deadline = day_ts + 17 * 3600
    on_full = datetime.fromtimestamp(day_ts, notices.ET).strftime("%A, %b %-d")
    kind = ("signature request" if re.search(r"sign", f"{subject} {e['from_addr']}", re.I) else
            "invitation" if re.search(r"rsvp|invit", subject, re.I) else "form")
    verb = {"signature request": "sign", "invitation": "answer", "form": "fill in"}[kind]
    options = [Option("Show me the link", "act",
                      say=f"Please show me the link to {verb} the {kind} from {P(who_name)} (their email \"{P(subject)}\" of "
                          f"{notices.date_short(e['date'])}) so I can do it myself. Don't sign, fill in, answer or "
                          "submit anything.")]
    remind = day_ts - 86400 + 9 * 3600
    if remind - _now() > 3 * 3600:
        remind_day = datetime.fromtimestamp(remind, notices.ET).strftime("%A")
        options.append(Option(f"Remind me {remind_day}", "wait", wait_days=(remind - _now()) / 86400))
    options.append(Option("Already done", "close"))
    return Candidate(
        noticer="form", subject=f"form:{account}:{e['from_addr']}:{mail.norm_subject(subject)[:60]}",
        owner=owner_for(account, e["from_addr"]), version=str(int(day_ts)),
        evidence=f"{who_name} sent a {kind}, \"{subject}\", and it expires {on_full}.",
        question="Want the link so you can take care of it, or a reminder the day before?",
        lock=f"A {kind} from {who_name.split()[0]} expires {notices.day(day_ts)}"[:60],
        facts={"who": who_name, "what": f"{kind} \"{subject}\"", "when": f"expires {on_full}"},
        deadline=deadline, excerpt=text[:1500], options=options)


FINDERS = {"unreplied": unreplied, "reply_waiting": reply_waiting, "waiting_in_app": waiting_in_app,
           "renewal": renewal, "promise": promise, "form": form}


def finders() -> dict:
    """Every noticer by id: these, the calendar ones and the money ones."""
    from . import calendar_noticers, money_noticers, supplies
    return {**FINDERS, **calendar_noticers.FINDERS, **money_noticers.FINDERS, **supplies.FINDERS}
