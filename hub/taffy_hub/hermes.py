""

import asyncio
import hashlib
import json
import re
import time
from collections.abc import AsyncIterator

import logging

import httpx

from . import activity, chatter, checklist, config, events, finalpass, finishjudge, plainupdate, replycheck, screen, store, topics

log = logging.getLogger("taffy.hermes")
TURN_TIMEOUT = httpx.Timeout(connect=10, read=900, write=30, pool=10)


GATEWAY_WAIT_SEC = 45
GATEWAY_WAIT_STEP = 3


def _home_runs_gateway() -> bool:
    return (config.HERMES_HOME / "gateway_state.json").exists()






RESTART_WAIT_SEC = 150


def _gateway_state() -> dict:
    try:
        state = json.loads((config.HERMES_HOME / "gateway_state.json").read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def gateway_id() -> tuple | None:
    ""
    state = _gateway_state()
    return (state.get("pid"), state.get("start_time")) if state.get("pid") else None


def _alive(pid) -> bool:
    import os
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (OSError, TypeError, ValueError):
        return True
    return True


def restarted_since(before: tuple | None) -> bool:
    ""
    if before is None:
        return False
    state = _gateway_state()
    if not state:
        return False
    return ((state.get("pid"), state.get("start_time")) != before or state.get("gateway_state") != "running"
            or not _alive(state.get("pid")))


async def until_running(before: tuple) -> bool:
    ""
    deadline = time.monotonic() + RESTART_WAIT_SEC
    while True:
        state = _gateway_state()
        if (state.get("gateway_state") == "running" and (state.get("pid"), state.get("start_time")) != before
                and _alive(state.get("pid"))):
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(GATEWAY_WAIT_STEP)


def _headers(bot_id: str) -> dict:
    return {"Authorization": f"Bearer {config.api_key(bot_id)}", "Content-Type": "application/json"}


_session_locks: dict[str, "asyncio.Lock"] = {}






LANES = {"team": "answering teammates", "prep": "preparing drafts", "quiet": "looking things over",
         "work": "working on the team's plan", "make": "making new Zips", "side": "looking things up on calls"}


def session_key(bot_id: str, lane: str | None = None) -> str:
    return f"{bot_id}#{lane}" if lane else bot_id


async def ensure_session(bot_id: str, fresh: bool = False, team: bool = False, lane: str | None = None) -> str:
    ""


    import asyncio
    lane = "team" if team else lane
    if lane is not None and lane not in LANES:
        raise ValueError(f"unknown lane {lane}")
    key = session_key(bot_id, lane)
    lock = _session_locks.setdefault(key, asyncio.Lock())
    async with lock:
        sid = None if fresh else store.session_for(key)
        if sid:
            return sid
        bot = config.BOTS_BY_ID[bot_id]
        title = f"{bot.name} {LANES[lane]}" if lane else f"{bot.name} in the Zero app"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{config.api_prefix(bot_id)}/api/sessions", headers=_headers(bot_id),
                                  json={"title": title})
            if r.status_code == 400 and "invalid_title" in r.text:

                import time
                r = await client.post(f"{config.api_prefix(bot_id)}/api/sessions", headers=_headers(bot_id),
                                      json={"title": f"{title} ({time.strftime('%b %-d %-I.%M %p')})"})
            r.raise_for_status()
            data = r.json()
        sid = (data.get("session") or {}).get("id") or data.get("id")
        if not sid:
            raise RuntimeError("Hermes did not return a session id")
        store.set_session(key, sid)
        return sid


async def _get_messages(bot_id: str, sid: str, raw_limit: int) -> httpx.Response:



    async with httpx.AsyncClient(timeout=30) as client:
        return await client.get(f"{config.api_prefix(bot_id)}/api/sessions/{sid}/messages",
                                headers=_headers(bot_id),
                                params={"inline_images": "false", "order": "latest", "limit": str(raw_limit),
                                        "include_compacted": "true"})


async def messages(bot_id: str, limit: int = 200) -> list[dict]:
    sid = await ensure_session(bot_id)
    raw_limit = min(500, max(40, limit * 4))
    r = await _get_messages(bot_id, sid, raw_limit)
    if r.status_code == 404:
        sid = await ensure_session(bot_id, fresh=True)
        r = await _get_messages(bot_id, sid, raw_limit)
    r.raise_for_status()
    data = r.json()
    raw = (data.get("data") or data.get("messages")) if isinstance(data, dict) else data
    raw = raw or []
    remembered: set[str] = set()
    try:


        from . import lineage
        calls = {str(m.get("tool_call_id")) for m in raw if m.get("role") == "tool" and m.get("tool_call_id")}
        found = _carded_calls(raw, bot_id, sid)
        lineage.remember_card(bot_id, found)
        remembered = lineage.carded(bot_id, calls - found)
    except Exception:
        log.warning("could not read the remembered cards", exc_info=True)
    shown = _shown(raw, bot_id, sid, remembered=remembered, openings=True)
    try:
        from . import firstline
        shown = firstline.weave(bot_id, shown)
    except Exception:
        log.warning("could not add the first lines", exc_info=True)
    shown = shown[-limit:]
    try:
        return lineage.stable_ids(bot_id, shown)
    except Exception:
        log.warning("could not keep message ids stable", exc_info=True)
        return shown





_CARD_REQUEST = re.compile(r"\bapp \(request (\d+)\)")
_HERMES_TIMEOUT = "BLOCKED: Command timed out without user response"


def _text(content) -> str:
    if isinstance(content, list):
        content = "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
    return str(content or "")


def _raised_card(content, bot_id: str | None, session_id: str | None) -> bool:
    ""



    text = _text(content)
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        text = " ".join(str(data.get(k) or "") for k in ("error", "output", "message"))
    if text.lstrip().startswith(_HERMES_TIMEOUT):
        return True
    m = _CARD_REQUEST.search(text)
    if not m:
        return False
    if bot_id is None:
        return True
    try:
        from . import consent
        card = consent.get_request(int(m.group(1)))
    except Exception:
        return False
    if not card or card.get("bot_id") != bot_id:
        return False
    if not card.get("session_id") or not session_id or card.get("session_id") == session_id:
        return True

    from . import lineage
    return lineage.same(bot_id, card.get("session_id"), session_id)


def _carded_calls(raw: list[dict], bot_id: str | None, session_id: str | None) -> set[str]:
    ""
    return {str(m.get("tool_call_id")) for m in raw
            if m.get("role") == "tool" and m.get("tool_call_id")
            and _raised_card(m.get("content"), bot_id, session_id)}


def _offer_short(bot_id: str, full: str, short: str, words: str | None = None) -> None:
    ""

    try:
        from . import history, shortreply
        shortreply.offer(bot_id, full, short, None if words is None else history.strip(words))
    except Exception:
        log.warning("could not keep %s's own short version", bot_id, exc_info=True)


def _shown(raw: list[dict], bot_id: str | None = None, session_id: str | None = None,
           remembered: set[str] | frozenset = frozenset(), openings: bool = False) -> list[dict]:
    ""



    carded = _carded_calls(raw, bot_id, session_id) | set(remembered)
    out = []
    asked = None
    tools_started = False
    turn_raw = []
    turn_id = None
    request, tools = "", set()
    stumbled = False
    targets = {}
    whole = replycheck.enforcing()
    first_of = {}
    for i, m in enumerate(raw):
        role = m.get("role")
        if m.get("display_kind") in ("hidden", "failed_turn"):
            continue
        content = _text(m.get("content"))
        if role == "user":
            if replycheck.starts_turn(m):
                turn_raw = []
                from . import turnsummary
                turn_id, content = turnsummary.peel(content)
                request, tools, stumbled = content, set(), False
            asked = content
            tools_started = False
        before_tools = not tools_started
        calls = m.get("tool_calls") if role == "assistant" else None
        if calls:
            tools_started = True
            tools.update((c.get("function") or c).get("name") for c in calls if isinstance(c, dict))
            stumbled = stumbled or replycheck.stumbled(content)
            targets.update((str(c.get("id")), replycheck.target((c.get("function") or c).get("arguments")))
                           for c in calls if isinstance(c, dict))
        stumbled = stumbled or (role == "tool" and bool(targets.get(str(m.get("tool_call_id"))))
                                and replycheck.failed(content))
        if role == "assistant" and not calls and finalpass.held(bot_id, content):
            turn_raw.append(m)
            continue
        if role == "assistant" and not calls and (finalpass.replaced(raw, i) or finalpass.rejected(raw, i)
                                                  or finalpass.quieted(raw, i)):
            turn_raw.append(m)
            if finalpass.replaced(raw, i):
                first_of[finalpass.pass_reply(raw, i)] = m
            continue
        read = not calls and (replycheck.from_teammate(request, tools) or stumbled)
        if role == "assistant" and (replycheck.live_held(raw, i, turn_raw) or replycheck.needed(content, read) and (
                replycheck.reason(content, turn_raw, bot_id, m.get("timestamp"))
                or not calls and whole and replycheck.whole_reply(content, raw[:i]))):
            turn_raw.append(m)
            if i in first_of:

                m = first_of[i]
                content = _text(m.get("content"))
            elif calls or not (content := _trimmed_retry(raw, i, bot_id, read, whole)):
                continue
        else:
            turn_raw.append(m)
        parts = []
        if role == "assistant":
            content, own_short = chatter.split_short(content)
            own_short = chatter.without_buzz_mark(own_short) if own_short else own_short
            parts = [p for p in (chatter.without_hermes_notes(p) for p in topics.split(content)) if p.strip()]
            content = chatter.without_hermes_notes(content)
            if own_short is not None and bot_id:
                _offer_short(bot_id, content, own_short, asked)
        if role not in ("user", "assistant") or not content.strip():
            continue
        item = {"id": m.get("id"), "role": role, "text": content, "ts": m.get("timestamp") or m.get("created_at")}
        if len(parts) > 1:
            item["topics"] = parts
        if turn_id:
            item["turn_id"] = turn_id
        if openings and role == "assistant":
            item["_firstline"] = before_tools
        if carded and isinstance(calls, list) and any(isinstance(c, dict) and str(c.get("id")) in carded for c in calls):
            item["before_card"] = True
        if calls:
            item["step"] = True
        out.append(item)



    last = None
    for item in out:
        ts = item.get("ts")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            if last is not None and ts <= last:
                item["ts"] = ts = last + 0.001
            last = ts
    return out


def _trimmed_retry(raw: list[dict], i: int, bot_id: str | None, read: bool, whole: bool) -> str | None:
    ""

    def note(m: dict) -> bool:
        return m.get("role") == "user" and _text(m.get("content")).startswith(replycheck.NOTE)
    upto = raw[:i + 1]
    if i + 1 < len(raw) and note(raw[i + 1]) or not any(note(m) for m in replycheck.current_turn(upto)):
        return None
    trimmed = replycheck.without_claim(_text(raw[i].get("content")), upto, bot_id)
    if not trimmed or replycheck.needed(trimmed, read) and (
            replycheck.reason(trimmed, upto, bot_id, raw[i].get("timestamp"))
            or replycheck.left_out(trimmed, upto) or replycheck.unsettled(trimmed, upto)
            or whole and replycheck.whole_reply(trimmed, raw[:i])):
        return None
    return trimmed


CLAUDE_PROVIDER = "claude-subscription-directsdk-experimental"


def primary_provider(bot_id: str) -> str:
    ""
    try:
        path = config.HERMES_HOME / ("config.yaml" if bot_id == "default" else f"profiles/{bot_id}/config.yaml")
        m = re.search(r"^model:\s*\n(?:[ \t]+.*\n)*?[ \t]+provider:\s*([\w.-]+)", path.read_text(), re.M)
        return m.group(1) if m else CLAUDE_PROVIDER
    except OSError:
        return CLAUDE_PROVIDER





BACKUP_KEEP_DAYS = 30


def _backup_key(bot_id: str, text: str) -> str:
    return hashlib.sha256(f"{bot_id}\n{' '.join((text or '').split())}".encode()).hexdigest()


@store.schema
def _backup_tables(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS backup_replies (key TEXT PRIMARY KEY, bot_id TEXT, created_at INTEGER)")


def note_backup(bot_id: str, *texts: str) -> None:
    with store._lock:
        conn = store.db()
        now = int(time.time())
        for t in dict.fromkeys(t for t in texts if t):
            conn.execute("INSERT OR REPLACE INTO backup_replies (key, bot_id, created_at) VALUES (?, ?, ?)",
                         (_backup_key(bot_id, t), bot_id, now))
        conn.execute("DELETE FROM backup_replies WHERE created_at < ?", (now - BACKUP_KEEP_DAYS * 86400,))
        conn.commit()


def mark_backup(bot_id: str, msgs: list[dict]) -> list[dict]:
    ""
    try:
        conn = store.db()
        out = []
        for m in msgs:
            if m.get("role") == "assistant" and any(
                    conn.execute("SELECT 1 FROM backup_replies WHERE key = ?", (_backup_key(bot_id, t),)).fetchone()
                    for t in (m.get("text"), m.get("full"), m.get("display")) if t):
                m = {**m, "backup": True}
            out.append(m)
        return out
    except Exception:
        log.warning("could not read the backup replies", exc_info=True)
        return msgs


async def stream_turn(bot_id: str, text: str, team: bool = False, lane: str | None = None) -> AsyncIterator[dict]:
    ""






    lane = "team" if team else lane
    chat = lane is None
    lead = text
    if chat:
        last_tools.pop(bot_id, None)
        last_limited.pop(bot_id, None)
        last_unsure.pop(bot_id, None)
        activity.begin(bot_id)
        from . import chatquestions, mailcard
        text = chatquestions.with_questions(bot_id, mailcard.with_drafts(bot_id, text))
    try:
        from . import team as team_messages, usage
        with team_messages.receiving_turn(bot_id, session_key(bot_id, lane), lead), \
                usage.errand(lane or "chat", bot_id) as worker:
            worker["outcome"] = "no_answer"
            async for ev in _stream_turn(bot_id, text, lane):
                if ev.get("type") == "run":
                    worker["task_id"] = ev.get("id")
                elif ev.get("type") in ("error", "yielded"):
                    worker["outcome"] = "failed" if ev["type"] == "error" else "yielded"
                elif ev.get("type") == "reply" and worker["outcome"] == "no_answer":
                    worker["outcome"] = "answered"
                if chat:
                    activity.note(bot_id, ev)
                if ev.get("type") in ("tool_done", "thinking"):
                    continue
                yield ev
    finally:
        if chat:
            finalpass.release(bot_id)
            activity.end(bot_id)
            screen.end_turn(bot_id)
            from . import usage
            usage.turn_ended(bot_id)


async def _stream_turn(bot_id: str, text: str, lane: str | None, _repairs: int = 0,
                       _wait_until: float | None = None, _request: str | None = None) -> AsyncIterator[dict]:
    event = None
    final_text = None
    pending_steer = ""
    backup = False
    on_claude = None
    run_id = None

    request = _request or f"{time.time():.6f}"
    memory_open = told_memory = False
    repair_notes: set[str] = set()
    tools: set[str] = set()
    stumbled = False
    request_started = False
    response_received = False
    last_error.pop(bot_id, None)
    last_raw.pop(bot_id, None)
    last_yield.pop(bot_id, None)
    try:
        sid = await (ensure_session(bot_id, team=True) if lane == "team" else
                     ensure_session(bot_id, lane=lane) if lane else ensure_session(bot_id))
        from . import team as team_messages
        team_messages.bind_receiving(bot_id, session_key(bot_id, lane), sid)
        url = f"{config.api_prefix(bot_id)}/api/sessions/{sid}/chat/stream"
        async with httpx.AsyncClient(timeout=TURN_TIMEOUT) as client:
            from . import uploads
            body = await asyncio.to_thread(uploads.content, text, bot_id)
            from . import usage
            request_started = True
            async with client.stream("POST", url, headers=_headers(bot_id),
                                     json={"input": body, "usage_context": usage.context()}) as r:
                response_received = True
                if r.status_code == 404:
                    store.set_session(session_key(bot_id, lane), "")
                if r.status_code >= 400:
                    last_error[bot_id] = "The bot could not start that turn. Try again in a moment."
                    last_raw[bot_id] = f"HTTP {r.status_code} starting the turn"
                    start_failed[bot_id] = True
                    yield {"type": "error", "text": last_error[bot_id]}
                    yield {"type": "done"}
                    return
                async for line in r.aiter_lines():
                    if line.startswith(":") or not line.strip():
                        continue
                    if line.startswith("event:"):
                        event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:])
                    except ValueError:
                        continue
                    if not run_id and isinstance(data, dict) and data.get("run_id"):
                        run_id = data["run_id"]
                        yield {"type": "run", "id": run_id}
                    if isinstance(data, dict) and data.get("pending_steer"):


                        pending_steer = str(data["pending_steer"])
                    stumbled = stumbled or (event == "tool.failed" and isinstance(data, dict)
                                            and bool(replycheck.target(data.get("args"))))
                    if event in ("tool.completed", "tool.failed") and lane is None and isinstance(data, dict) \
                            and screen.uses_computer(data.get("tool_name") or data.get("tool")):
                        screen.note_agent_done(bot_id)
                    if event in ("tool.completed", "tool.failed"):

                        yield {"type": "tool_done"}
                    if event == "approval.request" and isinstance(data, dict):

                        from . import approvals
                        waiting = await approvals.handle(bot_id, run_id or "", data, session_id=sid,
                                                         team=lane is not None)
                        if waiting:
                            yield {"type": "status", "text": waiting, "waiting": True}
                    elif event == "tool.started":
                        tool = data.get("tool_name") or data.get("tool")
                        tools.add(tool)
                        if lane is None:
                            last_tools[bot_id] = True
                        step = activity.label(tool, data.get("args"), data.get("preview"))
                        yield {"type": "tool_started", "did": activity.did(tool, data.get("args"))}
                        if step:
                            yield {"type": "status", "text": step}
                        if tool == "todo_list" and lane is None:
                            if (steps := checklist.write(sid, data.get("args"), turn=request)) is not None:
                                yield {"type": "checklist", **steps}
                        if lane is None:
                            if screen.uses_computer(tool):
                                screen.note_agent_use(bot_id, tool)
                            from . import board
                            board.note_chat_look(bot_id, tool, data.get("args"))
                            memory_open = tool == "memory" and events.writes_memory(data.get("args"))
                    elif event in ("tool.completed", "tool.failed") and memory_open \
                            and (data.get("tool_name") or data.get("tool")) == "memory":
                        memory_open = False
                        if event == "tool.completed" and not data.get("error") and not told_memory:
                            told_memory = True
                            events.memory_updated(bot_id)
                    elif event in ("assistant.commentary",):
                        if data.get("text"):
                            stumbled = stumbled or replycheck.stumbled(data["text"])
                            why = await _reply_problem(bot_id, sid, data["text"]) if lane is None else None
                            if why:
                                note = replycheck.correction(why)
                                if run_id and note not in repair_notes and len(repair_notes) < 2:
                                    repair_notes.add(note)
                                    await steer_outcome(bot_id, run_id, note)
                                yield {"type": "delta_reset"}
                                continue

                            shown = chatter.without_hermes_notes(chatter.split_short(data["text"])[0])
                            if shown.strip():
                                yield {"type": "commentary", "text": shown}
                    elif event == "reasoning.delta":



                        if lane is None and isinstance(data, dict) and data.get("delta"):
                            yield {"type": "thinking", "text": str(data["delta"])}
                    elif event == "assistant.delta":
                        if data.get("delta"):
                            yield {"type": "delta", "text": data["delta"]}
                    elif event == "assistant.completed":
                        final_text = data.get("content") or final_text



                        served = (data.get("runtime") or {}).get("provider")
                        backup = bool(served) and served != primary_provider(bot_id)
                        on_claude = served == CLAUDE_PROVIDER if served else None
                    elif event == "run.cancelled" and run_id and run_id in yielded_runs:


                        yielded_runs.discard(run_id)
                        last_yield[bot_id] = True
                        yield {"type": "yielded"}
                    elif event in ("run.failed", "run.cancelled"):
                        raw = str(data.get("error") or "")
                        last_raw[bot_id] = raw or ("cancelled" if event == "run.cancelled" else "failed, no error given")
                        keyed = event == "run.failed" and _bills_by_use(bot_id)
                        if event == "run.failed" and not keyed:
                            from . import health
                            health.turn_finished(raw)
                            limited = any(w in raw.lower() for w in health.LIMIT_WORDS)
                        else:


                            limited = False
                        last_error[bot_id] = ("That reply stopped partway." if event == "run.cancelled"
                                              else keyed_line(bot_id, raw) if keyed
                                              else (limit_line(raw) if limited else
                                                    "That reply ran into a problem. Try again in a moment."))
                        if limited and lane is None:
                            last_limited[bot_id] = True
                        yield {"type": "error", "text": last_error[bot_id], **({"limit": True} if limited else {})}
    except Exception as exc:
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) and not response_received and _home_runs_gateway():
            wait_until = _wait_until or time.monotonic() + GATEWAY_WAIT_SEC
            if time.monotonic() < wait_until:
                await asyncio.sleep(GATEWAY_WAIT_STEP)
                async for ev in _stream_turn(bot_id, text, lane, _repairs, wait_until, _request=request):
                    yield ev
                return


        if not request_started or (not response_received and isinstance(
                exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))):
            start_failed[bot_id] = True


        name = config.BOTS_BY_ID[bot_id].name if bot_id in config.BOTS_BY_ID else "Zero"
        last_error[bot_id] = f"{name} lost its connection partway through, so that reply didn't finish. Try again."

        engine = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) and not response_received
        last_raw[bot_id] = (ENGINE_DOWN if engine else "") + f"{type(exc).__name__}: {exc}"[:300]
        unsure = not start_failed.get(bot_id)
        if unsure and lane is None:
            last_unsure[bot_id] = True
        yield {"type": "error", "text": last_error[bot_id], **({"unsure": True} if unsure else {})}
    if final_text and lane is None and not chatter.interrupted(final_text):
        finalpass.hold(bot_id, final_text)
    if final_text and not chatter.interrupted(final_text):
        teammate = replycheck.from_teammate(text, tools) or stumbled
        why = await _reply_problem(bot_id, sid, final_text, teammate, final=True) if lane is None else None
        if why:
            log.warning("rejected %s's reply: %s", bot_id, why)
            yield {"type": "delta_reset"}


            trimmed = await _without_claim(bot_id, sid, final_text, teammate) if _repairs >= 1 else None
            if trimmed:
                log.warning("published %s's reply without its held send claim", bot_id)
                final_text = trimmed
            elif _repairs >= 1 and replycheck.shown_after_retry(why):
                log.warning("published %s's retried reply over a doubtful time in its record", bot_id)
            elif _repairs < 1:
                async for ev in _stream_turn(bot_id, replycheck.correction(why), lane, _repairs=_repairs + 1,
                                             _request=request):
                    yield ev
                return
            else:
                last_error[bot_id] = "I couldn't verify that reply. Please try again."
                last_raw[bot_id] = f"reply check: {why}"[:300]
                yield {"type": "error", "text": last_error[bot_id]}
                yield {"type": "done"}
                return






        checking = lane is None and not _repairs and not pending_steer
        judging = finishjudge.mode() if checking else "off"

        bare = None if tools else [{"role": "user", "content": text}, {"role": "assistant", "content": final_text}]
        from . import statuscheck
        held = await statuscheck.note(bot_id, final_text, lambda: _turn_messages(bot_id, sid)) if checking else None
        if held:
            note = held
        elif judging == "on":
            note = None
            if finishjudge.plain(bool(tools), text, final_text):
                finishjudge.record(bot_id, finishjudge.Verdict("skipped", skipped="conversation"), "on")
            elif (raw := bare or await _turn_messages(bot_id, sid)) is not None:
                verdict = await finishjudge.judge(raw, final_text, zips=(bot_id,))
                finishjudge.record(bot_id, verdict, "on")
                note = finishjudge.note(verdict)
        else:
            raw = None
            if checking and (tools or finalpass.promises(final_text) or finalpass.asks_first(final_text)):
                raw = await _turn_messages(bot_id, sid)
                note = await _final_pass(bot_id, sid, final_text, raw)
            else:
                note = None
            if judging == "shadow":
                finishjudge.shadow_later(bot_id, final_text, note, raw or bare, lambda: _turn_messages(bot_id, sid),
                                         bool(tools), text)

        plain = await plainupdate.check(text, final_text, zips=(bot_id,)) if checking and not note else None
        if plain == plainupdate.QUIET:
            log.info("kept %s's update out of the chat: nothing in it changes anything for its users", bot_id)
            yield {"type": "delta_reset"}
            async for ev in _stream_turn(bot_id, plainupdate.quiet_request(), lane, _repairs=1, _request=request):
                yield ev
            return
        note = note or plain
        if note:
            log.info("final pass for %s: %s", bot_id, note)
            yield {"type": "delta_reset"}
            replied = False
            async for ev in _stream_turn(bot_id, replycheck.correction(finalpass.marked(note)), lane, _repairs=1,
                                         _request=request):

                if ev.get("type") == "reply" and chatter.empty(ev.get("text")):
                    continue

                if ev.get("type") == "reply" and await _figures_moved(bot_id, sid, final_text, ev.get("text") or ""):
                    continue
                replied = replied or ev.get("type") == "reply"
                if replied or ev.get("type") not in ("error", "done"):
                    yield ev
            if replied:
                return
            last_error.pop(bot_id, None)
            yield {"type": "delta_reset"}
    written = final_text
    final_text, own_short = chatter.split_short(final_text)
    own_short = chatter.without_buzz_mark(own_short) if own_short else own_short
    parts = [p for p in (chatter.without_hermes_notes(p) for p in topics.split(final_text)) if p.strip()]
    final_text = chatter.without_hermes_notes(final_text)
    buzz = chatter.buzz_mark(written)
    todo = chatter.todo_marks(written)
    if buzz and not final_text.strip():
        final_text = chatter.SILENT
    if final_text and own_short is not None:
        _offer_short(bot_id, final_text, own_short, text)
    for note in repair_notes:
        pending_steer = pending_steer.replace(note, "").strip()
    if pending_steer:
        yield {"type": "pending_steer", "text": pending_steer}
    if lane is None:
        finalpass.release(bot_id)
    if final_text:
        from . import health
        health.turn_finished(None, backup=backup, claude=on_claude)
        if lane is None and not chatter.empty(final_text):
            from . import remindertime
            remindertime.check_later(bot_id, final_text)
        if backup:
            try:
                note_backup(bot_id, written, final_text)
            except Exception:
                log.warning("could not note a backup reply", exc_info=True)
        yield {"type": "reply", "text": final_text, **({"backup": True} if backup else {}),
               **({"topics": parts} if len(parts) > 1 else {}), **({"buzz": buzz} if buzz else {}),
               **({"todo": todo} if todo else {})}
    yield {"type": "done"}


async def _without_claim(bot_id: str, sid: str, text: str, teammate: bool) -> str | None:
    ""

    try:
        r = await _get_messages(bot_id, sid, 500)
        r.raise_for_status()
        data = r.json()
        raw = ((data.get("data") or data.get("messages")) if isinstance(data, dict) else data) or []
        trimmed = replycheck.without_claim(text, raw, bot_id)
        if trimmed and not await _reply_problem(bot_id, sid, trimmed, teammate, final=True):
            return trimmed
    except Exception:
        log.warning("could not trim %s's held reply", bot_id, exc_info=True)
    return None


async def _reply_problem(bot_id: str, sid: str, text: str, teammate: bool = False, final: bool = False) -> str | None:
    ""

    if not replycheck.needed(text, teammate):
        return None
    r = await _get_messages(bot_id, sid, 500)
    r.raise_for_status()
    data = r.json()
    raw = ((data.get("data") or data.get("messages")) if isinstance(data, dict) else data) or []
    why = replycheck.reason(text, raw, bot_id)
    if why or not final:
        return why
    why = replycheck.left_out(text, raw) or replycheck.unsettled(text, raw)
    if why:
        return why
    why = replycheck.whole_reply(text, raw)
    if why and not replycheck.enforcing():
        log.warning("reply check shadow: would hold %s's reply %r: %s", bot_id, text[:300], why)
        return None
    return why


async def _turn_messages(bot_id: str, sid: str) -> list[dict] | None:
    ""
    try:
        r = await _get_messages(bot_id, sid, 500)
        r.raise_for_status()
        data = r.json()
        return ((data.get("data") or data.get("messages")) if isinstance(data, dict) else data) or []
    except Exception:
        log.warning("could not read %s's turn", bot_id, exc_info=True)
        return None


async def _final_pass(bot_id: str, sid: str, text: str, raw: list[dict] | None) -> str | None:
    ""


    from . import home
    if raw is None or home.get("reply_check.final_pass") == "off":
        return None
    try:
        given = await asyncio.to_thread(finalpass.given, bot_id, sid)
        return finalpass.note(text, raw, bot_id, given)
    except Exception:
        log.warning("the final pass could not read %s's turn", bot_id, exc_info=True)
        return None


async def _figures_moved(bot_id: str, sid: str, first: str, second: str) -> bool:
    ""
    try:
        r = await _get_messages(bot_id, sid, 500)
        r.raise_for_status()
        data = r.json()
        raw = ((data.get("data") or data.get("messages")) if isinstance(data, dict) else data) or []
        moved = finalpass.moved_figures(first, second, replycheck.current_turn(raw))
    except Exception:
        log.warning("the final pass could not check %s's figures", bot_id, exc_info=True)
        return True
    if moved:
        log.warning("final pass for %s dropped: figures moved with no source: %s", bot_id, moved)
    return bool(moved)


async def steer_outcome(bot_id: str, run_id: str, text: str) -> bool | None:
    ""


    from . import mailcard
    text = mailcard.with_drafts(bot_id, text)
    try:

        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=3)) as client:
            r = await client.post(f"{config.api_prefix(bot_id)}/v1/runs/{run_id}/steer",
                                  headers=_headers(bot_id), json={"input": text})
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return False
    except Exception:
        return None
    if r.status_code == 200:
        try:
            return bool(r.json().get("accepted"))
        except ValueError:
            return None
    return False if r.status_code in (400, 404, 409) else None


async def steer(bot_id: str, run_id: str, text: str) -> bool:
    ""
    return await steer_outcome(bot_id, run_id, text) is True




last_error: dict[str, str] = {}


last_raw: dict[str, str] = {}
ENGINE_DOWN = "engine down: "


last_tools: dict[str, bool] = {}
last_limited: dict[str, bool] = {}
last_unsure: dict[str, bool] = {}


start_failed: dict[str, bool] = {}


yielded_runs: set[str] = set()
last_yield: dict[str, bool] = {}


last_buzz: dict[str, tuple[str, str] | None] = {}


last_todo: dict[str, list[str]] = {}


async def stop(bot_id: str, run_id: str) -> bool:
    ""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{config.api_prefix(bot_id)}/v1/runs/{run_id}/stop", headers=_headers(bot_id), json={})
            return r.status_code == 200
    except Exception:
        return False


async def run_turn(bot_id: str, text: str, team: bool = False, lane: str | None = None) -> str | None:
    ""



    from . import chatter
    reply = buzz = None
    todo = []
    async for ev in stream_turn(bot_id, text, team=team, lane=lane):
        if ev["type"] == "reply":
            reply = None if chatter.interrupted(ev["text"]) else ev["text"]
            buzz = ev.get("buzz") if reply else None
            todo = ev.get("todo") or [] if reply else []
    if not team and lane is None:
        last_buzz[bot_id] = buzz
        last_todo[bot_id] = todo
    return reply





_RETRY_IN = re.compile(r"(?:in|after)\s+(\d{1,5})\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b", re.I)
_RETRY_AFTER = re.compile(r"retry[-_ ]after\W{0,3}(\d{1,6})\b", re.I)
_RESETS_AT = re.compile(r"reset\w*\W{0,4}(?:at\W{0,3})?(\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?)", re.I)
_RESETS_EPOCH = re.compile(r"reset\w*\W{0,4}(1[6-9]\d{8})\b", re.I)


def retry_at(raw: str, now: float | None = None) -> float | str | None:
    ""
    import time as _time
    now = _time.time() if now is None else now
    t = str(raw or "")
    m = _RESETS_EPOCH.search(t)
    if m:
        return float(m.group(1))
    m = _RETRY_IN.search(t)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return now + n * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)
    m = _RETRY_AFTER.search(t)
    if m:
        return now + int(m.group(1))
    m = _RESETS_AT.search(t)
    if m:
        return " ".join(m.group(1).lower().replace(".", "").replace("am", " a.m.").replace("pm", " p.m.").split())
    return None


def _bills_by_use(bot_id: str) -> bool:
    from . import modelsetup
    try:
        return modelsetup.bills_by_use(bot_id)
    except Exception:
        return False



_KEY_REFUSED = re.compile(r"\b40[13]\b|unauthori[sz]ed|invalid[\s_-]*(?:api[\s_-]*)?key|incorrect api key|no api key|"
                          r"api[\s_-]*key\b.{0,40}\b(?:missing|not set|not found|invalid|revoked|expired)|"
                          r"MODEL_API_KEY|authentication|permission denied", re.I)
_BUSY = re.compile(r"\b429\b|rate[\s_-]*limit|too many requests|\b50[0234]\b|overloaded|unavailable", re.I)
KEY_LINE = "I can't think right now because my model's key isn't working. I've let the developer know."
BUSY_LINE = "My model is busy right now, so that reply didn't run. Try again in a minute."
_key_reported: set[str] = set()


def key_refused(raw: str) -> bool:
    return bool(_KEY_REFUSED.search(raw or ""))


def keyed_line(bot_id: str, raw: str) -> str:
    ""

    if key_refused(raw):
        if bot_id not in _key_reported:
            _key_reported.add(bot_id)
            try:
                from . import problems
                problems.report("default", {
                    "kind": "break", "concerns_zip": bot_id, "source": "code", "where": "hermes.keyed_line",
                    "what_happened": f"{bot_id}'s model provider refused the home's key, or the Zip has none, so its "
                                     "turns can't run.",
                    "evidence": " ".join(raw.split())[:200],
                    "expected": "A working key in the hub's model-keys folder, copied into the Zip's .env by "
                                "modelsetup.apply (docs/plans/2026-11-20-ember-zip.md)."})
            except Exception:
                log.warning("could not report %s's refused model key", bot_id, exc_info=True)
        return KEY_LINE
    if _BUSY.search(raw or ""):
        return BUSY_LINE
    return "That reply ran into a problem. Try again in a moment."


def limit_line(raw: str, now: float | None = None) -> str:
    ""
    import time as _time
    from datetime import datetime
    from . import home
    now = _time.time() if now is None else now
    when = retry_at(raw, now)
    if isinstance(when, float):
        d = datetime.fromtimestamp(when, home.TZ)
        when = d.strftime("%-I:%M %p").replace(":00 ", " ").replace("AM", "a.m.").replace("PM", "p.m.")
        if d.date() != datetime.fromtimestamp(now, home.TZ).date():
            when += " " + d.strftime("%A")
    head = "Your Zips have hit their plan's usage limit, so this reply didn't run."
    if when:
        return f"{head} Try again after {when}" + ("" if when.endswith(".") else ".")
    return f"{head} Try again once the limit resets; you'll get a note when they are back."


TOOL_LABELS = {
    "kanban_create": "Handing work to the team",
    "kanban_show": "Checking the board",
    "kanban_list": "Checking the board",
    "kanban_complete": "Wrapping up the task",
    "kanban_comment": "Leaving a note on the task",
    "connectors": "Using your connected accounts",
    "tally": "Reading transactions",
    "terminal": "Working on its computer",
    "read_file": "Reading a file",
    "write_file": "Writing a file",
    "web_search": "Searching the web",
    "web_extract": "Reading a web page",
    "browser_exec": "Using its browser",
    "computer_use": "Using its computer",
    "run_steps": "Clicking through steps",
    "memory": "Updating memory",
    "skill_view": "Reading a skill",
}


def _tool_label(name: str | None, preview: str | None) -> str:
    ""
    return activity.label(name, None, preview) or "Working"
