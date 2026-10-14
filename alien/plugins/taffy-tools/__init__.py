"""Taffy team tools: connectors bridge, Tally, mail, calendars, fast browser steps, and the sign-in gate.

Which tools a home gets comes from its settings (home.py): connectors only when its plan carries them, Tally only
where it is set up, email when it has a mailbox, calendar when it has a calendar, browser_steps only with the paid
judge on. Tool descriptions are said for the home's person when they are registered."""

import json
import re
from pathlib import Path

from . import asksignin, choices, consent, fast_steps, gate, home, link_click, mac, react, schemas, share, team, teammates, tools
from . import apps_tool, calendar_tool, email_tool, usage_log


def _no_browser_password_manager() -> None:
    """The bots' Chromium must never save passwords itself: there is no keyring on alien, so its own
    store would be weakly protected. Logins live only in the vault. Applied to every bot profile whose
    browser is not running (Chromium rewrites Preferences while it runs)."""
    import subprocess
    from pathlib import Path
    root = home.path("hermes_home", consent.REAL_HOME)
    homes = [root] + [p for p in (root / "profiles").glob("*") if p.is_dir()]
    for here in homes:
        prof = here / "bot-desktop" / "browser-profile"
        try:
            running = subprocess.run(["pgrep", "-f", f"user-data-dir={prof}"], capture_output=True).returncode == 0
        except Exception:
            running = True
        if running:
            continue
        prefs_path = prof / "Default" / "Preferences"
        try:
            prefs = json.loads(prefs_path.read_text()) if prefs_path.exists() else {}
        except ValueError:
            continue
        if prefs.get("credentials_enable_service") is False and \
                (prefs.get("profile") or {}).get("password_manager_enabled") is False and \
                (prefs.get("session") or {}).get("restore_on_startup") == 1:
            continue
        # Keep session cookies when the Zip's desktop stops for being idle, so a sign-in lasts (Oct 8 QA, M5: School
        # re-filled eLearning six times after its desktop restarted).
        prefs.setdefault("session", {})["restore_on_startup"] = 1
        prefs["credentials_enable_service"] = False
        prefs.setdefault("profile", {})["password_manager_enabled"] = False
        prefs.setdefault("autofill", {})["profile_enabled"] = False
        try:
            prefs_path.parent.mkdir(parents=True, exist_ok=True)
            prefs_path.write_text(json.dumps(prefs))
        except OSError:
            pass


def available() -> dict[str, bool]:
    """Which of the home-dependent tools this home registers (see the module's docstring)."""
    return {"connectors": home.connectors_on(), "tally": home.tally() is not None,
            "browser_steps": fast_steps.browser_steps_on(), "email": bool(home.mail_accounts()),
            "calendar": bool(home.calendars(consent.REAL_HOME))}


def register(ctx):
    try:
        _no_browser_password_manager()
    except Exception:
        pass
    on = available()
    if on["connectors"]:
        ctx.register_tool(name="connectors", toolset="taffy", schema=schemas.connectors(), handler=tools.connectors)
    if on["tally"]:
        ctx.register_tool(name="tally", toolset="taffy", schema=schemas.tally(), handler=tools.tally)

    def browser_steps(args: dict, **kwargs) -> str:
        forward = {k: v for k, v in kwargs.items() if k in ("task_id", "parent_agent", "session_id")}
        refused = fast_steps.refusal(args.get("steps") or [])
        if refused:
            return json.dumps({"error": refused})
        for step in args.get("steps") or []:
            kind, _target, url = fast_steps.parse(str(step))
            if kind == "goto":
                if consent.is_sensitive(consent.site_of(url)):
                    return json.dumps({"error": f"browser_steps does not run on {consent.site_of(url)}. "
                                                "Use the normal browser tools there."})
                blocked = gate.pre_tool_call("browser_navigate", {"url": url}, **forward)
                if blocked:
                    return json.dumps({"error": blocked["message"]})
        try:
            from tools.browser_vault_tool import _current_page_origin
            here = consent.site_of(_current_page_origin(forward.get("task_id") or "default") or "")
        except Exception:
            here = ""
        if here and consent.is_sensitive(here):
            return json.dumps({"error": f"browser_steps does not run on {here}. Use the normal browser tools there."})
        import os
        key = gate._fill_key(forward.get("session_id") or "", os.environ.get("HERMES_KANBAN_TASK", ""))
        told = []

        def dispatch(name, a):
            # Hermes runs no plugin hooks on a tool a plugin dispatches itself, so each step goes through the gate's
            # own pre and post checks here, exactly as the same browser tool called on its own would (Oct 8 QA round
            # 7, M1): typing into a password or code field is refused, a send's card shows what was typed, and every
            # snapshot is remembered so a click is judged by what it clicks.
            blocked = gate.pre_tool_call(name, a, **forward)
            if blocked:
                raise fast_steps.Blocked(str(blocked.get("message") or "Not done."))
            line = gate.told_line(key, name)
            if line:
                told.append(line)
            out = ctx.dispatch_tool(name, a, **forward)
            try:
                gate.post_tool_call(name, a, result=out, **forward)
            except Exception:
                pass
            if name in ("browser_click", "browser_navigate", "browser_press", "browser_type"):
                try:
                    from tools.browser_vault_tool import _current_page_origin
                    now = consent.site_of(_current_page_origin(forward.get("task_id") or "default") or "")
                except Exception:
                    now = ""
                if now and consent.is_sensitive(now):
                    raise fast_steps.Sensitive(now)
            return out

        try:
            out = fast_steps.run(dispatch, args, page=link_click.Page(forward.get("task_id")))
        except fast_steps.Sensitive as e:
            return json.dumps({"error": f"browser_steps stopped: the page moved to {e}, where it does not run. "
                                        "Use the normal browser tools there."})
        except Exception as e:
            return json.dumps({"error": f"browser_steps failed: {e}"})
        if told:
            out += ("\n\n" + home.personalize("Mention this to Colin in one plain line of your reply: you did this: ")
                    + "; ".join(dict.fromkeys(told)) + ".")
        return out

    if on["browser_steps"]:
        ctx.register_tool(name="browser_steps", toolset="taffy", schema=home.personalize_schema(fast_steps.SCHEMA),
                          handler=browser_steps)

    def click_opens_links(tool_name: str = "", args=None, next_call=None, **kw):
        """browser_click on a link that wraps onto two lines does nothing (link_click.py). Wraps the bot's own
        browser_click so that such a click opens the link's page, through the same checks as browser_navigate."""
        if tool_name != "browser_click" or not isinstance(args, dict) or not args.get("ref"):
            return next_call(args)
        forward = {k: kw[k] for k in ("task_id", "session_id") if kw.get(k)}

        def open_url(url):
            blocked = gate.pre_tool_call("browser_navigate", {"url": url}, **forward)
            if blocked:
                raise link_click.NotOpened(str(blocked.get("message") or "Not opened."))
            out = ctx.dispatch_tool("browser_navigate", {"url": url}, **forward)
            try:
                gate.post_tool_call("browser_navigate", {"url": url}, result=out, **forward)
            except Exception:
                pass
            if link_click.failed(out):
                raise link_click.NotOpened(str(out)[:300])
            return out

        box = []

        def do_click():
            box.append(next_call(args))
            return box[0]
        try:
            out, opened = link_click.click(args["ref"], do_click, link_click.Page(forward.get("task_id")), open_url)
        except link_click.NotOpened as e:
            return link_click.annotate_refused(box[0] if box else "", str(e))
        return link_click.annotate(out, opened)

    register_middleware = getattr(ctx, "register_middleware", None)
    if register_middleware is not None:
        register_middleware("tool_execution", click_opens_links)

    def team_add_bot(args: dict, **kwargs) -> str:
        try:
            return team.add_bot(args, gate._bot(), kwargs.get("session_id") or "")
        except Exception as e:
            return json.dumps({"error": f"team_add_bot failed: {e}"})

    ctx.register_tool(name="team_add_bot", toolset="taffy", schema=team.schema(), handler=team_add_bot)

    def share_screen(args: dict, **kwargs) -> str:
        try:
            return share.share(args, kwargs.get("session_id") or "")
        except Exception as e:
            return json.dumps({"error": f"share_screen failed: {e}"})

    said = home.personalize_schema
    ctx.register_tool(name="share_screen", toolset="taffy", schema=said(share.SCHEMA), handler=share_screen)

    ctx.register_tool(name="react", toolset="taffy", schema=said(react.SCHEMA), handler=react.react)
    ctx.register_tool(name="offer_choices", toolset="taffy", schema=said(choices.SCHEMA), handler=choices.offer_choices)
    ctx.register_tool(name="ask_to_sign_in", toolset="taffy", schema=said(asksignin.SCHEMA),
                      handler=asksignin.ask_to_sign_in)
    ctx.register_tool(name="close_sign_in_card", toolset="taffy", schema=said(asksignin.CLOSE_SCHEMA),
                      handler=asksignin.close_sign_in_card)
    ctx.register_tool(name="close_card", toolset="taffy", schema=said(asksignin.CLOSE_CARD_SCHEMA),
                      handler=asksignin.close_card)

    if on["email"]:
        ctx.register_tool(name="email", toolset="taffy", schema=email_tool.schema(), handler=email_tool.email_tool)

    if on["calendar"]:
        ctx.register_tool(name="calendar", toolset="taffy", schema=calendar_tool.schema(),
                          handler=calendar_tool.calendar_tool)

    ctx.register_tool(name="trello", toolset="taffy", schema=apps_tool.trello_schema(), handler=apps_tool.trello_tool)
    ctx.register_tool(name="drive", toolset="taffy", schema=apps_tool.drive_schema(), handler=apps_tool.drive_tool)
    for name in apps_tool.READ_ONLY:
        ctx.register_tool(name=name, toolset="taffy", schema=apps_tool.read_only_schema(name),
                          handler=apps_tool.read_only_handler(name))

    for name, op in mac.TOOLS.items():
        ctx.register_tool(name=name, toolset="taffy", schema=said(mac.schema(name)), handler=mac.handler(op))

    for name in teammates.SCHEMAS:
        ctx.register_tool(name=name, toolset="taffy", schema=said(teammates.SCHEMAS[name]),
                          handler=teammates.handler(name))
    ctx.register_hook("pre_llm_call", teammates.pre_llm_call)

    ctx.register_hook("pre_tool_call", gate.pre_tool_call)
    ctx.register_hook("post_tool_call", gate.post_tool_call)

    ctx.register_hook("transform_tool_result", transform_result)

    ctx.register_hook("post_api_request", usage_log.post_api_request)
    ctx.register_hook("post_auxiliary_call", usage_log.post_auxiliary_call)


LEAD_HEARS = ("Tell Colin in one line who has it now. The hub will post a board update in this conversation when the "
              "task finishes, gets blocked or needs review, about a minute after the change. Tell Colin the result "
              "then, in your own words, without him asking. Never tell him to check back or to remind you.")
OTHERS_HEAR = ("The hub tells {lead} when the task finishes, gets blocked or needs review, and {lead} tells Colin. "
               "Never tell Colin to check back or to remind you.")


def _is_lead() -> bool:
    """The lead runs in HERMES_HOME itself; every other Zip in HERMES_HOME/profiles/<id>."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home()).parent.name != "profiles"
    except Exception:
        return True


_VAULT_HINT = re.compile(r"[^.\"]*hermes vault add[^.\"]*\.?")
_ZERO_VAULT_HINT = ("Ask Colin to save the login: use ask_to_sign_in in a chat, or kanban_block with a title that "
                    "starts \"Sign in to <site>\" in a board task.")


def transform_result(tool_name: str = "", result=None, **kw):
    out = board_news(tool_name=tool_name, result=result, **kw)
    if out is not None:
        return out
    if tool_name in ("terminal", "execute_code") and isinstance(result, str) and \
            "BLOCKED: Command timed out without user response" in result:
        # Hermes stopped waiting on its own prompt while the hub's card for it stays up: the same one plain line as
        # the gate's cards (Colin's live test, Oct 8).
        note = home.personalize("The card for this is still in Colin's app. ") + gate.card_reply()
        try:
            data = json.loads(result)
        except ValueError:
            return result + "\n\n" + note
        if isinstance(data, dict):
            data["after_the_card"] = note
            return json.dumps(data, ensure_ascii=False)
        return result + "\n\n" + note
    if tool_name.startswith("browser_vault") and isinstance(result, str) and "hermes vault add" in result:
        # Hermes tells the bot to have the user run a terminal command (Oct 8 QA, L1); Zero's people use the app.
        return _VAULT_HINT.sub(home.personalize(_ZERO_VAULT_HINT).replace("\"", "'"), result, count=1).replace(
            "hermes vault add", "the app")
    import os
    key = gate._fill_key(kw.get("session_id") or "", os.environ.get("HERMES_KANBAN_TASK", ""))
    line = gate.told_line(key, tool_name)
    if not line or not isinstance(result, str):
        return None
    note = home.personalize("Mention this to Colin in one plain line of your reply: you did this: ") + f"{line}."
    try:
        data = json.loads(result)
    except ValueError:
        return result + "\n\n" + note
    if isinstance(data, dict):
        data["tell_person"] = note
        return json.dumps(data, ensure_ascii=False)
    return result + "\n\n" + note


def board_news(tool_name: str = "", result=None, **kw):
    """kanban_create says "subscribed": false because Hermes's own board notices are off (the hub sends them
    instead). Read literally, that made Taffy tell Colin to come back and ask; this says what really happens."""
    if tool_name != "kanban_create" or not isinstance(result, str):
        return None
    try:
        data = json.loads(result)
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None
    data.pop("subscribed", None)
    data["you_will_hear_back"] = home.personalize(LEAD_HEARS) if _is_lead() else \
        home.personalize(OTHERS_HEAR).replace("{lead}", team._lead_name())
    return json.dumps(data)
