""
















import asyncio

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from . import config, desk, looking, places, team, triggers, waits


def _wait_out(w: dict) -> dict:
    return {"id": w["id"], "what": w["what"], "from": w["expect_from"], "subject": w["subject"],
            "account": w["account"], "due": waits.say_date(w["due"]), "state": w["state"],
            "next_step": w["next_step"]}


def errands_op(op: str, bot_id: str, session_id: str, a: dict, origin_task: str | None = None) -> dict:
    ""
    try:
        if op == "wait":
            due = waits.parse_due(str(a.get("due") or ""))
            frm = a.get("from") or []
            if isinstance(frm, str):
                frm = [x for x in frm.replace(";", ",").split(",") if x.strip()]
            w = waits.start(bot_id, session_id or None, str(a.get("what") or ""), frm, str(a.get("subject") or ""),
                            due, str(a.get("next_step") or ""), str(a.get("account") or ""),
                            source=str(a.get("source") or "tool"), thread=str(a.get("thread") or ""),
                            task_id=origin_task or "")
            line = (f"Waiting for a reply from {waits._who(w['expect_from'])} until "
                    f"{waits.say_date(w['due'])}." if w["expect_from"] else
                    f"Waiting for a reply until {waits.say_date(w['due'])}." if w["thread"] else
                    f"I'll check back on {waits.say_date(w['due'])}.")
            return {"ok": True, "result": {"wait": w["id"], "said": line,
                                           "note": "The hub wakes you here when it arrives or the date passes. "
                                                   "Don't tell anyone to check back."}}
        if op == "list":
            return {"ok": True, "result": {"waits": [_wait_out(w) for w in waits.listed(bot_id)]}}
        if op in ("done", "extend"):
            wid = int(a.get("id") or 0)
            if op == "done":
                ok = waits.close(bot_id, wid, str(a.get("why") or "done"))
            else:
                ok = waits.extend(bot_id, wid, waits.parse_due(str(a.get("due") or "")))
            return {"ok": ok, **({"result": {"wait": wid, "state": waits.get(wid)["state"]}} if ok else
                                 {"error": f"You have no wait {wid}. errands list shows yours."})}
        if op == "sent":
            n = waits.note_sent(bot_id, str(a.get("account") or ""), a.get("to") or [])
            return {"ok": True, "result": {"remembered": n}}
        if op == "trigger_create":
            t = triggers.create(bot_id, str(a.get("name") or ""), str(a.get("instructions") or ""))
            return {"ok": True, "result": {"trigger": t["id"], "name": t["name"], "url": t["url"],
                                           "note": "To start it from the iPhone, the person adds the Zero app's own "
                                                   "Shortcuts action, Run a Zip Trigger, and picks this trigger: no "
                                                   "link to copy. For an automation, it goes in Shortcuts, "
                                                   "Automation, such as Arrive at a place, with Run Immediately. "
                                                   "The link (Settings, Triggers, Copy Link) is for other apps and "
                                                   "services, and starts it with POST. Say only the steps they "
                                                   "need, in plain words."}}
        if op == "mail_routine":
            frm = a.get("from") or []
            if isinstance(frm, str):
                frm = [x for x in frm.replace(";", ",").split(",") if x.strip()]
            t = triggers.create_mail(bot_id, str(a.get("name") or ""), str(a.get("instructions") or ""), frm,
                                     str(a.get("subject") or ""), str(a.get("account") or ""))
            senders = waits._who(t["mail_from"])
            return {"ok": True, "result": {"trigger": t["id"], "name": t["name"], "said": (
                f"It runs each time mail from {senders} arrives"
                + (f" in {t['mail_account']}" if t["mail_account"] else "") + f", up to {triggers.MAIL_DAY_MAX} "
                "times a day."), "note": "It starts with mail that arrives from now on. Tell the person in one line "
                "whose mail starts it and what you will do each time."}}
        if op == "triggers":
            return {"ok": True, "result": {"triggers": [
                {k: t[k] for k in ("id", "name", "instructions", "state", "fires", "last_fired", "kind")} |
                ({"from": t["mail_from"], "subject": t["mail_subject"], "account": t["mail_account"]}
                 if t["kind"] == "mail" else {})
                for t in triggers.listed(bot_id)]}}
        if op == "trigger_delete":
            ok = triggers.delete(str(a.get("id") or ""), bot_id)
            return {"ok": ok, **({"result": "Deleted."} if ok else {"error": "You have no trigger with that id."})}
        if op == "read_trigger":
            e = triggers.event(bot_id, int(a.get("event") or 0))
            return {"ok": bool(e), **({"result": e} if e else {"error": "No such request for your triggers."})}
        if op == "place":
            d = places.add(bot_id, str(a.get("what") or ""), str(a.get("place") or ""), str(a.get("on") or "arrive"),
                           a.get("every_time") is True, a.get("radius"))
            return {"ok": True, "result": {"reminder": d["id"], "said": places.said(d),
                                           "note": "Say this in one line, naming the place you picked. The Zero app on "
                                                   "the iPhone watches the place; the hub posts the reminder here when "
                                                   "it gets there."}}
        if op == "places":
            return {"ok": True, "result": {"place_reminders": [places.for_zip(d) for d in places.listed(bot_id)]}}
        if op == "place_cancel":
            rid = int(a.get("id") or 0)
            ok = places.cancel(bot_id, rid)
            return {"ok": ok, **({"result": "Cancelled."} if ok else
                                 {"error": f"You have no open place reminder {rid}. errands places shows yours."})}
    except (waits.Invalid, triggers.Invalid, places.Invalid) as e:
        return {"ok": False, "error": str(e)}
    except (TypeError, ValueError):
        return {"ok": False, "error": "Give the id as a number."}
    return {"ok": False, "error": f"unknown errands action {op}"}


def install(app, require_device, require_internal) -> None:
    from .app import MacBody

    @app.post("/internal/errands/{op}")
    async def internal_errands(op: str, body: MacBody, _: None = Depends(require_internal)):
        if body.bot_id not in config.BOTS_BY_ID:
            raise HTTPException(404, "Unknown bot.")
        if team.in_team_session(body.session_id) and op not in ("list", "triggers", "read_trigger", "places"):
            return {"ok": False, "error": "You are answering a teammate's question. Start errands in your own work."}
        return await asyncio.to_thread(errands_op, op, body.bot_id, body.session_id, body.args or {},
                                       body.origin_task)

    @app.post("/internal/textwatch/{op}")
    async def internal_textwatch(op: str, body: MacBody, _: None = Depends(require_internal)):
        ""
        from . import textwatch
        if body.bot_id not in config.BOTS_BY_ID:
            raise HTTPException(404, "Unknown bot.")
        if team.in_team_session(body.session_id) and op != "list":
            return {"ok": False, "error": "You are answering a teammate's question. Watch texts in your own work."}
        return await textwatch.op(op, body.bot_id, body.session_id, body.args or {})

    @app.get("/api/waits")
    def list_waits(_: str = Depends(require_device)):
        return {"waits": waits.for_app()}

    @app.delete("/api/waits/{wait_id}")
    def stop_wait(wait_id: int, _: str = Depends(require_device)):
        w = waits.get(wait_id)
        if not w or not waits.close(w["bot_id"], wait_id, "stopped by the person"):
            raise HTTPException(404, "That isn't waiting anymore.")
        return {"ok": True}

    @app.post("/api/bots/{bot_id}/seen")
    def chat_seen(bot_id: str, authorization: str | None = Header(None), _: str = Depends(require_device)):
        ""

        if bot_id not in config.BOTS_BY_ID:
            raise HTTPException(404, "Unknown bot.")
        triggers.seen(bot_id)
        desk.read_chat(bot_id, looking.key(authorization))
        return {"ok": True}

    @app.get("/api/triggers")
    def list_triggers(_: str = Depends(require_device)):
        return {"triggers": triggers.for_app()}

    @app.get("/api/triggers/{trigger_id}/link")
    def trigger_link(trigger_id: str, _: str = Depends(require_device)):
        link = triggers.link(trigger_id)
        if not link:
            raise HTTPException(404, "That trigger isn't here anymore.")
        return {"url": link}

    @app.put("/api/triggers/{trigger_id}")
    def set_trigger(trigger_id: str, body: dict, _: str = Depends(require_device)):
        if not triggers.set_state(trigger_id, str(body.get("state") or "")):
            raise HTTPException(404, "That trigger isn't here anymore.")
        return {"ok": True, "trigger": triggers.get(trigger_id)}

    @app.delete("/api/triggers/{trigger_id}")
    def delete_trigger(trigger_id: str, _: str = Depends(require_device)):
        if not triggers.delete(trigger_id):
            raise HTTPException(404, "That trigger isn't here anymore.")
        return {"ok": True}

    @app.post("/api/triggers/{trigger_id}/fire")
    async def fire_trigger(trigger_id: str, body: dict, _: str = Depends(require_device)):
        ""
        text = str(body.get("text") or "").encode()
        try:
            t, eid = await asyncio.to_thread(triggers.accept_from_device, trigger_id, text)
        except triggers.Refused as e:
            raise HTTPException(e.status, str(e))
        triggers.fire(t, eid, len(text.strip()))
        bot = config.BOTS_BY_ID.get(t["bot_id"])
        return {"ok": True, "started": t["name"], "bot_name": bot.name if bot else t["bot_id"]}




    @app.get("/hooks/{trigger_id}/{token}")
    async def hook_get(trigger_id: str, token: str):
        return JSONResponse({"error": "This link starts a trigger only with POST. In Shortcuts, set Get Contents of "
                                      "URL's Method to POST, or use the Zero app's Run a Zip Trigger action."}, 405)

    @app.post("/hooks/{trigger_id}/{token}")
    async def hook(trigger_id: str, token: str, request: Request):
        try:
            triggers.check_link(trigger_id, token)
        except triggers.Refused as e:
            return JSONResponse({"error": str(e)}, e.status)
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > triggers.BODY_MAX:
                return JSONResponse({"error": f"The request is larger than {triggers.BODY_MAX // 1024} KB."}, 413)
        try:
            t, eid = await asyncio.to_thread(triggers.accept, trigger_id, token, body)
        except triggers.Refused as e:
            return JSONResponse({"error": str(e)}, e.status)
        triggers.fire(t, eid, len(body.strip()))
        return JSONResponse({"ok": True, "started": t["name"]}, 202)

    @app.post("/api/places/sync")
    def places_sync(body: dict, _: str = Depends(require_device)):
        ""
        places.report(str(body.get("permission") or ""))
        return {"places": places.for_device()}

    @app.post("/api/places/{rid}/spot")
    def place_spot(rid: int, body: dict, _: str = Depends(require_device)):
        ""
        found = body.get("found") is not False
        try:
            lat, lon = (float(body["lat"]), float(body["lon"])) if found else (None, None)
            d = places.spot(rid, lat, lon, str(body.get("label") or ""))
        except (KeyError, TypeError, ValueError, places.Invalid):
            raise HTTPException(400, "Send lat and lon, or found false.")
        if not d:
            raise HTTPException(404, "That reminder isn't here anymore.")
        return {"ok": True}

    @app.post("/api/places/{rid}/crossed")
    async def place_crossed(rid: int, body: dict, _: str = Depends(require_device)):
        ""

        at = body.get("at")
        try:
            fired = await places.crossed(rid, str(body.get("event") or ""),
                                         float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None)
        except places.Invalid as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "fired": fired}
