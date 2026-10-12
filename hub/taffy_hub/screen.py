"""Each bot's own desktop on alien (Hermes Bot Screen), watched and taken over from the app.

The screen is a TigerVNC RFB Unix socket under the bot's HERMES_HOME. The hub splices it into a
WebSocket for the noVNC page in the app, using Hermes's own RFB input filter and control lease, so a
watcher can never type into the bot's screen and the bot is locked out while Colin holds control.
Requires the hub to run in Hermes's Python environment.
"""

import asyncio
import base64
import logging
import os
import secrets
import subprocess
import time
from pathlib import Path

from . import config, who

log = logging.getLogger("taffy.screen")

VIEWER_ID = "taffy-app"
_tickets: dict[str, tuple[str, bool, float]] = {}  # ticket -> (bot, wants_control, expires_at)
# Open control sockets per bot, and when the last one closed. The watcher hands control back to a
# bot whose takeover has had no open socket for RELEASE_GRACE_SEC, so a killed app never strands it.
_control_sockets: dict[str, int] = {}
_viewers: dict[str, int] = {}      # every open screen view, watching or in control
_control_closed_at: dict[str, float] = {}
_taken_at: dict[str, float] = {}
RELEASE_GRACE_SEC = 120
# A Zip counts as using its computer for this long after its last computer or browser step in a chat turn.
USE_WINDOW_SEC = 90
_agent_used: dict[str, float] = {}
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


UNIX_TABLE = Path("/proc/net/unix")   # the kernel's list of Unix sockets (tests point it at a fixture)
_ACCEPTING = 0x10000                   # __SO_ACCEPTCON: the socket is listening


def _socket_alive(sock: Path) -> bool:
    """Whether the screen's server is listening on its socket, read from the kernel's socket table.

    Never by connecting: Xvnc counts a connection that closes before the VNC handshake as a failed login, and
    after five of them it refuses every connection on that socket with "Too many security failures", for a
    wait that doubles each time something knocks again (Oct 9: the Computer card's status and picture checks,
    every 2.5 seconds on each device, kept Zero's screen refused from the moment it woke, so Take Over could
    never show it)."""
    if not sock.exists():
        return False
    try:
        lines = UNIX_TABLE.read_text().splitlines()[1:]
    except OSError:
        return True   # no socket table (not Linux): the socket file is the best sign there is
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


def start(bot_id: str) -> tuple[bool, str]:
    return _cli(bot_id, "start")


def take_control(bot_id: str) -> dict:
    from tools.bot_desktop import lease
    lease.acquire(VIEWER_ID, profile_key=str(profile_home(bot_id)), reason=f"{who.first()} took over in the Taffy app")
    _taken_at[bot_id] = time.time()
    _control_closed_at.pop(bot_id, None)
    return status(bot_id)


def sweep_stale_control() -> None:
    """Hands control back to any bot whose takeover has had no open viewer for a while."""
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


IDLE_STOP_SEC = 4 * 3600          # a running desktop keeps the bot's browser signed in, so it stays a while
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
    """Stops bot desktops nobody has used for a while. Hermes only does this inside its desktop app, so a
    desktop a board task started would otherwise run until the next reboot. Never touches a bot that is
    working (busy), a screen someone is watching, or one Colin has taken over. A bot whose task waits on
    Colin (a sign-in page, a filled cart) keeps its screen unless memory is low. Returns the bots stopped."""
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
        if time.time() - last < limit or still_busy(b):   # a turn may have started since busy was read
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
    """Whether a Hermes tool drives the bot's screen (its computer, its browser, or clicking through steps)."""
    return bool(tool) and (tool.startswith(("computer", "browser")) or tool == "run_steps")


def note_agent_use(bot_id: str) -> None:
    _agent_used[bot_id] = time.time()


def live_states(busy: set[str]) -> dict[str, str]:
    """Which computers the app's chats should show a card for right now: "working" while a Zip in a turn is
    using its screen, "control" while Colin holds it. Only what is already true; nothing here changes a lease."""
    out = {}
    now = time.time()
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
    """One JPEG of the bot's screen for the card in the chat: (picture, "") or (None, "asleep" | "private").
    Read-only, like Hermes's own thumbnail: it never touches the lease. It is refused while someone other than
    this app holds control, and dropped if that happened during the grab (the frame could show what they type)."""
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
    token = set_hermes_home_override(home)   # Hermes reads the profile's screen from its own home
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
    """Whether the screen turned a viewer away: Xvnc answers with protocol 3.3 and a reason, such as "Too many
    security failures" after connections that closed before the handshake (see _socket_alive)."""
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
    """Splices the bot's RFB socket and a WebSocket. Input passes only while Colin holds the lease."""
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

    opening = bytearray()   # the screen's first words, kept to tell a refusal apart from a normal start

    async def to_viewer():
        last = 0.0
        while True:
            chunk = await reader.read(64 * 1024)
            if not chunk:
                break
            if len(opening) < 256:
                opening.extend(chunk[:256 - len(opening)])
            await ws.send_bytes(chunk)
            if time.time() - last > 60:  # an open viewer counts as use, so the idle stop leaves it alone
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
