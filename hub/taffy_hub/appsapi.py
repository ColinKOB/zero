""


















import asyncio
import base64
import hashlib
import json

from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse

from . import apps, connections, keys, oauth, plaid, service_keys



DONE_SCRIPT = """
(function () {
  var parts = location.pathname.split('/');
  var state = parts[parts.length - 1];
  var m = /[#&]token=([A-Za-z0-9]+)/.exec(location.hash || '');
  history.replaceState(null, '', location.pathname);
  var say = function (t) { document.getElementById('m').textContent = t; };
  if (!m) { say('Trello did not connect. Go back to Zero and try again.'); return; }
  fetch('/connect/trello/finish', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({state: state, token: m[1]})})
    .then(function (r) { return r.json(); })
    .then(function (r) {
      say(r.ok ? 'Trello is connected. Going back to Zero.' : r.detail);
      location.href = 'zero://connected/trello?ok=' + (r.ok ? '1' : '0');
    })
    .catch(function () { say('Zero could not be reached. Go back to Zero and try again.'); });
})();
"""
DONE_HASH = base64.b64encode(hashlib.sha256(DONE_SCRIPT.encode()).digest()).decode()
DONE_PAGE = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
             "<meta name='referrer' content='no-referrer'><title>Zero</title></head>"
             "<body style='font-family:-apple-system,system-ui,sans-serif;padding:32px;font-size:18px'>"
             "<p id='m'>Connecting Trello.</p><script>" + DONE_SCRIPT + "</script></body></html>")
DONE_HEADERS = {"Content-Security-Policy": f"default-src 'none'; script-src 'sha256-{DONE_HASH}'; connect-src 'self'; "
                                           "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
                "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}


_signing_in: set[asyncio.Task] = set()


async def _finish_github(state: str, wait: int) -> None:
    ""


    try:
        while True:
            await asyncio.sleep(wait)
            out = await asyncio.to_thread(oauth.device_poll, state)
            if not out.get("pending"):
                break
            wait = out["interval"]
        await _woke(out)
    except Exception:
        apps.log.exception("apps: finishing the github sign-in failed")


async def _finish_bank(state: str) -> None:
    ""

    try:
        while True:
            await asyncio.sleep(plaid.POLL_SEC)
            out = await asyncio.to_thread(plaid.link_finish, state)
            if not out.get("pending"):
                break
        await _woke(out)
    except Exception:
        apps.log.exception("apps: finishing the bank connection failed")


async def _woke(out: dict) -> dict:
    ""
    if isinstance(out, dict) and out.get("ok"):
        from . import accounts
        await accounts.app_connected()
    return out


def install(app, require_device, require_internal) -> None:
    @app.get("/api/apps")
    async def list_apps(bank: int = 0, _: str = Depends(require_device)):
        from . import accounts
        return await asyncio.to_thread(apps.payload, accounts.cached(), bool(bank))

    @app.post("/api/apps/{app_id}/start")
    async def start_app(app_id: str, body: dict | None = None, relay: int = 0, _: str = Depends(require_device)):
        if service_keys.is_id(app_id):
            try:
                return await asyncio.to_thread(service_keys.start, app_id)
            except service_keys.ServiceError as e:
                raise HTTPException(409, str(e))
        try:

            out = await asyncio.to_thread(apps.start, app_id, relay_ok=bool(relay), connection=body)
        except apps.AppError as e:
            raise HTTPException(409 if not e.connected else 400, str(e))
        if out.get("mode") in ("device", "bank"):
            finish = _finish_github(out["state"], out["interval"]) if out["mode"] == "device" else _finish_bank(out["state"])
            task = asyncio.create_task(finish)
            _signing_in.add(task)
            task.add_done_callback(_signing_in.discard)
        return out

    @app.post("/api/apps/{app_id}/code")
    async def app_code(app_id: str, body: dict, _: str = Depends(require_device)):
        body = body if isinstance(body, dict) else {}
        if service_keys.is_id(app_id):
            try:
                return await _woke(await asyncio.to_thread(service_keys.save, app_id, body))
            except service_keys.ServiceError as e:
                raise HTTPException(404, str(e))
        if app_id in oauth.PROVIDERS:
            return await _woke(await asyncio.to_thread(oauth.finish, app_id, str(body.get("code") or ""),
                                                       str(body.get("state") or "")))
        if app_id in keys.BY_ID:
            def save_key():
                selection = connections.key_finish(app_id, str(body["state"])) if body.get("state") else None
                with connections.using(selection):
                    out = apps.connect_key(app_id, str(body.get("code") or ""))
                if out.get("ok") and body.get("state"):
                    connections.key_done(str(body["state"]))
                return out
            try:
                return await _woke(await asyncio.to_thread(save_key))
            except apps.AppError as e:
                raise HTTPException(400, str(e))
        if app_id != "trello":
            raise HTTPException(404, "There is no such app.")
        return await _woke(await asyncio.to_thread(apps.connect_trello, str(body.get("code") or "")))

    @app.post("/api/apps/github/poll")
    async def github_poll(body: dict, _: str = Depends(require_device)):
        body = body if isinstance(body, dict) else {}
        return oauth.device_status(str(body.get("state") or ""))

    @app.post("/api/apps/plaid/poll")
    async def bank_poll(body: dict, _: str = Depends(require_device)):
        body = body if isinstance(body, dict) else {}
        return await asyncio.to_thread(plaid.link_finish, str(body.get("state") or ""), bool(body.get("closed")))

    @app.delete("/api/apps/{app_id}")
    async def app_disconnect(app_id: str, _: str = Depends(require_device)):
        if service_keys.is_id(app_id):
            try:
                return await asyncio.to_thread(service_keys.remove, app_id)
            except service_keys.ServiceError as e:
                raise HTTPException(404, str(e))
        try:
            return await asyncio.to_thread(apps.disconnect, app_id)
        except apps.AppError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/apps/{app_id}/share")
    async def app_share(app_id: str, body: dict, _: str = Depends(require_device)):
        if not service_keys.is_id(app_id):
            raise HTTPException(404, "There is no such key.")
        try:
            return await asyncio.to_thread(service_keys.share, app_id, bool((body or {}).get("shared")))
        except service_keys.ServiceError as e:
            raise HTTPException(404, str(e))

    @app.post("/internal/service-keys/ask")
    async def internal_key_ask(body: dict, _: None = Depends(require_internal)):
        from . import accounts, config, team, watcher
        bot_id, session = str(body.get("bot_id") or ""), str(body.get("session_id") or "") or None
        bot = config.BOTS_BY_ID.get(bot_id)
        if not bot:
            raise HTTPException(404, "Unknown bot.")
        if team.in_team_session(session):
            return {"ok": False, "error": "You are answering a teammate. Ask for the key from your own chat."}
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        try:
            fid, said = await asyncio.to_thread(service_keys.ask, bot_id, args, session, body.get("origin_task"))
        except service_keys.ServiceError as e:
            return {"ok": False, "error": str(e)}
        if fid:
            name = service_keys.slug(args.get("service"))
            title = (service_keys.PRESETS.get(name) or {}).get("name") or " ".join(str(args.get("service")).split())[:40]
            why = str(args.get("why") or "").rstrip(".")
            await watcher.push(f"{bot.name} needs {title}", f"Can you add a {title} key so I can {why}?", bot_id)
        return {"ok": True, "result": said}

    @app.post("/internal/service-keys/call")
    async def internal_key_call(body: dict, _: None = Depends(require_internal)):
        from . import config
        bot_id = str(body.get("bot_id") or "")
        if bot_id not in config.BOTS_BY_ID:
            raise HTTPException(404, "Unknown bot.")
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        try:
            return {"ok": True, "result": await asyncio.to_thread(service_keys.call, bot_id, args)}
        except service_keys.ServiceError as e:
            return {"ok": False, "error": str(e)}

    @app.get("/api/apps/{app_id}/connections")
    async def app_connections(app_id: str, _: str = Depends(require_device)):
        try:
            return await asyncio.to_thread(connections.payload, app_id)
        except apps.AppError as e:
            raise HTTPException(400, str(e))

    @app.put("/api/apps/{app_id}/connections/{connection_id}")
    async def update_connection(app_id: str, connection_id: str, body: dict, _: str = Depends(require_device)):
        try:
            out = await asyncio.to_thread(connections.update, app_id, connection_id, body)
            return await _woke(out)
        except apps.AppError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/apps/{app_id}/connections/{connection_id}")
    async def delete_connection(app_id: str, connection_id: str, _: str = Depends(require_device)):
        if app_id == "plaid":
            return await asyncio.to_thread(plaid.disconnect, connection_id)

        def remove():
            selection = connections.begin(app_id, {"connection_id": connection_id})
            with connections.using(selection):
                return apps.disconnect(app_id)
        try:
            return await asyncio.to_thread(remove)
        except apps.AppError as e:
            raise HTTPException(400, str(e))

    @app.get("/connect/trello/done/{state}")
    def trello_done(state: str):
        return HTMLResponse(DONE_PAGE, headers=DONE_HEADERS)

    @app.post("/connect/trello/finish")
    async def trello_finish(body: dict):
        body = body if isinstance(body, dict) else {}
        out = await _woke(await asyncio.to_thread(apps.finish, str(body.get("state") or ""),
                                                  str(body.get("token") or "")))
        return {"ok": out["ok"], "detail": out["detail"]}

    @app.post("/internal/apps/call")
    async def internal_apps_call(body: dict, _: None = Depends(require_internal)):
        ""

        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        app_id, action = str(args.get("app") or ""), str(args.get("action") or "")
        try:
            result = await asyncio.to_thread(apps.call, app_id, action, args, str(body.get("bot_id") or ""))
        except apps.AppError as e:
            if not e.connected:

                from . import accounts

                card = None if args.get("no_card") else \
                    await accounts.need_app(str(body.get("bot_id") or ""), app_id, e.why, body.get("session_id") or None)
                return {"ok": True, "result": {"connected": False, "message": card or str(e)}}
            return {"ok": False, "error": str(e)}
        except Exception:
            apps.log.exception("apps: %s %s failed", app_id, action)
            return {"ok": False, "error": "That didn't work just now. Try again in a minute."}
        return {"ok": True, "result": json.loads(json.dumps(result, default=str))}
