"""On-Device Voice calls: the phone listens and speaks, the hub writes the words.

The phone hears the person with Apple's on-device speech recognition and speaks with Kyutai Pocket TTS, so a
call costs nothing per minute. The words come from here: Haiku on the home's own Claude plan, streamed, with the
same briefing, the same speaking rules and the same hand-off to the Zip (voice.ask_bot and voice.update_job) as a
ChatGPT voice call. Haiku is the fast talker that ChatGPT's realtime voice is on the other path; the Zip (Opus
through Hermes) still does the real work, and its answers come back as news that Haiku says in its own words.

How a turn runs (POST /api/voice/{id}/turn, a stream of events):
- The phone sends the person's words as soon as it thinks the turn is over, sometimes before it is sure
  (`speculative`). A speculative turn is written but not acted on: its hand-off waits until /confirm, and a
  newer request with the same turn number replaces it. Only confirmed turns enter the conversation.
- Claude runs as `claude -p` in the exact shape the plan broker allows (alien/broker/zero_broker.py DIRECTSDK),
  so a home on a shared plan works the same as a home on its own plan. The history is replayed as stream-json
  frames that Claude acknowledges without asking the model, and only the last message asks it.
- To save the half second Claude takes to start, the next turn's process is started ahead of time with the
  history already replayed, and waits for the last message (`Run`, `_prewarm`).
- The model can call ask_bot or update_job (an inert tool list, inert_mcp.py, names them; --max-turns 1 ends
  the run at the call). The hub runs the tool once the turn is confirmed, exactly as for a ChatGPT call, and the
  result is queued with voice.say. voice.speak sends it to the phone as a `news` event; the phone asks for it at
  its next pause as a turn of its own, and Haiku says it.
- /said tells the hub what the phone actually spoke (cut short by an interruption or not) and when the first
  sound came, for the history, the transcript and the saved call.

Saved call events (calllog.py), in addition to the shared ones: start (engine "on-device"), line, reply (per
turn: speculative, ms to the first text, warm or cold start, hand-off), tool, first_reply (ms from the end of the
person's speech to the phone's first sound), interrupted, error, end, summary.
"""

import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import briefing, calllog, config, voice, who

log = logging.getLogger("taffy.ondevice")

ENGINE = "on-device"
MODEL = briefing.MODEL            # claude-haiku-5-5: thinking off, low effort, streamed
EFFORT = "low"
MAX_TOKENS = 600
ROUTE = "speech recognition and voice on the phone; words streamed from Haiku on the home's Claude plan"
VOICES = ["george", "alba"]
DEFAULT_VOICE = "george"
VOICE_SETTING = "on_device_voice"  # the home's On-Device Voice, the same on every device (voice.pick_voice)
PREFIX = "mcp__hermes__"          # how Claude Code names a tool from the inert list's server, "hermes"
TOOLS = ("ask_bot", "update_job", "work_status")
INERT = Path(__file__).with_name("inert_mcp.py")
WARM_MAX_AGE_SEC = 240            # the broker ends a run after 900 s; a waiting process is replaced well before
FIRST_EVENT_TIMEOUT_SEC = 25
TURN_TIMEOUT_SEC = 90
TEXT_LIMIT = 4000
NEWS_MARK = "[News from your background work]"
CONNECTED = "[The call connected.]"
SORRY = "Sorry, I couldn't get an answer just then. Can you say that again?"
SORRY_SIGNED_OUT = "Sorry, I can't answer right now. Claude needs to be signed in again on your computer."
SORRY_SHARED = "Sorry, I can't answer right now. The shared Claude plan needs to be signed in again."


PROMPT_TEMPLATE = """{{briefing}}

--- How to handle this call ---
You are on a live voice call with {first}. Talk naturally, warmly and briefly, the way a friend who knows {first} \
would, using what is above. Keep most replies to one or two short sentences, the key point first.

You know {first}'s name and everything above from your first words. Never say {first} has not told you \
something that is above. If you do not have something, say "I don't have that" and hand it off to check.

You have a background agent: yourself, with full access to {reach}. Hand off to it with the ask_bot tool whenever \
the answer needs anything that is not above, any lookup, any of {possessive} accounts, the web, a computer, or \
doing anything. Write one short natural line first, such as "Let me check that.", then call ask_bot with \
{possessive} request in full, with every detail from the conversation it needs. Never say you will check, look or \
find out without calling a tool in that same reply: the answer always comes back to you on this call. When a job is \
already running in this call and {first} adds to it, changes it or answers its question, call update_job instead. \
When {first} asks what you or the team are working on, or how things stand, call work_status. Never make up facts, \
dates, deadlines, times, prices or names. Never say you cannot do something before handing it off; the background \
agent has the tools.

If the words were clearly not meant for you (someone else in the room, a TV, or your own words coming back), write \
nothing at all.

A message that starts with "[News from your background work]" is not from {first}. It is what your background \
work found, or a question it has. Tell {first} the result in your own words, most useful part first, up to about \
four sentences, and do not repeat what {first} asked. If it says work is still running, say you are on it. If it \
says the details are in the chat, say "I put the details in our chat." A note in square brackets that starts with \
"[You handed" records a hand-off you already made, and "[The call connected.]" marks the start of the call; never \
say either aloud.

If {first} adds to or changes something you are already working on (for example items on a shopping list, \
preferences, answers to your questions), hand that off too so the work picks it up.

Never place orders, send messages or spend money on your own. Get things ready: a message you write waits \
for {possessive} okay on {possessive} phone, and a cart stops at checkout with the total read back so {first} can \
finish it from the app. Never tell {first} to do the whole thing when you can get it ready.

{{rules}}

{{spoken}}"""

# What the call test taught about this voice (docs/research/2026-10-08-kokoro-call-test.md, 2026-10-09-george-questions.md):
# a short first sentence starts the voice sooner, a comma after it leaves a gap, and "Oh, man" sounds wrong.
SPOKEN_TEMPLATE = """How your words become speech on this call:
- A speech voice on {possessive} phone reads your words aloud, so write plain spoken English: no symbols, emojis, \
lists, headings or markdown, and nothing in brackets.
- Make the first sentence short, two to six words, and end it with a period, an exclamation point or a question \
mark, like "Sure thing." or "Good question." Vary it. Never end the first sentence with a comma. Keep later \
sentences under about twenty-five words.
- Never say "Oh, man" or "Oh man".
- What {first} says comes from speech recognition on the phone and may have small errors. Go with the most likely \
meaning."""


def prompt(bot_id: str, brief: str) -> str:
    bot = config.BOTS_BY_ID.get(bot_id)
    name = bot.name if bot else "Zero"
    return voice._fill(PROMPT_TEMPLATE, again=True).format(briefing=brief, rules=voice.speaking_rules(name),
                                                           spoken=voice._fill(SPOKEN_TEMPLATE))


def tool_manifest() -> list[dict]:
    """ask_bot and update_job as the inert tool list names them (MCP inputSchema)."""
    out = []
    for t in (voice.ask_bot_tool(), voice.update_job_tool(), voice.work_status_tool()):
        desc = t["description"]
        if t["name"] == "ask_bot":
            desc += f" Pass {who.possessive()} request in full, with every detail from the conversation it needs."
        out.append({"name": t["name"], "description": desc, "inputSchema": t["inputSchema"]})
    return out


def request_body() -> dict:
    """The model settings (CLAUDE_CODE_EXTRA_BODY): no thinking, low effort, a short reply, the two tools."""
    return {"thinking": {"type": "disabled"}, "context_management": {"edits": []},
            "output_config": {"effort": EFFORT}, "max_tokens": MAX_TOKENS,
            "tools": [{"name": PREFIX + t["name"], "description": t["description"], "input_schema": t["inputSchema"]}
                      for t in tool_manifest()]}


# MARK: Conversation state

@dataclass
class Turn:
    n: int
    kind: str                      # "words" (the person spoke) or "news" (background work came back)
    text: str
    speculative: bool
    asked_at: float = field(default_factory=time.time)
    reply: str = ""
    handoff: tuple | None = None   # (tool, argument)
    first_text_at: float = 0.0
    warm: bool = False
    done: bool = False
    failed: str | None = None
    confirmed: bool = False
    logged: bool = False
    handed: bool = False
    spoken: str | None = None
    cut_off: bool = False
    task: asyncio.Task | None = None


@dataclass
class Local:
    greeting: str
    greeting_late: str = ""        # said instead when the person waited long for the call (voice.greeting)
    voice_name: str = DEFAULT_VOICE
    system: str = ""
    brief_task: asyncio.Task | None = None
    turns: dict = field(default_factory=dict)      # n: Turn
    news: dict = field(default_factory=dict)       # id: text
    news_seq: int = 0
    warm: "Run | None" = None
    warming: asyncio.Task | None = None
    closed: bool = False


def user_content(t: Turn) -> str:
    return f"{NEWS_MARK} {t.text}" if t.kind == "news" else t.text


def history(local: Local) -> list[tuple[str, str]]:
    """The conversation as Claude sees it: confirmed, finished turns only, each with what was really spoken."""
    msgs = [("user", CONNECTED), ("assistant", local.greeting)]
    for n in sorted(local.turns):
        t = local.turns[n]
        if not (t.confirmed and t.done):
            continue
        msgs.append(("user", user_content(t)))
        said = t.reply.strip()
        if t.cut_off:
            heard = (t.spoken or "").strip()
            said = heard + " (cut off)" if heard else "(cut off before saying anything)"
        if said:
            msgs.append(("assistant", said))
        if t.handoff:
            msgs.append(("user", f"[You handed this to your background work with {t.handoff[0]}: "
                                 f"{t.handoff[1]} Its answer will come to you as news.]"))
    return msgs


def merge(msgs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[list] = []
    for role, text in msgs:
        if out and out[-1][0] == role:
            out[-1][1] += "\n\n" + text
        else:
            out.append([role, text])
    return [(r, t) for r, t in out]


def split(msgs: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], str]:
    """(history to replay, ending with the last assistant message; the user text that asks the model)."""
    last = max(i for i, (r, _) in enumerate(msgs) if r == "assistant")
    return msgs[:last + 1], "\n\n".join(t for _, t in msgs[last + 1:])


# MARK: Claude runs

def _env() -> dict:
    """The plan sign-in, never an API key: every ANTHROPIC_* override is dropped."""
    drop = ("CLAUDE_CONFIG_DIR", "HERMES_HOME", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
            "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_EXTRA_BODY", "CLAUDE_CODE_EFFORT_LEVEL")
    env = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("ANTHROPIC_")}
    env["HOME"] = str(config.HOME)
    env["PATH"] = f"{config.HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    env.update(ENABLE_TOOL_SEARCH="false", DISABLE_AUTO_COMPACT="1", DISABLE_COMPACT="1", DISABLE_AUTOUPDATER="1",
               CLAUDE_CODE_TOTAL_TOKENS_REMINDER="off", CLAUDE_CODE_MAX_OUTPUT_TOKENS=str(MAX_TOKENS))
    return env


def claude_exe() -> str:
    from .accounts import claude_bin
    exe = claude_bin()
    if not exe:
        raise FileNotFoundError("claude")
    return exe


def argv(exe: str, tmp: Path) -> list[str]:
    """The plan broker's DIRECTSDK shape, value for value, plus --effort."""
    mcp = {"mcpServers": {"hermes": {"command": sys.executable, "args": [str(INERT), str(tmp / "tools.json")]}}}
    return [exe, "-p", "--model", MODEL, "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--include-partial-messages", "--tools", "", "--system-prompt-file", str(tmp / "system.md"),
            "--settings", str(tmp / "settings.json"), "--setting-sources", "", "--strict-mcp-config",
            "--disable-slash-commands", "--max-turns", "1", "--permission-mode", "dontAsk",
            "--no-session-persistence", "--mcp-config", json.dumps(mcp), "--effort", EFFORT]


def _frame(role: str, text: str, query: bool = True) -> dict:
    f = {"type": role, "message": {"role": role, "content": [{"type": "text", "text": text}]}}
    if role == "user" and not query:
        f["shouldQuery"] = False
    return f


class RunError(RuntimeError):
    def __init__(self, message: str, signed_out: bool = False):
        super().__init__(message)
        self.signed_out = signed_out


class Run:
    """One `claude -p` process for one turn. `open` starts it and replays the history; `ask` sends the last
    message and streams the answer. A run waiting in Local.warm has done `open` already."""

    def __init__(self, system: str, replay: list[tuple[str, str]]):
        self.system = system
        self.replay = replay
        self.key = (system, tuple(replay))
        self.started = time.time()
        self.proc: asyncio.subprocess.Process | None = None
        self.tmp: Path | None = None
        self.ready = False
        self.used = False

    @property
    def fresh(self) -> bool:
        return self.ready and not self.used and time.time() - self.started < WARM_MAX_AGE_SEC and \
            self.proc is not None and self.proc.returncode is None

    async def open(self):
        exe = claude_exe()
        self.tmp = Path(tempfile.mkdtemp(prefix="zero-ondevice-"))
        (self.tmp / "system.md").write_text(self.system)
        (self.tmp / "tools.json").write_text(json.dumps(tool_manifest()))
        (self.tmp / "settings.json").write_text(json.dumps({"env": {"CLAUDE_CODE_EXTRA_BODY": json.dumps(request_body())}}))
        self.proc = await asyncio.create_subprocess_exec(
            *argv(exe, self.tmp), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=_env(), cwd=str(self.tmp), limit=2 ** 24, start_new_session=True)
        for role, text in self.replay:
            self._write(_frame(role, text, query=False))
            if role != "user":
                continue
            await self.proc.stdin.drain()
            while True:   # Claude acknowledges a frame it was told not to ask about with a zero-turn result
                ev = await self._next(FIRST_EVENT_TIMEOUT_SEC)
                if ev is None:
                    raise RunError("Claude ended before the history was replayed.")
                if ev.get("type") == "result":
                    if ev.get("num_turns") != 0 or ev.get("is_error"):
                        raise RunError("Claude did not accept the history: " + str(ev.get("subtype")))
                    break
        await self.proc.stdin.drain()
        self.ready = True

    def _write(self, frame: dict):
        self.proc.stdin.write((json.dumps(frame) + "\n").encode())

    async def _next(self, timeout: float) -> dict | None:
        while True:
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout)
            if not line:
                return None
            try:
                return json.loads(line)
            except ValueError:
                continue     # a stray line from a wrapper is not the stream

    async def ask(self, text: str):
        """Yields ("text", delta) and ("tool", name, input). Raises RunError when Claude fails."""
        self.used = True
        self._write(_frame("user", text))
        await self.proc.stdin.drain()
        self.proc.stdin.close()
        streamed, finals, tools, result, error, code = "", "", [], None, None, None
        timeout = FIRST_EVENT_TIMEOUT_SEC
        deadline = time.time() + TURN_TIMEOUT_SEC
        while True:
            ev = await self._next(max(1.0, min(timeout, deadline - time.time())))
            if ev is None:
                break
            kind = ev.get("type")
            if kind == "stream_event":
                d = (ev.get("event") or {}).get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    streamed += d["text"]
                    yield ("text", d["text"])
            elif kind == "assistant":
                msg = ev.get("message") or {}
                if ev.get("error") or msg.get("error"):
                    error = " ".join(b.get("text", "") for b in msg.get("content") or [] if b.get("type") == "text")
                    code = str(ev.get("error") or msg.get("error"))
                    continue
                for b in msg.get("content") or []:
                    if b.get("type") == "text":
                        finals += b.get("text", "")
                    elif b.get("type") == "tool_use":
                        tools.append((str(b.get("name") or "").removeprefix(PREFIX), b.get("input") or {}))
            elif kind == "result":
                result = ev
            if time.time() > deadline:
                raise RunError("Claude took too long.")
        if finals.startswith(streamed) and len(finals) > len(streamed):
            yield ("text", finals[len(streamed):])     # anything the partial stream missed
        for name, args in tools:
            yield ("tool", name, args)
        if error is not None or result is None:
            detail = (error or "no result") + (f" ({code})" if code else "")
            raise RunError("Claude failed: " + detail[:300], signed_out=code == "authentication_failed"
                           or "log in" in (error or "").lower() or "/login" in (error or ""))
        if result.get("is_error") and not (tools and result.get("subtype") == "error_max_turns"):
            raise RunError("Claude failed: " + str(result.get("subtype") or result.get("result") or "")[:300])

    def kill(self):
        proc, self.proc = self.proc, None
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                asyncio.get_running_loop().create_task(proc.wait())
            except RuntimeError:
                pass
        if self.tmp is not None:
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.tmp = None


# MARK: Calls

def local_of(call: voice.Call) -> Local:
    return call.local


async def start(bot_id: str, voice_name: str | None, reason: str | None = None,
                chosen: bool | None = None) -> voice.Call:
    """A call starts at once: the phone plays its own greeting while the briefing loads here."""
    began = time.time()
    name = voice.pick_voice(voice_name, chosen, VOICE_SETTING, VOICES, DEFAULT_VOICE)
    call = voice.Call(id="od-" + uuid.uuid4().hex[:20], bot_id=bot_id, voice=name, started=began, engine=ENGINE)
    call.local = Local(greeting=voice.greeting(), greeting_late=voice.greeting(late=True), voice_name=name)
    voice.calls[call.id] = call
    bot = config.BOTS_BY_ID.get(bot_id)
    calllog.begin(call.id, at=began, bot=bot_id, bot_name=bot.name if bot else bot_id, voice=name, engine=ENGINE,
                  reply_model=MODEL, route=ROUTE, reason=(reason or "")[:40] or None)
    call.local.brief_task = asyncio.create_task(briefing.for_call(bot_id))
    asyncio.create_task(voice._watchdog(call))
    asyncio.create_task(voice._speaker(call))
    _prewarm(call)
    return call


async def _system(call: voice.Call) -> str:
    local = local_of(call)
    if not local.system:
        try:
            brief = await asyncio.wait_for(asyncio.shield(local.brief_task), 20)
        except Exception:
            log.warning("on-device call %s: no briefing", call.id, exc_info=True)
            from . import onboarding
            name = config.BOTS_BY_ID[call.bot_id].name if call.bot_id in config.BOTS_BY_ID else "Zero"
            brief = f"You are {name}, {onboarding.person_name()}'s assistant."
        local.system = prompt(call.bot_id, brief)
    return local.system


def _prewarm(call: voice.Call, delay: float = 0.0) -> None:
    """Starts the next turn's Claude with the history as it stands, unless one is already waiting with it."""
    local = local_of(call)
    if local.closed:
        return
    if local.warming and not local.warming.done():
        local.warming.cancel()

    async def go():
        if delay:
            await asyncio.sleep(delay)
        system = await _system(call)
        replay, _ = split(merge(history(local)))
        key = (system, tuple(replay))
        if local.warm is not None and local.warm.key == key and local.warm.fresh:
            return
        old, local.warm = local.warm, None
        if old is not None:
            old.kill()
        run = Run(system, replay)
        try:
            await run.open()
        except asyncio.CancelledError:
            run.kill()
            raise
        except Exception:
            run.kill()
            log.warning("on-device call %s: could not start Claude ahead of time", call.id, exc_info=True)
            return
        if local.closed:
            run.kill()
            return
        local.warm = run
    try:
        local.warming = asyncio.get_running_loop().create_task(go())
    except RuntimeError:
        pass


async def _run_for(call: voice.Call, replay: list) -> tuple[Run, bool]:
    """The waiting process when it has this history, else a new one. Then another starts waiting, so a
    speculative turn that is replaced does not wait for Claude to start."""
    local = local_of(call)
    system = await _system(call)
    key = (system, tuple(replay))
    if local.warming and not local.warming.done() and (local.warm is None or not local.warm.fresh):
        try:
            await asyncio.wait_for(asyncio.shield(local.warming), 1.5)   # it may be a moment from ready
        except Exception:
            pass
    run, warm = local.warm, True
    if run is not None and run.key == key and run.fresh:
        local.warm = None
    else:
        warm = False
        run = Run(system, replay)
        await run.open()
    _prewarm(call)
    return run, warm


def _sorry(e: BaseException) -> str:
    from . import home
    if isinstance(e, FileNotFoundError) or getattr(e, "signed_out", False):
        return SORRY_SHARED if home.plan_kind() == "shared" else SORRY_SIGNED_OUT
    return SORRY


async def _produce(call: voice.Call, t: Turn, q: asyncio.Queue):
    run = None
    try:
        msgs = merge(history(local_of(call)) + [("user", user_content(t))])
        replay, final = split(msgs)
        run, t.warm = await _run_for(call, replay)
        async for ev in run.ask(final):
            if ev[0] == "text":
                if not t.first_text_at:
                    t.first_text_at = time.time()
                t.reply += ev[1]
                call.last_activity = time.time()
                await q.put({"type": "text", "text": ev[1]})
            elif ev[0] == "tool" and ev[1] in TOOLS and t.handoff is None:
                if ev[1] == "work_status":
                    t.handoff = ("work_status", "What is in progress.")
                    continue
                arg = ev[2].get("request") if ev[1] == "ask_bot" else ev[2].get("note")
                if isinstance(arg, str) and arg.strip():
                    t.handoff = (ev[1], arg.strip()[:TEXT_LIMIT])
        if t.handoff and not t.reply.strip():
            t.reply = "One sec."     # the line before a hand-off must never be silence
            t.first_text_at = t.first_text_at or time.time()
            await q.put({"type": "text", "text": t.reply})
        t.done = True
        await q.put({"type": "done", "text": t.reply, "handoff": t.handoff[0] if t.handoff else None})
    except asyncio.CancelledError:
        if t.confirmed and not t.done:
            t.done = True        # cut off: what was written so far stays, /said says what was heard
            t.cut_off = True
        raise
    except Exception as e:
        log.warning("on-device call %s: turn %d failed: %s", call.id, t.n, e)
        t.failed = calllog.short(e)
        line = _sorry(e)
        t.reply = line
        t.done = True
        calllog.write(call.id, "error", stage="reply", message=t.failed, turn=t.n)
        await q.put({"type": "text", "text": line})
        await q.put({"type": "done", "text": line, "error": True})
    finally:
        if run is not None:
            run.kill()
        if t.done:
            _settle(call, t)


async def turn(call: voice.Call, n: int, text: str | None = None, news: int | None = None,
               speculative: bool = False):
    """Streams one reply as events: {"type": "text", "text": ...} pieces, then {"type": "done", ...}."""
    local = local_of(call)
    call.last_activity = time.time()     # the idle watchdog (voice._watchdog) counts turns as activity
    if news is not None:
        content, kind, speculative = local.news.get(news), "news", False
        if not content:
            yield {"type": "done", "text": "", "error": True}
            return
    else:
        content, kind = (text or "").strip()[:TEXT_LIMIT], "words"
    prior = local.turns.get(n)
    t = Turn(n=n, kind=kind, text=content, speculative=speculative)
    if prior is not None:
        if prior.task and not prior.task.done():
            prior.task.cancel()
        t.handed = prior.handed       # words that arrived late never start the same work twice
        if prior.confirmed and prior.kind == "words" and call.transcript and \
                call.transcript[-1] == (who.first(), prior.text):
            call.transcript.pop()     # the longer words replace it below
    for other in local.turns.values():   # the phone moved on: an older reply still being written is dropped
        if other.n < n and other.task and not other.task.done() and not other.confirmed:
            other.task.cancel()
    local.turns[n] = t
    if not speculative:
        _confirm(call, t)
    q: asyncio.Queue = asyncio.Queue()
    t.task = asyncio.create_task(_produce(call, t, q))
    try:
        while True:
            ev = await q.get()
            yield ev
            if ev["type"] == "done":
                return
    finally:
        if not t.task.done():
            t.task.cancel()     # the phone hung up on this reply (a speculative start it no longer wants)


def confirm(call: voice.Call, n: int) -> bool:
    t = local_of(call).turns.get(n)
    if t is None:
        return False
    _confirm(call, t)
    return True


def _confirm(call: voice.Call, t: Turn) -> None:
    if t.confirmed:
        return
    t.confirmed = True
    if t.kind == "words" and t.text:
        now = time.time()
        call.transcript.append((who.first(), t.text))
        call.last_user_delta = now
        calllog.write(call.id, "line", who=who.first(), text=calllog.short(t.text, calllog.TEXT_LIMIT))
        voice.heard_line(call, t.text)     # a goodbye here ends the call once it is said back
    if t.done:
        _settle(call, t)


def _settle(call: voice.Call, t: Turn) -> None:
    """A confirmed turn is finished: saved, its hand-off started once, and the next turn's Claude started."""
    if not (t.confirmed and t.done):
        return
    if not t.logged:
        t.logged = True
        calllog.write(call.id, "reply", turn=t.n, kind=t.kind, speculative=t.speculative or None, warm=t.warm,
                      ms_first_text=int((t.first_text_at - t.asked_at) * 1000) if t.first_text_at else None,
                      ms_total=int((time.time() - t.asked_at) * 1000), handoff=t.handoff[0] if t.handoff else None,
                      error=t.failed, chars=len(t.reply))
    if t.handoff and not t.handed:
        t.handed = True
        asyncio.get_running_loop().create_task(_handoff(call, t))
    _prewarm(call, delay=0.2)


async def _handoff(call: voice.Call, t: Turn) -> None:
    """The same work a ChatGPT call's relay does: the Zip answers, or the job starts and follows the call."""
    tool, arg = t.handoff
    began, failed = time.time(), None
    call.last_tool_at = began
    try:
        if tool == "work_status":
            text = await asyncio.to_thread(voice.work_status, call)
        else:
            text = await (voice.ask_bot(call, arg) if tool == "ask_bot" else voice.update_job(call, arg))
    except Exception as e:
        log.exception("on-device call %s: %s failed", call.id, tool)
        failed = calllog.short(e)
        text = f"That did not work. Tell {who.first()} you could not get it and offer to try again."
    calllog.write(call.id, "tool", at=began, name=tool, asked=calllog.short(arg), result=calllog.short(text),
                  secs=round(time.time() - began, 1), error=failed)
    if text and not call.closed:
        voice.say(call, text, must=True)


def said(call: voice.Call, n: int, spoken: str, cut_off: bool = False, first_audio_ms: int | None = None) -> bool:
    """What the phone actually spoke for turn n, and how long after the person stopped its first sound came."""
    local = local_of(call)
    t = local.turns.get(n)
    if t is None:
        return False
    now = time.time()
    call.last_activity = now
    spoken = (spoken or "").strip()[:TEXT_LIMIT]
    t.spoken, t.cut_off = spoken, bool(cut_off) or t.cut_off
    call.last_voice_sound = now
    if spoken:
        call.transcript.append(("You", spoken))
        bot = config.BOTS_BY_ID.get(call.bot_id)
        calllog.write(call.id, "line", who=bot.name if bot else "Zero", text=calllog.short(spoken, calllog.TEXT_LIMIT))
        if not t.cut_off and not t.handoff and t.kind == "words":
            voice.voice_line(call, spoken)   # said goodbye back: the phone ends the call
    if t.cut_off:
        calllog.write(call.id, "interrupted", note="The person started talking while the voice was speaking.",
                      turn=n)
    if isinstance(first_audio_ms, int) and 0 <= first_audio_ms < 120_000:
        calllog.write(call.id, "first_reply", via="on-device", turn=n, ms_after_speech=first_audio_ms)
    _prewarm(call, delay=0.2)
    return True


async def news(call: voice.Call, text: str) -> None:
    """voice.speak for an on-device call: the phone asks for the news at its next pause, as a turn."""
    local = local_of(call)
    local.news_seq += 1
    local.news[local.news_seq] = text[:TEXT_LIMIT]
    call.last_voice_sound = time.time()      # voice._speaker waits for the voice to start on it
    await call.events.put({"type": "news", "id": local.news_seq})


def greeted(call: voice.Call, late: bool = False) -> None:
    """The phone played the greeting itself (a take made ahead of time): it goes in the transcript. After a long
    wait the phone says the late greeting instead, and the history follows."""
    local = local_of(call)
    if late and local.greeting_late:
        local.greeting = local.greeting_late
    call.transcript.append(("You", local.greeting))
    bot = config.BOTS_BY_ID.get(call.bot_id)
    calllog.write(call.id, "line", who=bot.name if bot else "Zero", text=local.greeting)
    call.last_voice_sound = time.time()
    if late:
        _prewarm(call)        # the waiting Claude has the other greeting in its history


def closed(call: voice.Call) -> None:
    local = local_of(call)
    local.closed = True
    if local.warming and not local.warming.done():
        local.warming.cancel()
    if local.warm is not None:
        local.warm.kill()
        local.warm = None
    for t in local.turns.values():
        if t.task and not t.task.done():
            t.task.cancel()
