""























from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import briefing, calllog, shortreply, who

log = logging.getLogger("taffy.callsort")

KINDS = ("aside", "correction", "lookup", "task", "stop")
SORT_TIMEOUT_SEC = 8
JOIN_WAIT_SEC = 4.0
SIDE_TIMEOUT_SEC = 240
MAX_SIDES = 3
LABEL_MAX = 60
RECENT_LINES = 3

PROMPT = """{first} is on a voice call. Work running for {first} now:
{job}
{first}'s recent words on the call:
{recent}

The voice just handed over this, from {first}: {text}

Sort it, for what it means to the running job. Answer with one JSON object and nothing else:
{{"kind": "aside|correction|lookup|task|stop", "label": "...", "job_label": "...", "target": "..."}}

- aside: chat, a comment, or thinking out loud that asks for no lookup and no change ("if it's too hot I'll wear \
shorts"). The running job should not be touched.
- correction: changes, narrows or answers the running job ("make it Thursday", "the blue one", "yes, go ahead").
- lookup: a new, separate question to find something out, not about the running job ("what time does my DoorDash \
get here?").
- task: a new request to do or change something (send, book, buy, add, remember) that is not about the running job.
- stop: tells it to stop, drop or cancel the running job ("stop", "never mind", "forget it").
When unsure between aside and anything else, choose aside only if nothing is being asked for.
"label": for a lookup, what it checks as a short noun phrase, like "your DoorDash order". Otherwise "".
"job_label": the main job as a short noun phrase, like "the weather", "your eLearning assignments".
"target": for a correction, which running work it is about: "main" for the main job, or the tag of the second \
lookup it changes, like "L1". Otherwise "main"."""

SIDE_GUIDE = (
    "[Quick lookup from a voice call] {first} is on a live call and asked this while another job of yours runs. Only "
    "read, look up and check here; change nothing, send nothing, sign in to nothing new. Your reply is read aloud: "
    "one or two plain sentences with the answer first, no lists, no links, no file paths, no markdown, {units_short}, "
    "{tz_short} time. State a date or time only when you have checked it. If you could not find it, say what you "
    "could not check.\n\nQuestion from the call: ")

ASIDE_RESULT = "That needs nothing from the running job. Say nothing about it."
CORRECTION_RESULT = "Passed on to the running job. No result yet, so say nothing about it."
STOP_RESULT = "Stopped. Tell {first} you stopped."


@dataclass
class Side:
    ""
    n: int
    question: str
    label: str
    task_no: int
    started: float = field(default_factory=time.time)
    done: bool = False
    task: asyncio.Task | None = None


@dataclass
class Answer:
    label: str
    text: str
    task_no: int
    main: bool = False


def _json(text: str) -> dict | None:
    t = re.sub(r"^```(json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        return None
    try:
        out = json.loads(t[i:j + 1])
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def _label(text) -> str:
    return " ".join(str(text or "").split())[:LABEL_MAX].strip(" .")


SYSTEM = "You sort what a person says during a voice call. Answer with the JSON object asked for and nothing else."
_settings: Path | None = None


def _settings_file() -> Path:
    ""


    global _settings
    if _settings is None or not _settings.exists():
        folder = Path(tempfile.mkdtemp(prefix="zero-callsort-"))
        _settings = folder / "settings.json"
        body = {"thinking": {"type": "disabled"}, "context_management": {"edits": []}, "max_tokens": 120}
        _settings.write_text(json.dumps({"env": {"CLAUDE_CODE_EXTRA_BODY": json.dumps(body)}}))
    return _settings


async def ask_haiku(prompt: str) -> str:
    ""


    from . import claudeaccounts, home, ondevice, shortreply, usage
    if home.get("plan.claude.mode") == "broker" or shortreply.route()[0].plan != "claude" \
            or shortreply.claude_held():
        return await briefing.ask_haiku(prompt, timeout=SORT_TIMEOUT_SEC, effort="low")
    started = time.monotonic()
    proc, result, err = None, None, b""
    try:
        proc = await asyncio.create_subprocess_exec(
            ondevice.claude_exe(), "-p", "--model", briefing.MODEL, "--tools", "", "--system-prompt", SYSTEM,
            "--settings", str(_settings_file()), "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
            "--no-session-persistence", "--max-turns", "1", "--output-format", "json", "--effort", "low",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=ondevice._env(), cwd="/tmp")
        out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), SORT_TIMEOUT_SEC)
        lines = out.decode().strip().splitlines()
        result = json.loads(lines[-1]) if lines else None
    finally:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        usage.log_cli("hub-callsort", result, briefing.MODEL, started)
    text = ((result or {}).get("result") or "").strip()
    if not text or (result or {}).get("is_error"):
        said = f"{text} {err.decode(errors='replace')[-400:]}".strip()
        kind = claudeaccounts.refusal(said)
        if kind:
            shortreply.hold_claude(kind)
            return await briefing.ask_haiku(prompt, timeout=max(1.0, SORT_TIMEOUT_SEC - (time.monotonic() - started)),
                                            effort="low")
        raise shortreply.HelperError("Claude", briefing.MODEL, "failed", said or "an empty answer")
    return text


@dataclass
class Sorted:
    kind: str
    label: str = ""
    job_label: str = ""
    target: str = "main"


async def sort(call, text: str, job: str) -> Sorted | None:
    ""
    from . import voice
    recent = voice.person_lines(call.transcript)[-RECENT_LINES:]
    prompt = PROMPT.format(first=who.first(), job=job or "work for the call", text=text[:1200],
                           recent="\n".join(f"- {line[:300]}" for line in recent) or "- (nothing yet)")
    began = time.monotonic()
    try:
        with shortreply.reading_text(prompt, (call.bot_id,)):
            out = _json(await asyncio.wait_for(ask_haiku(prompt), SORT_TIMEOUT_SEC))
    except Exception as e:
        calllog.write(call.id, "sort", error=calllog.short(e), secs=round(time.monotonic() - began, 1))
        return None
    kind = str((out or {}).get("kind") or "").strip().lower()
    secs = round(time.monotonic() - began, 2)
    if kind not in KINDS:
        calllog.write(call.id, "sort", error="no kind", secs=secs)
        return None
    label, job_label = _label(out.get("label")), _label(out.get("job_label"))
    target = str((out or {}).get("target") or "main").strip().lower()
    calllog.write(call.id, "sort", kind=kind, label=label or None, target=target, secs=secs, text=calllog.short(text))
    return Sorted(kind, label, job_label, target)


def running_job(call):
    ""
    for turn in (call.job, call.pending_turn):
        if turn is not None and not turn.done:
            return turn
    return None


def sides_running(call) -> bool:
    return any(not s.done for s in call.sides)


def busy(call) -> bool:
    ""

    return (running_job(call) is not None or sides_running(call) or bool(call.answers)
            or any(item[2] == ANSWERS_TAG for item in call.speech))


def main_label(call) -> str:
    ""
    job = running_job(call)
    if call.job_label and job is not None and call.job_label_turn == job.id:
        return call.job_label
    return MAIN_LABEL


def job_text(call) -> str:
    ""

    lines = []
    if call.job is not None and not call.job.done:
        lines.append(f"- [main] {call.job_task}")
    elif call.pending_turn is not None and not call.pending_turn.done:
        lines.append(f"- [main] {call.pending_task}")
    lines += [f"- [L{s.n}] second lookup: {s.question}" for s in call.sides if not s.done]
    return "\n".join(lines)


NEW_REQUEST = "new request"


async def route(call, text: str) -> str | None:
    ""

    from . import turns, voice
    job = running_job(call)
    if not busy(call):
        return None
    sorted_ = await sort(call, text, job_text(call))
    if sorted_ is None:
        return None
    kind, label, job_label = sorted_.kind, sorted_.label, sorted_.job_label
    if job is not None and job_label and call.job_label_turn != job.id:
        call.job_label, call.job_label_turn = job_label, job.id
    first = who.first()
    if kind == "aside":
        return ASIDE_RESULT
    if kind == "correction":
        digits = re.search(r"\d+", sorted_.target)
        side = next((s for s in call.sides if not s.done and digits and str(s.n) == digits.group()), None)
        if side is None and job is None:
            side = next((s for s in reversed(call.sides) if not s.done), None)
        if side is not None:
            side.task.cancel()
            side.done = True
            return await start_side(call, f"{side.question} (Update from {first}: {text})", side.label)
        if job is not None:

            if running_job(call) is not job:
                return NEW_REQUEST
            voice._add_words(call)
            if await turns.steer(call.bot_id, f"Update from {first} on the call: " + text):
                await call.events.put({"type": "status", "text": "Passed on"})
                return CORRECTION_RESULT
        return None
    if kind == "stop":
        await stop_all(call)
        return STOP_RESULT.format(first=first)
    if kind == "lookup":
        return await start_side(call, text, label)
    return None




async def start_side(call, question: str, label: str) -> str | None:
    ""
    from . import voice
    if sum(1 for s in call.sides if not s.done) >= MAX_SIDES:
        return None
    n = len(call.sides) + 1
    task_no = len(call.tasks) + 1
    call.tasks[f"side-{n}"] = task_no
    side = Side(n=n, question=question, label=label or "that", task_no=task_no)
    call.sides.append(side)
    calllog.write(call.id, "side_started", n=n, question=calllog.short(question), label=side.label)
    await call.events.put({"type": "status", "text": "Working on it"})
    await voice.cue(call, "started", task_no)
    side.task = asyncio.create_task(_run_side(call, side))
    return (f"Started a second lookup: {side.label}. No result yet, so say nothing about it. The job already "
            "running keeps going, and what this finds will come back here.")


async def _run_side(call, side: Side) -> None:
    from . import hermes, voice
    text = None
    try:
        guide = voice._fill(SIDE_GUIDE)
        async with call_lock(call):
            reply = await asyncio.wait_for(hermes.run_turn(call.bot_id, guide + side.question, lane="side"),
                                           SIDE_TIMEOUT_SEC)
        text = voice.brief(reply) if reply else None
    except asyncio.CancelledError:
        side.done = True
        raise
    except Exception as e:
        log.warning("second lookup %d failed: %s", side.n, e)
        calllog.write(call.id, "error", stage="side", message=calllog.short(e))
    side.done = True
    calllog.write(call.id, "side_result", n=side.n, text=calllog.short(text or "", 1000) or None,
                  secs=round(time.time() - side.started, 1))
    answer = Answer(label=side.label, task_no=side.task_no,
                    text=text or f"I couldn't check {side.label}. Want me to try again?")
    if call.closed:
        asyncio.create_task(voice._after_hang_up(call, answer.text, title=side.question))
        return
    deliver(call, answer)


_locks: dict[tuple, asyncio.Lock] = {}


def call_lock(call) -> asyncio.Lock:
    ""
    key = (call.bot_id, id(asyncio.get_running_loop()))
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]




def active(call) -> bool:
    ""
    return any(not s.done for s in call.sides) or bool(call.answers)


MAIN_LABEL = "your earlier request"
ANSWERS_TAG = "callsort-answers"


def _running_labels(call, held: list | None = None) -> list[str]:
    ""
    labels = [s.label for s in call.sides if not s.done]
    main_held = any(a.main for a in (call.answers if held is None else held))
    if running_job(call) is not None and not main_held:
        labels.insert(0, main_label(call))
    return labels


def deliver(call, answer: Answer) -> None:
    ""

    call.answers.append(answer)
    if call.flush_task is None or call.flush_task.done():
        call.flush_task = asyncio.get_running_loop().create_task(_flush(call))


async def _flush(call) -> None:
    from . import voice
    try:
        if _running_labels(call):
            await asyncio.sleep(JOIN_WAIT_SEC)
        still = _running_labels(call)
        answers, call.answers = call.answers, []
        if not answers or call.closed:
            if answers:
                asyncio.create_task(voice._after_hang_up(call, "\n\n".join(a.text for a in answers),
                                                       title=" and ".join(a.label for a in answers)))
            return
        text = "\n\n".join(a.text for a in answers if a.text)
        if still:
            text += f"\n\nI'm still checking {' and '.join(still)}."
        for a in answers[1:]:
            await voice.cue(call, "done", a.task_no)
        calllog.write(call.id, "answers_joined", n=len(answers), still=still or None)
        voice.say(call, text, must=True, final=True, tag=ANSWERS_TAG, done_cue=answers[0].task_no)
    except Exception:
        log.exception("could not say the answers for call %s", call.id)


def leftovers(call) -> list[str]:
    ""
    texts = [a.text for a in call.answers if a.text]
    call.answers = []
    return texts


async def stop_all(call) -> None:
    ""
    from . import turns, voice
    for side in call.sides:
        if side.task is not None and not side.task.done():
            side.task.cancel()
        side.done = True
    call.answers = []
    voice.unsay(call, ANSWERS_TAG)
    if running_job(call) is not None:
        await turns.stop(call.bot_id)
    calllog.write(call.id, "stopped_by_voice")
