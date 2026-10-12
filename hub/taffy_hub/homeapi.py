"""The endpoints that read and change the home itself (plan 2026-10-09, section 9, "New hub endpoints"):

  GET  /api/home-settings                 the person, quiet hours, locale, proactivity, the plan summary, Google setup
  PUT  /api/home-settings                 change any of those (partial bodies are fine)
  GET  /api/accounts/mailboxes            the home's mailboxes and calendars with their state
  POST /api/accounts/mailboxes            add a mailbox with an app password (checked before it is kept)
  DELETE /api/accounts/mailboxes/{id}
  POST /api/accounts/calendars            add a CalDAV calendar, a private iCal address or the Google calendar
  DELETE /api/accounts/calendars/{id}
  GET  /api/accounts/google               what the app needs for Sign In with Google, and the connected accounts
  POST /api/accounts/google               the app's authorization code and PKCE verifier
  DELETE /api/accounts/google/{address}   disconnect: revoke at Google, forget the tokens, drop its mailbox and calendar
  POST /internal/google/token             a short-lived access token for the plugin's email and calendar tools
  POST /internal/accounts/google_token    the same in the plugin's request and answer shape (WP2)
  GET  /api/pair/qr?code=...              the pairing link and QR code, for the home scripts on this server only
                                          (internal key, loopback; also at /internal/pair/qr)

Every account answer is {ok, checked, detail, ...} with `detail` one plain sentence. Registered by app.py through
install(), which hands over the two auth dependencies.
"""

import asyncio
import logging
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from . import config, google, home, mailboxes, notices, onboarding, store

log = logging.getLogger("taffy.homeapi")

UNITS, SPELLINGS, CLOCKS = ("us", "metric", "uk"), ("american", "british"), ("12h", "24h")


class SettingsError(ValueError):
    pass


def settings_payload() -> dict:
    """GET /api/home-settings, in the shape the app reads (docs/qa/2026-10-09-zfa-wp4.md)."""
    out = home.summary()
    out["person"]["name"] = onboarding.person_name(True)
    out["person"]["first_name"] = onboarding.person_name(False)
    out["proactivity"] = {"enabled": notices.enabled()}
    owner = str(home.get("plan.owner_name") or "").strip()
    out["plan_owner"] = owner if out["plan"] == "shared" else ""
    out["mail"] = {"accounts": mailboxes.mailboxes()}
    out["calendars"] = mailboxes.calendars()
    out["google"] = {**google.setup(), "accounts": [google.describe(a) for a in google.accounts()]}
    return out


def _hour(v, field: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 23:
        raise SettingsError(f"The {field} should be an hour from 0 to 23.")
    return v


def clean_settings(body: dict) -> tuple[dict, bool | None]:
    """The home.json changes a PUT asks for, and the proactivity switch. Raises SettingsError with a plain sentence."""
    if not isinstance(body, dict):
        raise SettingsError("Check the settings and try again.")
    changes: dict = {}
    person = body.get("person") or {}
    locale = body.get("locale") or {}
    if not isinstance(person, dict) or not isinstance(locale, dict):
        raise SettingsError("Check the settings and try again.")
    p: dict = {}
    if "name" in person:
        name = " ".join(str(person.get("name") or "").split())
        if not name or len(name) > 60:
            raise SettingsError("Enter a name of up to 60 letters.")
        p["name"], p["first_name"] = name, name.split()[0]
    if "timezone" in person:
        tz = str(person.get("timezone") or "").strip()
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise SettingsError("That time zone isn't one this home knows.") from None
        if not re.fullmatch(r"[A-Za-z_]+(/[A-Za-z0-9_+-]+){0,2}", tz):
            raise SettingsError("That time zone isn't one this home knows.")
        p["timezone"] = tz
    if "quiet_hours" in person:
        q = person.get("quiet_hours") or {}
        if not isinstance(q, dict):
            raise SettingsError("Quiet hours need a start and an end.")
        start, end = _hour(q.get("start"), "start of quiet hours"), _hour(q.get("end"), "end of quiet hours")
        if start == end:
            raise SettingsError("Quiet hours should start and end at different times.")
        p["quiet_hours"] = {"start": start, "end": end}
    if p:
        changes["person"] = p
    loc: dict = {}
    for key, allowed in (("units", UNITS), ("spelling", SPELLINGS), ("clock", CLOCKS)):
        if key in locale:
            if locale[key] not in allowed:
                raise SettingsError(f"Pick one of these for {key}: {', '.join(allowed)}.")
            loc[key] = locale[key]
    if loc:
        changes["locale"] = loc
    proactive = body.get("proactivity")
    enabled = None
    if proactive is not None:
        if not isinstance(proactive, dict) or not isinstance(proactive.get("enabled", False), bool):
            raise SettingsError("Questions on their own are on or off.")
        enabled = proactive.get("enabled")
    return changes, enabled


def apply_settings(body: dict) -> dict:
    changes, enabled = clean_settings(body)
    if changes:
        home.save(changes)
    name = (changes.get("person") or {}).get("name")
    if name:
        onboarding.set_person_name(name)
    if enabled is not None:
        notices.set_enabled(enabled)
    return settings_payload()


class TokenBody(BaseModel):
    address: str
    bot_id: str = ""


def pair_qr(code: str) -> dict:
    from .cli import pair_link
    server = str(home.get("network.public_url") or "").strip()
    if not server:
        raise HTTPException(409, "This home has no address for phones yet.")
    link = pair_link(server, code)
    out = {"server": server, "code": code, "link": link, "svg": None}
    try:
        import io
        import qrcode
        import qrcode.image.svg
        buf = io.BytesIO()
        qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage).save(buf)
        out["svg"] = buf.getvalue().decode()
    except Exception:
        pass
    return out


def install(app, require_device, require_internal) -> None:
    @app.get("/api/home-settings")
    def get_home_settings(_: str = Depends(require_device)):
        return settings_payload()

    @app.put("/api/home-settings")
    def put_home_settings(body: dict, _: str = Depends(require_device)):
        try:
            return apply_settings(body)
        except SettingsError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/accounts/mailboxes")
    def list_mailboxes(_: str = Depends(require_device)):
        return mailboxes.status()

    @app.post("/api/accounts/mailboxes")
    async def add_mailbox(body: dict, _: str = Depends(require_device)):
        return await asyncio.to_thread(mailboxes.add_password_mailbox, body)

    @app.delete("/api/accounts/mailboxes/{mailbox_id}")
    def delete_mailbox(mailbox_id: str, _: str = Depends(require_device)):
        out = mailboxes.remove_mailbox(mailbox_id)
        if not out["ok"]:
            raise HTTPException(404, out["detail"])
        return out

    @app.post("/api/accounts/calendars")
    async def add_calendar(body: dict, _: str = Depends(require_device)):
        return await asyncio.to_thread(mailboxes.add_calendar, body)

    @app.delete("/api/accounts/calendars/{calendar_id}")
    def delete_calendar(calendar_id: str, _: str = Depends(require_device)):
        out = mailboxes.remove_calendar(calendar_id)
        if not out["ok"]:
            raise HTTPException(404, out["detail"])
        return out

    @app.get("/api/accounts/google")
    def google_status(_: str = Depends(require_device)):
        return {**google.setup(), "accounts": [google.describe(a) for a in google.accounts()]}

    @app.post("/api/accounts/google")
    async def google_connect(body: dict, _: str = Depends(require_device)):
        return await asyncio.to_thread(mailboxes.connect_google, body)

    @app.delete("/api/accounts/google/{address}")
    async def google_disconnect(address: str, _: str = Depends(require_device)):
        return await asyncio.to_thread(mailboxes.disconnect_google, address)

    @app.post("/internal/google/token")
    async def internal_google_token(body: TokenBody, _: None = Depends(require_internal)):
        address = body.address.strip().lower()
        known = {m["address"].lower() for m in home.mail_accounts() if home.is_google(m)} | \
                {home.calendar_address(c) for c in home.calendars() if home.is_google_calendar(c)}
        if address not in known:
            return {"ok": False, "state": google.MISSING, "error": f"{address} isn't connected to Google here."}
        try:
            tok = await asyncio.to_thread(google.access_token, address)
        except google.GoogleError as e:
            return {"ok": False, "state": e.state, "error": str(e)}
        return {"ok": True, "address": address, **tok}

    @app.post("/internal/accounts/google_token")
    async def internal_account_google_token(body: dict, _: None = Depends(require_internal)):
        """The plugin's email and calendar tools (WP2's contract): {bot_id, session_id, args: {account, address,
        purpose}} -> {ok: true, result: {access_token, expires_at, expires_in}}, or {ok: true, result: {connected:
        false, message}} when the sign-in is missing or expired, or {ok: false, error}."""
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        address = str(args.get("address") or "").strip().lower()
        account = str(args.get("account") or "").strip()
        if not address and account:
            m = next((m for m in home.mail_accounts() if m["id"] == account), None)
            c = next((c for c in home.calendars() if c.get("id") == account), None)
            address = (m["address"].lower() if m else home.calendar_address(c) if c else "")
        known = {m["address"].lower() for m in home.mail_accounts() if home.is_google(m)} | \
                {home.calendar_address(c) for c in home.calendars() if home.is_google_calendar(c)}
        if not address or address not in known:
            return {"ok": True, "result": {"connected": False,
                                           "message": "That account isn't connected to Google here. Ask the person "
                                                      "to sign in with Google in the app."}}
        try:
            tok = await asyncio.to_thread(google.access_token, address)
        except google.GoogleError as e:
            if e.state == google.CONNECTED:   # Google could not be reached; the sign-in itself is fine
                return {"ok": False, "error": str(e)}
            return {"ok": True, "result": {"connected": False, "state": e.state, "message": str(e)}}
        return {"ok": True, "result": {"access_token": tok["access_token"], "expires_at": tok["expires_at"],
                                       "expires_in": max(0, int(tok["expires_at"] - time.time())),
                                       "token_type": "Bearer", "address": address, "scopes": tok["scopes"]}}

    @app.get("/api/pair/qr")      # the plan's name for it; loopback scripts only, like /internal/*
    @app.get("/internal/pair/qr")
    def internal_pair_qr(code: str = "", _: None = Depends(require_internal)):
        code = re.sub(r"[^A-Za-z0-9]", "", code)
        if not code:
            code = store.new_pairing_code(15 * 60)
        return pair_qr(code)
