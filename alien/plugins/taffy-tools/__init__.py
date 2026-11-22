""






import json
import os
import re
import time
from pathlib import Path


from . import failclosed
failclosed.install()
_import_began = time.monotonic()

from . import askcode, asksignin, choices, claudeaccount, codesession, consent, fast_steps, gate, home, kitcard, link_click, mac, project, react, results, schemas, sessioncard, share, team, team_space, teammates, todolist, tools, wallet, workproject
from . import teamchats
from . import intent
from . import apps_tool, browser_restart, expect, repeats, snapshot_view, stale_refs, zip_cursor, login_notes, bank_tool, bills_tool, calendar_tool, contacts_tool, downloads, email_tool, errands, handoff, lookup, media_tool, messages_tool, models, pagelook, pages_tool, proposal, reminders_tool, reply_rules, service_tool, siblings, tiers, usage_log, watch_texts_tool, xpost


def _no_browser_password_manager() -> None:
    ""


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
    ""
    return {"connectors": home.connectors_on(), "tally": home.tally() is not None,
            "browser_steps": fast_steps.browser_steps_on(),

            "email": bool(home.mail_accounts()) or email_tool.zip_mail_on(),
            "calendar": bool(home.calendars(consent.REAL_HOME))}


def jobs_report_here(tool_name: str = "", args=None, **kw):
    ""




    if tool_name not in tiers.JOB_TOOLS or not isinstance(args, dict):
        return None
    action = str(args.get("action") or "").lower()
    if action not in ("create", "add", "update", "edit"):
        return None
    deliver = args.get("deliver")
    if deliver == "local" or (deliver in (None, "") and action not in ("create", "add")):
        return None
    return {"action": "modify", "args": {"deliver": "local"}}


def jobs_keep_zip_tools(tool_name: str = "", args=None, **kw):
    ""





    if tool_name not in tiers.JOB_TOOLS or not isinstance(args, dict):
        return None
    if str(args.get("action") or "").lower() not in ("create", "add", "update", "edit"):
        return None
    tools = args.get("enabled_toolsets")
    if not isinstance(tools, list) or not any(str(t).strip() for t in tools) or "taffy" in tools:
        return None
    return {"action": "modify", "args": {"enabled_toolsets": [*tools, "taffy"]}}


def _sign_in_follows_the_switch() -> None:
    ""








    from tools.registry import registry
    entry = registry.get_entry("mac_codex")
    if entry is not None and not getattr(entry.dynamic_schema_overrides, "zero_sign_in", False):
        def overrides():
            return {"parameters": home.personalize_schema(mac.schema("mac_codex"))["parameters"]}
        overrides.zero_sign_in = True
        entry.dynamic_schema_overrides = overrides






    import sys
    model_tools = sys.modules.get("model_tools")
    base = getattr(model_tools, "_tool_defs_cache_key", None)
    if base is not None and not getattr(base, "zero_sign_in", False):
        def key(*args, **kwargs):
            out = base(*args, **kwargs)
            return out if out is None else (*out, mac.saved_logins_on())
        key.zero_sign_in = True
        model_tools._tool_defs_cache_key = key


def _sign_in_key_later(**kw):
    ""
    try:
        _sign_in_follows_the_switch()
    except Exception:
        pass
    return None


def _offer_expect() -> None:
    ""


    from tools.registry import registry
    entry = registry.get_entry("browser_click")
    if entry is None or getattr(entry.dynamic_schema_overrides, "zero_expect", False):
        return
    base = entry.dynamic_schema_overrides

    def overrides():
        out = dict(base() or {}) if base is not None else {}
        params = out.get("parameters") or entry.schema.get("parameters") or {}
        out["parameters"] = {**params, "properties": {**(params.get("properties") or {}), "expect": expect.PARAM}}
        out["description"] = f"{out.get('description') or entry.schema.get('description') or ''} {expect.HINT}".strip()
        return out
    overrides.zero_expect = True
    entry.dynamic_schema_overrides = overrides


def _board_tasks_know_the_line() -> None:
    ""

    from tools.registry import registry
    entry = registry.get_entry("kanban_create")
    if entry is None or getattr(entry.dynamic_schema_overrides, "zero_build_line", False):
        return
    base = entry.dynamic_schema_overrides

    def overrides():
        out = dict(base() or {}) if base is not None else {}
        said = out.get("description") or entry.schema.get("description") or ""
        out["description"] = f"{said} {home.personalize(codesession.FIX_OR_BUILD)}".strip()
        return out
    overrides.zero_build_line = True
    entry.dynamic_schema_overrides = overrides


LOAD_WARN_SEC = 4.0


def register(ctx):
    global _import_began
    began, _import_began = _import_began or time.monotonic(), 0.0
    failclosed.install()
    try:
        _no_browser_password_manager()
    except Exception:
        pass
    try:
        _offer_expect()
    except Exception:
        pass
    try:
        _board_tasks_know_the_line()
    except Exception:
        pass
    on = available()
    if on["connectors"]:
        ctx.register_tool(name="connectors", toolset="taffy", schema=schemas.connectors(), handler=tools.connectors)
    if on["tally"]:
        ctx.register_tool(name="tally", toolset="taffy", schema=schemas.tally(), handler=tools.tally)

    def browser_steps(args: dict, *, exact=False, **kwargs) -> str:
        if exact:
            args = {"steps": args.get("steps"), "min_confidence": 1.0}

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




            blocked = gate.pre_tool_call(name, a, **forward)
            if blocked:
                raise fast_steps.Blocked(str(blocked.get("message") or "Not done."))
            line = gate.told_line(key, name)
            if line:
                told.append(line)
            zip_cursor.safely(zip_cursor.before_browser_step, name, a, forward.get("task_id") or "default")
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
            out = fast_steps.run(dispatch, args, page=link_click.Page(forward.get("task_id")),
                                 picker=fast_steps.exact_pick if exact else None,
                                 tool_name="browser_actions" if exact else "browser_steps")
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

    def browser_actions(args: dict, **kwargs) -> str:
        steps = args.get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 25 or not all(
                isinstance(step, str) and step.strip() for step in steps):
            return json.dumps({"error": "Give 1 to 25 nonempty browser steps."})
        return browser_steps(args, exact=True, **kwargs)

    from . import login_notes
    ctx.register_tool(name="login_note", toolset="taffy", schema=home.personalize_schema(login_notes.SCHEMA),
                      handler=login_notes.tool)

    ctx.register_tool(name="browser_actions", toolset="taffy", schema=home.personalize_schema(fast_steps.EXACT_SCHEMA),
                      handler=browser_actions)

    def click_opens_links(tool_name: str = "", args=None, next_call=None, **kw):
        ""



        if tool_name != "browser_click" or not isinstance(args, dict) or not args.get("ref"):
            return next_call(args)
        forward = {k: kw[k] for k in ("task_id", "session_id") if kw.get(k)}
        task = forward.get("task_id") or "default"
        want = " ".join(args["expect"].split()) if isinstance(args.get("expect"), str) else ""
        if "expect" in args:
            args = {k: v for k, v in args.items() if k != "expect"}
        if want:
            before = stale_refs.text(task)
            meant = stale_refs.element(task, str(args["ref"]))
            again_ok = gate.press_again_ok(str(args["ref"]), forward.get("session_id") or "")

        file_link = []

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
                if downloads.aborted(out):
                    file_link.append(url)
                raise link_click.NotOpened(str(out)[:300])
            return out

        def read_page():
            ""
            if gate.pre_tool_call("browser_snapshot", {"full": True}, **forward):
                return None
            page_out = ctx.dispatch_tool("browser_snapshot", {"full": True}, **forward)
            gate.post_tool_call("browser_snapshot", {"full": True}, result=page_out, **forward)
            try:
                page_data = json.loads(page_out)
            except (TypeError, ValueError):
                return None
            if link_click.failed(page_data) or not isinstance(page_data.get("snapshot"), str):
                return None
            return page_data["snapshot"]

        box = []

        def do_click():
            box.append(next_call(args))
            return box[0]
        try:
            out, opened = link_click.click(args["ref"], do_click, link_click.Page(forward.get("task_id")), open_url)
        except link_click.NotOpened as e:
            if file_link:
                return downloads.with_hint(box[0] if box else "", file_link[0])
            return link_click.annotate_refused(box[0] if box else "", str(e))
        out = link_click.annotate(out, opened)
        if link_click.failed(out):
            return out
        try:
            result = json.loads(out)
        except (TypeError, ValueError):
            return out
        if not isinstance(result, dict):
            return out
        try:
            snapshot = read_page()
            if snapshot is None:
                raise RuntimeError("The page could not be read")
            result["snapshot"] = snapshot
            miss = stale_refs.missed(forward.get("task_id") or "default", str(args["ref"]), result["snapshot"])
            if miss:
                result["click_did_not_take"] = miss
        except Exception:
            result["verification_error"] = (
                "The click ran, but its page could not be read. Take fresh state before acting again.")
        if want and isinstance(result.get("snapshot"), str):
            def press_again(read):


                if not before or not stale_refs.only(before, meant):
                    return False
                ref = stale_refs.only(read, meant)


                if not ref or not gate.press_again_ok(ref, forward.get("session_id") or ""):
                    return False
                return link_click.Page(forward.get("task_id")).click_center(ref)
            try:
                result["expect"], result["snapshot"] = expect.verify(
                    want, before, result["snapshot"], read_page, press_again if again_ok else None)
            except Exception:
                result["expect"] = "The click ran, but whether the page changed could not be checked. Read the page."
        return json.dumps(result)

    def page_words(tool_name: str = "", args=None, next_call=None, **kw):
        ""
        if not isinstance(args, dict) or args.get("full") or tool_name not in ("browser_snapshot", "browser_navigate"):
            return next_call(args)
        if tool_name == "browser_snapshot":
            return snapshot_view.snapshot(lambda full: next_call({**args, "full": full}))
        forward = {k: kw[k] for k in ("task_id", "session_id") if kw.get(k)}

        def read_whole():
            if gate.pre_tool_call("browser_snapshot", {"full": True}, **forward):
                return None
            out = ctx.dispatch_tool("browser_snapshot", {"full": True}, **forward)
            gate.post_tool_call("browser_snapshot", {"full": True}, result=out, **forward)
            return out

        return snapshot_view.navigate(next_call(args), read_whole)

    def tells_of_a_restart(tool_name: str = "", args=None, next_call=None, **kw):
        ""
        out = next_call(args)
        if tool_name not in browser_restart.WATCHED:
            return out
        forward = {k: kw[k] for k in ("task_id", "session_id") if kw.get(k)}

        def reopen(url):
            if gate.pre_tool_call("browser_navigate", {"url": url}, **forward):
                raise link_click.NotOpened(url)
            opened = ctx.dispatch_tool("browser_navigate", {"url": url}, **forward)
            gate.post_tool_call("browser_navigate", {"url": url}, result=opened, **forward)
            if link_click.failed(opened):
                raise link_click.NotOpened(url)
        try:
            note = browser_restart.after(forward.get("task_id") or "", tool_name, link_click.Page(forward.get("task_id")).url, reopen)
        except Exception:
            return out
        return _with_note(out, "browser_restarted", note) if note and isinstance(out, str) else out

    register_middleware = getattr(ctx, "register_middleware", None)
    if register_middleware is not None:
        register_middleware("tool_execution", click_opens_links)


        register_middleware("tool_execution", tells_of_a_restart)
        register_middleware("tool_execution", page_words)


        register_middleware("tool_execution", zip_cursor.browser_middleware)

    def team_add_bot(args: dict, **kwargs) -> str:
        try:
            return team.add_bot(args, gate._bot(), kwargs.get("session_id") or "")
        except Exception as e:
            return json.dumps({"error": f"team_add_bot failed: {e}"})

    ctx.register_tool(name="team_add_bot", toolset="taffy", schema=team.schema(), handler=team_add_bot)


    ctx.register_tool(name="browser_download", toolset="taffy", schema=home.personalize_schema(downloads.SCHEMA),
                      handler=downloads.handler(gate._bot))

    def share_screen(args: dict, **kwargs) -> str:
        try:
            return share.share(args, kwargs.get("session_id") or "", kwargs.get("task_id"))
        except Exception as e:
            return json.dumps({"error": f"share_screen failed: {e}"})

    said = home.personalize_schema
    ctx.register_tool(name="share_screen", toolset="taffy", schema=said(share.SCHEMA), handler=share_screen)

    def share_result(args: dict, **kwargs) -> str:
        try:
            return results.share_result(args, **kwargs)
        except Exception as e:
            return json.dumps({"error": f"share_result failed: {e}"})

    ctx.register_tool(name="share_result", toolset="taffy", schema=said(results.SCHEMA), handler=share_result)

    def preview_screenshot(args: dict, **kwargs):
        try:
            return pagelook.preview_screenshot(args, **kwargs)
        except Exception as e:
            return json.dumps({"error": f"preview_screenshot failed: {e}"})

    ctx.register_tool(name="preview_screenshot", toolset="taffy", schema=said(pagelook.SCHEMA),
                      handler=preview_screenshot)

    ctx.register_tool(name="react", toolset="taffy", schema=said(react.SCHEMA), handler=react.react)
    ctx.register_tool(name="offer_choices", toolset="taffy", schema=said(choices.SCHEMA), handler=choices.offer_choices)
    ctx.register_tool(name="offer_project", toolset="taffy", schema=said(project.SCHEMA), handler=project.offer_project)
    ctx.register_tool(name="session_card", toolset="taffy", schema=said(sessioncard.SCHEMA),
                      handler=sessioncard.session_card)
    ctx.register_tool(name="card", toolset="taffy", schema=said(kitcard.SCHEMA), handler=kitcard.card)
    ctx.register_tool(name="app_todo_list", toolset="taffy", schema=said(todolist.SCHEMA), handler=todolist.todo_list)
    ctx.register_tool(name="work_project", toolset="taffy", schema=said(workproject.SCHEMA),
                      handler=workproject.work_project)
    ctx.register_tool(name="code_session", toolset="taffy", schema=said(codesession.schema()),
                      handler=codesession.code_session)
    ctx.register_tool(name="ask_to_sign_in", toolset="taffy", schema=said(asksignin.SCHEMA),
                      handler=asksignin.ask_to_sign_in)
    ctx.register_tool(name="ask_to_wake_mac", toolset="taffy", schema=said(asksignin.WAKE_SCHEMA),
                      handler=asksignin.ask_to_wake_mac)
    ctx.register_tool(name="ask_to_connect", toolset="taffy", schema=said(asksignin.CONNECT_SCHEMA),
                      handler=asksignin.ask_to_connect)
    ctx.register_tool(name="close_sign_in_card", toolset="taffy", schema=said(asksignin.CLOSE_SCHEMA),
                      handler=asksignin.close_sign_in_card)
    ctx.register_tool(name="hand_over", toolset="taffy", schema=said(asksignin.HAND_OVER_SCHEMA),
                      handler=asksignin.hand_over)
    ctx.register_tool(name="ask_for_code", toolset="taffy", schema=said(askcode.ASK_SCHEMA),
                      handler=askcode.ask_for_code)
    ctx.register_tool(name="close_code_card", toolset="taffy", schema=said(askcode.CLOSE_SCHEMA),
                      handler=askcode.close_code_card)

    def enter_texted_code(args: dict, **kwargs) -> str:



        forward = {k: v for k, v in kwargs.items() if k in ("task_id", "parent_agent", "session_id")}
        key = gate._fill_key(forward.get("session_id") or "", os.environ.get("HERMES_KANBAN_TASK", ""))
        return askcode.enter_texted_code(
            args, lambda name, a: ctx.dispatch_tool(name, a, **forward), site=gate._site({}, forward.get("task_id") or ""),
            secure=lambda: gate._page_is_https(forward.get("task_id") or ""),
            label_of=lambda ref: consent.ref_label(key, ref)[0], mark_filled=lambda: gate._mark_filled(key),
            signed_in=lambda: consent.mark_signed_in(gate._bot(), gate._site({}, forward.get("task_id") or "")), **forward)

    ctx.register_tool(name="enter_texted_code", toolset="taffy", schema=said(askcode.ENTER_SCHEMA),
                      handler=enter_texted_code)
    ctx.register_tool(name="close_card", toolset="taffy", schema=said(asksignin.CLOSE_CARD_SCHEMA),
                      handler=asksignin.close_card)
    ctx.register_tool(name="explain_card", toolset="taffy", schema=said(asksignin.EXPLAIN_CARD_SCHEMA),
                      handler=asksignin.explain_card)


    ctx.register_tool(name="post_to_x", toolset="taffy", schema=said(xpost.SCHEMA),
                      handler=xpost.handler(lambda name, a, **kw: ctx.dispatch_tool(name, a, **kw)))


    ctx.register_tool(name="ask_for_api_key", toolset="taffy", schema=said(service_tool.ASK_SCHEMA),
                      handler=service_tool.ask_for_api_key)
    ctx.register_tool(name="service_api", toolset="taffy", schema=said(service_tool.CALL_SCHEMA),
                      handler=service_tool.service_api)



    if on["email"]:
        ctx.register_tool(name="email", toolset="taffy", schema=email_tool.schema(), handler=email_tool.email_tool)
    else:
        ctx.register_tool(name="email", toolset="taffy", schema=email_tool.none_schema(),
                          handler=email_tool.no_mailbox)



    for name, schema, handler in wallet.TOOLS:
        ctx.register_tool(name=name, toolset="taffy", schema=said(schema), handler=handler)
    wallet.install_redeemer()
    gate.install_login_site()
    from . import shared_state
    shared_state.install()
    from . import login_access
    login_access.install()


    ctx.register_tool(name="errands", toolset="taffy", schema=said(errands.SCHEMA), handler=errands.errands)


    if on["calendar"]:
        ctx.register_tool(name="calendar", toolset="taffy", schema=calendar_tool.schema(),
                          handler=calendar_tool.calendar_tool)
    else:



        ctx.register_tool(name="calendar", toolset="taffy", schema=calendar_tool.none_schema(),
                          handler=calendar_tool.no_calendar)



    ctx.register_tool(name="trello", toolset="taffy", schema=apps_tool.trello_schema(), handler=apps_tool.trello_tool)
    ctx.register_tool(name="drive", toolset="taffy", schema=apps_tool.drive_schema(), handler=apps_tool.drive_tool)
    for name in apps_tool.READ_ONLY:
        ctx.register_tool(name=name, toolset="taffy", schema=apps_tool.read_only_schema(name),
                          handler=apps_tool.read_only_handler(name))
    for name in apps_tool.KEYED:
        ctx.register_tool(name=name, toolset="taffy", schema=apps_tool.keyed_schema(name),
                          handler=apps_tool.keyed_handler(name))
    ctx.register_tool(name="bank", toolset="taffy", schema=bank_tool.schema(), handler=bank_tool.bank)
    ctx.register_tool(name="bills", toolset="taffy", schema=bills_tool.schema(), handler=bills_tool.bills)
    ctx.register_tool(name="make_media", toolset="taffy", schema=media_tool.schema(),
                      handler=media_tool.make_media)


    ctx.register_tool(name="messages", toolset="taffy", schema=messages_tool.schema(), handler=messages_tool.messages)

    ctx.register_tool(name="watch_texts", toolset="taffy", schema=watch_texts_tool.schema(),
                      handler=watch_texts_tool.watch_texts)

    for name, op in mac.TOOLS.items():
        ctx.register_tool(name=name, toolset="taffy", schema=said(mac.schema(name)), handler=mac.handler(op))
    try:
        _sign_in_follows_the_switch()
    except Exception:
        pass


    ctx.register_tool(name="reminders", toolset="taffy", schema=reminders_tool.schema(),
                      handler=reminders_tool.reminders_tool)

    ctx.register_tool(name="contacts", toolset="taffy", schema=contacts_tool.schema(), handler=contacts_tool.contacts_tool)

    ctx.register_tool(name="look_up", toolset="taffy", schema=lookup.schema(), handler=lookup.look_up)

    ctx.register_tool(name="team_chats", toolset="taffy", schema=said(teamchats.SCHEMA), handler=teamchats.team_chats)

    ctx.register_tool(name="share_page", toolset="taffy", schema=pages_tool.schema(), handler=pages_tool.share_page)


    for name in teammates.SCHEMAS:
        ctx.register_tool(name=name, toolset="taffy", schema=said(teammates.SCHEMAS[name]),
                          handler=teammates.handler(name))


    ctx.register_hook("pre_llm_call", _sign_in_key_later)
    ctx.register_hook("pre_llm_call", claudeaccount.pre_llm_call)
    ctx.register_hook("pre_llm_call", teammates.recent_chat_pre_llm_call)
    ctx.register_hook("pre_llm_call", teammates.right_now_pre_llm_call)
    ctx.register_hook("pre_llm_call", teammates.standing_pre_llm_call)
    ctx.register_hook("pre_llm_call", teammates.rules_pre_llm_call)
    ctx.register_hook("pre_llm_call", teammates.pre_llm_call)
    for _ in range(teammates.MORE_MEMORY_HOOKS):
        ctx.register_hook("pre_llm_call", teammates.memory_more_pre_llm_call)
    ctx.register_hook("pre_llm_call", teammates.problems_pre_llm_call)
    ctx.register_hook("pre_llm_call", reply_rules.public_web_pre_llm_call)
    ctx.register_hook("pre_llm_call", lead_team_pre_llm_call)
    ctx.register_hook("pre_llm_call", choices.scheduled_questions_pre_llm_call)
    ctx.register_hook("pre_llm_call", proposal.pre_llm_call)
    ctx.register_hook("pre_llm_call", quiet_run_pre_llm_call)
    ctx.register_hook("pre_llm_call", teammates.voice_pre_llm_call)
    ctx.register_hook("pre_llm_call", teammates.item_links_pre_llm_call)
    ctx.register_hook("pre_llm_call", teammates.done_pre_llm_call)
    ctx.register_hook("pre_llm_call", teammates.routes_pre_llm_call)
    ctx.register_hook("pre_llm_call", intent.pre_llm_call)
    ctx.register_hook("pre_llm_call", siblings.start_pre_llm_call)
    teammates.register_roster_hook(ctx)


    for name in team_space.SCHEMAS:
        if name == "team_start":
            ctx.register_tool(name=name, toolset="taffy", schema=said(team_space.SCHEMAS[name]), handler=team_space.team_start)
        else:
            ctx.register_tool(name=name, toolset="taffy", schema=said(team_space.SCHEMAS[name]),
                              handler=team_space.handler(name))



    from . import frozen
    ctx.register_hook("pre_tool_call", frozen.before_tool)
    ctx.register_hook("post_tool_call", frozen.after_tool)

    ctx.register_hook("pre_tool_call", jobs_report_here)
    ctx.register_hook("pre_tool_call", jobs_keep_zip_tools)

    ctx.register_hook("pre_tool_call", big_task_model)

    from . import private_zips as _private_zips
    ctx.register_hook("pre_tool_call", _private_zips.hand_off_guard)
    ctx.register_hook("pre_tool_call", handoff.before_tool)

    ctx.register_hook("pre_tool_call", gate.pre_tool_call)
    ctx.register_hook("post_tool_call", gate.post_tool_call)


    from . import boardstep
    ctx.register_hook("pre_tool_call", boardstep.before_tool)
    ctx.register_hook("pre_tool_call", shared_state.before_tool)

    ctx.register_hook("pre_tool_call", teammates.hand_off_check)
    ctx.register_hook("post_tool_call", shared_state.after_tool)



    ctx.register_hook("transform_tool_result", transform_result)


    ctx.register_hook("post_api_request", usage_log.post_api_request)
    ctx.register_hook("post_auxiliary_call", usage_log.post_auxiliary_call)
    ctx.register_hook("api_request_error", usage_log.api_request_error)
    ctx.register_hook("pre_llm_call", usage_log.errand_started)
    ctx.register_hook("post_llm_call", usage_log.errand_finished)
    ctx.register_hook("post_llm_call", zip_cursor.turn_ended)


    took = time.monotonic() - began
    import logging
    logging.getLogger(__name__).log(logging.WARNING if took > LOAD_WARN_SEC else logging.INFO,
                                    "taffy-tools loaded in %.1fs (Hermes drops a plugin at 10s)", took)



LEAD_HEARS = ("Tell Colin in one line who has it now. The hub will post a board update in this conversation when the "
              "task finishes, gets blocked or needs review, about a minute after the change. Tell Colin the result "
              "then, in your own words, without him asking. Never tell him to check back or to remind you. If the "
              "work isn't done by the task's \"Result by:\" time, or by the end of today when it has none, the hub "
              "wakes you to chase it.")
OTHERS_HEAR = ("The hub tells {lead} when the task finishes, gets blocked or needs review, and {lead} tells Colin. "
               "Never tell Colin to check back or to remind you.")


def big_task_model(tool_name: str = "", args=None, **kw):
    ""


    if tool_name != "kanban_create" or not isinstance(args, dict) or not args.get("assignee"):
        return None
    m = models.pick(home.get("models"), str(args["assignee"]), "big")
    if m is None:
        return None
    return {"action": "modify", "args": {"model": m.id, "provider": m.provider}}


def _is_lead() -> bool:
    ""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home()).parent.name != "profiles"
    except Exception:
        return True


@teammates.every_few_turns
def lead_team_pre_llm_call(platform: str = "", **kw):
    ""
    return {"context": home.personalize(reply_rules.LEAD_TEAM)} if platform == "api_server" and _is_lead() else None


_VAULT_HINT = re.compile(r"[^.\"]*hermes vault add[^.\"]*\.?")
_ZERO_VAULT_HINT = ("Ask Colin to save the login: use ask_to_sign_in in a chat, or kanban_block with a title that "
                    "starts \"Sign in to <site>\" in a board task.")


REFUSED_PAGE = ("This site refuses the home's browser. In a chat with Colin, when his request needs this page, open it "
                "on his Mac with mac_start and mac_open and read it with mac_ui. On a board task or a scheduled run, use "
                "another source and say the page refused you.")
_REFUSED_TITLE = re.compile(r"(403\W*)?(forbidden|access denied)", re.I)


def _refused(result) -> bool:
    ""

    try:
        data = json.loads(result) if isinstance(result, str) else None
    except ValueError:
        return False
    if not isinstance(data, dict) or not _REFUSED_TITLE.fullmatch(str(data.get("title") or "").strip()):
        return False
    return len(str(data.get("snapshot") or "")) < 300


def _with_note(result: str, key: str, note: str) -> str:
    ""
    try:
        data = json.loads(result)
    except ValueError:
        return result + "\n\n" + note
    if isinstance(data, dict):
        data[key] = note
        return json.dumps(data, ensure_ascii=False)
    return result + "\n\n" + note


def transform_result(tool_name: str = "", result=None, **kw):
    out = _result_notes(tool_name, result, **kw)
    text = result if out is None else out


    if tool_name == "ask_teammate" or not isinstance(text, str):
        return out
    try:
        landed = teammates.answers_landed(kw.get("session_id") or "")
    except Exception:
        gate.log.warning("could not check for a teammate's answer", exc_info=True)
        landed = None
    if not landed:
        return out
    return _with_note(text, "teammate_answer", landed)


def _result_notes(tool_name: str = "", result=None, **kw):
    out = _transformed(tool_name, result, **kw)
    try:
        text = result if out is None else out
        error = repeats.failed(text)
        heard = repeats.words(repeats.count(kw, tool_name, error, kw.get("args")), tool_name, home.first_name()) \
            if error else ""
        if heard:
            data = json.loads(text)
            data["what_to_do_now"] = heard
            out = json.dumps(data, ensure_ascii=False)
    except Exception:
        gate.log.warning("could not count a repeated failure", exc_info=True)

    try:
        masked = gate.reads_protected(tool_name, kw.get("args"), kw.get("task_id") or "")
    except Exception:
        masked = True
    if masked:
        text = result if out is None else out
        return consent.mask_secrets(text) if isinstance(text, str) else out
    return out


def _transformed(tool_name: str = "", result=None, **kw):
    try:
        from . import private_zips
        hidden = private_zips.board_result(tool_name, result, gate._bot(), (kw.get("args") or {}).get("board"))
    except Exception:
        gate.log.warning("could not keep a private Zip's tasks out of a board read", exc_info=True)
        hidden = json.dumps({"error": "The board could not be read just now. Try again."}) \
            if tool_name in ("kanban_list", "kanban_show", "kanban_attachments", "kanban_get", "kanban_search") else None
    if hidden is not None:
        return hidden
    out = board_news(tool_name=tool_name, result=result, **kw)
    if out is not None:
        return out
    if tool_name in ("browser_vault_list", "browser_vault_fill"):

        try:
            from . import login_notes
            site = gate._site(kw.get("args") or {}, kw.get("task_id") or "")
            noted = login_notes.with_notes(tool_name, result, site)
        except Exception:
            gate.log.warning("could not add a login's notes", exc_info=True)
            noted = None
        if noted is not None:
            return noted
    if tool_name in ("terminal", "execute_code") and isinstance(result, str) and \
            "BLOCKED: Command timed out without user response" in result:


        return _with_note(result, "after_the_card",
                          home.personalize("The card for this is still in Colin's app. ") + gate.card_reply())
    if tool_name.startswith("browser_") and isinstance(result, str) and "internal address (about:blank)" in result:


        return _with_note(result, "what_to_do", "The tab is blank (about:blank), so no page is open: the last click "
                          "or load did not finish. Open the page again with browser_navigate and its address.")
    if tool_name == "browser_navigate" and _refused(result):


        return _with_note(result, "what_to_do", home.personalize(REFUSED_PAGE))
    if tool_name == "browser_vault_fill" and isinstance(result, str) and '"origin_mismatch"' in result:

        return result.rstrip() + ("\n\nOpen the login page of the site this login was saved for with browser_navigate, "
                                  "check it loaded with browser_snapshot, then fill again.")
    if tool_name in ("web_search", "web_extract"):

        return reply_rules.mark_web_result(tool_name, result)
    if tool_name == "browser_navigate" and downloads.aborted(result):

        url = str((kw.get("args") or {}).get("url") or "")
        if url:
            return downloads.with_hint(result, url)
    if tool_name.startswith("browser_vault") and isinstance(result, str) and "hermes vault add" in result:

        return _VAULT_HINT.sub(home.personalize(_ZERO_VAULT_HINT).replace("\"", "'"), result, count=1).replace(
            "hermes vault add", "the app")
    import os
    key = gate._fill_key(kw.get("session_id") or "", os.environ.get("HERMES_KANBAN_TASK", ""))
    line = gate.told_line(key, tool_name)
    if not isinstance(result, str):
        return None
    try:
        data = json.loads(result)
    except ValueError:
        data = None
    notes = {}
    if tool_name == "reminders" and isinstance(data, dict):

        line = data.get("instead") or ("" if data.get("error") else line)
    if line:

        notes["tell_person"] = home.personalize("Mention this to Colin in one plain line of your reply: you did "
                                                "this: ") + f"{line}."
        if tool_name in tiers.JOB_TOOLS:

            notes["tell_person"] += " If you set up more than one routine this turn, name them all in that one line."
    args = kw.get("args") if isinstance(kw.get("args"), dict) else {}
    if tool_name in tiers.JOB_TOOLS and str(args.get("action") or "").lower() in ("create", "add", "update", "edit") \
            and isinstance(data, dict) and not data.get("error") and data.get("success") is not False:
        notes["where_it_posts"] = home.personalize(JOB_POSTS_HERE)
    if tool_name in ("kanban_show", "kanban_comment") and not os.environ.get("HERMES_KANBAN_TASK") \
            and gate._kanban("SELECT 1 FROM tasks WHERE id=? AND assignee=?", (str(args.get("task_id") or ""), gate._bot())):
        notes["your_own_task"] = home.personalize(OWN_TASK)
    if not notes:
        return None
    if isinstance(data, dict):
        data.update(notes)
        return json.dumps(data, ensure_ascii=False)
    return result + "\n\n" + " ".join(notes.values())





OWN_TASK = ("This task is assigned to you, so it is your own work running in the background. Speak of it as yours "
            "(\"I'm still pulling your audit\"), never as a separate worker (\"I told the task\"). Tell Colin you'll "
            "report back in this chat: its result comes here when it finishes.")




JOB_POSTS_HERE = ("When this job runs, what it says posts in this chat, and a reminder also notifies Colin's phone. "
                  "That is what deliver \"local\" does here, so leave deliver as it is.")





QUIET_RUN = ("This scheduled run's final reply posts in Colin's chat. Post only what he would want to read there: "
             "something that needs him, or news he hasn't heard. If this run found neither, reply with only [SILENT], "
             "even when the job asks for a log or a summary. What you checked, numbers that stayed the same and "
             "\"all clear\" are not news. A reminder, or a brief or digest he asked to get on a schedule, always posts.")


def quiet_run_pre_llm_call(platform: str = "", session_id: str = "", **kw):
    if platform != "cron" and not (session_id or "").startswith("cron:") and not gate._session_env("HERMES_CRON_SESSION"):
        return None
    return {"context": home.personalize(QUIET_RUN)}


def board_news(tool_name: str = "", result=None, **kw):
    ""

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
    args = kw.get("args") if isinstance(kw.get("args"), dict) else {}
    same_work = siblings.created_note(args, str(data.get("task_id") or ""))
    if same_work:
        data["same_work"] = same_work
    return json.dumps(data)
