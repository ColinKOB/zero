""
























from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from datetime import date, datetime

from . import facts, home, store, who

log = logging.getLogger("taffy.bills")

ENABLED = False
TIMEOUT_SEC = 25

CERTAINTY = ("statement", "said", "estimated")
STATES = ("open", "paid", "settled")
SHOWN_DAYS = 45
BLOCK_MAX = 700
OTHERS_MAX = 300
SOURCES_KEPT = 6
TEXT_MAX = 160
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class BillError(Exception):
    ""


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS bills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payee TEXT NOT NULL,
        what TEXT NOT NULL DEFAULT '',
        amount REAL,
        due TEXT NOT NULL DEFAULT '',
        due_from TEXT NOT NULL DEFAULT 'estimated',
        ending TEXT NOT NULL DEFAULT '',
        autopay INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'open',
        said TEXT NOT NULL DEFAULT '',
        settled_at REAL,
        sources TEXT NOT NULL DEFAULT '[]',
        bot_id TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL)""")


def _db() -> sqlite3.Connection:
    return store.db()


def _flat(text, limit: int = TEXT_MAX) -> str:
    return " ".join(str(text or "").split())[:limit]


def _row(r) -> dict:
    out = dict(r)
    out["sources"] = json.loads(out.get("sources") or "[]")
    out["autopay"] = bool(out.get("autopay"))
    return out


def get(bill_id: int) -> dict | None:
    r = _db().execute("SELECT * FROM bills WHERE id=?", (int(bill_id),)).fetchone()
    return _row(r) if r else None


def all_bills() -> list[dict]:
    return [_row(r) for r in _db().execute("SELECT * FROM bills ORDER BY id")]


def shown(now: float | None = None) -> list[dict]:
    ""
    cutoff = (now or time.time()) - SHOWN_DAYS * 86400
    return [b for b in all_bills() if b["state"] == "open" or (b["settled_at"] or 0) >= cutoff]


def revision() -> str:
    ""
    r = _db().execute("SELECT COUNT(*), COALESCE(MAX(updated_at), 0) FROM bills WHERE state != 'open'").fetchone()
    return f"{r[0]}:{r[1]}"




def _day(text) -> str:
    value = _flat(text, 10)
    if not value:
        return ""
    if not _DAY.match(value):
        raise BillError("Dates look like 2026-11-18.")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise BillError("Dates look like 2026-11-18.") from None
    return value


def _ending(text) -> str:
    ""
    chars = re.sub(r"[^A-Za-z0-9]", "", str(text or ""))
    return chars[-4:].upper() if len(chars) >= 4 else ""


def due_words(b: dict) -> str:
    ""
    if not b.get("due"):
        return "due date not known"
    d = date.fromisoformat(b["due"])
    on = f"{d.strftime('%a, %b')} {d.day}"
    return {"statement": f"due {on} (on the statement)", "said": f"due {on} ({who.first()} said)",
            "estimated": f"due around {on} (an estimate)"}.get(b.get("due_from"), f"due {on}")


def _money(x) -> str:
    return f"${x:,.2f}" if isinstance(x, (int, float)) else "amount not known"


def line(b: dict, amounts: bool = True) -> str:
    name = b["payee"] + (f" {b['what']}" if b.get("what") else "")
    if b["state"] == "open":
        bits = [_money(b["amount"])] if amounts else []
        bits.append(due_words(b))
        src = b["sources"][-1]["from"] if b["sources"] else ""
        return f"[{b['id']}] {name}: {', '.join(bits)}" + (", on autopay" if b["autopay"] else "") + \
               (f". From {src}." if src else ".")
    when = datetime.fromtimestamp(b["settled_at"] or b["updated_at"], home.zone())
    how = "paid" if b["state"] == "paid" else f"settled by {who.first()}"
    said = f": \"{b['said']}\"" if b["said"] else ""
    return f"[{b['id']}] {name}: {how} on {when.strftime('%b')} {when.day}{said}. An older notice about it is not due."


def block(bot_id: str) -> str:
    ""
    money = bot_id in facts.MONEY_BOTS
    if not money and bot_id != "default":
        return ""
    bills = [b for b in shown() if money or b["state"] != "open"]
    if not bills:
        return ""
    head = (f"{who.hub_note()} Bills on record (the bills tool; this is the one record of each bill, newer than any "
            "note, memory or email that disagrees):" if money else
            f"{who.hub_note()} Bills {who.first()} settled lately (the Money Zip keeps the record):")
    cap = BLOCK_MAX if money else OTHERS_MAX
    out = head
    for b in sorted(bills, key=lambda b: (b["state"] != "open", b["due"] or "9999")):
        nxt = f"{out}\n{line(b, amounts=money)}"
        if len(nxt) > cap:
            break
        out = nxt
    return out


def settled_words() -> str:
    ""
    done = [b for b in shown() if b["state"] != "open"]
    return " ".join(line(b, amounts=False) for b in done)[:600]




SYSTEM = "You keep one record per bill a person owes and answer with JSON only."
PROMPT = """{first} keeps one record per bill. A new notice about a bill just came in.

The new notice: {notice}

The bills on record:
{records}

Is the new notice about one of these bills? Pick one:
- "same": the same payment as record N, restated or corrected (a new amount or date for that same payment).
- "older": about the same payment as record N, but written before what the record holds, or about a payment \
{first} already settled or replaced there. It changes nothing.
- "next": record N's bill, but a new payment due after the one the record holds, such as next month's statement.
- "new": a bill that is none of these.

A bill to a different company, or for a different service, is always "new", however close its date or amount is to \
a record's. A notice that says its bill was paid, deferred or replaced is still "new" when no record is that bill.

Reply with JSON only: {{"verdict": "same", "record": 2, "same_company": true, "why": "a few plain words"}}
"same_company" is whether the notice and record N are bills to the same company for the same service."""


def _describe(n: dict) -> str:
    bits = [n["payee"] + (f" ({n['what']})" if n.get("what") else "")]
    if n.get("amount") is not None:
        bits.append(_money(n["amount"]))
    if n.get("due"):
        bits.append(f"due {n['due']} ({n['due_from']})")
    if n.get("seen"):
        bits.append(f"written {n['seen']}")
    return ", ".join(bits) + f". Source: {n['source'] or 'not given'}."


def _on_record(b: dict) -> str:
    srcs = "; ".join(f"{s['from']} ({s.get('seen') or datetime.fromtimestamp(s['at'], home.zone()).date()})"
                     for s in b["sources"][-3:])
    state = {"open": "open", "paid": "paid",
             "settled": f"settled by {who.first()}" + (f", who said \"{b['said']}\"" if b["said"] else "")}[b["state"]]
    return (f"{b['id']}. {b['payee']}" + (f" ({b['what']})" if b["what"] else "") +
            f", {_money(b['amount'])}, due {b['due'] or 'not known'} ({b['due_from']}), {state}. Sources: {srcs or 'none'}")


async def _judge(notice: dict, records: list[dict], ask=None) -> dict | None:
    from . import shortreply
    if ask is None:
        if not ENABLED:
            return None
        from . import shortreply
        ask = shortreply.ask_helper
    from . import ownerrules
    prompt = PROMPT.format(first=who.first(), notice=_describe(notice),
                           records="\n".join(_on_record(b) for b in records)) \
        + ownerrules.block(ownerrules.MONEY, _describe(notice), "money", noticer="bill")
    try:
        with shortreply.reading(()):
            answer = await asyncio.wait_for(ask(prompt, timeout=TIMEOUT_SEC, system=SYSTEM, max_tokens=120),
                                            TIMEOUT_SEC + 5)
    except Exception as e:
        log.info("no helper answer about a bill notice (%s)", type(e).__name__)
        return None
    m = re.search(r"\{.*\}", answer or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("verdict") not in ("same", "older", "next", "new"):
        return None
    if data["verdict"] != "new":





        ids = {b["id"] for b in records}
        if not isinstance(data.get("record"), int) or data["record"] not in ids or data.get("same_company") is not True:
            log.info("a bill notice matched no record the model would stand behind (%s): recorded as new", data["verdict"])
            return {"verdict": "new", "record": None, "why": _flat(data.get("why"))}
    return data




def _notice(bot_id: str, a: dict) -> dict:
    payee = _flat(a.get("payee"), 60)
    if not payee:
        raise BillError("Say who the bill is to (payee).")
    due_from = str(a.get("due_from") or "").strip()
    due = _day(a.get("due"))
    if due and due_from not in CERTAINTY:
        raise BillError("Say how the due date is known: statement, said or estimated.")
    amount = a.get("amount")
    if amount is not None:
        try:
            amount = round(float(str(amount).replace("$", "").replace(",", "")), 2)
        except ValueError:
            raise BillError("amount is dollars, like 358.36.") from None
    n = {"payee": payee, "what": _flat(a.get("what"), 40), "amount": amount, "due": due,
         "due_from": due_from or "estimated", "source": _flat(a.get("source")), "seen": _day(a.get("seen")),
         "ending": _ending(a.get("reference")), "autopay": a.get("autopay")}
    if any(facts.secret(n[k]) for k in ("payee", "what", "source")):
        raise BillError("Leave account numbers and codes out; the last four characters go in reference.")
    return n


def _source(n: dict, t: float) -> dict:
    return {"from": n["source"] or "not given", "seen": n["seen"], "at": t}


def _merge(b: dict, n: dict, t: float, reopen: bool) -> dict:
    ""
    out = {"amount": b["amount"] if n["amount"] is None else n["amount"], "what": n["what"] or b["what"],
           "ending": n["ending"] or b["ending"], "sources": (b["sources"] + [_source(n, t)])[-SOURCES_KEPT:]}
    weaker = n["due_from"] == "estimated" and b["due_from"] != "estimated" and b["due"]
    if n["due"] and (reopen or not weaker):
        out.update(due=n["due"], due_from=n["due_from"])
    if n["autopay"] is not None:
        out["autopay"] = int(bool(n["autopay"]))
    if reopen:
        out.update(state="open", said="", settled_at=None)
    return out


def _write(bill_id: int, fields: dict) -> None:
    fields = {**fields, "updated_at": time.time()}
    if "sources" in fields:
        fields["sources"] = json.dumps(fields["sources"])
    with store._lock:
        c = _db()
        c.execute(f"UPDATE bills SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), bill_id))
        c.commit()


def _insert(bot_id: str, n: dict, t: float) -> int:
    with store._lock:
        c = _db()
        cur = c.execute(
            "INSERT INTO bills (payee, what, amount, due, due_from, ending, autopay, sources, bot_id, created_at, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (n["payee"], n["what"], n["amount"], n["due"], n["due_from"], n["ending"], int(bool(n["autopay"])),
             json.dumps([_source(n, t)]), bot_id, t, t))
        c.commit()
        return int(cur.lastrowid)


async def record(bot_id: str, a: dict, ask=None) -> str:
    ""

    n = _notice(bot_id, a)
    t = time.time()
    records = all_bills()
    match = next((b for b in records if n["ending"] and b["ending"] == n["ending"]), None)
    verdict = "same" if match else "new"





    known = match is not None and (not n["due"] or n["due"] == match["due"])
    if records and not known:
        judged = await _judge(n, [match] if match else records, ask)
        if judged is None:
            raise BillError("I couldn't tell whether this is a bill already on record. Try again in a minute.")
        if match is None:
            verdict = judged["verdict"]
            match = next((b for b in records if b["id"] == judged.get("record")), None)
        else:
            verdict = "same" if judged["verdict"] == "new" else judged["verdict"]
    if verdict == "new" or match is None:
        bill_id = _insert(bot_id, n, t)
        return f"Recorded as a new bill.\n{line(get(bill_id))}"
    if verdict == "older" or (verdict == "same" and match["state"] != "open"):


        _write(match["id"], {"sources": (match["sources"] + [_source(n, t)])[-SOURCES_KEPT:]})
        return f"Nothing changed: that notice is about a payment the record already covers.\n{line(get(match['id']))}"
    _write(match["id"], _merge(match, n, t, reopen=verdict == "next"))
    said = "Opened its next payment" if verdict == "next" else "Updated the record"
    return f"{said}.\n{line(get(match['id']))}"


def settle(bot_id: str, a: dict) -> str:
    ""
    try:
        bill_id = int(a.get("bill"))
    except (TypeError, ValueError):
        raise BillError("Give the bill's number from list or record.") from None
    b = get(bill_id)
    if not b:
        raise BillError("There is no bill with that number. Read list first.")
    state = str(a.get("state") or "")
    if state not in STATES:
        raise BillError("state is paid, settled or open.")
    said = _flat(a.get("said"))
    if state == "settled" and not said:
        raise BillError(f"Put what {who.first()} said in said, in their words.")
    if facts.secret(said):
        raise BillError("Leave account numbers and codes out of said.")
    t = time.time()
    _write(bill_id, {"state": state, "said": said if state != "open" else "",
                     "settled_at": t if state != "open" else None})
    return line(get(bill_id))


def from_card(bill_id, action: str, picked: str, due: str | None = None) -> str:
    ""


    try:
        b = get(int(bill_id))
    except (TypeError, ValueError):
        b = None
    if not b:
        return "Closed."
    if due and b["due"] != due:
        return "Closed. That payment was already settled, and the newer one is still open."
    if action == "bill_autopay":
        _write(b["id"], {"autopay": 1})
        return "Got it. It's on autopay, so I won't ask about it again."
    _write(b["id"], {"state": "paid", "said": _flat(picked), "settled_at": time.time()})
    return "Closed. I won't ask about this again."


def tool(bot_id: str, a: dict) -> str:
    ""
    bills = shown()
    if not bills:
        return "No bills on record yet."
    amounts = bot_id in facts.MONEY_BOTS
    return "\n".join(line(b, amounts=amounts) for b in sorted(bills, key=lambda b: (b["state"] != "open",
                                                                                     b["due"] or "9999")))
