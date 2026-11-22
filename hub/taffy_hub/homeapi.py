""

























import asyncio
import logging
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from . import config, google, home, mailboxes, notices, onboarding

log = logging.getLogger("taffy.homeapi")

UNITS, SPELLINGS, CLOCKS = ("us", "metric", "uk"), ("american", "british"), ("12h", "24h")


class SettingsError(ValueError):
    pass


def settings_payload() -> dict:
    ""
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
    ""
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
        out = await asyncio.to_thread(mailboxes.add_password_mailbox, body)
        if out.get("ok"):
            from . import accounts
            await accounts.app_connected()
        return out

    @app.delete("/api/accounts/mailboxes/{mailbox_id}")
    def delete_mailbox(mailbox_id: str, _: str = Depends(require_device)):
        out = mailboxes.remove_mailbox(mailbox_id)
        if not out["ok"]:
            raise HTTPException(404, out["detail"])
        return out

    @app.put("/api/accounts/mailboxes/{mailbox_id}/belongs")
    def mailbox_belongs(mailbox_id: str, body: dict, _: str = Depends(require_device)):
        bot = body.get("bot_id")
        out = mailboxes.set_belongs(mailbox_id, str(bot) if bot else None)
        if not out["ok"]:
            raise HTTPException(400, out["detail"])
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

    @app.put("/api/accounts/calendars/{calendar_id}/default")
    def default_calendar(calendar_id: str, _: str = Depends(require_device)):
        out = mailboxes.set_default_calendar(calendar_id)
        if not out["ok"]:
            raise HTTPException(400, out["detail"])
        return out

    @app.post("/internal/accounts/calendar_default")
    def internal_default_calendar(body: dict, _: None = Depends(require_internal)):
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        out = mailboxes.set_default_calendar(str(args.get("calendar") or ""))
        return {"ok": True, "result": out} if out["ok"] else {"ok": False, "error": out["detail"]}

    @app.get("/api/accounts/google")
    def google_status(_: str = Depends(require_device)):
        return {**google.setup(), "accounts": [google.describe(a) for a in google.accounts()]}

    @app.post("/api/accounts/google")
    async def google_connect(body: dict, _: str = Depends(require_device)):
        out = await asyncio.to_thread(mailboxes.connect_google, body)
        if out.get("ok"):
            from . import accounts
            await accounts.app_connected()
        return out

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
        ""


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
            if e.state == google.CONNECTED:
                return {"ok": False, "error": str(e)}
            return {"ok": True, "result": {"connected": False, "state": e.state, "message": str(e)}}
        return {"ok": True, "result": {"access_token": tok["access_token"], "expires_at": tok["expires_at"],
                                       "expires_in": max(0, int(tok["expires_at"] - time.time())),
                                       "token_type": "Bearer", "address": address, "scopes": tok["scopes"]}}

    @app.post("/internal/mail/written_to")
    async def internal_written_to(body: dict, _: None = Depends(require_internal)):
        ""

        from . import mail, mailcursor
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        wanted = list(dict.fromkeys(str(a).strip().lower() for a in args.get("addresses") or [] if a))[:10]
        found: set[str] = set()
        for account in list(mail.ACCOUNTS):
            if not (rest := [a for a in wanted if a not in found]):
                break
            try:
                found |= await asyncio.to_thread(mailcursor.written_to, account, rest)
            except Exception:
                log.warning("could not search %s's sent mail", account, exc_info=True)
        return {"ok": True, "written": sorted(found)}

    @app.post("/api/zip-mail/address")
    @app.post("/internal/zip-mail/address")
    async def zip_mail_address(body: dict, _: None = Depends(require_internal)):
        from . import zipmail
        try:
            out = await asyncio.to_thread(zipmail.create, str(body.get("bot_id") or ""))
        except zipmail.ZipMailError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, **out}

