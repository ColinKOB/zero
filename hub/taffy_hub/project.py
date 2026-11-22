""









import json
import re
import secrets
import time
from datetime import datetime
from urllib.parse import unquote, urlparse

from . import choices, home, mail, store, who

MAX_RESOURCES = 8
KINDS = ("url", "file", "app")


WRITING_APP = "writing app"

WRITING_APPS = (("google docs", {"kind": "url", "target": "https://docs.google.com/document/u/0/", "label": "Google Docs"}),
                ("microsoft word", {"kind": "app", "target": "Microsoft Word", "label": "Microsoft Word"}),
                ("word", {"kind": "app", "target": "Microsoft Word", "label": "Microsoft Word"}),
                ("pages", {"kind": "app", "target": "Pages", "label": "Pages"}),
                ("libreoffice", {"kind": "app", "target": "LibreOffice", "label": "LibreOffice"}),
                ("scrivener", {"kind": "app", "target": "Scrivener 3", "label": "Scrivener"}),
                ("ulysses", {"kind": "app", "target": "Ulysses", "label": "Ulysses"}),
                ("obsidian", {"kind": "app", "target": "Obsidian", "label": "Obsidian"}),
                ("notion", {"kind": "app", "target": "Notion", "label": "Notion"}),
                ("textedit", {"kind": "app", "target": "TextEdit", "label": "TextEdit"}))


class ProjectError(Exception):
    ""




def _line(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _resource(raw, what: str) -> dict:
    if isinstance(raw, str):
        raw = {"target": raw}
    if not isinstance(raw, dict):
        raise ProjectError(f"Give {what} as an object with kind, target and label.")
    if str(raw.get("kind") or "").strip().lower() == "email":
        ref = {"account": str(raw.get("account") or "").strip(),
               "id": str(raw.get("id") or "").strip(), "folder": str(raw.get("folder") or "").strip()}
        try:
            target, fallback = mail.message_link(ref["account"], ref["folder"], ref["id"])
        except mail.MailError as e:
            raise ProjectError(str(e)) from None

        return {"kind": "url" if target else "app", "target": target or "Mail",
                "label": fallback or _line(raw.get("label"), 60) or "Email", "email": ref}
    target = str(raw.get("target") or "").strip()
    if not target or len(target) > 2000 or "\x00" in target:
        raise ProjectError(f"{what[:1].upper() + what[1:]} needs a target: a link, a file path on the Mac, or an app.")
    kind = str(raw.get("kind") or "").strip().lower()
    if not kind:
        kind = "url" if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I) else "file" if target[:1] in "/~" else "app"
    if kind not in KINDS:
        raise ProjectError(f"The kind of {what} must be url, file or app.")
    if kind == "url":
        u = urlparse(target)
        if u.scheme.lower() == "file":
            kind, target = "file", unquote(u.path)
        elif u.scheme.lower() not in ("http", "https") or not u.netloc:
            raise ProjectError(f"Links must start with https:// ({what} doesn't).")
    if kind == "file" and not (target.startswith("/") or target.startswith("~/")):
        raise ProjectError(f"A file must be a full path on {who.mac()}, starting with / or ~/ ({what} isn't).")
    if kind == "app" and ("/" in target or len(target) > 100):
        raise ProjectError(f"An app is its name, like Pages or Microsoft Word ({what} isn't).")
    label = _line(raw.get("label"), 60) or _default_label(kind, target)
    return {"kind": kind, "target": target, "label": label}


def _default_label(kind: str, target: str) -> str:
    if kind == "url":
        host = urlparse(target).netloc.lower()
        return (host[4:] if host.startswith("www.") else host)[:60]
    if kind == "file":
        return target.rstrip("/").rsplit("/", 1)[-1][:60]
    return target[:60]


def _due(value) -> float | None:
    ""

    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise ProjectError(f"I can't read the due date {s!r}. Use a date and time like 2026-10-16T23:59.") from None
    if dt.tzinfo is None:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            dt = dt.replace(hour=23, minute=59)
        dt = dt.replace(tzinfo=home.zone())
    return dt.timestamp()


def parse(args: dict, least: int = 1) -> dict:
    ""

    if not isinstance(args, dict):
        raise ProjectError("Send the project as an object.")
    title = _line(args.get("title"), 200)
    if not title or len(title) > 80:
        raise ProjectError("Give the project a short title, under 80 characters, like \"English paper\".")
    raw = args.get("resources")
    if not isinstance(raw, list) or not least <= len(raw) <= MAX_RESOURCES:
        raise ProjectError(f"List {max(least, 1)} to {MAX_RESOURCES} resources to open, in order.")
    if not args.get("workspace"):
        raise ProjectError(f"Name the workspace: the document {who.first()} writes in, opened last, or the app "
                           f"{who.first()} writes in, such as {{\"kind\": \"app\", \"target\": \"Pages\"}}.")
    workspace = _resource(args["workspace"], "the workspace")
    resources, seen = [], {workspace["target"]}
    for i, r in enumerate(raw, 1):
        res = _resource(r, f"resource {i}")
        if res["target"] not in seen:
            seen.add(res["target"])
            resources.append(res)
    return {"id": str(args.get("id") or ""), "title": title, "due": _due(args.get("due")),
            "match": _line(args.get("match"), 200), "resources": resources, "workspace": workspace}




def _body(row) -> dict:
    try:
        b = json.loads(row["body"] or "{}")
    except ValueError:
        return {}
    return b if isinstance(b, dict) and isinstance(b.get("kit"), dict) else {}


def live(bot_id: str) -> list[tuple[int, dict]]:
    ""
    rows = store.db().execute("SELECT * FROM feed WHERE kind='project' AND bot_id=? ORDER BY id DESC", (bot_id,))
    return [(r["id"], b) for r in rows.fetchall() if (b := _body(r)) and b.get("state") != "dismissed"]


def writing_app() -> tuple[dict, str] | None:
    ""

    from . import facts
    holds, now_arg = facts.live()
    rows = facts._db().execute(f"SELECT text FROM facts WHERE {holds} AND visibility='team' AND "
                               "instr(subject, ?) > 0 ORDER BY id DESC", now_arg + (WRITING_APP,)).fetchall()
    for r in rows:
        low = r["text"].lower()
        found = [(low.find(name), name, ws) for name, ws in WRITING_APPS if re.search(rf"\b{name}\b", low)]
        if found:
            return dict(min(found, key=lambda x: x[0])[2]), r["text"]
    return None


def offer(bot_id: str, args: dict, during_turn: bool) -> str:
    args = dict(args) if isinstance(args, dict) else args
    said = ""
    if isinstance(args, dict) and not args.get("workspace"):
        known = writing_app()
        if not known:
            first = who.first()
            raise ProjectError(f"I don't know which app {first} writes in. Ask {first} once with offer_choices (such as "
                               f"Pages, Microsoft Word and Google Docs), save the answer with save_fact using the subject "
                               f"\"{WRITING_APP}\", then offer the project again.")
        args["workspace"], fact = known
        said = f" It ends on {known[0]['label']}, the app {who.first()} writes in (saved fact: \"{fact}\")."
    kit = parse(args)
    same = next((b["kit"] for _, b in live(bot_id) if b["kit"].get("title", "").lower() == kit["title"].lower()), None)
    kit["id"] = same["id"] if same else "pk_" + secrets.token_hex(6)
    if during_turn:
        choices.defer(bot_id, lambda: publish(bot_id, kit))
        return (f"{who.first()} will see the project card under your reply. Keep your reply short and don't list what "
                "is in it." + said)
    publish(bot_id, kit)
    return f"{who.first()} will see the project card in your chat." + said


def publish(bot_id: str, kit: dict) -> int:
    ""

    for fid, b in live(bot_id):
        if b["kit"].get("id") == kit["id"] or b["kit"].get("title", "").lower() == kit["title"].lower():
            _save(fid, {**b, "state": "dismissed", "replaced": True})
    return store.add_feed("project", bot_id, None, kit["title"],
                          json.dumps({"kit": kit, "state": "open", "opened_at": None, "skipped": []}),
                          written_by="hub", written_for="person")


def _save(feed_id: int, body: dict) -> None:
    store.update_feed(feed_id, body["kit"]["title"], json.dumps(body))


def card(bot_id: str, feed_id: int) -> dict:
    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind='project' AND bot_id=?",
                             (feed_id, bot_id)).fetchone()
    body = _body(row) if row else {}
    if not body:
        raise ProjectError("That project card is gone.")
    return body


def save(bot_id: str, feed_id: int, resources: list, workspace) -> dict:
    ""
    body = card(bot_id, feed_id)
    kit = parse({**body["kit"], "resources": resources, "workspace": workspace}, least=0)
    previous = {(r["kind"], r["target"]): r["email"]
                for r in [*body["kit"]["resources"], body["kit"]["workspace"]] if "email" in r}
    for r in [*kit["resources"], kit["workspace"]]:
        if ref := previous.get((r["kind"], r["target"])):
            r["email"] = ref
    body["kit"] = {**kit, "id": body["kit"]["id"]}
    _save(feed_id, body)
    return body


def opened(bot_id: str, feed_id: int, skipped: list) -> dict:
    ""
    body = card(bot_id, feed_id)
    body.update(state="opened", opened_at=time.time(),
                skipped=[{"label": _line(s.get("label"), 60), "reason": _line(s.get("reason"), 120)}
                         for s in (skipped or [])[:MAX_RESOURCES + 1] if isinstance(s, dict)])
    _save(feed_id, body)
    return body


async def open_on_mac(bot_id: str, feed_id: int) -> None:
    ""

    from . import mac
    card(bot_id, feed_id)
    if not mac.online():
        raise ProjectError("Your Mac is off.")
    try:
        await mac.prompt_project(bot_id, feed_id)
    except mac.MacError:
        raise ProjectError("Your Mac is off.") from None




def find_for_deadline(bot_id: str, text: str) -> dict | None:
    ""
    from .calendar_noticers import alike
    for _, b in live(bot_id):
        kit = b["kit"]
        if alike(f"{kit.get('title', '')} {kit.get('match', '')}", text):
            return kit
    return None


def post_for(kit_id: str, owner: str, chat: str) -> int | None:
    ""

    for _, b in live(owner):
        if b["kit"].get("id") == kit_id:
            return publish(chat, b["kit"])
    return None
