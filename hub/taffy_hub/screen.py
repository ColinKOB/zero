""







import asyncio
import base64
import json
import logging
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import config, wallpapers, who

log = logging.getLogger("taffy.screen")

VIEWER_ID = "taffy-app"
_tickets: dict[str, tuple[str, bool, float]] = {}


_control_sockets: dict[str, int] = {}
_viewers: dict[str, int] = {}
_control_closed_at: dict[str, float] = {}
_taken_at: dict[str, float] = {}
RELEASE_GRACE_SEC = 120

USE_WINDOW_SEC = 90
_agent_used: dict[str, float] = {}


QUICK_READS = frozenset(("browser_navigate", "browser_snapshot", "browser_scroll", "browser_back", "browser_get_text",
                         "browser_get_images", "browser_vision", "browser_console"))
VISIT_SEC = 15
_visit_since: dict[str, float] = {}
_reading: set[str] = set()
SNAPSHOT_MAX = (720, 450)


def profile_home(bot_id: str) -> Path:
    return config.HERMES_HOME if bot_id == "default" else config.HERMES_HOME / "profiles" / bot_id


def _cli(bot_id: str, *args: str) -> tuple[bool, str]:
    cmd = [str(config.HERMES_BIN)] + ([] if bot_id == "default" else ["-p", bot_id]) + ["computer-use", "screen", *args]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, HERMES_HOME=str(config.HERMES_HOME)))
    out = "\n".join(l for l in (p.stdout + p.stderr).splitlines() if "SyntaxWarning" not in l and "WSL launcher" not in l)
    return p.returncode == 0, out.strip()


def status(bot_id: str) -> dict:
    from tools.bot_desktop import lease
    home = profile_home(bot_id)
    sock = home / "bot-desktop" / "rfb.sock"
    l = lease.get(str(home))
    return {"running": _socket_alive(sock), "holder": l.holder,
            "you_have_control": l.holder == lease.HUMAN and l.viewer_id == VIEWER_ID,
            "reason": l.reason or ""}


UNIX_TABLE = Path("/proc/net/unix")
_ACCEPTING = 0x10000


def _socket_alive(sock: Path) -> bool:
    ""






    if not sock.exists():
        return False
    try:
        lines = UNIX_TABLE.read_text().splitlines()[1:]
    except OSError:
        return True
    paths = {str(sock), os.path.realpath(sock)}
    for line in lines:
        parts = line.split()
        if len(parts) < 8 or parts[-1] not in paths:
            continue
        try:
            if int(parts[3], 16) & _ACCEPTING:
                return True
        except ValueError:
            continue
    return False


def _give_background(bot_id: str) -> None:
    ""
    try:
        wallpapers.ensure(bot_id)
    except Exception:
        log.warning("could not set %s's background", bot_id, exc_info=True)


def start(bot_id: str) -> tuple[bool, str]:
    _give_background(bot_id)
    return _cli(bot_id, "start")


def restart(bot_id: str) -> tuple[bool, str]:
    ""

    was_on = status(bot_id)["running"]
    hand_back(bot_id)
    ok, out = _cli(bot_id, "stop")
    if not ok and was_on:
        return False, out
    _give_background(bot_id)
    return _cli(bot_id, "start")


CURSOR_SOCKET = "zip-cursor.sock"


def hide_cursor(bot_id: str) -> None:
    ""

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(b'{"op": "hide"}', str(profile_home(bot_id) / "bot-desktop" / CURSOR_SOCKET))
    except OSError:
        pass


def take_control(bot_id: str) -> dict:
    from tools.bot_desktop import lease
    lease.acquire(VIEWER_ID, profile_key=str(profile_home(bot_id)), reason=f"{who.first()} took over in the app")
    hide_cursor(bot_id)
    _taken_at[bot_id] = time.time()
    _control_closed_at.pop(bot_id, None)
    return status(bot_id)


def copy_text(bot_id: str) -> str:
    ""
    from tools.bot_desktop import lease
    home = profile_home(bot_id)
    before = lease.get(str(home))
    if before.holder != lease.HUMAN or before.viewer_id != VIEWER_ID:
        raise PermissionError("Take over the computer before copying.")
    desktop = home / "bot-desktop"
    display = (desktop / "display").read_text().strip()
    if not display.isdigit():
        raise ValueError("The computer is not connected yet.")
    env = dict(os.environ, DISPLAY=":" + display, XAUTHORITY=str(desktop / "Xauthority"))
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("_xclipboard.py"))],
                            capture_output=True, timeout=5, env=env)
    after = lease.get(str(home))
    if (after.holder, after.viewer_id, after.since) != (before.holder, before.viewer_id, before.since):
        raise PermissionError("Take over the computer before copying.")
    if result.returncode:
        raise ValueError("Copy did not finish. Try again.")
    copied = json.loads(result.stdout)
    if copied.get("error"):
        raise ValueError(copied["error"])
    text = copied.get("text")
    if not isinstance(text, str) or len(text.encode("utf-8")) > 128 * 1024:
        raise ValueError("Copy less text and try again.")
    return text


def sweep_stale_control() -> None:
    ""
    from tools.bot_desktop import lease
    now = time.time()
    for b in list(config.BOTS_BY_ID):
        if _control_sockets.get(b, 0) > 0:
            continue
        l = lease.get(str(profile_home(b)))
        if l.holder != lease.HUMAN or l.viewer_id != VIEWER_ID:
            continue
        since = _control_closed_at.get(b) or _taken_at.get(b) or l.since or 0
        if now - since > RELEASE_GRACE_SEC:
            hand_back(b)

            from . import signinclosed
            try:
                signinclosed.abandoned(b)
            except Exception:
                log.exception("could not settle %s's abandoned sign-in", b)


IDLE_STOP_SEC = 4 * 3600
IDLE_STOP_LOW_MEMORY_SEC = 20 * 60
LOW_MEMORY_MB = 3000


def _available_mb() -> int:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 1 << 20


def sweep_idle_desktops(busy: set[str], waiting: set[str] = frozenset(),
                        still_busy=lambda b: False) -> list[str]:
    ""



    from tools.bot_desktop import lease
    low = _available_mb() < LOW_MEMORY_MB
    limit = IDLE_STOP_LOW_MEMORY_SEC if low else IDLE_STOP_SEC
    keep = set(busy) | (set() if low else set(waiting))
    stopped = []
    for b in list(config.BOTS_BY_ID):
        d = profile_home(b) / "bot-desktop"
        if b in keep or _viewers.get(b, 0) > 0 or not _socket_alive(d / "rfb.sock"):
            continue
        if lease.get(str(profile_home(b))).holder == lease.HUMAN:
            continue
        try:
            last = max((d / n).stat().st_mtime for n in ("activity", "env") if (d / n).exists())
        except ValueError:
            continue
        if time.time() - last < limit or still_busy(b):
            continue
        ok, out = _cli(b, "stop")
        if ok:
            stopped.append(b)
        else:
            log.warning("could not stop %s's idle desktop: %s", b, out[-200:])
    return stopped


def hand_back(bot_id: str) -> dict:
    from tools.bot_desktop import lease
    lease.release(VIEWER_ID, profile_key=str(profile_home(bot_id)))
    _taken_at.pop(bot_id, None)
    return status(bot_id)


def uses_computer(tool: str | None) -> bool:
    ""
    return bool(tool) and (tool.startswith(("computer", "browser")) or tool == "run_steps")


def note_agent_use(bot_id: str, tool: str = "") -> None:
    now = time.time()
    if tool in QUICK_READS:
        _reading.add(bot_id)
        if now - _visit_since.setdefault(bot_id, now) < VISIT_SEC:
            return
    _agent_used[bot_id] = now


def note_agent_done(bot_id: str) -> None:
    _reading.discard(bot_id)


def end_turn(bot_id: str) -> None:
    ""

    _agent_used.pop(bot_id, None)
    _visit_since.pop(bot_id, None)
    _reading.discard(bot_id)
    hide_cursor(bot_id)


def live_states(busy: set[str]) -> dict[str, str]:
    ""

    out = {}
    now = time.time()
    for b in _reading:
        if now - _visit_since.get(b, now) >= VISIT_SEC:
            _agent_used[b] = now
    for b, at in list(_agent_used.items()):
        if now - at > USE_WINDOW_SEC:
            _agent_used.pop(b, None)
        elif b in busy:
            out[b] = "working"
    for b in list(_taken_at):
        try:
            if status(b)["you_have_control"]:
                out[b] = "control"
            else:
                _taken_at.pop(b, None)
        except Exception:
            _taken_at.pop(b, None)
    return out


def snapshot(bot_id: str) -> tuple[bytes | None, str]:
    ""


    from tools.bot_desktop import lease
    home = profile_home(bot_id)
    key = str(home)

    def elsewhere() -> bool:
        l = lease.get(key)
        return l.holder == lease.HUMAN and l.viewer_id != VIEWER_ID

    if not _socket_alive(home / "bot-desktop" / "rfb.sock"):
        return None, "asleep"
    if elsewhere():
        return None, "private"
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from tools.bot_desktop.thumbnail import thumbnail_data_url
    token = set_hermes_home_override(home)
    try:
        url = thumbnail_data_url(SNAPSHOT_MAX, 68)
    finally:
        reset_hermes_home_override(token)
    if elsewhere():
        return None, "private"
    if not url or "," not in url:
        return None, "asleep"
    return base64.b64decode(url.split(",", 1)[1]), ""


def refused(greeting: bytes) -> bool:
    ""

    return greeting.startswith(b"RFB 003.003") and len(greeting) > 16 and greeting[12:16] == b"\0\0\0\0"


def mint_ticket(bot_id: str, control: bool) -> str:
    now = time.time()
    for t in [t for t, v in _tickets.items() if v[2] < now]:
        _tickets.pop(t, None)
    ticket = secrets.token_urlsafe(24)
    _tickets[ticket] = (bot_id, control, now + 30)
    return ticket


def redeem_ticket(ticket: str) -> tuple[str, bool] | None:
    v = _tickets.pop(ticket, None)
    if not v or v[2] < time.time():
        return None
    return v[0], v[1]


async def bridge(ws, bot_id: str, wants_control: bool) -> None:
    ""
    from tools.bot_desktop import lease
    from tools.bot_desktop.rfb_filter import RfbClientFilter

    home = profile_home(bot_id)
    sock = home / "bot-desktop" / "rfb.sock"
    try:
        reader, writer = await asyncio.open_unix_connection(str(sock))
    except OSError:
        log.warning("%s's screen viewer could not connect: the screen is not running", bot_id)
        await ws.close(code=4001, reason="screen is not running")
        return
    if wants_control:
        _control_sockets[bot_id] = _control_sockets.get(bot_id, 0) + 1
    _viewers[bot_id] = _viewers.get(bot_id, 0) + 1

    def may_input() -> bool:
        if not wants_control:
            return False
        l = lease.get(str(home))
        return l.holder == lease.HUMAN and l.viewer_id == VIEWER_ID

    gate = RfbClientFilter(may_input)
    activity = home / "bot-desktop" / "activity"

    async def to_screen():
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes") or (msg.get("text") or "").encode()
            out = gate.feed(data)
            if out:
                writer.write(out)
                await writer.drain()

    opening = bytearray()

    async def to_viewer():
        last = 0.0
        while True:
            chunk = await reader.read(64 * 1024)
            if not chunk:
                break
            if len(opening) < 256:
                opening.extend(chunk[:256 - len(opening)])
            await ws.send_bytes(chunk)
            if time.time() - last > 60:
                last = time.time()
                try:
                    activity.touch()
                except OSError:
                    pass

    tasks = [asyncio.create_task(to_screen()), asyncio.create_task(to_viewer())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if refused(bytes(opening)):
            reason = bytes(opening)[20:].decode("utf-8", "replace").strip()
            log.warning("%s's screen refused the viewer: %s", bot_id, reason or "no reason given")
        _viewers[bot_id] = max(0, _viewers.get(bot_id, 1) - 1)
        if wants_control:
            _control_sockets[bot_id] = max(0, _control_sockets.get(bot_id, 1) - 1)
            if _control_sockets[bot_id] == 0:
                _control_closed_at[bot_id] = time.time()
        writer.close()
        try:
            await ws.close()
        except Exception:
            pass
