""
























import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta

from . import config, home, mail, shortreply, store, triggers, who

log = logging.getLogger("taffy_hub.waits")

MAX_OPEN = 40
MAX_DAYS = 60
DEFAULT_DAYS = 7
SCAN = (20, 100, 300)
MARGIN = 3600
BUSY_SEC, IDLE_SEC = 120, 600



RETRY_DELAYS = (60, 300, 900, 1800, 3600, 3600, 3600)
BLIND_TELL_SEC = 86400
OVERDUE_EVERY_SEC = 86400
OVERDUE_FROM_HOUR = 9
WHAT_MAX, STEP_MAX, SUBJECT_MAX, FROM_MAX = 300, 600, 200, 10
ADDRESS = re.compile(r"^[^@\s]+@[a-z0-9.-]+\.[a-z]{2,}$|^@[a-z0-9.-]+\.[a-z]{2,}$")


class Invalid(ValueError):
    ""


def now() -> float:
    return time.time()




def parse_due(text: str | None, at: float | None = None) -> float:
    ""

    at = now() if at is None else at
    tz = home.zone()
    base = datetime.fromtimestamp(at, tz)
    s = " ".join(str(text or "").lower().split())
    if not s:
        due = at + DEFAULT_DAYS * 86400
    elif m := re.fullmatch(r"in (\d{1,3}) (hour|day|week)s?", s):
        due = at + int(m.group(1)) * {"hour": 3600, "day": 86400, "week": 7 * 86400}[m.group(2)]
    elif s in ("tomorrow", "today"):
        day = base + timedelta(days=1 if s == "tomorrow" else 0)
        due = day.replace(hour=17, minute=0, second=0, microsecond=0).timestamp()
    else:
        try:
            dt = datetime.fromisoformat(s.upper() if "t" in s else s)
        except ValueError:
            raise Invalid(f"I can't read the date {text!r}. Use a date like 2026-10-20, or 'in 3 days'.") from None
        if dt.tzinfo is None:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                dt = dt.replace(hour=17)
            dt = dt.replace(tzinfo=tz)
        due = dt.timestamp()
    if due <= at:
        raise Invalid("That date has already passed. Give one in the future.")
    return min(due, at + MAX_DAYS * 86400)


def say_date(ts: float) -> str:
    ""
    d = datetime.fromtimestamp(ts, home.zone())
    today = datetime.fromtimestamp(now(), home.zone())
    if d.date() == today.date():
        return f"{d.strftime('%I:%M %p').lstrip('0')} today"
    out = f"{d:%A}, {d:%b} {d.day}"
    return out if d.year == today.year else f"{out}, {d.year}"


def _who(expect: list[str]) -> str:
    ""

    addrs = [e for e in expect if not e.startswith("@")]
    doms = [e[1:] for e in expect if e.startswith("@")
            and not any(a.rsplit("@", 1)[1] == e[1:] or a.rsplit("@", 1)[1].endswith("." + e[1:]) for a in addrs)]
    return ", ".join((addrs + doms)[:2])




def zips_own(account: dict) -> bool:
    return bool(account.get("zips_own")) and bool(account.get("owner_zip"))


def own_mailboxes() -> dict[str, str]:
    ""
    return {str(a["id"]): str(a["owner_zip"]) for a in home.mail_accounts() if zips_own(a)}


def readable_by(bot_id: str) -> list[str]:
    ""
    out = []
    for a in home.mail_accounts():
        if zips_own(a) and str(a["owner_zip"]) != bot_id:
            continue
        out.append(str(a["id"]))
    return out




@store.schema
def _wake_schema(conn) -> None:
    columns = {r[1] for r in conn.execute("PRAGMA table_info(wake_notes)")}
    for name, definition in (("state", "TEXT NOT NULL DEFAULT 'pending'"),
                             ("attempts", "INTEGER NOT NULL DEFAULT 0"),
                             ("retry_at", "REAL NOT NULL DEFAULT 0"),
                             ("wait_id", "INTEGER"), ("failure_feed_id", "INTEGER")):
        if name not in columns:
            conn.execute(f"ALTER TABLE wake_notes ADD COLUMN {name} {definition}")


@store.schema
def _task_schema(conn) -> None:
    ""

    columns = {r[1] for r in conn.execute("PRAGMA table_info(waits)")}
    for name, definition in (("task_id", "TEXT NOT NULL DEFAULT ''"), ("task_settled", "REAL"),
                             ("next_look", "REAL NOT NULL DEFAULT 0"), ("blind_told", "REAL")):
        if name not in columns:
            conn.execute(f"ALTER TABLE waits ADD COLUMN {name} {definition}")


def _clean_from(values) -> list[str]:
    out = []
    for v in values or []:
        v = str(v or "").strip().lower()
        m = re.search(r"<([^>]+)>", v)
        v = (m.group(1) if m else v).strip()
        if v and not v.startswith("@") and "@" not in v and "." in v:
            v = "@" + v
        if not ADDRESS.match(v):
            raise Invalid(f"{v!r} is not an email address or @domain.")
        if v not in out:
            out.append(v)
    if len(out) > FROM_MAX:
        raise Invalid(f"At most {FROM_MAX} addresses or domains per wait.")
    return out


def _row(r) -> dict:
    d = dict(r)
    d["expect_from"] = json.loads(d.get("expect_from") or "[]")
    d["answer"] = json.loads(d["answer"]) if d.get("answer") else None
    return d


def start(bot_id: str, session_id: str | None, what: str, expect_from=None, subject: str = "",
          due_ts: float | None = None, next_step: str = "", account: str = "", source: str = "tool",
          thread: str = "", task_id: str = "") -> dict:
    what = " ".join(str(what or "").split())
    if not what:
        raise Invalid("Say what you are waiting on, in a few words.")
    if bot_id not in config.BOTS_BY_ID:
        raise Invalid("unknown Zip")
    expect = _clean_from(expect_from)
    account = str(account or "").strip()
    if account and account not in readable_by(bot_id):
        raise Invalid(f"There is no mailbox {account!r} you can watch.")
    t = now()
    due = due_ts if due_ts is not None else parse_due("", t)
    if due <= t:
        raise Invalid("That date has already passed. Give one in the future.")
    due = min(due, t + MAX_DAYS * 86400)
    with store._lock:
        c = store.db()
        n = c.execute("SELECT COUNT(*) FROM waits WHERE bot_id=? AND state='open'", (bot_id,)).fetchone()[0]
        if n >= MAX_OPEN:
            raise Invalid(f"You already wait on {n} things. Close the ones that are settled first (errands list).")
        wid = c.execute(
            "INSERT INTO waits (bot_id, session_id, what, expect_from, subject, account, thread, next_step, due, "
            "state, created, updated, source, task_id) VALUES (?,?,?,?,?,?,?,?,?, 'open', ?,?,?,?)",
            (bot_id, session_id or None, what[:WHAT_MAX], json.dumps(expect), " ".join(str(subject or "").split())
             [:SUBJECT_MAX], account, re.sub(r"[^A-Za-z0-9]", "", str(thread or ""))[:40],
             " ".join(str(next_step or "").split())[:STEP_MAX], due, t, t, source,
             re.sub(r"[^A-Za-z0-9_-]", "", str(task_id or ""))[:64])).lastrowid
        c.commit()
    return get(wid)


def get(wait_id: int) -> dict | None:
    r = store.db().execute("SELECT * FROM waits WHERE id=?", (int(wait_id),)).fetchone()
    return _row(r) if r else None


def listed(bot_id: str | None = None, open_only: bool = True) -> list[dict]:
    q = "SELECT * FROM waits WHERE 1=1" + (" AND bot_id=?" if bot_id else "") + \
        (" AND state='open'" if open_only else "") + " ORDER BY due"
    return [_row(r) for r in store.db().execute(q, (bot_id,) if bot_id else ())]


def _set(wait_id: int, **cols) -> None:
    cols["updated"] = now()
    with store._lock:
        store.db().execute(f"UPDATE waits SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?",
                           (*cols.values(), int(wait_id)))
        store.db().commit()


def close(bot_id: str, wait_id: int, why: str = "") -> bool:
    w = get(wait_id)
    if not w or w["bot_id"] != bot_id or w["state"] not in ("open", "overdue", "answered"):
        return False
    _set(wait_id, state="closed", close_reason=" ".join(str(why or "done").split())[:200])
    return True


def extend(bot_id: str, wait_id: int, due_ts: float) -> bool:
    ""
    with store._lock:
        n = store.db().execute("UPDATE waits SET state='open', due=?, updated=? WHERE id=? AND bot_id=? AND "
                               "state != 'closed'", (min(due_ts, now() + MAX_DAYS * 86400), now(), int(wait_id),
                                                     bot_id)).rowcount
        store.db().commit()
    return n > 0


def close_for_mailbox(account: str, keep_bot: str | None, why: str) -> int:
    ""

    with store._lock:
        c = store.db()
        rows = c.execute("SELECT * FROM waits WHERE account=? AND state='open' AND bot_id != ?",
                         (account, keep_bot or "")).fetchall()
        for r in rows:
            c.execute("UPDATE waits SET state='closed', close_reason=?, updated=? WHERE id=?", (why, now(), r["id"]))
            c.execute("INSERT INTO wake_notes (bot_id, note, at, started) VALUES (?,?,?,0)", (
                r["bot_id"], f"{who.hub_note()} Your wait {r['id']} about \"{r['what']}\" ended: {why} The reply "
                f"can't reach you there now. Tell {who.first()} in one line what that errand still needs, or start "
                "a new wait on a mailbox you can read.", now()))
        c.commit()
    return len(rows)




def _words(s: str) -> list[str]:
    return [w for w in mail.norm_subject(s).split() if not w.isdigit()]


def _subject_fits(want: str, got: str) -> bool:
    if not want:
        return True
    a, b = _words(want), _words(got)
    if not a:
        return True
    joined_a, joined_b = " ".join(a), " ".join(b)
    if joined_a and joined_a in joined_b:
        return True
    shared = set(a) & set(b)
    return len(shared) >= min(4, len(set(a)))


def _sender_fits(expect: list[str], addr: str) -> bool:
    addr = (addr or "").lower().strip()
    if "@" not in addr:
        return False
    dom = addr.rsplit("@", 1)[1]
    for e in expect:
        if e.startswith("@"):
            d = e[1:]
            if dom == d or dom.endswith("." + d):
                return True
        elif addr == e:
            return True
    return False


def routine_fits(t: dict, env: dict) -> bool:
    ""

    if env.get("date", 0) <= t["created"] - 60:
        return False
    if t.get("mail_account") and env.get("account") != t["mail_account"]:
        return False
    if (env.get("from_addr") or "").lower() in mail.OWN:
        return False
    return _sender_fits(t["mail_from"], env.get("from_addr", "")) and \
        _subject_fits(t.get("mail_subject") or "", env.get("subject", ""))


def matches(w: dict, env: dict) -> bool:
    ""

    if env.get("date", 0) <= w["created"] - 60:
        return False
    if w.get("account") and env.get("account") != w["account"]:
        return False
    if w.get("thread") and env.get("thread") == w["thread"]:

        return env.get("date", 0) > w["created"] and (env.get("from_addr") or "").lower() not in mail.OWN
    if not w.get("expect_from"):
        return False
    return _sender_fits(w["expect_from"], env.get("from_addr", "")) and \
        _subject_fits(w.get("subject", ""), env.get("subject", ""))




def reply_note(w: dict, env: dict) -> str:
    plan = f" Then do what you planned: {w['next_step']}" if w.get("next_step") else \
        " Then finish what you started."
    return (f"{who.hub_note()} Errand update (wait {w['id']}). The reply you were waiting for about \"{w['what']}\" "
            f"arrived in the {env['account']} mailbox (folder {env.get('folder') or 'INBOX'}, id {env['id']}). Read "
            f"it with the email tool.{plan} Tell {who.first()} the result in a line or two. If it needs "
            f"{who.first()}, ask then. If the reply only says they are on it, keep waiting with errands extend.")


def overdue_note(w: dict) -> str:
    plan = f" Your plan was: {w['next_step']}" if w.get("next_step") else ""
    if not w.get("expect_from") and not w.get("thread"):
        return (f"{who.hub_note()} Errand check (wait {w['id']}). Today is the day you set to check on "
                f"\"{w['what']}\".{plan} Check it now (the site, the order or the mail), then tell {who.first()} "
                "what you found in a line or two. If the plan was to ask whether they want a message, draft or note "
                "ready, have it ready now instead and lead with it (a draft, or a mail card, which sends nothing until "
                f"{who.first()} taps it). Wait again with errands wait if it is still not settled.")
    return (f"{who.hub_note()} Errand update (wait {w['id']}). No reply about \"{w['what']}\" by "
            f"{say_date(w['due'])}.{plan} Decide the next step yourself: check its status on the site, send one "
            "short follow-up (it goes on a card as usual unless a rule covers it), wait longer with errands extend, "
            f"or tell {who.first()} it is stuck and what you suggest. Don't ask {who.first()} to check back.")


def new_mail_note(bot_id: str, env: dict) -> str:
    return (f"{who.hub_note()} New mail in your own mailbox ({env['account']}, folder INBOX, id {env['id']}) from "
            f"someone you wrote to before. Read it with the email tool and handle it as part of that errand. Tell "
            f"{who.first()} only if it needs them or settles something they asked for.")




def note_sent(bot_id: str, account: str, recipients) -> int:
    ""
    if own_mailboxes().get(account) != bot_id:
        return 0
    n = 0
    with store._lock:
        c = store.db()
        for r in recipients or []:
            m = re.search(r"[^<\s]+@[^>\s]+", str(r or ""))
            if not m:
                continue
            c.execute("INSERT INTO correspondents (bot_id, account, address, last_sent) VALUES (?,?,?,?) "
                      "ON CONFLICT(bot_id, account, address) DO UPDATE SET last_sent=excluded.last_sent",
                      (bot_id, account, m.group(0).lower(), now()))
            n += 1
        c.commit()
    return n


def _correspondent(bot_id: str, account: str, addr: str) -> bool:
    return store.db().execute("SELECT 1 FROM correspondents WHERE bot_id=? AND account=? AND address=?",
                              (bot_id, account, (addr or "").lower())).fetchone() is not None


def _seen(account: str, msg_id: str) -> bool:
    return store.db().execute("SELECT 1 FROM mail_seen WHERE account=? AND msg_id=?",
                              (account, msg_id)).fetchone() is not None


def _mark_seen(account: str, msg_id: str) -> None:
    with store._lock:
        store.db().execute("INSERT OR IGNORE INTO mail_seen (account, msg_id, at) VALUES (?,?,?)",
                           (account, msg_id, now()))
        store.db().execute("DELETE FROM mail_seen WHERE at < ?", (now() - 90 * 86400,))
        store.db().commit()




def blind_note(w: dict, since: float) -> str:
    return (f"{who.hub_note()} Errand check (wait {w['id']}). I could not read the mailbox for \"{w['what']}\" since "
            f"{say_date(since)}, so I don't know whether a reply came. Don't tell {who.first()} that nobody replied. I "
            "will keep looking and wake you when I can read it. If this can't wait, check another way or say what you "
            "could not check.")


def overdue_again_note(rows: list[dict]) -> str:
    items = "\n".join(f"- Wait {w['id']}: \"{w['what']}\", due {say_date(w['due'])}."
                      + (f" Your plan was: {w['next_step']}" if w.get("next_step") else "") for w in rows)
    return (f"{who.hub_note()} Errand check. These waits of yours are past their date and still open:\n{items}\n"
            "Settle each one now: do the next step, close it with errands close if it no longer matters, or give it a "
            f"new date with errands extend. Tell {who.first()} only what changes something for them. If nothing does, "
            "reply [SILENT].")


def _overdue_due(t: float) -> dict[str, list[dict]]:
    ""
    if datetime.fromtimestamp(t, home.zone()).hour < OVERDUE_FROM_HOUR:
        return {}
    out: dict[str, list[dict]] = {}
    for r in store.db().execute("SELECT * FROM waits WHERE state='overdue' AND next_look<=? ORDER BY due", (t,)):
        w = _row(r)
        out.setdefault(w["bot_id"], []).append(w)
    return out


def _since(account: str, open_waits: list[dict], t: float) -> float:
    ""

    last = store.get_setting(f"waits_scanned:{account}")
    if last:
        return float(last) - MARGIN
    created = [w["created"] for w in open_waits if not w["account"] or w["account"] == account]
    return min(created + [t - 3 * 86400])


def _scan(account: str, since: float) -> list[dict]:
    ""

    envs: list[dict] = []
    for size in SCAN:
        envs = mail.envelopes(account, "INBOX", size)
        if len(envs) < size or min(e["date"] for e in envs) <= since:
            break
    else:
        log.warning("waits: more than %d new messages in %s since the last pass; older ones go unread", SCAN[-1],
                    account)
    return [e for e in envs if e["date"] > since]


async def _wake(bot_id: str, note: str, on_start=None, skip_if=None) -> None:
    from . import turns
    await turns.wake(bot_id, note, on_start=on_start, skip_if=skip_if, raise_on_failure=True)


def _commit_wake(bot_id: str, note: str, wait: tuple[int, dict] | None = None,
                 seen: tuple[str, str] | None = None) -> bool:
    ""

    with store._lock:
        c = store.db()
        if wait is not None:
            wid, cols = wait
            cols = {**cols, "updated": now()}


            if not c.execute(f"UPDATE waits SET {', '.join(f'{k}=?' for k in cols)} WHERE id=? AND state='open'",
                             (*cols.values(), wid)).rowcount:
                c.rollback()
                return False
        if seen is not None:
            c.execute("INSERT OR IGNORE INTO mail_seen (account, msg_id, at) VALUES (?,?,?)", (*seen, now()))
        c.execute("INSERT INTO wake_notes (bot_id, note, at, started, wait_id) VALUES (?,?,?,0,?)",
                  (bot_id, note, now(), wait[0] if wait else None))
        c.commit()
    return True


async def check_once(at: float | None = None) -> list[tuple[str, str]]:
    ""

    t = now() if at is None else at
    open_waits = listed()
    woke: list[tuple[str, str]] = []
    owned = own_mailboxes()
    routines = triggers.mail_routines()
    accounts = set(owned)
    for w in open_waits:
        if w["expect_from"] or w.get("thread"):
            accounts.update([w["account"]] if w["account"] else readable_by(w["bot_id"]))
    for r in routines:
        accounts.update([r["mail_account"]] if r["mail_account"] else readable_by(r["bot_id"]))
    envs: dict[str, list[dict]] = {}
    blind: dict[str, float] = {}
    for account in sorted(accounts):
        since = _since(account, open_waits, t)
        try:
            envs[account] = await asyncio.to_thread(_scan, account, since)
            if store.get_setting(f"waits_blind:{account}") is not None:
                store.set_setting(f"waits_blind:{account}", None)
        except mail.MailError as e:
            log.info("waits: could not read %s: %s", account, e)
            envs[account] = []
            blind[account] = float(store.get_setting(f"waits_blind:{account}") or t)
            store.set_setting(f"waits_blind:{account}", blind[account])
    claimed: set[tuple[str, str]] = set()
    answered: dict[tuple[str, str], str] = {}
    for w in open_waits:
        if not w["expect_from"] and not w.get("thread"):
            continue
        mine = [w["account"]] if w["account"] else readable_by(w["bot_id"])
        hits = [e for a in mine for e in envs.get(a, []) if matches(w, e) and (a, e["id"]) not in claimed
                and not _seen(a, e["id"])]
        if not hits:
            continue
        env = min(hits, key=lambda e: e["date"])
        claimed.add((env["account"], env["id"]))
        note = reply_note(w, env)
        if _commit_wake(w["bot_id"], note, wait=(w["id"], {
                "state": "answered", "wakes": w["wakes"] + 1,
                "answer": json.dumps({k: env.get(k) for k in ("account", "folder", "id", "from_addr", "date")})}),
                seen=(env["account"], env["id"])):
            woke.append((w["bot_id"], note))
            answered[(env["account"], env["id"])] = w["bot_id"]
        else:
            claimed.discard((env["account"], env["id"]))
    for r in routines:
        mine = [r["mail_account"]] if r["mail_account"] else readable_by(r["bot_id"])
        for env in sorted((e for a in mine for e in envs.get(a, []) if routine_fits(r, e)), key=lambda e: e["date"]):
            key = (env["account"], env["id"])
            note = triggers.take_mail(r, env, run=answered.get(key) != r["bot_id"])
            if note is None:
                continue
            woke.append((r["bot_id"], note))
            if env["account"] in owned:
                claimed.add(key)
                _mark_seen(*key)
    for w in open_waits:
        if w["due"] <= t and get(w["id"])["state"] == "open":


            unread = [blind[a] for a in ([w["account"]] if w["account"] else readable_by(w["bot_id"])) if a in blind] \
                if w["expect_from"] or w.get("thread") else []
            if unread:
                if t - min(unread) >= BLIND_TELL_SEC and not w.get("blind_told"):
                    note = blind_note(w, min(unread))
                    if _commit_wake(w["bot_id"], note, wait=(w["id"], {"blind_told": t})):
                        woke.append((w["bot_id"], note))
                continue
            note = overdue_note(w)
            if _commit_wake(w["bot_id"], note, wait=(w["id"], {"state": "overdue", "wakes": w["wakes"] + 1,
                                                                "next_look": t + OVERDUE_EVERY_SEC})):
                woke.append((w["bot_id"], note))

    for bot_id, rows in _overdue_due(t).items():
        note = overdue_again_note(rows)
        with store._lock:
            c = store.db()
            c.execute(f"UPDATE waits SET next_look=?, wakes=wakes+1 WHERE id IN ({','.join('?' * len(rows))})",
                      (t + OVERDUE_EVERY_SEC, *[w["id"] for w in rows]))
            c.execute("INSERT INTO wake_notes (bot_id, note, at, started) VALUES (?,?,?,0)", (bot_id, note, t))
            c.commit()
        woke.append((bot_id, note))
    for account, bot_id in owned.items():
        for env in envs.get(account, []):
            if (account, env["id"]) in claimed or _seen(account, env["id"]):
                continue
            if env["date"] >= t - 3 * 86400 and _correspondent(bot_id, account, env.get("from_addr", "")):
                note = new_mail_note(bot_id, env)
                _commit_wake(bot_id, note, seen=(account, env["id"]))
                woke.append((bot_id, note))
            else:
                _mark_seen(account, env["id"])
    for account, items in envs.items():
        if items:


            store.set_setting(f"waits_scanned:{account}", min(t, max(e["date"] for e in items)))
    pump()
    if wait_for_wakes and _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)
    return woke


_pending: set = set()
_inflight: set[int] = set()
_loop: asyncio.AbstractEventLoop | None = None
wait_for_wakes = False


def queue_wake(bot_id: str, note: str, delay_sec: float = 0, wait_id: int | None = None) -> int:
    ""

    with store._lock:
        nid = store.db().execute("INSERT INTO wake_notes (bot_id, note, at, started, retry_at, wait_id) "
                                 "VALUES (?,?,?,0,?,?)", (bot_id, note, now(), now() + delay_sec if delay_sec else 0,
                                                         wait_id)).lastrowid
        store.db().commit()
    return nid


def _start_wake(bot_id: str, note: str) -> None:
    ""
    queue_wake(bot_id, note)
    pump()


def kick() -> None:
    ""
    loop = _loop
    if loop is None or not loop.is_running():
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
    loop.call_soon_threadsafe(pump)


def pump() -> int:
    ""


    rows = store.db().execute("SELECT id, bot_id, note, tell_id FROM wake_notes WHERE started=0 "
                              "AND state IN ('pending', 'retry') AND retry_at<=? ORDER BY id", (now(),)).fetchall()
    n = 0
    for r in rows:
        if r["id"] in _inflight:
            continue
        _inflight.add(r["id"])
        task = asyncio.get_running_loop().create_task(_run_wake(r["id"], r["bot_id"], r["note"], r["tell_id"]))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
        n += 1
    return n


def _mark_started(nid: int) -> None:
    with store._lock:
        store.db().execute("UPDATE wake_notes SET started=1 WHERE id=?", (nid,))
        store.db().commit()


async def _run_wake(nid: int, bot_id: str, note: str, tell_id: int | None = None) -> None:
    from . import turns
    try:
        if tell_id is not None:
            from . import team
            await team.handle_tell(tell_id, bot_id, on_start=lambda: _mark_started(nid), recovery_note=note)
        else:
            await _wake(bot_id, note, on_start=lambda: _mark_started(nid), skip_if=lambda: _wait_closed(nid))
    except asyncio.CancelledError:
        raise
    except turns.WakeFailed as exc:
        await _wake_failed(nid, bot_id, exc.retryable)
    except Exception:
        log.exception("waits: could not wake %s", bot_id)
        row = store.db().execute("SELECT started FROM wake_notes WHERE id=?", (nid,)).fetchone()
        if row:
            await _wake_failed(nid, bot_id, not row["started"])
    else:
        with store._lock:
            store.db().execute("DELETE FROM wake_notes WHERE id=?", (nid,))
            store.db().commit()
    finally:
        _inflight.discard(nid)


def _wait_closed(nid: int) -> bool:
    row = store.db().execute("SELECT waits.state FROM waits JOIN wake_notes ON waits.id=wake_notes.wait_id "
                              "WHERE wake_notes.id=?", (nid,)).fetchone()
    return row is not None and row["state"] == "closed"





async def _wake_failed(nid: int, bot_id: str, retryable: bool) -> None:
    from . import handoff
    row = store.db().execute("SELECT * FROM wake_notes WHERE id=?", (nid,)).fetchone()
    if row is None:
        return
    if _wait_closed(nid) or bot_id not in config.BOTS_BY_ID:
        with store._lock:
            store.db().execute("UPDATE wake_notes SET state='failed' WHERE id=?", (nid,))
            store.db().commit()
        return
    failure = await handoff.classify(bot_id)
    if failure.final:
        with store._lock:
            store.db().execute("UPDATE wake_notes SET state='failed' WHERE id=?", (nid,))
            store.db().commit()
        return
    if failure.held:
        with store._lock:
            try:
                store.db().execute("DELETE FROM wake_notes WHERE id=?", (nid,))
                handoff.hold(bot_id, failure, via="wake", wait_id=row["wait_id"],
                             note=row["note"] if retryable else handoff.retry_note(work=_first_note(row["note"])))
            except BaseException:
                store.db().rollback()
                raise
        return
    if retryable or handoff.HEAD in row["note"]:
        _retain_failure(nid, retryable)
        return
    attempts = row["attempts"] + 1
    if attempts <= len(handoff.RETRY_DELAYS):
        with store._lock:
            store.db().execute("UPDATE wake_notes SET state='retry', attempts=?, started=0, retry_at=?, note=? WHERE id=?",
                               (attempts, now() + handoff.RETRY_DELAYS[attempts - 1],
                                handoff.retry_note(work=_first_note(row["note"])), nid))
            store.db().commit()
        return
    wait = get(row["wait_id"]) if row["wait_id"] is not None else None
    with store._lock:
        try:
            store.db().execute("UPDATE wake_notes SET state='failed', attempts=? WHERE id=?", (attempts, nid))
            hid = handoff.start_handoff(
                bot_id, failure, what=wait["what"] if wait else "", tries=attempts, wait_id=row["wait_id"],
                minutes=round((now() - row["at"]) / 60),
                facts="It was a follow-up the Zip had set itself" +
                      (f", waiting on {_who(wait['expect_from'])}." if wait and wait["expect_from"] else "."))
        except BaseException:
            store.db().rollback()
            raise
    await handoff.finish_handoff(hid, bot_id, failure)


_RETRY_MARK = "What you were working on:\n\n"


def _first_note(note: str) -> str:
    ""
    return note.split(_RETRY_MARK, 1)[1] if _RETRY_MARK in note else note


def _retain_failure(nid: int, retryable: bool) -> None:
    ""

    with store._lock:
        c = store.db()
        row = c.execute("SELECT * FROM wake_notes WHERE id=?", (nid,)).fetchone()
        if not row:
            return
        attempts = row["attempts"] + 1
        retry = retryable and attempts <= len(RETRY_DELAYS)
        feed_id = row["failure_feed_id"]
        if not retry and feed_id is None:
            wait = get(row["wait_id"]) if row["wait_id"] is not None else None
            about = f" about {wait['what']}" if wait else ""
            bot = config.BOTS_BY_ID.get(row["bot_id"])
            from . import nightly
            nightly.add(c, f"wake:{nid}", "wake", row["bot_id"],
                        f"{bot.name if bot else 'A helper'}'s follow-up{about}", "connection" if retryable else "",
                        "Ask me to try again." if retryable else "Ask me to check what is left.", row["at"], now())
        c.execute("UPDATE wake_notes SET state=?, attempts=?, started=?, retry_at=?, failure_feed_id=? WHERE id=?",
                  ("retry" if retry else "failed", attempts, 0 if retryable else row["started"],
                   now() + RETRY_DELAYS[attempts - 1] if retry else 0, feed_id, nid))
        c.commit()


def interrupted_note(note: str) -> str:
    ""
    return (f"{who.hub_note()} The hub restarted while you were working on this, so part of it may already be done. "
            "Check first (your Sent mail, the site, this chat) and don't repeat a send, a submission or a booking "
            f"that already happened. Then finish what is left. What you were working on:\n\n{note}")


def resume_pending() -> int:
    ""
    rows = store.db().execute("SELECT id, note, started, tell_id FROM wake_notes "
                              "WHERE state IN ('pending', 'retry') ORDER BY id").fetchall()
    with store._lock:
        for r in rows:
            if r["started"]:
                if r["tell_id"] is not None:
                    store.db().execute("UPDATE team_msgs SET delivered=0 WHERE id=?", (r["tell_id"],))
                store.db().execute("UPDATE wake_notes SET note=?, started=0 WHERE id=?",
                                   (interrupted_note(r["note"]), r["id"]))
        store.db().commit()
    pump()
    return len(rows)


async def run() -> None:
    global _loop
    _loop = asyncio.get_running_loop()
    try:
        n = resume_pending()
        if n:
            log.info("waits: sent %d wake(s) the last hub run did not finish", n)
    except Exception:
        log.exception("waits: could not resume pending wakes")
    while True:
        try:
            await check_once()
            pump()
        except Exception:
            log.exception("waits: a pass failed")
        busy = bool(listed()) or bool(own_mailboxes()) or bool(triggers.mail_routines()) or \
            bool(store.db().execute("SELECT 1 FROM wake_notes WHERE state != 'failed' LIMIT 1").fetchone())
        await asyncio.sleep(BUSY_SEC if busy else IDLE_SEC)











SETTLE_ENABLED = False
SETTLE_TIMEOUT_SEC = 25
OUTCOME_MAX = 1500

SETTLE_PROMPT = """{zip} started a wait while working on the task "{title}".
Waiting on: {what}
From: {frm}
Check-back date: {due}
Planned next step when the reply comes or the date passes: {step}

That task has now {ended}. How it ended, in the words it was closed with:
{outcome}

Decide what of the wait still applies.
- The reply itself may still matter (for example to tell {first} what they said): then keep waiting.
- A planned step the task's outcome already did, or made pointless, goes: drafting or sending a message that has \
already gone out, a reminder for work that is finished. Keep only the parts that still apply, in the same words.
- If the reply no longer matters either, stop waiting.
When you are not sure, keep everything as it is.

Reply with JSON only: {{"keep_waiting": true or false, "next_step": "the planned step with only what still applies, \
or empty", "dropped": "the part that no longer applies, in a few words, or empty", "why": "one short plain sentence"}}"""


async def _settle_ask(task_id: str, bot_id: str, prompt: str, ask) -> dict | None:
    ""

    from . import stillasks
    if ask is None:
        if not SETTLE_ENABLED:
            return None
        ask = shortreply.ask_helper
    from . import ownerrules
    prompt += ownerrules.block(ownerrules.QUESTION, prompt)
    try:
        with shortreply.reading_task(task_id, also=(bot_id,)):
            answer = await asyncio.wait_for(ask(prompt, timeout=SETTLE_TIMEOUT_SEC, system=stillasks.SYSTEM,
                                                max_tokens=200), SETTLE_TIMEOUT_SEC + 5)
    except Exception as e:
        log.info("no helper answer about a closed task's wait (%s)", type(e).__name__)
        return None
    return stillasks._parse(answer)


def _flat(text) -> str:
    return " ".join(str(text or "").split())


async def settle_task(task_id: str, title: str, ended: str, outcome: str, ask=None) -> list[int]:
    ""


    if not task_id:
        return []
    rows = store.db().execute("SELECT * FROM waits WHERE task_id=? AND state IN ('open','overdue') AND task_settled "
                              "IS NULL ORDER BY id", (task_id,)).fetchall()
    changed = []
    for r in rows:
        w = _row(r)
        bot = config.BOTS_BY_ID.get(w["bot_id"])
        data = await _settle_ask(task_id, w["bot_id"], SETTLE_PROMPT.format(
                zip=bot.name if bot else "The Zip", first=who.first(), title=_flat(title)[:200], what=w["what"],
                frm=_who(w["expect_from"]) or ("the same email thread" if w["thread"] else "(a check-back on the date)"),
                due=say_date(w["due"]), step=w["next_step"] or "(none written)", ended=ended,
                outcome=_flat(outcome)[:OUTCOME_MAX] or "(no note)"), ask)
        if not data or not isinstance(data.get("keep_waiting"), bool):
            continue
        keep = data["keep_waiting"]
        step = _flat(data.get("next_step"))[:STEP_MAX] if keep else w["next_step"]
        dropped, why = _flat(data.get("dropped"))[:200], _flat(data.get("why"))[:200]
        cols = {"task_settled": now(), "updated": now()}
        if not keep:
            cols.update(state="closed", close_reason=(why or f"the task it was for has {ended}")[:200])
        elif step != w["next_step"]:
            cols["next_step"] = step
        note = None
        if not keep:
            note = (f"{who.hub_note()} The task \"{_flat(title)[:120]}\" has {ended}, so I stopped your wait {w['id']} "
                    f"about \"{w['what']}\": {why or 'the reply no longer matters.'}")
        elif step != w["next_step"]:
            kept = f" The plan now: {step}" if step else " No step is planned for the reply now: read it and say what it means."
            note = (f"{who.hub_note()} The task \"{_flat(title)[:120]}\" has {ended}. I kept your wait {w['id']} for the "
                    f"reply about \"{w['what']}\" and dropped {dropped or 'the planned step'}"
                    f"{': ' + why if why else '.'}{kept}")
        with store._lock:
            c = store.db()
            if not c.execute(f"UPDATE waits SET {', '.join(f'{k}=?' for k in cols)} WHERE id=? AND task_settled IS "
                             "NULL AND state IN ('open','overdue')", (*cols.values(), w["id"])).rowcount:
                c.rollback()
                continue
            if note:
                note += " Nothing needs doing now: reply [SILENT] unless this changes what you tell " + who.first() + "."
                c.execute("INSERT INTO wake_notes (bot_id, note, at, started) VALUES (?,?,?,0)",
                          (w["bot_id"], note, now()))
            c.commit()
        if note:
            changed.append(w["id"])
    if changed:
        kick()
        log.info("task %s closed: reconciled waits %s", task_id, changed)
    return changed




def for_app() -> list[dict]:
    out = []
    for w in listed():
        bot = config.BOTS_BY_ID.get(w["bot_id"])
        frm = _who(w["expect_from"])
        out.append({"id": w["id"], "bot_id": w["bot_id"], "bot_name": bot.name if bot else w["bot_id"],
                    "what": w["what"], "from": frm, "due": w["due"], "due_text": say_date(w["due"]),
                    "line": (f"From {frm}, by {say_date(w['due'])}" if frm else f"Checking back {say_date(w['due'])}")})
    return out
