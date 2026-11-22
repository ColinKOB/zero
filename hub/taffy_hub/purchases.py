""















import hashlib
import json
import logging
import secrets
import sqlite3
import time
from datetime import datetime

from . import config, home, store, wallet, who

log = logging.getLogger("taffy.purchases")

TOKEN_SEC = 600
SUBMIT_SEC = 30 * 60
PAY_CARD_SEC = 24 * 3600
TOTAL_SLACK = 0.10
COUNTED = ("reserved", "filled", "clicking", "submitted", "confirmed", "uncertain")
PAY_KIND = "wallet_pay"
RECEIPT_KIND = "wallet_receipt"
DECISIONS = ("pay", "not_now", "always_here")


class Refused(Exception):
    ""


class NotFound(Exception):
    pass


@store.schema
def _tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS purchases (
            id TEXT PRIMARY KEY,
            item_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'card',
            bot TEXT NOT NULL,
            origin TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            what TEXT,
            authorized_by TEXT,
            state TEXT NOT NULL,
            reason TEXT,
            token_hash TEXT,
            expires_at REAL,
            max_total_cents INTEGER,
            total_cents INTEGER,
            total_checked INTEGER,
            session_id TEXT,
            pay_feed INTEGER,
            receipt_feed INTEGER,
            counted_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS purchases_token ON purchases (token_hash) WHERE token_hash IS NOT NULL;
        CREATE INDEX IF NOT EXISTS purchases_item ON purchases (item_id, created_at);
    """)


class _Tx:
    ""


    def __enter__(self) -> sqlite3.Connection:
        store.db()
        self.c = sqlite3.connect(config.HUB_DB, timeout=15, isolation_level=None)
        self.c.row_factory = sqlite3.Row
        self.c.execute("BEGIN IMMEDIATE")
        return self.c

    def __exit__(self, kind, exc, tb) -> None:
        try:
            self.c.execute("ROLLBACK" if kind else "COMMIT")
        finally:
            self.c.close()


def _hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode()).hexdigest()


def money(cents: int, currency: str = "USD") -> str:
    return f"${cents / 100:,.2f}" if currency == "USD" else f"{cents / 100:,.2f} {currency}"


def _month_start(now: float) -> float:
    d = datetime.fromtimestamp(now, home.zone())
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def _month_cents(c: sqlite3.Connection, item_id: str, now: float, but: str = "") -> int:


    q = (f"SELECT COALESCE(SUM(MAX(amount_cents, COALESCE(total_cents, 0))), 0) FROM purchases WHERE item_id=? "
         f"AND counted_at>=? AND id<>? AND state IN ({','.join('?' * len(COUNTED))})")
    return int(c.execute(q, (item_id, _month_start(now), but, *COUNTED)).fetchone()[0])


def _sweep(c: sqlite3.Connection, now: float) -> list[str]:
    ""

    c.execute("UPDATE purchases SET state='released', updated_at=? WHERE state='reserved' AND expires_at<?", (now, now))
    stale = [r["id"] for r in c.execute("SELECT id FROM purchases WHERE state='asked' AND created_at<?",
                                        (now - PAY_CARD_SEC,))]
    c.executemany("UPDATE purchases SET state='released', reason='expired', updated_at=? WHERE id=?",
                  [(now, i) for i in stale])
    return stale


def _expire_cards(ids: list[str]) -> None:
    for i in ids:
        p = get(i)
        if p:
            _close_pay_card(p, "expired")


def _cents(dollars) -> int:
    return int(round(float(dollars) * 100))


def _row(c: sqlite3.Connection, purchase_id: str):
    return c.execute("SELECT * FROM purchases WHERE id=?", (purchase_id,)).fetchone()


def get(purchase_id: str) -> dict | None:
    row = store.db().execute("SELECT * FROM purchases WHERE id=?", (purchase_id,)).fetchone()
    return dict(row) if row else None


def _usable(item: dict | None, bot: str) -> str | None:
    ""
    if item is None:
        return "That item is gone from your saved items."
    if bot not in item["zips"] or not item["handles"].get(bot):
        return f"{who.first()} hasn't given you {item['label']}. Ask {who.first()} to add you to it in Settings."
    if item.get("paused"):
        return (f"{who.first()} paused {item['label']}. Don't try another way to pay; tell {who.first()} in a "
                "sentence.")
    return None




def request(bot: str, item: dict, origin: str, amount_cents: int, currency: str, what: str,
            session_id: str | None) -> dict:
    ""

    why = _usable(item, bot)
    if why:
        raise Refused(why)
    kind = item["kind"]



    limits = item.get("limits") or wallet.DEFAULT_LIMITS
    now, replaced = time.time(), None
    with _Tx() as c:
        stale = _sweep(c, now)
        over = kind == "card" and currency == "USD" and \
            _month_cents(c, item["id"], now) + amount_cents > _cents(limits["per_month"])
        reason = "identity" if kind == "identity" else "over_month" if over else "confirm"
        open_card = c.execute("SELECT * FROM purchases WHERE state='asked' AND bot=? AND item_id=? AND origin=? "
                              "ORDER BY created_at DESC LIMIT 1", (bot, item["id"], origin)).fetchone()
        if open_card is not None and (open_card["amount_cents"], open_card["currency"], open_card["what"] or "") \
                != (amount_cents, currency, what or ""):


            c.execute("UPDATE purchases SET state='released', reason='replaced', updated_at=? WHERE id=?",
                      (now, open_card["id"]))
            replaced, open_card = dict(open_card), None
        if open_card is not None:
            pid = open_card["id"]
            c.execute("UPDATE purchases SET reason=?, session_id=?, updated_at=? WHERE id=?",
                      (reason, session_id, now, pid))
        else:
            pid = "p_" + secrets.token_hex(6)
            c.execute("INSERT INTO purchases (id, item_id, kind, bot, origin, amount_cents, currency, what, state, "
                      "reason, session_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?, 'asked', ?,?,?,?)",
                      (pid, item["id"], kind, bot, origin, amount_cents, currency, what, reason, session_id, now,
                       now))
    _expire_cards(stale)
    if replaced:
        _close_pay_card({**replaced, "state": "released"}, "expired")
    _pay_card(pid)
    return {"purchase_id": pid, "reason": reason, "item": item, "origin": origin, "new_card": open_card is None}


def _month_note(p: dict, item: dict) -> str | None:
    ""
    if p["reason"] != "over_month":
        return None
    limits = item.get("limits") or wallet.DEFAULT_LIMITS
    return f"This goes over the {money(_cents(limits['per_month']))} monthly limit."


def _pay_body(p: dict, item: dict, state: str) -> dict:
    note = _month_note(p, item)
    return {"purchase_id": p["id"], "amount_cents": p["amount_cents"], "currency": p["currency"],
            "origin": p["origin"], "item_label": item["label"], "last4": item["meta"].get("last4") or "",
            "what": p["what"] or "", "bot": p["bot"], "state": state, "item_kind": item["kind"],
            **({"note": note} if note else {})}


def pay_title(p: dict, item: dict) -> str:
    site = wallet.site_name(p["origin"])
    if item["kind"] == "identity":
        return f"Use {item['label']} on {site}?"
    note = _month_note(p, item)
    return f"Pay {money(p['amount_cents'], p['currency'])} at {site} with {item['label']}" + \
        (f" for {p['what']}?" if p["what"] else "?") + (f" {note}" if note else "")


def _pay_card(purchase_id: str) -> None:
    ""
    p = get(purchase_id)
    item = wallet.stored(p["item_id"]) or {"label": "", "meta": {}, "kind": p["kind"]}
    body = json.dumps(_pay_body(p, item, "open"))
    if p["pay_feed"]:
        store.update_feed(p["pay_feed"], pay_title(p, item), body)
        store.touch_feed(p["pay_feed"])
        return
    feed_id = store.add_feed(PAY_KIND, p["bot"], None, pay_title(p, item), body, written_by="zip", written_for="person")
    with store._lock:
        store.db().execute("UPDATE purchases SET pay_feed=? WHERE id=?", (feed_id, purchase_id))
        store.db().commit()


def _close_pay_card(p: dict, state: str) -> None:
    if not p.get("pay_feed"):
        return
    item = wallet.stored(p["item_id"]) or {"label": "", "meta": {}, "kind": p["kind"]}
    store.update_feed(p["pay_feed"], pay_title(p, item), json.dumps(_pay_body(p, item, state)))




def decide(purchase_id: str, decision: str) -> dict:
    ""

    if decision not in DECISIONS:
        raise Refused("Answer pay, not_now or always_here.")
    p = get(purchase_id)
    if p is None:
        raise NotFound("That card is gone.")
    if p["state"] == "asked" and time.time() - p["created_at"] > PAY_CARD_SEC:
        with _Tx() as c:
            stale = _sweep(c, time.time())
        _expire_cards(stale)
        p = get(purchase_id)
    if p["state"] != "asked":
        raise Refused("That card ran out. Ask the Zip again if you still want it." if p["reason"] == "expired" else
                      "The Zip changed this purchase. Check its new card." if p["reason"] == "replaced" else
                      "That card was already answered.")
    item = wallet.stored(p["item_id"])
    site = wallet.site_name(p["origin"])
    if decision == "not_now":
        with _Tx() as c:
            done = c.execute("UPDATE purchases SET state='refused', updated_at=? WHERE id=? AND state='asked'",
                             (time.time(), purchase_id)).rowcount
        if not done:
            raise Refused("That card was already answered.")
        _close_pay_card(p, "not_now")
        what = f"using {item['label'] if item else 'it'} on {site}" if p["kind"] == "identity" else \
            f"paying {money(p['amount_cents'], p['currency'])} at {site}"
        note = (f"{who.hub_note()} {who.first()} said not now to {what}. Don't pay or try another way. Finish what "
                f"you can and tell {who.first()} in a sentence what's left.")
        return {"purchase": get(purchase_id), "bot": p["bot"], "note": note, "session_id": p["session_id"]}
    if item is None:
        raise Refused("That item is gone.")
    if p["bot"] not in item["zips"] or not item["handles"].get(p["bot"]):
        bot = config.BOTS_BY_ID.get(p["bot"])
        raise Refused(f"{bot.name if bot else 'This Zip'} no longer has {item['label']}. Add it in Settings first.")
    if item.get("paused"):
        raise Refused(f"{item['label']} is paused. Turn it back on in Settings first.")
    token, now = secrets.token_urlsafe(18), time.time()
    with _Tx() as c:
        done = c.execute("UPDATE purchases SET state='reserved', authorized_by=?, token_hash=?, expires_at=?, "
                         "max_total_cents=?, counted_at=?, updated_at=? WHERE id=? AND state='asked'",
                         (f"card:{purchase_id}", _hash(token), now + TOKEN_SEC,
                          round(p["amount_cents"] * (1 + TOTAL_SLACK)), now, now, purchase_id)).rowcount
    if not done:
        raise Refused("That card was already answered.")
    _close_pay_card(p, "paid")
    handle = item["handles"][p["bot"]]
    if p["kind"] == "identity":
        note = (f"{who.hub_note()} {who.first()} approved using {item['label']} on {site}. On that page "
                f"({p['origin']}), call browser_vault_fill with handle {handle} and approval {token}. The approval "
                "works once, on that site, for the next 10 minutes. Then carry on.")
    else:
        note = (f"{who.hub_note()} {who.first()} approved paying {money(p['amount_cents'], p['currency'])} at {site} "
                f"with {item['label']}. On the checkout page ({p['origin']}), call browser_vault_fill with handle "
                f"{handle} and approval {token}. The approval works once, on that site, for the next 10 minutes. "
                f"Then place the order and call report_purchase with purchase {purchase_id}.")
    return {"purchase": get(purchase_id), "bot": p["bot"], "note": note, "session_id": p["session_id"]}




def _over(row, total_cents: int) -> bool:
    ""
    return total_cents > (row["max_total_cents"] or round(row["amount_cents"] * (1 + TOTAL_SLACK)))


def _too_much(row, total_cents: int, done: str) -> str:
    return (f"The order total on the page is {money(total_cents, row['currency'])}, more than the "
            f"{money(row['amount_cents'], row['currency'])} approved. {done}Don't place the order. Tell {who.first()} "
            "the total; a new approval with use_card puts the real amount on a card.")


def redeem(bot: str, token: str, handle: str, origin: str, total_cents: int | None) -> dict:
    ""
    now = time.time()
    with _Tx() as c:
        row = c.execute("SELECT * FROM purchases WHERE token_hash=?", (_hash(token),)).fetchone()

        if row is None or row["bot"] != bot or not str(row["authorized_by"] or "").startswith("card:"):
            return {"ok": False, "error": "That approval isn't valid. Ask again with use_card or use_id."}
        if row["state"] != "reserved":
            return {"ok": False, "error": "That approval was already used. Each one works once."}
        if (row["expires_at"] or 0) < now:
            c.execute("UPDATE purchases SET state='released', updated_at=? WHERE id=?", (now, row["id"]))
            return {"ok": False, "error": "That approval ran out. Ask again with use_card or use_id."}
        item = wallet.stored(row["item_id"])
        why = _usable(item, bot)
        if why:
            c.execute("UPDATE purchases SET state='released', updated_at=? WHERE id=?", (now, row["id"]))
            return {"ok": False, "error": why}
        if item["handles"][bot] != handle:
            return {"ok": False, "error": f"That approval is for {item['label']}. Fill that one."}
        if origin != row["origin"]:
            return {"ok": False, "error": f"That approval is for {row['origin']}, and this page is {origin}. Nothing "
                                          "was filled. Go to the checkout page on that site and try again."}
        if row["kind"] == "card" and total_cents is not None and _over(row, total_cents):
            c.execute("UPDATE purchases SET state='refused', total_cents=?, total_checked=1, updated_at=? "
                      "WHERE id=?", (total_cents, now, row["id"]))
            return {"ok": False, "error": _too_much(row, total_cents, "Nothing was filled. ")}
        c.execute("UPDATE purchases SET state='filled', total_cents=?, total_checked=?, updated_at=? WHERE id=?",
                  (total_cents, int(total_cents is not None), now, row["id"]))
        return {"ok": True, "purchase_id": row["id"]}


def unfill(bot: str, purchase_id: str) -> bool:
    ""

    now = time.time()
    with _Tx() as c:
        cur = c.execute("UPDATE purchases SET state=CASE WHEN expires_at>=? THEN 'reserved' ELSE 'released' END, "
                        "total_cents=NULL, total_checked=0, updated_at=? WHERE id=? AND bot=? AND state='filled'",
                        (now, now, purchase_id, bot))
        return cur.rowcount == 1




def active(bot: str, origin: str) -> bool:
    ""

    now = time.time()
    row = store.db().execute(
        "SELECT 1 FROM purchases WHERE bot=? AND origin=? AND kind='card' AND ((state='reserved' AND expires_at>=?) "
        "OR (state='filled' AND updated_at>=?)) LIMIT 1", (bot, origin, now, now - SUBMIT_SEC)).fetchone()
    return row is not None


def step(bot: str, origin: str, total_cents: int | None = None) -> dict | None:
    ""


    now = time.time()
    with _Tx() as c:
        row = c.execute("SELECT * FROM purchases WHERE bot=? AND origin=? AND kind='card' AND ((state='reserved' AND "
                        "expires_at>=?) OR (state='filled' AND updated_at>=?)) ORDER BY updated_at DESC LIMIT 1",
                        (bot, origin, now, now - SUBMIT_SEC)).fetchone()
        if row is None:
            return None
        item = wallet.stored(row["item_id"])
        if _usable(item, bot):
            return None
        if total_cents is not None and _over(row, total_cents):
            return {"error": _too_much(row, total_cents, "")}
    return {"ok": True}


def submit(bot: str, origin: str, total_cents: int | None = None) -> dict | None:
    ""





    now = time.time()
    with _Tx() as c:
        row = c.execute("SELECT * FROM purchases WHERE bot=? AND origin=? AND kind='card' AND state='filled' "
                        "AND updated_at>=? ORDER BY updated_at DESC LIMIT 1", (bot, origin, now - SUBMIT_SEC)).fetchone()
        if row is None:
            return None
        item = wallet.stored(row["item_id"])
        if _usable(item, bot):
            c.execute("UPDATE purchases SET state='released', updated_at=? WHERE id=?", (now, row["id"]))
            return None
        if total_cents is not None and _over(row, total_cents):
            c.execute("UPDATE purchases SET state='refused', total_cents=?, total_checked=1, updated_at=? WHERE id=?",
                      (total_cents, now, row["id"]))
            return {"error": _too_much(row, total_cents, "")}
        if total_cents is None and not row["total_checked"]:
            c.execute("UPDATE purchases SET state='refused', updated_at=? WHERE id=?", (now, row["id"]))
            return {"error": f"I can't read the order total on this page, so I can't check it against what "
                             f"{who.first()} approved. Don't place the order. Tell {who.first()} in a sentence the total "
                             f"you see, and that the order is filled and waiting for {who.first()} to place it."}
        if total_cents is not None:
            c.execute("UPDATE purchases SET total_cents=?, total_checked=1 WHERE id=?", (total_cents, row["id"]))
        c.execute("UPDATE purchases SET state='clicking', updated_at=? WHERE id=? AND state='filled'",
                  (now, row["id"]))
    return get(row["id"])


def clicked(bot: str, origin: str, ok: bool) -> dict | None:
    ""


    now = time.time()
    with _Tx() as c:
        row = c.execute("SELECT * FROM purchases WHERE bot=? AND origin=? AND kind='card' AND state='clicking' "
                        "ORDER BY updated_at DESC LIMIT 1", (bot, origin)).fetchone()
        if row is None:
            return None
        c.execute("UPDATE purchases SET state=?, updated_at=? WHERE id=? AND state='clicking'",
                  ("submitted" if ok else "filled", now, row["id"]))
    if ok:
        _receipt(row["id"])
    return get(row["id"])


def _receipt_body(p: dict, item: dict | None, state: str) -> dict:
    checked = bool(p["total_checked"])
    return {"purchase_id": p["id"], "amount_cents": p["total_cents"] if checked else p["amount_cents"],
            "currency": p["currency"], "origin": p["origin"], "last4": (item or {}).get("meta", {}).get("last4") or "",
            "what": p["what"] or "", "state": state, "total_checked": checked, "bot": p["bot"]}


def receipt_title(p: dict, item: dict | None) -> str:
    amount = p["total_cents"] if p["total_checked"] else p["amount_cents"]
    return f"Paid {money(amount, p['currency'])} at {wallet.site_name(p['origin'])}" + \
        (f" with {item['label']}" if item else "")


def _receipt(purchase_id: str, state: str | None = None) -> None:
    p = get(purchase_id)
    item = wallet.stored(p["item_id"])
    body = json.dumps(_receipt_body(p, item, state or p["state"]))
    if p["receipt_feed"]:
        store.update_feed(p["receipt_feed"], receipt_title(p, item), body)
        return
    feed_id = store.add_feed(RECEIPT_KIND, p["bot"], None, receipt_title(p, item), body,
                             written_by="hub", written_for="record")
    with store._lock:
        store.db().execute("UPDATE purchases SET receipt_feed=? WHERE id=?", (feed_id, purchase_id))
        store.db().commit()


def report(bot: str, purchase_id: str, outcome: str) -> str:
    ""
    if outcome not in ("confirmed", "uncertain"):
        raise Refused("Say confirmed (the page showed an order confirmation) or uncertain.")
    p = get(str(purchase_id or ""))
    if p is None or p["bot"] != bot or p["kind"] != "card":
        raise Refused("There's no purchase with that number. Use the one you were given.")
    if p["state"] not in ("filled", "clicking", "submitted"):
        raise Refused("That purchase is already reported, or it never went through.")
    with _Tx() as c:
        c.execute("UPDATE purchases SET state=?, updated_at=? WHERE id=?", (outcome, time.time(), purchase_id))
    _receipt(purchase_id)
    if outcome == "uncertain":
        return (f"Noted. Don't try the order again. Tell {who.first()} in a sentence what the page showed, so "
                f"{who.first()} can check the account.")
    return f"Noted. Tell {who.first()} in a sentence that the order went through."


def not_me(purchase_id: str) -> dict:
    ""

    p = get(purchase_id)
    if p is None:
        raise NotFound("That purchase is gone.")
    item = wallet.set_paused(p["item_id"], True)
    now = time.time()
    with _Tx() as c:
        open_cards = [r["id"] for r in c.execute("SELECT id FROM purchases WHERE item_id=? AND state='asked'",
                                                 (p["item_id"],))]
        c.execute("UPDATE purchases SET state='released', updated_at=? WHERE item_id=? AND state='reserved'",
                  (now, p["item_id"]))
        c.execute("UPDATE purchases SET state='released', reason='paused', updated_at=? WHERE item_id=? AND "
                  "state='asked'", (now, p["item_id"]))
        c.execute("UPDATE purchases SET state='refused', updated_at=? WHERE item_id=? AND state='filled' AND id<>?",
                  (now, p["item_id"], purchase_id))
    _expire_cards(open_cards)
    _receipt(purchase_id, "not_me")
    bot = config.BOTS_BY_ID.get(p["bot"])
    name = bot.name if bot else "A Zip"
    label = item["label"] if item else "the card"
    store.add_card_note(p["bot"], f"{who.first()} said they didn't make the purchase at {wallet.site_name(p['origin'])}"
                                  f" and paused {label}. Don't try to pay another way.")
    return {"paused": p["item_id"], "bot": p["bot"],
            "message": f"{name} made this purchase. {label} is paused for all Zips until you turn it back on in "
                       "Settings."}
