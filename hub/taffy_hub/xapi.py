""













import hashlib
import logging
import sqlite3
import time

import httpx

from . import consent, service_keys, store

log = logging.getLogger("taffy.xapi")

SERVICE = "x"
CREATE_URL = "https://api.x.com/2/tweets"
LINK = "https://x.com/i/web/status/{id}"
TIMEOUT_SEC = 30
REPEAT_SEC = 24 * 3600

MAYBE_SENT = ("X did not confirm the post. It may or may not have gone out, so I did not send it again. Check the "
              "profile on X and tell {who} what you find.")
OUT_OF_CREDITS = ("Not posted: the X account has no API credits left. {who} can add credits in the X Developer "
                  "Console, and then I can post it.")
NO_WRITE = ("Not posted: X refused the keys permission to post. In the X Developer Console, the app's permission "
            "must be Read and Write, and the Access Token must be made again after that change.")
BAD_KEYS = ("Not posted: X did not accept the saved keys. They may have been regenerated. {who} needs to make new "
            "ones in the X Developer Console.")
NO_REPLY = ("Not posted: X lets an API app reply only when the post's author mentioned or quoted this account. Give "
            "{who} the post's link instead.")
TOO_FAST = "Not posted: X says this account has hit its limit for now. Try again later."
DUPLICATE = "Not posted: X refused it because this account already posted the same words."


@store.schema
def _schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS x_posts (bot_id TEXT NOT NULL, fingerprint TEXT NOT NULL, "
                 "post_id TEXT, at REAL NOT NULL, PRIMARY KEY (bot_id, fingerprint))")


class Refused(Exception):
    ""


def _fingerprint(text: str, reply_id: str) -> str:
    return hashlib.sha256(f"{reply_id}\n{' '.join(text.split())}".encode()).hexdigest()


def _claim(bot_id: str, fingerprint: str) -> None:
    ""
    now = time.time()
    with store.db() as c:
        c.execute("DELETE FROM x_posts WHERE at<?", (now - REPEAT_SEC,))
        took = c.execute("INSERT OR IGNORE INTO x_posts (bot_id, fingerprint, at) VALUES (?,?,?)",
                         (bot_id, fingerprint, now)).rowcount == 1
        row = None if took else c.execute("SELECT post_id FROM x_posts WHERE bot_id=? AND fingerprint=?",
                                          (bot_id, fingerprint)).fetchone()
    if took:
        return
    if row and row["post_id"]:
        raise Refused(f"This exact post is already on X at {LINK.format(id=row['post_id'])}, so I did not send it "
                      "again.")
    raise Refused(_said(MAYBE_SENT))


def _free(bot_id: str, fingerprint: str) -> None:
    with store.db() as c:
        c.execute("DELETE FROM x_posts WHERE bot_id=? AND fingerprint=?", (bot_id, fingerprint))


def _sent(bot_id: str, fingerprint: str, post_id: str) -> None:
    with store.db() as c:
        c.execute("UPDATE x_posts SET post_id=? WHERE bot_id=? AND fingerprint=?", (post_id, bot_id, fingerprint))


def _said(message: str) -> str:
    from . import who
    return message.format(who=who.first())


def _send(url: str, headers: dict, body: dict) -> httpx.Response:
    ""
    return httpx.post(url, headers=headers, json=body, timeout=TIMEOUT_SEC)


def _reply_json(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _refusal(status: int, data: dict, stored: dict) -> str | None:
    ""
    words = " ".join(str(data.get(k) or "") for k in ("title", "detail", "type")).lower()
    detail = service_keys.scrub(str(data.get("detail") or data.get("title") or ""), stored)[:200]
    if status == 402 or "credit" in words:
        return _said(OUT_OF_CREDITS)
    if status == 401:
        return _said(BAD_KEYS)
    if status == 403:
        if "oauth1 app permissions" in words or "permission" in words:
            return NO_WRITE
        if "duplicate" in words:
            return DUPLICATE
        if "reply" in words or "conversation" in words:
            return _said(NO_REPLY)
        return f"Not posted: X refused this post ({detail})." if detail else "Not posted: X refused this post."
    if status == 429 or "usage-capped" in words:
        return TOO_FAST
    if 400 <= status < 500:
        return f"Not posted: X would not take this post ({detail})." if detail else "Not posted: X would not take it."
    return None


def post(bot_id: str, text: str, reply_id: str = "", test: bool = False) -> dict:
    ""

    try:
        stored = service_keys.load(bot_id, SERVICE)
    except service_keys.KeyFileBad as e:
        raise Refused(f"Not posted: {e}") from None
    if stored is None:
        return {"connected": False}
    if test or consent.test_mode():
        return {"result": "Test mode: the X keys are in place and the post is ready. Nothing was posted."}
    fingerprint = _fingerprint(text, reply_id)
    _claim(bot_id, fingerprint)
    body = {"text": text, **({"reply": {"in_reply_to_tweet_id": reply_id}} if reply_id else {})}
    headers = {"Authorization": service_keys.oauth1_header("POST", CREATE_URL, stored["values"])}
    try:
        r = _send(CREATE_URL, headers, body)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        _free(bot_id, fingerprint)
        raise Refused("Not posted: X could not be reached. Try again in a few minutes.") from None
    except httpx.HTTPError:
        log.warning("x post for %s: no answer from X", bot_id)
        raise Refused(_said(MAYBE_SENT)) from None
    data = _reply_json(r)
    post_id = (data.get("data") or {}).get("id") if isinstance(data.get("data"), dict) else None
    if 200 <= r.status_code < 300 and isinstance(post_id, str) and post_id.isdigit():
        _sent(bot_id, fingerprint, post_id)
        return {"result": "Posted on X.", "url": LINK.format(id=post_id)}
    log.warning("x post for %s: X answered %s", bot_id, r.status_code)
    sentence = None if 200 <= r.status_code < 300 else _refusal(r.status_code, data, stored)
    if sentence is None:
        raise Refused(_said(MAYBE_SENT))
    _free(bot_id, fingerprint)
    raise Refused(sentence)





REPLY_PREFIX = "In reply to "


def card_call(r: dict) -> tuple[str, str] | None:
    ""

    from . import plugintools
    if r.get("kind") != "publish" or consent.base(r.get("site") or "") != "x.com" or r.get("test"):
        return None
    tiers, xpost = plugintools.module("tiers"), plugintools.module("xpost")
    if tiers is None or xpost is None:
        return None
    text, details = r.get("inputs") or "", r.get("details") or ""
    link = details[len(REPLY_PREFIX):] if details.startswith(REPLY_PREFIX) else ""
    for args in ([{"text": text, "reply_to": link}] if link else [{"text": text}, {"text": text, "reply_to": ""}]):
        if tiers.Action("publish", "").fingerprint("post_to_x", args) == r.get("fingerprint"):
            try:
                return text, xpost.reply_id(args)
            except xpost.Refused:
                return None
    return None


def post_from_card(r: dict) -> dict | None:
    ""


    call = card_call(r)
    try:
        if call is None or service_keys.load(r["bot_id"], SERVICE) is None:
            return None
    except service_keys.KeyFileBad:
        return None

    with consent.db() as c:
        c.execute("UPDATE requests SET used=? WHERE id=? AND used IS NULL", (time.time(), r["id"]))
    try:
        out = post(r["bot_id"], *call)
    except Refused as e:
        said = str(e)
        return {"again": said} if said.startswith("Not posted") else {"unsure": said}
    except Exception:
        log.exception("x post from card %s", r.get("id"))
        return {"unsure": _said(MAYBE_SENT)}
    return {"url": out.get("url") or ""}


def reopen(r: dict, sentence: str) -> None:
    ""
    with consent.db() as c:
        c.execute("UPDATE requests SET status='pending', decided=NULL, used=NULL, because=?, raised=? WHERE id=?",
                  (sentence[:300], time.time(), r["id"]))
