""

















import json
import re
import time

from . import config, store, who

KIND = "account"
_SECTIONS = ("your job", "your focus on this team")


def _job(bot_id: str) -> str:
    ""

    from . import screen
    try:
        text = (screen.profile_home(bot_id) / "SOUL.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    keep, out = False, []
    for line in text.splitlines():
        if line.startswith("#"):
            keep = line.lstrip("#").strip().lower() in _SECTIONS
            continue
        if keep:
            out.append(line)
    return "\n".join(out)


def _named(text: str) -> list[str]:
    ""
    from . import apps
    found = []
    for a in apps.CATALOG:
        if a.native:
            m = re.search(r"\b" + re.escape(a.name) + r"\b", text)
            if m:
                found.append((m.start(), a.id))
    return [app_id for _, app_id in sorted(found)]


def _asked(bot_ids: list[str]) -> tuple[list[str], set[str]]:
    ""


    from . import accounts, apps, consent, keys
    if not bot_ids:
        return [], set()
    marks = ",".join("?" * len(bot_ids))
    asked, latest = [], {}
    for row in store.db().execute(f"SELECT kind, body FROM feed WHERE kind IN ('account','login') AND bot_id IN "
                                  f"({marks}) ORDER BY id", bot_ids):
        try:
            card = json.loads(row["body"] or "{}")
        except ValueError:
            continue
        if not isinstance(card, dict) or card.get("setup"):
            continue
        if row["kind"] == "login":
            base = consent.base(consent.site_of(str(card.get("site") or "")))
            app_id = keys.for_site(base) or ("github" if base == "github.com" else "")
        else:
            app_id = card.get("app") or accounts.SERVICE_APPS.get(str(card.get("service") or ""), "")
        if app_id in apps.BY_ID and apps.BY_ID[app_id].native:
            if app_id not in asked:
                asked.append(app_id)
            if row["kind"] == "account":
                latest[app_id] = card.get("state")
    return asked, {a for a, state in latest.items() if state == "declined"}


def _team(bot_id: str) -> list[str]:
    from . import teams
    t = teams.member_of(bot_id)
    if not t:
        return []
    return [r["bot_id"] for r in store.db().execute("SELECT bot_id FROM team_members WHERE team_id=?", (t["id"],))
            if r["bot_id"] != bot_id]


NEW_SEC = 2 * 86400


def _new(bot_id: str) -> bool:
    entry = next((e for e in config._registry_entries() if e.get("id") == bot_id), {})
    return time.time() - float(entry.get("created_at") or 0) < NEW_SEC


def needed(bot_id: str) -> list[str]:
    ""



    own, declined = _asked([bot_id])
    team = [] if own or not _new(bot_id) else _asked(_team(bot_id))[0]
    out = []
    for app_id in _named(_job(bot_id)) + own + team:
        if app_id not in out and app_id not in declined:
            out.append(app_id)
    return out


def _rows(bot_id: str, app_ids: list[str]) -> list[dict]:
    ""
    from . import apps
    rows = []
    for app_id in app_ids:
        try:
            connected = apps.connected_natively(app_id, bot_id=bot_id)
            if not connected:
                with apps.connections.for_bot(app_id, bot_id):
                    if not apps.connectable(app_id):
                        continue
        except Exception:
            continue
        rows.append({"id": app_id, "name": apps.BY_ID[app_id].name, "connected": connected})
    return rows


def _latest(bot_id: str):
    for row in store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 50",
                                  (KIND, bot_id)):
        card = json.loads(row["body"] or "{}")
        if card.get("setup"):
            return row, card
    return None, None


def _words(bot_id: str, rows: list[dict]) -> tuple[str, str]:
    ""
    names = [r["name"] for r in rows]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    done = sum(r["connected"] for r in rows)
    left = len(rows) - done
    return (f"{config.BOTS_BY_ID[bot_id].name} will use {listed}",
            (f"{done} connected, " if done else "") + f"{left} to connect")


def _card(bot_id: str, rows: list[dict], card: dict | None = None) -> dict:
    ""

    from . import accounts, apps, connections
    first = next(r for r in rows if not r["connected"])


    with connections.for_bot(first["id"], bot_id):
        selection = connections.capture()
    line, count = _words(bot_id, rows)
    service = {v: k for k, v in accounts.SERVICE_APPS.items()}.get(first["id"], first["id"])
    return {**(card or {}), "service": service, "app": first["id"], "via": "zero", "setup": True, "name": first["name"],
            "group": "",
            "why": f"{line}. {count}.", "apps": rows, "line": line, "count": count, "state": "open",
            "provider": apps.SIGN_IN_WITH.get(first["id"], first["name"]), "can": apps.can_me(first["id"]),
            "key": apps.uses_key(first["id"]), "connection_id": (selection or {}).get("id")}


def setup(bot_id: str) -> int | None:
    ""


    if bot_id not in config.BOTS_BY_ID:
        return None
    want = needed(bot_id)
    row, card = _latest(bot_id)
    listed = {a["id"] for a in (card or {}).get("apps") or []}
    if not want or set(want) <= listed:
        return None
    rows = _rows(bot_id, want)
    if all(r["connected"] for r in rows):
        return None
    from . import accounts
    waiting = {}
    for old, body in list(accounts._open_cards(bot_id)):
        if body.get("via") == "zero" and not body.get("setup") and body.get("app") in {r["id"] for r in rows}:
            waiting[body["app"]] = body.get("session_id")
            body.update(state="moved", at=time.time())
            store.update_feed(old["id"], old["title"], json.dumps(body))
    if row is not None and card.get("state") == "open":
        card.update(state="moved", at=time.time())
        store.update_feed(row["id"], row["title"], json.dumps(card))
        waiting = {**(card.get("waiting") or {}), **waiting}
    body = _card(bot_id, rows)
    body["waiting"] = waiting
    body["told"] = bool(waiting)
    return store.add_feed(KIND, bot_id, None, f"Apps for {config.BOTS_BY_ID[bot_id].name}", json.dumps(body),
                          written_by="hub", written_for="person")


def setup_all() -> list[int]:
    ""

    made = []
    for b in list(config.BOTS):
        try:
            fid = setup(b.id)
        except Exception:
            import logging
            logging.getLogger("taffy.zipapps").warning("start card for %s failed", b.id, exc_info=True)
            continue
        if fid:
            made.append(fid)
    return made


def join(bot_id: str, app_id: str, session_id: str | None) -> str | None:
    ""

    row, card = _latest(bot_id)
    if row is None or card.get("state") != "open" or app_id not in {a["id"] for a in card.get("apps") or []}:
        return None
    card.setdefault("waiting", {})[app_id] = session_id
    store.update_feed(row["id"], row["title"], json.dumps(card))
    store.touch_feed(row["id"])
    name = next(a["name"] for a in card["apps"] if a["id"] == app_id)
    return (f"{name} is on the card {who.first()} has to connect your apps, now at the bottom of your chat. Stop "
            f"working on what needs {name}. {who.quiet_card()} You'll get a note here when it's connected.")


def settle(row, card: dict) -> list[tuple[str, str, str | None]]:
    ""

    from . import accounts, apps
    bot_id = row["bot_id"]
    if bot_id not in config.BOTS_BY_ID:
        return []
    rows = []
    for a in card.get("apps") or []:
        try:
            now = apps.connected_natively(a["id"], bot_id=bot_id)
        except Exception:
            now = a.get("connected", False)
        rows.append({**a, "connected": bool(now)})
    wake = []
    waiting = dict(card.get("waiting") or {})
    for r in rows:
        if r["connected"] and r["id"] in waiting:
            session = waiting.pop(r["id"])
            wake.append((bot_id, accounts._connected_note(r["name"], f"the {apps.TOOLS.get(r['id'], r['id'])} tool"),
                         session))
    if rows == card.get("apps") and not wake:
        return []
    if all(r["connected"] for r in rows):
        body = {**card, "apps": rows, "waiting": waiting, "state": "connected", "at": time.time(),
                "count": f"{len(rows)} connected"}
        from . import asks
        asks.answered("account", row["id"], bot_id, row["title"], "Connected. Check your own account access.")
    else:
        body = {**_card(bot_id, rows, card), "waiting": waiting}
    store.update_feed(row["id"], row["title"], json.dumps(body))
    return wake


async def announce() -> None:
    ""
    import asyncio
    from . import watcher
    for fid in await asyncio.to_thread(setup_all):
        row = store.db().execute("SELECT bot_id, body FROM feed WHERE id=?", (fid,)).fetchone()
        card = json.loads(row["body"])
        if card.get("told"):
            continue
        left = [a["name"] for a in card["apps"] if not a["connected"]]
        names = left[0] if len(left) == 1 else ", ".join(left[:-1]) + " and " + left[-1]
        await watcher.push(f"{config.BOTS_BY_ID[row['bot_id']].name} needs {names}",
                           f"Can you connect {names}? Then I can work without stopping to ask.", row["bot_id"])
