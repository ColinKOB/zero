""






















import asyncio
import hashlib
import json
import logging
import re
import sys
import time
import types
from datetime import datetime, timedelta

from . import config, home, mail, notices, settled, who
from .notices import Candidate, Option

log = logging.getLogger("taffy.noticers")
P = notices.plain


class _AllMail:
    ""

    def get(self, account, default=None):
        return mail.all_mail_folder(account) or default

    def __contains__(self, account):
        return bool(mail.all_mail_folder(account))

    def __getitem__(self, account):
        f = mail.all_mail_folder(account)
        if not f:
            raise KeyError(account)
        return f


ACCOUNTS = mail.ACCOUNTS
ALL_MAIL = _AllMail()
MAIL_FRESH_SEC = 60 * 60
PER_NOTICER = 3
BODY_READS_PER_PASS = 6





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
    ""

    name = (name or "").strip()
    if name and "@" not in name:
        return name
    return f"someone at {addr.split('@')[-1]}" if "@" in (addr or "") else (addr or "someone")


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




class SourceDown(Exception):
    ""


class Sources:
    ""

    _tally_down: str | None = None

    def __init__(self):
        self._mail: dict[tuple, tuple[float, list]] = {}
        self._bodies: dict[tuple, str] = {}
        self._replied: dict[tuple, tuple[float, bool | None]] = {}
        self.body_reads = 0
        self._booking_bodies: set[tuple] = set()
        self.booking_reads = 0

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
        ""
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

    async def booking_body(self, e: dict) -> str | None:
        ""
        from . import bookings
        key = (e["account"], e["folder"], e["id"], e["date"])
        if key in self._booking_bodies:
            return None
        if self.booking_reads >= 8:
            return None
        self.booking_reads += 1
        try:
            raw = await asyncio.to_thread(bookings.read_message, e)
        except Exception:
            log.info("booking message not read", exc_info=True)
            return None
        if len(self._booking_bodies) >= 400:
            self._booking_bodies.clear()
        self._booking_bodies.add(key)
        return raw

    async def replied(self, account: str, addr: str, after: float) -> bool | None:
        ""
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
        ""
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

    async def chat(self, bot_id: str) -> list[dict]:
        ""
        from . import hermes, history
        try:
            return history.visible(await hermes.messages(bot_id, limit=120))
        except Exception:
            log.info("chat with %s not read for noticing", bot_id, exc_info=True)
            return []

    def notes_of(self, bot_id: str) -> list[str]:
        ""
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
        ""
        return notices.calendar_events()

    def trello(self) -> list[dict]:
        from . import calcache
        return calcache.trello_cards()

    def course_site(self) -> list[dict]:
        ""
        from . import school
        return school.deadlines()

    def work_files(self, days: int = 3) -> list[tuple[str, str, float]]:
        ""
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
        ""
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
        ""

        key = ("wrote", account, addr)
        hit = self._replied.get(key)
        if hit and _now() - hit[0] < MAIL_FRESH_SEC:
            return bool(hit[1])
        try:
            found = await asyncio.to_thread(mail.envelopes, account, "Sent", 5, ["to", addr])
            answer = any(any(p[1] == addr for p in x["to"]) for x in found)
        except Exception:
            answer = False
        self._replied[key] = (_now(), answer)
        return answer

    async def purchases(self, start_days: int = 200, end_days: int = 0) -> list[dict]:
        ""


        if not home.tally():
            return []
        tools = _plugin_tools()
        if tools is None:
            return []
        day = lambda ago: datetime.fromtimestamp(_now() - ago * 86400, notices.ET).strftime("%Y-%m-%d")
        ask = {"action": "purchases", "start_date": day(start_days)}
        if end_days:
            ask["end_date"] = day(end_days)



        try:
            out = json.loads(await asyncio.to_thread(tools.tally, ask))
            err = out.get("error") if isinstance(out, dict) else "Tally answered with something other than an object"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
        rows = out.get("purchases") if not err else None
        if not isinstance(rows, list):
            err = str(err or "Tally answered without a purchases list")
            if err != self._tally_down:
                log.warning("Tally not read for noticing; the charge and bill checks wait until it is: %s", err)
            self._tally_down = err
            raise SourceDown(err)
        self._tally_down = None
        return [r for r in rows if isinstance(r, dict)]

    def notes(self) -> list[str]:
        ""
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
_PLUGIN_PKG = "taffy_hub_plugin"


def _plugin_tools():
    ""
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



    pkg = types.ModuleType(_PLUGIN_PKG)
    pkg.__path__ = [str(path.parent)]
    sys.modules.setdefault(_PLUGIN_PKG, pkg)
    spec = importlib.util.spec_from_file_location(f"{_PLUGIN_PKG}.tools", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    _TOOLS = mod
    return mod


SOURCES = Sources()



WAKES = {
    "timer": ("unreplied", "reply_waiting", "renewal", "form", "school_deadline", "due_soon",
              "conflict", "booking", "prep", "followup", "window", "charge", "bill", "running_low"),
    "mail": ("unreplied", "reply_waiting", "renewal", "form", "booking"),
    "calendar": ("conflict", "booking", "prep", "followup", "school_deadline", "due_soon"),
    "trello": ("school_deadline", "due_soon"),
    "call": ("followup",),
    "quiet": ("unreplied", "reply_waiting", "renewal", "promise", "form", "school_deadline",
              "due_soon", "conflict", "booking", "prep", "followup", "window", "charge", "bill", "running_low"),
    "promise": ("promise",),
    "night": ("due_soon", "prep", "form"),
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
        except SourceDown:
            found = []
        except Exception:
            log.exception("noticer %s failed", name)
            found = []


        fresh = [c for c in found if not notices.settled(c)]
        fresh.sort(key=lambda c: (c.deadline is None, c.deadline or 0, -float(c.facts.get("_at") or 0)))
        out.extend(fresh[:PER_NOTICER])
    return out


async def check_new_mail(src: Sources | None = None) -> bool:
    ""
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




WAIT_BUSINESS_DAYS = 5
SENT_WINDOW_DAYS = 21


async def unreplied(src: Sources) -> list[Candidate]:
    t = _now()
    found = []
    for account in ACCOUNTS:
        latest: dict[tuple, dict] = {}
        names: dict[str, str] | None = None
        for e in await src.envelopes(account, "Sent"):
            people = [p for p in e["to"] if mail.is_person(p[1])]
            if t - e["date"] > SENT_WINDOW_DAYS * 86400 or len(e["to"]) != 1 or len(people) != 1:
                continue
            key = (people[0][1], mail.norm_subject(e["subject"]))
            if key not in latest or e["date"] > latest[key]["date"]:
                latest[key] = {**e, "who": people[0]}
        for (addr, ns), e in sorted(latest.items(), key=lambda kv: -kv[1]["date"]):
            if t < business_days_after(e["date"], WAIT_BUSINESS_DAYS):
                continue
            if notices.settled(_unreplied(account, e, addr, ns)):
                continue
            replied = await src.replied(account, addr, e["date"])
            if replied is not False:
                continue
            body = await src.body(e)
            if body is None or not mail.asks_something(body):
                continue
            if not (e["who"][0] or "").strip():
                if names is None:
                    names = {x["from_addr"].lower(): x["from_name"] for x in await src.envelopes(account, "INBOX")
                             if x.get("from_name") and "@" not in x["from_name"]}
                e = {**e, "who": (names.get(addr.lower(), ""), addr)}
            c = _unreplied(account, e, addr, ns)
            if await settled.by_text(c, e["who"][0], addr, e["date"], mail.own_text(body), theirs=True):
                continue
            c.facts["_at"] = e["date"]
            c.prepare = {"bot": c.owner}
            found.append(c)
    return found


def _unreplied(account: str, e: dict, addr: str, ns: str) -> Candidate:
    name = _first(e["who"][0], addr)
    first = name if name.startswith("someone at ") else name.split()[0] if " " not in addr and name != addr else name
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




REPLY_AFTER_SEC = 48 * 3600
REPLY_WINDOW_DAYS = 14


REGULAR_SENDS = 3
REGULAR_DAYS = 120


async def regular_sender(src: Sources, account: str, addr: str) -> bool:
    ""

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
        checked: set[str] = set()
        for e in sorted(inbox, key=lambda x: -x["date"]):
            age = t - e["date"]
            if age < REPLY_AFTER_SEC or age > REPLY_WINDOW_DAYS * 86400:
                continue
            if not mail.is_person(e["from_addr"], e["from_name"]) or "answered" in e["flags"]:
                continue
            if mail.is_notification(e["subject"]):
                continue
            ns = mail.norm_subject(e["subject"])
            if sent is None:
                sent = await src.envelopes(account, "Sent")
            if any(s["date"] > e["date"] and mail.norm_subject(s["subject"]) == ns
                   and any(p[1] == e["from_addr"] for p in s["to"]) for s in sent):
                continue
            old = notices.row(f"mail-in:{account}:{e['from_addr']}:{ns}")
            if old and old["state"] in ("picked", "dismissed", "stopped", "handled", "preparing", "open"):
                continue
            if await regular_sender(src, account, e["from_addr"]):
                continue
            body = await src.body(e)
            if body is None or not mail.asks_something(body) or mail.is_notification("", body) or mail.is_bulk(body):
                continue
            c = _reply_waiting(account, e, ns, mail.own_text(body))


            newest = c.subject not in checked
            checked.add(c.subject)
            if await settled.by_text(c, e["from_name"], e["from_addr"], e["date"], mail.own_text(body), newest=newest):
                continue
            c.facts["_at"] = e["date"]
            if await src.wrote_to(account, e["from_addr"]):
                c.prepare = {"bot": c.owner}
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











def close_doubles() -> int:
    ""

    rows = notices._db().execute(
        f"SELECT * FROM notices WHERE noticer='waiting_in_app' AND (state='open' OR (state='expired' AND end_reason "
        f"IN ({','.join('?' * len(notices.STEP_BACK))})))", notices.STEP_BACK).fetchall()
    for r in rows:
        notices.passed(dict(r), f"{notices._bot_name(r['owner'])} asks this on its own card.")
    if rows:
        log.info("closed %d waiting card(s) that repeated a Zip's own card", len(rows))
    return len(rows)




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
    ""

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


_DOMAIN = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:app|com|net|org|io|dev|tech|co|ai|me|us|site|xyz|shop|store))\b", re.I)

_DECIDED = re.compile(r"\b(decided (not )?to (renew|keep|cancel|let)|decided against|(not to|won't|don't|will not|do not) "
                      r"renew|(already |was |is |been )cancel(l?ed)|cancel(l?ed) it|let it (lapse|expire))\b", re.I)
_UNDECIDED = re.compile(r"\b(haven't|hasn't|have not|has not|not yet|un)decided\b|\bdecision not made\b|"
                        r"\bnot (yet )?(sure|decided)\b", re.I)


def _registrable(domain: str) -> str:
    return ".".join(domain.lower().strip(".").split(".")[-2:])


def _renewed_thing(e: dict, text: str) -> str:
    ""


    own = _registrable(e["from_addr"].split("@")[-1]) if "@" in (e.get("from_addr") or "") else ""
    for m in _DOMAIN.finditer(text):
        if _registrable(m.group(1)) != own:
            return m.group(1).lower()
    return own or e["from_name"] or "A subscription"


def _decided(thing: str, notes: list[str]) -> bool:
    ""
    key = thing.lower()
    return any(key in n.lower() and _DECIDED.search(n) and not _UNDECIDED.search(n) for n in notes)


async def renewal(src: Sources) -> list[Candidate]:
    t = _now()
    found: dict[str, Candidate] = {}
    notes = src.notes()
    for account in ACCOUNTS:
        for e in sorted(await src.envelopes(account, "INBOX"), key=lambda x: -x["date"]):
            if t - e["date"] > 45 * 86400 or not RENEW.search(e["subject"]):
                continue
            body = await src.body(e)
            text = f"{e['subject']}\n{mail.own_text(body or '')}"
            dates = [d for d in dates_in(text, e["date"]) if _in_window(d)]
            if not dates:
                continue
            thing = _renewed_thing(e, text)
            if _decided(thing, notes):
                continue
            m = _MONEY.search(text)
            c = _renewal(thing, min(dates), m.group(0) if m else "",
                         f"an email from {thing} on {notices.date_short(e['date'])}", text,
                         "builder" if HOSTING.search(text) else "money")
            found.setdefault(c.subject, c)
    for note in notes:
        if not RENEW.search(note):
            continue
        dates = [d for d in dates_in(note, t) if _in_window(d)]
        if not dates:
            continue
        m = _DOMAIN.search(note)
        thing = m.group(1).lower() if m else " ".join(note.split()[:4])
        if _decided(thing, notes):
            continue
        money = _MONEY.search(note)
        c = _renewal(thing, min(dates), money.group(0) if money else "", "your notes", note,
                     "builder" if HOSTING.search(note) else "money")
        found.setdefault(c.subject, c)


    from . import money_noticers
    for name, when, amount, text in await money_noticers.renewing(src):
        said = money_noticers.words(name)
        if _decided(name.lower(), notes) or any(said & money_noticers.words(f"{c.facts['what']} {c.excerpt}")
                                                for c in found.values()):
            continue
        c = _renewal(name, when, money_noticers.money(amount), "your card's charges", text,
                     "builder" if HOSTING.search(name) else "money")
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




PROMISE = re.compile(
    r"\b(?P<lead>i'll|i will|i'm going to|i am going to|i need to|i have to|i gotta|remind me to|remind me)\b"
    r"(?P<what>[^.?!\n]{3,120}?)\s*\b(?P<when>tomorrow|tonight|later today|this afternoon|this evening|today|"
    r"this weekend|next week|on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|monday|tuesday|"
    r"wednesday|thursday|friday|saturday|sunday)\b", re.I)
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


ASK_HOUR = {"this afternoon": 17, "this evening": 20, "tonight": 20}


def promises_in(text: str, ts: float) -> list[dict]:
    ""
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
        remind = m.group("lead").lower().startswith("remind")
        out.append({"what": what, "when": m.group("when"), "due": due.timestamp(), "remind": remind,

                    "ask": due.replace(hour=ASK_HOUR.get(when, 9 if remind else 12)).timestamp()})
    return out


async def promise(src: Sources) -> list[Candidate]:
    t = _now()
    found = []
    groups = await src.board()
    tasks = [x for g in groups.values() for x in g]
    for bot in list(config.BOTS):
        if bot.sandbox:
            continue
        msgs = await src.chat(bot.id)
        for i, m in enumerate(msgs):
            ts = m.get("ts")
            if m.get("class") != "colin" or not isinstance(ts, (int, float)) or t - ts > 7 * 86400:
                continue
            for p in promises_in(m.get("display") or m.get("text") or "", ts):
                ask_at = max(p["ask"], ts + 2 * 3600)
                end = p["due"] + 23 * 3600
                if not ask_at <= t < end:
                    continue
                words = {w for w in re.findall(r"[a-z]{4,}", p["what"].lower())}
                if any(len(words & set(re.findall(r"[a-z]{4,}", (x.get("title") or "").lower()))) >= 2
                       and float(x.get("created_at") or 0) >= ts for x in tasks):
                    continue
                later = "\n".join(f"{who.first() if n.get('role') == 'user' else bot.name}: "
                                  f"{' '.join((n.get('display') or '').split())[:300]}" for n in msgs[i + 1:i + 9])
                found.append(_promise(bot.id, m, p, later, end))
    return found


def _promise(bot_id: str, m: dict, p: dict, later: str, end: float) -> Candidate:
    zip_name = "me" if bot_id == "default" else notices._bot_name(bot_id)
    said = notices.day(m["ts"])
    key = hashlib.sha1(f"{p['what']}|{int(p['due'])}".encode()).hexdigest()[:12]
    if p["remind"]:
        evidence = f"On {said} you asked {zip_name} to remind you to {p['what']} {p['when']}."
        question = "Want me to find time for it today, or is it done?"
    else:
        evidence = f"On {said} you told {zip_name} you would {p['what']} {p['when']}."
        question = "Did it happen, or should I find time for it today?"
    return Candidate(
        noticer="promise", subject=f"promise:{bot_id}:{key}", owner="default",
        evidence=evidence.replace("On today", "Earlier today").replace("On yesterday", "Yesterday"),
        question=question, lock=f"Did you {p['what']}?"[:60],
        facts={"who": who.first(), "what": p["what"], "when": f"said {notices.date_short(m['ts'])} for {p['when']}"},
        deadline=end, excerpt=f"{who.first()}: {m.get('display') or ''}\n{later}"[:1500],
        options=[Option("Find time for it today", "act",
                        say=f"I haven't done it yet: {p['what']}. Please find time for it on my calendar today."),
                 Option("It's done", "done", follow="Good. Closed."),
                 Option("Drop it", "done", follow="Dropped. I won't ask about it again.")])




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
                continue
            if DONE_WORDS.search(e["subject"]):
                continue
            ns = mail.norm_subject(e["subject"])
            if any(x["date"] > e["date"] and mail.norm_subject(x["subject"]).endswith(ns[-40:]) and
                   DONE_WORDS.search(x["subject"]) for x in inbox):
                continue
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


FINDERS = {"unreplied": unreplied, "reply_waiting": reply_waiting, "renewal": renewal, "promise": promise, "form": form}


def finders() -> dict:
    ""
    from . import booking_noticers, calendar_noticers, money_noticers, supplies
    return {**FINDERS, **calendar_noticers.FINDERS, **booking_noticers.FINDERS,
            **money_noticers.FINDERS, **supplies.FINDERS}
