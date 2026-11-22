""





































from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from . import config, store, who

log = logging.getLogger("taffy.donecard")

KIND = "todo_card"
ENABLED = False
LEFT_MAX = 6
LINE_SEC = 30



ASKS = ("choices", "approval", "team_need", "task", "mail")

ROW_KEYS = ("id", "kind", "title", "bot_id", "at", "section", "revision", "project_id", "user_state", "short_title",
            "ask", "due_at", "due_timed", "act", "context", "short_act", "group", "why_kind", "why", "why_label",
            "from_line", "people", "action")
ZIP_WHY = "You said it's done."
MOVED_SEC = 60


def is_todo(row: dict) -> bool:
    ""

    return row.get("kind") not in ASKS and not row.get("suggest")


def _slim(row: dict) -> dict:
    out = {k: row[k] for k in ROW_KEYS if k in row}
    reference = row.get("reference") or {}
    out["reference"] = {k: reference[k] for k in ("yours", "list_name") if k in reference}
    out["summary"] = " ".join(str(row.get("summary") or "").split())[:200]
    return out


def row_now(item_id: str) -> dict | None:
    ""

    from . import work
    return next((_slim(r) for r in work.overview().get("needs_you", []) if r.get("id") == item_id), None)


def snapshot(ids: list[str], updated: dict[str, dict] | None = None) -> dict | None:
    ""



    from . import work
    overview = work.overview()
    wanted, updated = set(ids), updated or {}
    done = [d for d in overview.get("done_today", []) if d.get("id") in wanted]
    needs = [r for r in overview.get("needs_you", []) if is_todo(r)]

    needs = [r for r in needs if r.get("group") == "now"] + [r for r in needs if r.get("group") != "now"]
    changed = [{"id": r["id"], "was": updated[r["id"]], **change_of(updated[r["id"]], r)}
               for r in needs if r.get("id") in updated]
    if not done and not changed:
        return None
    today = [r for r in needs if r.get("group") == "now"]
    shown = {r["id"] for r in today[:LEFT_MAX]} | {c["id"] for c in changed}
    return {"v": 1, "done": done, "updated": changed, "left": [_slim(r) for r in needs if r["id"] in shown],
            "more": len([r for r in today if r["id"] not in shown]), "count": len(overview.get("done_today", []))}


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def when_words(at: float, now: float | None = None) -> str:
    ""


    from datetime import datetime

    from . import home
    zone = home.zone()
    d = datetime.fromtimestamp(at, zone)
    days = (d.date() - datetime.fromtimestamp(time.time() if now is None else now, zone).date()).days
    day = {0: "today", 1: "tomorrow", -1: "yesterday"}.get(days) or (d.strftime("%A") if 1 < days <= 6 else
                                                                   d.strftime("%b %-d"))
    if (d.hour, d.minute) not in ((0, 0), (23, 59)):
        clock = d.strftime("%-I:%M %p").replace(":00 ", " ")
        return f"{day[:1].upper()}{day[1:]} at {clock}"
    return "Due today" if days == 0 else f"By {day}"


def change_of(was: dict, now: dict) -> dict:
    ""


    a, b = was.get("due_at"), now.get("due_at")
    if b is not None and (a is None or abs(float(b) - float(a)) >= MOVED_SEC):
        return {"change_kind": "date", "change": when_words(float(b))}
    if a is not None and b is None:
        return {"change_kind": "date", "change": "No due date"}
    for key in ("context", "summary"):
        words = _flat(now.get(key))
        if words and words != _flat(was.get(key)):
            return {"change_kind": "detail", "change": words[:160]}
    return {"change_kind": "", "change": ""}


def title_for(card: dict) -> str:
    ""
    done = card.get("done") or []
    first = str((done[0] if done else {}).get("title") or "")
    more = len(done) - 1
    if first:
        return f"Done: {first}" + (f" and {more} more" if more > 0 else "")
    changed = card.get("updated") or []
    now = next((r for r in card.get("left") or [] if changed and r.get("id") == changed[0].get("id")), {})
    title = str(now.get("act") or now.get("short_title") or now.get("title") or "")
    return f"Updated: {title}" if title else "Updated"




_generation = 0


def cancel_writes() -> None:
    global _generation
    with store._lock:
        _generation += 1
        _told.clear()


def _post(bot_id: str, card: dict, generation: int) -> int | None:
    with store._lock:
        if generation != _generation:
            return None
        return store.add_feed(KIND, bot_id, None, title_for(card), json.dumps(card), written_by="hub",
                              written_for="record")






_told: dict[str, dict] = {}
_posting: set = set()


def told(bot_id: str, item_ids: list[str], during_turn: bool) -> None:
    ""


    if during_turn and item_ids:
        rows = _queue(bot_id)["done"]
        rows.extend(i for i in item_ids if i not in rows)


def changed(bot_id: str, item_id: str, was: dict | None, during_turn: bool) -> None:
    ""


    if during_turn and was:
        _queue(bot_id)["updated"].setdefault(item_id, was)


def _queue(bot_id: str) -> dict:
    with store._lock:
        if bot_id not in _told:
            _told[bot_id] = {"done": [], "updated": {}}
            from . import choices
            choices.defer(bot_id, lambda: _publish_told(bot_id))
        return _told[bot_id]


def _publish_told(bot_id: str) -> None:
    ""
    with store._lock:
        told_now, generation = _told.pop(bot_id, None), _generation
    if not told_now or not (told_now["done"] or told_now["updated"]):
        return

    def read_and_post() -> None:
        card = snapshot(told_now["done"], told_now["updated"])
        if card:
            _post(bot_id, card, generation)

    async def go() -> None:
        try:
            await asyncio.to_thread(read_and_post)
        except Exception:
            log.warning("could not put up the to-do card for %d rows", len(told_now["done"]) + len(told_now["updated"]),
                        exc_info=True)
    try:
        task = asyncio.get_running_loop().create_task(go())
        _posting.add(task)
        task.add_done_callback(_posting.discard)
    except RuntimeError:
        read_and_post()




def soon(item_ids: list[str]) -> None:
    ""
    if not ENABLED or not item_ids:
        return
    from . import signin
    if signin.loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(by_itself(list(item_ids)), signin.loop)
    except Exception:
        log.warning("could not start the to-do card for %d rows", len(item_ids), exc_info=True)


LINE_SYSTEM = "You write one short chat line, in the first person, as a helper on a person's team. Plain text only."
LINE_PROMPT = """{first} just got something on {possessive} to-do list done, and you crossed it off. Under your line, a \
card shows the crossed-off item and what is left today, so leave that out.

What got done, and how you know:
{done}

Write one short line to {first}, in the first person, that says what {first} did that finished it, like "You replied \
to Dana, so I crossed that off." Use only the facts above. No greeting, no names of helpers, no emojis, no dashes, \
no list. Answer with the line and nothing else."""


async def line(done: list[dict], ask=None) -> str | None:
    ""

    from . import shortreply
    zips = tuple(sorted({str(d.get("bot_id") or "default") for d in done}))
    if not done or shortreply.withheld(*zips):
        return None
    ask = ask or shortreply.ask_helper
    facts = "\n".join(f"- {' '.join(str(d.get('title') or '').split())[:160]}"
                      + (f" ({' '.join(str(d.get('why')).split())[:80]})" if d.get("why") else "") for d in done[:5])
    prompt = LINE_PROMPT.format(first=who.first(), possessive=who.possessive(), done=facts)
    try:
        with shortreply.reading_text(facts, zips):
            said = await asyncio.wait_for(ask(prompt, timeout=LINE_SEC, system=LINE_SYSTEM, max_tokens=80), LINE_SEC + 5)
    except Exception as e:
        log.info("no line over the to-do card (%s)", type(e).__name__)
        return None
    said = str(said or "").strip().strip('"').strip()
    if not said or len(said) > 200 or "\n" in said or re.search(r"[\u2013\u2014]", said):
        return None
    return " ".join(said.split())


async def by_itself(item_ids: list[str], ask=None) -> int | None:
    ""

    generation = _generation
    card = await asyncio.to_thread(snapshot, item_ids)
    if not card:
        return None
    said = await line(card["done"], ask=ask)
    if said and not await asyncio.to_thread(_say, said, generation):
        return None
    return await asyncio.to_thread(_post, "default", card, generation)


def _say(said: str, generation: int) -> int | None:
    from . import notify
    with store._lock:
        if generation != _generation:
            return None
        return store.add_feed("message", "default", None, notify.sender("default")[1], said,
                              written_by="model", written_for="person")
