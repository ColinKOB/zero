"""The endpoints for Connect Apps (apps.py; docs/specs/2026-10-11-native-connectors-design.md):

  GET    /api/apps                       every app with its state, and the connected count
  POST   /api/apps/{id}/start            where the app sends the person to connect (Trello's page, Google sign-in, a
                                         provider's sign-in page, or GitHub's device code)
  POST   /api/apps/{id}/code             the code the person copied from Trello's page, or the code and state a
                                         provider sent back to zero://oauth/<provider> (oauth.py)
  POST   /api/apps/github/poll           whether the person has entered GitHub's device code yet
  DELETE /api/apps/{id}                  disconnect
  GET    /connect/trello/done/{state}    Trello's redirect lands here (no device key: the state is the key)
  POST   /connect/trello/finish          that page hands over the token, once
  POST   /internal/apps/call             the plugin's app tools (internal key, loopback)

Registered by app.py through install(), which hands over the two auth dependencies.
"""

import asyncio
import base64
import hashlib
import json

from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse

from . import apps, oauth

# The page Trello sends the person to. It reads the token from the address's fragment (which never reaches a
# server), hands it to the hub with the single-use state, and opens Zero again. No outside script, font or image.
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


def install(app, require_device, require_internal) -> None:
    @app.get("/api/apps")
    async def list_apps(_: str = Depends(require_device)):
        from . import accounts
        return await asyncio.to_thread(apps.payload, accounts.cached())

    @app.post("/api/apps/{app_id}/start")
    def start_app(app_id: str, relay: int = 0, _: str = Depends(require_device)):
        try:
            return apps.start(app_id, relay_ok=bool(relay))
        except apps.AppError as e:
            raise HTTPException(409 if not e.connected else 400, str(e))

    @app.post("/api/apps/{app_id}/code")
    async def app_code(app_id: str, body: dict, _: str = Depends(require_device)):
        body = body if isinstance(body, dict) else {}
        if app_id in oauth.PROVIDERS:
            return await asyncio.to_thread(oauth.finish, app_id, str(body.get("code") or ""),
                                           str(body.get("state") or ""))
        if app_id != "trello":
            raise HTTPException(404, "There is no such app.")
        return await asyncio.to_thread(apps.connect_trello, str(body.get("code") or ""))

    @app.post("/api/apps/github/poll")
    async def github_poll(body: dict, _: str = Depends(require_device)):
        body = body if isinstance(body, dict) else {}
        return await asyncio.to_thread(oauth.device_poll, str(body.get("state") or ""))

    @app.delete("/api/apps/{app_id}")
    async def app_disconnect(app_id: str, _: str = Depends(require_device)):
        try:
            return await asyncio.to_thread(apps.disconnect, app_id)
        except apps.AppError as e:
            raise HTTPException(400, str(e))

    @app.get("/connect/trello/done/{state}")
    def trello_done(state: str):
        return HTMLResponse(DONE_PAGE, headers=DONE_HEADERS)

    @app.post("/connect/trello/finish")
    async def trello_finish(body: dict):
        body = body if isinstance(body, dict) else {}
        out = await asyncio.to_thread(apps.finish, str(body.get("state") or ""), str(body.get("token") or ""))
        return {"ok": out["ok"], "detail": out["detail"]}

    @app.post("/internal/apps/call")
    async def internal_apps_call(body: dict, _: None = Depends(require_internal)):
        """{bot_id, session_id, args: {app, action, ...}} -> {ok: true, result} or {ok: true, result: {connected:
        false, message}} or {ok: false, error}. The plugin's gate has already decided whether this may run."""
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        app_id, action = str(args.get("app") or ""), str(args.get("action") or "")
        try:
            result = await asyncio.to_thread(apps.call, app_id, action, args)
        except apps.AppError as e:
            if not e.connected:
                return {"ok": True, "result": {"connected": False, "message": str(e)}}
            return {"ok": False, "error": str(e)}
        except Exception:
            apps.log.exception("apps: %s %s failed", app_id, action)
            return {"ok": False, "error": "That didn't work just now. Try again in a minute."}
        return {"ok": True, "result": json.loads(json.dumps(result, default=str))}
