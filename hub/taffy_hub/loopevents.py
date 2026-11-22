""






import asyncio
import json
import logging
import re
import sqlite3

from . import briefing, config, facts, openloops, shortreply, store

log = logging.getLogger("taffy.loopevents")


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.executescript("""
        CREATE TABLE IF NOT EXISTS open_loop_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT NOT NULL, kind TEXT NOT NULL, event_key TEXT NOT NULL,
            data TEXT NOT NULL, created INTEGER NOT NULL, processed INTEGER NOT NULL DEFAULT 0,
            retry_at INTEGER NOT NULL DEFAULT 0,
            UNIQUE(bot_id, kind, event_key)
        );
    """)


def record(bot_id: str, kind: str, key: str, data: dict) -> None:
    if not key or kind not in ("task_done", "mail_sent", "calendar_created", "wait_answered", "wait_closed"):
        return



    if facts.secret(json.dumps({k: v for k, v in data.items() if k not in (*openloops.REFS, "reply_to")})):
        return
    with store._lock:
        c = store.db()
        c.execute("INSERT OR IGNORE INTO open_loop_events (bot_id,kind,event_key,data,created) VALUES (?,?,?,?,?)",
                  (bot_id, kind, key, json.dumps(data), openloops.now()))
        c.commit()
        settle(bot_id)
    briefing.invalidate(bot_id)


def task_completed(ev: dict) -> None:
    from . import board
    owners = board.task_zips(ev)
    sealed = config.sealed(*owners)
    bots = {ev.get("assignee") or "default", "default"}
    if ev.get("created_by") in config.BOTS_BY_ID:
        bots.add(ev["created_by"])
    for bot in bots:
        briefing.invalidate(bot)
        if ev.get("summary") and sealed and not config.is_private(bot):


            record(bot, "task_done", f"{ev['task_id']}:{ev.get('id', '')}",
                   {"task": ev["task_id"], "title": ev.get("title", ""), "sealed_by": sorted(
                       z for z in owners if config.is_private(z))})
        elif ev.get("summary"):
            record(bot, "task_done", f"{ev['task_id']}:{ev.get('id', '')}",
                   {"task": ev["task_id"], "title": ev.get("title", ""), "result": ev["summary"]})


def _matches(identity: dict, kind: str, data: dict) -> bool:
    required = {"task_done": ("task",), "mail_sent": ("thread", "message", "card"),
                "calendar_created": ("event",), "wait_answered": ("wait",), "wait_closed": ("wait",)}[kind]
    if conflicts(identity, {k: v for k, v in data.items() if k != "people"}):
        return False
    if kind == "mail_sent" and (not identity.get("account") or identity["account"] != data.get("account")):
        return False
    if kind == "calendar_created" and (not identity.get("calendar") or identity["calendar"] != data.get("calendar")):
        return False
    return any(identity.get(k) and identity[k] in (
        str(data.get(k) or ""), str(data.get("reply_to") or "") if k == "message" else "") for k in required)


def conflicts(identity: dict, data: dict) -> bool:
    for k in openloops.REFS:
        if identity.get(k) and data.get(k) and identity[k] != str(data[k]):
            if k == "message" and identity[k] == data.get("reply_to"):
                continue
            return True
    if (identity.get("account") and identity["account"] == data.get("account") and any(
            identity.get(k) and identity[k] in (
                str(data.get(k) or ""), str(data.get("reply_to") or "") if k == "message" else "")
            for k in ("thread", "message", "card"))):
        return False
    people = data.get("people")
    return bool(people and identity.get("person") and identity["person"] not in people)


def names_this_send(loop, data: dict) -> bool:
    ""




    if not openloops._tokens(data.get("subject") or "") & openloops._tokens(
            f"{loop['topic']} {loop['summary']} {loop['next_step']}"):
        return False
    step = openloops._key(loop["next_step"])
    for person in data.get("to") or []:
        words = openloops._key(person.get("name") if isinstance(person, dict) else "").split()
        for name in [" ".join(words[i:i + 2]) for i in range(len(words) - 1)] or words:
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", step):
                return True
    return False


def sent_line(kind: str, data: dict) -> str:
    ""

    if kind != "mail_sent":
        return ""
    names = ", ".join(p.get("name") or p.get("addr") or "" for p in data.get("to") or [] if isinstance(p, dict))
    return (f"This email to {names or 'its recipient'} about \"{data.get('subject') or ''}\" has gone out. In every note, "
            "even one about other work, every step toward this email is finished, however the note words it: asking for "
            "their address or what to say, drafting it, and sending it. Write the next step without those. A step about "
            "different mail to them stays.\n")


def settle(bot_id: str) -> None:
    ""
    c = store.db()
    rows = c.execute("SELECT * FROM open_loops WHERE bot_id=? AND status='open'", (bot_id,)).fetchall()
    if not rows:
        return
    events = [(r["kind"], json.loads(r["data"]), r["created"]) for r in c.execute(
        "SELECT kind,data,created FROM open_loop_events WHERE bot_id=? ORDER BY id DESC LIMIT 200", (bot_id,))]
    active = {}
    for row in rows:
        ident = openloops.identity_of(row["identity"], row["topic"])
        for index, (kind, data, created) in enumerate(events):
            if row["user_edited_at"] and created <= row["user_edited_at"]:
                continue
            if not _matches(ident, kind, data):
                continue
            if index not in active:
                active[index] = current(kind, data)
            if not active[index]:
                continue
            action = ident.get("action")
            if (kind, action) in (("task_done", "task"), ("mail_sent", "send"),
                                  ("calendar_created", "book"), ("wait_closed", "wait"), ("wait_closed", "review")):
                openloops.close(bot_id, row["id"])
                break
            if kind == "wait_answered" and action == "wait":
                openloops.save(bot_id, row["topic"], "The reply arrived. Its contents still need review.",
                               "Review the reply and decide the next step.", row["id"], "event",
                               identity={**ident, "action": "review"})
                break


def current(kind: str, data: dict) -> bool:
    if kind in ("wait_answered", "wait_closed"):
        row = store.db().execute("SELECT state FROM waits WHERE id=?", (data["wait"],)).fetchone()
        return bool(row and row["state"] == ("answered" if kind == "wait_answered" else "closed"))
    if kind == "task_done":
        from . import board
        try:
            with board._conn() as c:
                row = c.execute("SELECT status FROM tasks WHERE id=?", (data["task"],)).fetchone()
            return not row or row["status"] == "done"
        except sqlite3.Error:
            pass
    return True


def sync_waits(bot_id: str) -> None:
    ""
    with store._lock:
        c = store.db()
        changed = False
        for row in c.execute("SELECT id,state,updated,what FROM waits WHERE bot_id=? AND state IN ('answered','closed') "
                             "AND updated>?", (bot_id, store.get_setting("openloops_waits_erased_at", 0))).fetchall():
            kind = "wait_answered" if row["state"] == "answered" else "wait_closed"
            changed |= bool(c.execute("INSERT OR IGNORE INTO open_loop_events (bot_id,kind,event_key,data,created) VALUES (?,?,?,?,?)",
                      (bot_id, kind, f"{row['id']}:{row['updated']}", json.dumps({"wait": str(row["id"]), "what": row["what"]}),
                       row["updated"])).rowcount)
        c.commit()
        settle(bot_id)
        if changed:
            briefing.invalidate(bot_id)


def material(bot_id: str) -> dict:
    recent = [dict(r) for r in store.db().execute(
        "SELECT kind,data FROM open_loop_events WHERE bot_id=? ORDER BY id DESC LIMIT 12", (bot_id,))]
    waits = [dict(r) for r in store.db().execute(
        "SELECT id AS wait,account,thread,what,state,next_step FROM waits WHERE bot_id=? AND updated>? "
        "ORDER BY updated DESC LIMIT 40", (bot_id, store.get_setting("openloops_waits_erased_at", 0)))]
    cards = [dict(r) for r in store.db().execute(
        "SELECT id AS card,title FROM feed WHERE bot_id=? AND kind='mail' ORDER BY id DESC LIMIT 20", (bot_id,))]
    try:
        from . import board
        tasks = [{"task": t["id"], "title": t["title"]} for group in board.grouped().values() for t in group
                 if t.get("assignee") in (bot_id, None) or bot_id == "default"]
    except Exception:
        tasks = []
    return {"events": recent, "waits": waits, "cards": cards, "tasks": tasks, "decisions": decisions(bot_id)}


DECISIONS_PER_LOOP, DECISIONS_MAX = 2, 12


def decisions(bot_id: str) -> list[dict]:
    ""

    out: dict[int, dict] = {}
    for lp in store.db().execute("SELECT topic,summary,next_step FROM open_loops WHERE bot_id=? AND status='open' "
                                 "ORDER BY updated_at DESC", (bot_id,)).fetchall():
        for f in facts.search(f"{lp['topic']}. {lp['summary']} {lp['next_step']}", bot_id, limit=DECISIONS_PER_LOOP,
                              kinds=("decision",)):
            out.setdefault(f["id"], {"fact": f["id"], "decision": f["text"], "saved": facts._day(f["observed_at"])})
    return list(out.values())[:DECISIONS_MAX]


def for_model(found: dict) -> dict:
    ""


    from . import privatewords
    return {k: privatewords.keep(v, json.dumps) if isinstance(v, list) else v for k, v in found.items()}


def context(bot_id: str) -> str:
    return json.dumps(for_model(material(bot_id)))


def known_refs(material: dict, loops: list[dict]) -> dict[str, set[str]]:
    values = [openloops.identity_of(r["identity"], r["topic"]) for r in loops]
    values += [json.loads(r["data"]) for r in material["events"]]
    values += material["waits"] + material["cards"] + material["tasks"]
    return {k: {str(v[k]) for v in values if v.get(k)} for k in openloops.REFS}


async def reconcile(bot_id: str) -> None:
    ""
    before = openloops._tails.get(bot_id)
    openloops._tails[bot_id] = me = asyncio.current_task()
    generation = openloops._generation
    try:
        if before is not None and before is not me and not before.done():
            await asyncio.wait({before})
        for ev in store.db().execute("SELECT * FROM open_loop_events WHERE bot_id=? AND processed=0 AND retry_at<=? ORDER BY id",
                                     (bot_id, openloops.now())).fetchall():
            snapshot = {r["id"]: r for r in openloops.open_loops(bot_id)}
            data = json.loads(ev["data"])
            if not current(ev["kind"], data):
                snapshot = {}


            snapshot = {i: r for i, r in snapshot.items() if not conflicts(
                openloops.identity_of(r["identity"], r["topic"]), data) or _matches(
                openloops.identity_of(r["identity"], r["topic"]), ev["kind"], data) or (
                ev["kind"] == "mail_sent" and names_this_send(r, data) and not conflicts(
                    openloops.identity_of(r["identity"], r["topic"]), {**data, "people": []}))}



            from . import privatewords
            snapshot = {i: r for i, r in snapshot.items() if not privatewords.holds(openloops._current(bot_id, [r]))}
            if shortreply.withheld(*(data.get("sealed_by") or ())):
                snapshot = {}
            if snapshot:
                if not openloops._budget_ok():
                    return
                with store._lock:
                    store.db().execute("UPDATE open_loop_events SET retry_at=? WHERE id=?", (openloops.now() + 300, ev["id"]))
                    store.db().commit()
                prompt = ("Reconcile these pending-work notes with one authoritative event. Use only the supplied "
                          "event's result as evidence. A task finishing a check does not complete work it found still due. "
                          "Change only the specific work and person the event settles. Close finished steps, revise "
                          "partly finished work, and leave unrelated notes alone. Never add a loop. Never change an "
                          "identity.subject or identity.person. Preserve the ids and the day of anything the person said or "
                          "decided. Preferences are facts. Return JSON "
                          "{\"loops\":[{\"id\":1,\"topic\":\"...\",\"summary\":\"...\",\"next\":\"...\","
                          "\"state\":\"closed\",\"identity\":{...}}]}. Use state open when work remains. "
                          "There is no three-change limit. A note with action buy or submit closes only with "
                          "\"proof\": the order, confirmation or return number exactly as the event gives it.\n"
                          f"Event: {ev['kind']} {ev['data']}\n{sent_line(ev['kind'], data)}Notes:\n" +
                          openloops._current(bot_id, list(snapshot.values())))
                try:


                    with shortreply.reading_text(f"{ev['data']}\n{sent_line(ev['kind'], data)}", (bot_id,),
                                                 titles=data.get("sealed_by") or ()):
                        out = openloops._json(await openloops.ask_haiku(prompt))
                except Exception:
                    log.warning("loop event read failed", exc_info=True)
                    return
                if out is None or not isinstance(out.get("loops"), list):
                    return
                known = {k: {str(data[k])} if data.get(k) else set() for k in openloops.REFS}
                for row in snapshot.values():
                    for k, value in openloops.identity_of(row["identity"], row["topic"]).items():
                        if k in known:
                            known[k].add(value)
                with store._lock:
                    if current(ev["kind"], data):
                        openloops.apply(out, bot_id, "event", generation, snapshot=snapshot, limit=openloops.MAX_OPEN,
                                        event=True, known=known, seen=ev["data"], must_add=ev["kind"] == "task_done")
            with store._lock:
                if generation != openloops._generation:
                    return
                store.db().execute("UPDATE open_loop_events SET processed=1 WHERE id=?", (ev["id"],))
                store.db().commit()
    finally:
        if openloops._tails.get(bot_id) is me:
            openloops._tails.pop(bot_id, None)


def retry_pending() -> None:
    if not openloops.ENABLED:
        return
    for row in store.db().execute("SELECT DISTINCT bot_id FROM open_loop_events WHERE processed=0 AND retry_at<=?",
                                 (openloops.now(),)):
        bot = row["bot_id"]
        if bot not in openloops._tails:
            task = asyncio.create_task(reconcile(bot))
            openloops._tails[bot] = task
            openloops._running.add(task)
            task.add_done_callback(openloops._running.discard)
