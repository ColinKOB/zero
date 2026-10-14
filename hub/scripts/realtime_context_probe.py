"""Live check of quiet context on a call (docs/qa/2026-10-10-memory-facts.md, phase 3).

Starts its own `codex app-server` (the ChatGPT plan's realtime voice, as calls use it; a separate process from any
hub's), opens a Realtime v3 session over a websocket instead of WebRTC, plays a recorded utterance into it as 24 kHz
PCM, and records every event with its time. Conditions:

  none     the utterance with no context
  search   the same, with the hub's own path: facts.call_context on the words recognized so far (every 0.6 s) and on
           the whole line, pushed with thread/realtime/appendText role developer (session.context.append in v3)
  full     as calls run after this change: the briefing carries the facts store (facts.briefing_text) and the
           search pushes on top
  silent   only an appendText note and no audio: does a developer note start an answer by itself?

The facts come from a temporary store seeded with fictional facts about the test persona; nothing live is read.

    cd hub && ZERO_HOME=/tmp/zm/eval-home.json ~/taffy/.venv/bin/python scripts/realtime_context_probe.py \\
        --audio /tmp/zm-audio --out /tmp/zm-rt
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import config, facts, store, team, voice  # noqa: E402

UTTERANCES = {
    "job": "I'm thinking about taking that job with Gregg's, but I'm not sure. It could get in the way of my other "
           "passions.",
    "coffee": "Can you order new coffee for me?",
}
FACTS = [
    ("default", "job search", "plan", "Sam applied on October 6, 2026 for the Kitchen Operations Manager job at "
                                      "Gregg's Restaurants in East Providence; the posting lists $61k to $67k."),
    ("default", "goals", "plan", "Sam wants to write a bread cookbook by next fall."),
    ("default", "soccer", "plan", "Sam coaches niece Ana's soccer team on Sunday mornings and loves it."),
    ("default", "bakery", "other", "Sam runs Rivera Bakes, open 6 a.m. Tuesday to Sunday."),
    ("default", "coffee", "preference", "Sam's usual coffee is Peet's Big Bang whole bean, bought at Wegmans."),
    ("inbox", "accountant", "person", "Sam's accountant is Marcus Lee at Lee Tax Group."),
]
BRIEF = ("You are Nova, Sam Rivera's assistant. Sam is 34, lives in Providence, Rhode Island, with partner Alex and "
         "their dog Pepper, and runs a small bakery.")


def seed() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zm-rt-"))
    config.HUB_DB = tmp / "hub.db"
    config.HERMES_HOME = tmp / "hermes"
    config.HERMES_HOME.mkdir()
    team.NOTES_FILE = tmp / "notes.md"
    config.reload_bots = lambda: False
    for b in [config.Bot("default", "Nova", "Lead", "#F2711C", "sparkles"),
              config.Bot("inbox", "Inbox", "Mail", "#06B6D4", "tray")]:
        config.BOTS_BY_ID[b.id] = b
    config.BOTS[:] = list(config.BOTS_BY_ID.values())
    store._local.conn = None
    store._schema_ready = False
    facts.reset_for_tests()
    for bot, subject, kind, text in FACTS:
        facts.save(bot, text, subject=subject, kind=kind, limits=False)


class Codex:
    def __init__(self, log):
        self.log, self.next, self.waiters, self.listen = log, 0, {}, None

    async def start(self):
        exe = os.path.expanduser("~/.local/bin/codex")
        self.proc = await asyncio.create_subprocess_exec(exe, "app-server", stdin=asyncio.subprocess.PIPE,
                                                         stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                                                         limit=2 ** 24, start_new_session=True, cwd="/tmp")
        asyncio.create_task(self._read())
        await self.call("initialize", {"clientInfo": {"name": "zm-probe", "version": "1"},
                                       "capabilities": {"experimentalApi": True}})
        self.proc.stdin.write(b'{"method": "initialized"}\n')

    async def _read(self):
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                return
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if "id" in m and "method" not in m:
                f = self.waiters.pop(m["id"], None)
                if f and not f.done():
                    f.set_result(m)
            elif "id" in m:
                self.log({"event": "request", "method": m.get("method"), "params": str(m.get("params"))[:300]})
                reply = {"id": m["id"], "result": {"contentItems": [{"type": "inputText", "text":
                         "No answer available in this test."}], "success": True}}
                self.proc.stdin.write((json.dumps(reply) + "\n").encode())
            elif self.listen:
                self.listen(m.get("method") or "", m.get("params") or {})

    async def call(self, method, params, timeout=40):
        self.next += 1
        fut = asyncio.get_running_loop().create_future()
        self.waiters[self.next] = fut
        self.proc.stdin.write((json.dumps({"id": self.next, "method": method, "params": params}) + "\n").encode())
        await self.proc.stdin.drain()
        r = await asyncio.wait_for(fut, timeout)
        if "error" in r:
            raise RuntimeError(json.dumps(r["error"])[:300])
        return r.get("result") or {}

    async def stop(self):
        try:
            os.killpg(self.proc.pid, 15)
        except ProcessLookupError:
            pass


class Speech:
    """A microphone track: silence until say() is called, then the utterance, then silence."""

    def __init__(self):
        from aiortc.mediastreams import AudioStreamTrack

        class Track(AudioStreamTrack):
            def __init__(inner):
                super().__init__()
                inner.buf, inner.pos, inner.ts = b"", 0, 0

            async def recv(inner):
                import av
                import fractions
                rate, n = 48000, 960
                if inner.ts == 0:
                    inner.start = time.time()
                wait = inner.start + inner.ts / rate - time.time()
                if wait > 0:
                    await asyncio.sleep(wait)
                chunk = inner.buf[inner.pos:inner.pos + n * 2]
                inner.pos += len(chunk)
                chunk = chunk + b"\0" * (n * 2 - len(chunk))
                frame = av.AudioFrame(format="s16", layout="mono", samples=n)
                frame.planes[0].update(chunk)
                frame.sample_rate, frame.pts, frame.time_base = rate, inner.ts, fractions.Fraction(1, rate)
                inner.ts += n
                return frame
        self.track = Track()

    def say(self, data: bytes) -> float:
        self.track.buf, self.track.pos = data, 0
        return len(data) / 2 / 48000


def pcm(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        return w.readframes(w.getnframes()), w.getframerate()


async def session(cx: Codex, cond: str, utter: str, audio: Path | None, log) -> dict:
    began = time.time()
    t = lambda: round(time.time() - began, 3)  # noqa: E731
    ev: list = []
    st = {"heard": "", "pushed": set(), "searched": "", "ctx": [], "assistant": [], "user_done": None,
          "first_user_delta": None, "last_user_delta": None, "first_out": None, "task": None, "again": False}

    async def push(text, final):
        text = " ".join(text.split())
        if len(text.split()) < voice.CONTEXT_MIN_WORDS or text == st["searched"]:
            return
        st["searched"] = text
        s0 = time.time()
        note, keys = facts.call_context(text, "default", st["pushed"])
        ms = int((time.time() - s0) * 1000)
        if not note:
            st["ctx"].append({"t": t(), "final": final, "search_ms": ms, "items": 0})
            return
        st["pushed"].update(keys)
        await cx.call("thread/realtime/appendText", {"threadId": tid, "text": note, "role": "developer"}, 5)
        st["ctx"].append({"t": t(), "final": final, "search_ms": ms, "items": len(keys), "note": note})

    async def now():
        while True:
            st["again"] = False
            await push(st["heard"], False)
            if not st["again"]:
                return

    def listen(method, params):
        rec = {"t": t(), "method": method}
        if "delta" in params:
            rec["delta"] = str(params.get("delta"))[:200]
        if "text" in params:
            rec["text"] = str(params.get("text"))[:600]
        if params.get("role"):
            rec["role"] = params["role"]
        if "error" in method:
            rec["params"] = json.dumps(params)[:400]
        if method.endswith("outputAudio/delta"):
            rec.pop("delta", None)
        ev.append(rec)
        role = params.get("role")
        if method == "thread/realtime/transcript/delta" and role == "user":
            st["heard"] += params.get("delta") or ""
            st["first_user_delta"] = st["first_user_delta"] or t()
            st["last_user_delta"] = t()
            if cond in ("search", "full"):
                if st["task"] is None or st["task"].done():
                    st["task"] = asyncio.get_running_loop().create_task(now())
                else:
                    st["again"] = True
        elif method == "thread/realtime/transcript/done" and role == "user":
            st["user_done"] = t()
            if cond in ("search", "full"):
                asyncio.get_running_loop().create_task(push(params.get("text") or "", True))
        elif role and role != "user" and method.startswith("thread/realtime/transcript"):
            st["first_out"] = st["first_out"] or t()
            if method.endswith("/done"):
                st["assistant"].append(params.get("text"))
        elif "outputAudio" in method or "output" in method.lower():
            st["first_out"] = st["first_out"] or t()

    th = await cx.call("thread/start", {"ephemeral": True, "model": voice.RELAY_MODEL,
                                        "config": {"model_reasoning_effort": "low"}, "environments": [],
                                        "sandbox": "read-only", "approvalPolicy": "never",
                                        "baseInstructions": voice.relay_instructions(),
                                        "dynamicTools": [voice.ask_bot_tool(), voice.update_job_tool(),
                                                         voice.work_status_tool()]})
    tid = (th.get("thread") or {}).get("id") or th.get("threadId")
    cx.listen = listen
    brief = BRIEF if cond != "full" else (BRIEF + "\n\nWhat the team knows about Sam:\n"
                                          + facts.briefing_text("default"))
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.contrib.media import MediaBlackhole
    mic = Speech()
    pc = RTCPeerConnection()
    pc.addTrack(mic.track)
    pc.createDataChannel("oai-events")
    hole = MediaBlackhole()

    @pc.on("track")
    def on_track(track):
        hole.addTrack(track)
    await pc.setLocalDescription(await pc.createOffer())
    answer = asyncio.get_running_loop().create_future()
    inner = listen

    def listen2(method, params):
        if method == "thread/realtime/sdp" and not answer.done():
            answer.set_result(params.get("sdp") or "")
        inner(method, params)
    cx.listen = listen2
    await cx.call("thread/realtime/start", {"threadId": tid, "outputModality": "audio", "voice": "cove",
                                            "version": "v3", "transport": {"type": "webrtc",
                                                                           "sdp": pc.localDescription.sdp},
                                            "clientManagedHandoffs": False, "includeStartupContext": False,
                                            "prompt": voice.voice_prompt("default", brief)}, 30)
    await pc.setRemoteDescription(RTCSessionDescription(await asyncio.wait_for(answer, 30), "answer"))
    await hole.start()
    await asyncio.sleep(3.0)
    if audio is not None:
        data, rate = pcm(audio)
        assert rate == 48000, rate
        secs = mic.say(data)
        await asyncio.sleep(secs)
        audio_end = t()
        await asyncio.sleep(12)
    else:
        audio_end = t()
        await cx.call("thread/realtime/appendText", {"threadId": tid, "role": "developer", "text":
                      "[Context for this call] Sam's usual coffee is Peet's Big Bang whole bean from Wegmans."}, 5)
        await asyncio.sleep(8)
    try:
        await cx.call("thread/realtime/stop", {"threadId": tid}, 10)
    except Exception:
        pass
    cx.listen = None
    await hole.stop()
    await pc.close()
    out = {"cond": cond, "utterance": utter, "audio_end": audio_end, "first_user_delta": st["first_user_delta"],
           "last_user_delta": st["last_user_delta"], "user_done": st["user_done"], "first_out": st["first_out"],
           "context": [{k: v for k, v in c.items() if k != "note"} for c in st["ctx"]],
           "context_notes": [c.get("note") for c in st["ctx"] if c.get("note")],
           "assistant": st["assistant"], "methods": sorted({e["method"] for e in ev}), "events": ev}
    log({k: v for k, v in out.items() if k != "events"})
    return out


async def main(a) -> None:
    seed()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []

    def log(rec):
        print(json.dumps(rec)[:2500], flush=True)
    cx = Codex(log)
    await cx.start()
    try:
        plan = [(c, u) for c in a.conds.split(",") for u in a.utterances.split(",")]
        for rep in range(a.repeat):
            for cond, u in plan:
                if cond == "silent":
                    if rep == 0 and u == plan[0][1]:
                        results.append(await session(cx, "silent", "", None, log))
                    continue
                results.append(await session(cx, cond, UTTERANCES[u], Path(a.audio) / f"{u}.wav", log))
    finally:
        await cx.stop()
        (out / "probe.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", default="/tmp/zm-audio")
    ap.add_argument("--out", default="/tmp/zm-rt")
    ap.add_argument("--conds", default="none,search,silent")
    ap.add_argument("--utterances", default="job,coffee")
    ap.add_argument("--repeat", type=int, default=1)
    asyncio.run(main(ap.parse_args()))
