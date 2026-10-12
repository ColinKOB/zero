"""Trello through its official REST API, with no model in between (docs/specs/2026-10-11-native-connectors-design.md).

Zero has one Trello API key (a Power-Up's key, made once by Colin at trello.com/power-ups/admin; public by design,
home.json `apps.trello.key`). Each person authorizes it on Trello's own page and Zero keeps the token that page hands
back, in this home only (apps.py's store: folder mode 700, file mode 600). The token is sent in the Authorization
header, never in a URL, so it cannot land in a log line. It is never returned to the app or to a Zip.

Every function here takes the token from apps.secret("trello") itself. A 401 marks the connection needs_sign_in
(the person revoked Zero in Trello's settings, or the token was deleted). Trello's documented limits are 100
requests per 10 seconds per token and 300 per 10 seconds per key, shared by every home that uses Zero's key; a
429 is a plain "try again" sentence.

Tests point API at a local fake.
"""

import logging
import re

import httpx

from . import home

log = logging.getLogger("taffy.trello")

API = "https://api.trello.com/1"
AUTHORIZE_URL = "https://trello.com/1/authorize"
TIMEOUT_SEC = 20
CARD_FIELDS = "name,idList,idBoard,due,dueComplete,labels,shortUrl,closed,desc,dateLastActivity"
BOARDS_MAX = 20
CARDS_MAX = 300
DESC_MAX = 2000

# Trello's tokens: 64 hex characters (older), or ATTA followed by letters and digits (since 2023).
TOKEN = re.compile(r"\b(ATTA[A-Za-z0-9]{40,250}|[0-9a-f]{64})\b")
ID = re.compile(r"^[0-9a-f]{24}$")
SHORT = re.compile(r"^[A-Za-z0-9]{8}$")
CARD_LINK = re.compile(r"trello\.com/c/([A-Za-z0-9]{8})")
BOARD_LINK = re.compile(r"trello\.com/b/([A-Za-z0-9]{8})")


class TrelloError(RuntimeError):
    """`str(e)` is one plain sentence for the person or the Zip; `state` is "needs_sign_in" when Trello refused the
    token, else "connected" (Trello was busy or could not be reached, or the request was wrong)."""

    def __init__(self, message: str, state: str = "connected"):
        super().__init__(message)
        self.state = state


def key() -> str | None:
    k = str(home.get("apps.trello.key") or "").strip()
    return k if re.fullmatch(r"[0-9a-f]{32}", k) else None


def find_token(text: str) -> str | None:
    """The token in what the person pasted (Trello's page shows it alone, but a copy can carry words around it)."""
    m = TOKEN.search(str(text or ""))
    return m.group(1) if m else None


def authorize_url(return_url: str | None = None) -> str | None:
    """Trello's own Allow page for Zero's key. With `return_url` (an allowed origin of the key) Trello sends the token
    back in the address's fragment; without it, Trello shows the token on the page for the person to copy."""
    k = key()
    if not k:
        return None
    from urllib.parse import urlencode
    q = {"expiration": "never", "name": "Zero", "scope": "read,write", "response_type": "token", "key": k}
    if return_url:
        q.update(return_url=return_url, callback_method="fragment")
    return f"{AUTHORIZE_URL}?{urlencode(q)}"


# MARK: Talking to Trello

def _headers(token: str) -> dict:
    return {"Authorization": f'OAuth oauth_consumer_key="{key() or ""}", oauth_token="{token}"',
            "Accept": "application/json"}


def request(method: str, path: str, token: str | None = None, params: dict | None = None, body: dict | None = None):
    """One call to Trello. Tests replace API. Raises TrelloError with a plain sentence."""
    from . import apps
    tok = token or apps.secret("trello")
    if not tok:
        raise TrelloError("Trello isn't connected.", "missing")
    if not key():
        raise TrelloError("Trello isn't set up for this home yet.", "missing")
    try:
        with httpx.Client(timeout=TIMEOUT_SEC) as c:
            r = c.request(method, f"{API}{path}", params=params or None, json=body, headers=_headers(tok))
    except httpx.HTTPError:
        raise TrelloError("Trello couldn't be reached just now.") from None
    if r.status_code == 401:
        if token is None:
            apps.mark("trello", apps.NEEDS_SIGN_IN)
        log.info("trello: the token was refused")
        raise TrelloError("Trello asked you to connect it again.", apps.NEEDS_SIGN_IN)
    if r.status_code == 429:
        raise TrelloError("Trello is busy. Try again in a few seconds.")
    if r.status_code == 404:
        raise TrelloError("Trello couldn't find that.")
    if r.status_code >= 400:
        log.info("trello: %s %s answered %s", method, path.split("?")[0][:40], r.status_code)
        raise TrelloError("Trello didn't accept that." if r.status_code < 500 else "Trello couldn't answer just now.")
    return r.json() if r.content else {}


def get(path: str, **params):
    return request("GET", path, params=params)


# MARK: Shapes the Zips read

def _clean(v, limit: int = 300) -> str:
    return " ".join(str(v or "").split())[:limit]


def _card(c: dict, lists: dict[str, str] | None = None, boards: dict[str, str] | None = None) -> dict:
    out = {"id": c.get("id"), "name": _clean(c.get("name"), 300), "url": c.get("shortUrl") or c.get("url") or "",
           "due": c.get("due"), "due_complete": bool(c.get("dueComplete")),
           "labels": [_clean(x.get("name") or x.get("color"), 40) for x in c.get("labels") or [] if isinstance(x, dict)],
           "list_id": c.get("idList"), "board_id": c.get("idBoard")}
    if lists is not None and c.get("idList") in lists:
        out["list"] = lists[c["idList"]]
    if boards is not None and c.get("idBoard") in boards:
        out["board"] = boards[c["idBoard"]]
    if c.get("desc"):
        out["description"] = str(c["desc"])[:DESC_MAX]
    if c.get("closed"):
        out["archived"] = True
    return out


def me(token: str | None = None) -> dict:
    m = request("GET", "/members/me", token=token, params={"fields": "id,username,fullName"})
    return {"id": m.get("id"), "username": m.get("username"), "name": m.get("fullName")}


def boards() -> list[dict]:
    rows = get("/members/me/boards", filter="open", fields="id,name,shortUrl,dateLastActivity")
    return [{"id": b.get("id"), "name": _clean(b.get("name"), 120), "url": b.get("shortUrl")}
            for b in (rows or [])[:BOARDS_MAX * 5]]


def _board_id(board: str) -> str:
    """A board id from its id, its 8-letter short link, a link to it, or its name (one of the person's boards)."""
    b = str(board or "").strip()
    if not b:
        t = home.trello() or {}
        if t.get("board_id"):
            return str(t["board_id"])
        raise TrelloError("Say which board.")
    m = BOARD_LINK.search(b)
    if m:
        return m.group(1)
    if ID.match(b) or SHORT.match(b) and not b.isalpha():
        return b
    wanted = b.lower()
    rows = boards()
    hit = [x for x in rows if x["name"].lower() == wanted] or [x for x in rows if wanted in x["name"].lower()]
    if len(hit) == 1 or (hit and hit[0]["name"].lower() == wanted):
        return hit[0]["id"]
    if not hit:
        raise TrelloError(f'There is no open board called "{_clean(b, 80)}". Boards: '
                          + ", ".join(x["name"] for x in rows[:BOARDS_MAX]) + ".")
    raise TrelloError("More than one board matches: " + ", ".join(x["name"] for x in hit[:8]) + ". Say which.")


def board(board_ref: str = "") -> dict:
    """A board's open lists, with each list's open cards."""
    bid = _board_id(board_ref)
    b = get(f"/boards/{bid}", fields="id,name,shortUrl")
    lists = get(f"/boards/{bid}/lists", filter="open", fields="id,name")
    cards = get(f"/boards/{bid}/cards", filter="open", fields=CARD_FIELDS)
    names = {x["id"]: _clean(x.get("name"), 80) for x in lists or []}
    by_list: dict[str, list] = {x["id"]: [] for x in lists or []}
    for c in (cards or [])[:CARDS_MAX]:
        if c.get("idList") in by_list:
            short = _card(c)
            short.pop("description", None)
            short.pop("list_id", None)
            short.pop("board_id", None)
            by_list[c["idList"]].append(short)
    return {"board": {"id": b.get("id"), "name": _clean(b.get("name"), 120), "url": b.get("shortUrl")},
            "lists": [{"id": lid, "name": names[lid], "cards": by_list[lid]} for lid in by_list]}


def _card_id(card: str) -> str:
    c = str(card or "").strip()
    m = CARD_LINK.search(c)
    if m:
        return m.group(1)
    if ID.match(c) or SHORT.match(c):
        return c
    raise TrelloError("Give the card's id or link from boards, board or search.")


def card(card_ref: str) -> dict:
    """One card with its description, checklists and latest comments. Comments come from other people too."""
    cid = _card_id(card_ref)
    c = get(f"/cards/{cid}", fields=CARD_FIELDS, checklists="all", checklist_fields="name",
            board="true", board_fields="name", list="true", list_fields="name")
    out = _card(c)
    out["board"] = _clean((c.get("board") or {}).get("name"), 120)
    out["list"] = _clean((c.get("list") or {}).get("name"), 80)
    out["checklists"] = [{"name": _clean(cl.get("name"), 120),
                          "items": [{"name": _clean(i.get("name"), 200), "done": i.get("state") == "complete"}
                                    for i in cl.get("checkItems") or []][:50]}
                         for cl in c.get("checklists") or []][:10]
    acts = get(f"/cards/{cid}/actions", filter="commentCard", limit=20)
    out["comments"] = [{"by": _clean((a.get("memberCreator") or {}).get("fullName"), 80), "at": a.get("date"),
                        "text": str((a.get("data") or {}).get("text") or "")[:1000]} for a in acts or []]
    return out


def search(query: str) -> dict:
    q = _clean(query, 200)
    if not q:
        raise TrelloError("Say what to look for.")
    r = get("/search", query=q, modelTypes="cards,boards", card_fields=CARD_FIELDS, cards_limit=25,
            card_board="true", board_fields="name,shortUrl", boards_limit=10, partial="true")
    cards = []
    for c in r.get("cards") or []:
        row = _card(c)
        row["board"] = _clean((c.get("board") or {}).get("name"), 120)
        row.pop("description", None)
        cards.append(row)
    return {"cards": cards, "boards": [{"id": b.get("id"), "name": _clean(b.get("name"), 120), "url": b.get("shortUrl")}
                                       for b in r.get("boards") or []]}


def _list_id(list_ref: str, board_ref: str = "") -> tuple[str, str]:
    """(list id, list name) from an id, or a list's name on the board (the home's board when none is named)."""
    lr = str(list_ref or "").strip()
    if not lr:
        raise TrelloError("Say which list.")
    if ID.match(lr):
        return lr, ""
    bid = _board_id(board_ref)
    lists = get(f"/boards/{bid}/lists", filter="open", fields="id,name") or []
    wanted = lr.lower()
    hit = [x for x in lists if _clean(x.get("name")).lower() == wanted] or \
          [x for x in lists if wanted in _clean(x.get("name")).lower()]
    if not hit:
        raise TrelloError(f'That board has no list called "{_clean(lr, 80)}". Lists: '
                          + ", ".join(_clean(x.get("name"), 60) for x in lists) + ".")
    if len(hit) > 1 and _clean(hit[0].get("name")).lower() != wanted:
        raise TrelloError("More than one list matches: " + ", ".join(_clean(x.get("name"), 60) for x in hit) + ".")
    return hit[0]["id"], _clean(hit[0].get("name"), 80)


# MARK: Changes (each one waits for the person's yes on a card first; the plugin's gate decides, apps_tool.classify)

def create_card(list_ref: str, name: str, board_ref: str = "", description: str = "", due: str | None = None,
                position: str = "") -> dict:
    lid, _ = _list_id(list_ref, board_ref)
    if not _clean(name):
        raise TrelloError("A card needs a name.")
    body = {"idList": lid, "name": _clean(name, 300), "pos": position if position in ("top", "bottom") else "bottom"}
    if description:
        body["desc"] = str(description)[:DESC_MAX * 4]
    if due:
        body["due"] = due
    return _card(request("POST", "/cards", body=body))


def update_card(card_ref: str, changes: dict, board_ref: str = "") -> dict:
    """`changes`: name, description, due (or None to clear), due_complete, list (a list on the card's board),
    archived."""
    cid = _card_id(card_ref)
    body: dict = {}
    if changes.get("name"):
        body["name"] = _clean(changes["name"], 300)
    if "description" in changes and changes["description"] is not None:
        body["desc"] = str(changes["description"])[:DESC_MAX * 4]
    if "due" in changes:
        body["due"] = changes["due"] or None
    if "due_complete" in changes and changes["due_complete"] is not None:
        body["dueComplete"] = bool(changes["due_complete"])
    if "archived" in changes and changes["archived"] is not None:
        body["closed"] = bool(changes["archived"])
    if changes.get("list"):
        here = board_ref
        if not here and not ID.match(str(changes["list"])):
            here = (get(f"/cards/{cid}", fields="idBoard") or {}).get("idBoard") or ""
        body["idList"], _ = _list_id(changes["list"], here)
    if not body:
        raise TrelloError("Say what to change on the card.")
    return _card(request("PUT", f"/cards/{cid}", body=body))


def comment(card_ref: str, text: str) -> dict:
    cid = _card_id(card_ref)
    t = str(text or "").strip()
    if not t:
        raise TrelloError("Say what the comment says.")
    a = request("POST", f"/cards/{cid}/actions/comments", body={"text": t[:16000]})
    return {"id": a.get("id"), "card": cid}


def revoke(token: str) -> None:
    """Deletes the token at Trello (best effort), so Zero no longer appears among the person's applications."""
    try:
        request("DELETE", f"/tokens/{token}", token=token)
    except TrelloError:
        log.info("trello: revoking at Trello failed; the token is forgotten here anyway")


# MARK: The hub's hourly copy (calcache.py), read directly instead of through a model

def cache_cards(start: float, end: float) -> list[dict]:
    """Open cards with a due date in [start, end) and every card in a list whose name starts with "Doing", on the
    home's board (home.json integrations.trello) or, without one, the person's open boards. The shape of
    calcache.parse_cards."""
    from . import notices
    t = home.trello() or {}
    ids = [str(t["board_id"])] if t.get("board_id") else [b["id"] for b in boards()[:BOARDS_MAX]]
    out = []
    for bid in ids:
        b = get(f"/boards/{bid}", fields="name")
        lists = {x["id"]: _clean(x.get("name"), 80) for x in get(f"/boards/{bid}/lists", filter="open",
                                                                   fields="id,name") or []}
        for c in (get(f"/boards/{bid}/cards", filter="open", fields=CARD_FIELDS) or [])[:CARDS_MAX]:
            lname = lists.get(c.get("idList"), "")
            due = notices._ts(c.get("due")) if c.get("due") else None
            if not ((due is not None and start <= due < end) or lname.lower().startswith("doing")):
                continue
            out.append({"id": _clean(c.get("id"), 80), "name": _clean(c.get("name"), 200),
                        "board": _clean(b.get("name"), 120), "list": lname, "due": due,
                        "due_complete": bool(c.get("dueComplete")),
                        "labels": [_clean(x.get("name") or x.get("color"), 40) for x in c.get("labels") or []
                                   if isinstance(x, dict)][:8],
                        "url": _clean(c.get("shortUrl"), 300)})
    return sorted(out, key=lambda x: (x["due"] or 0, x["id"]))
