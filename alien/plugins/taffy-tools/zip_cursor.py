""

















import contextlib
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

HELPER = Path(__file__).with_name("zip_cursor_overlay.py")
SOCKET_NAME = "zip-cursor.sock"
LEAD_COLOR = "#F2711C"
SHOWN_SEC = 25.0
ORIGIN_SEC = 15.0
ACK_SEC = 0.4
START_SEC = 5.0


ORIGIN_JS = ("JSON.stringify([screenX, screenY, outerWidth - innerWidth, outerHeight - innerHeight, innerWidth, "
             "innerHeight, devicePixelRatio || 1])")

CU_POINTS = {"click", "double_click", "right_click", "middle_click", "drag", "scroll"}
CU_PRESSES = {"click", "double_click", "right_click", "middle_click", "drag"}

_shown: dict[str, float] = {}
_origins: dict[str, tuple] = {}
_captures: dict[str, dict] = {}
_starting: set[str] = set()
_lock = threading.Lock()




def _screen():
    ""
    from tools.bot_desktop import runtime
    if runtime.sandbox_screen_running():
        return None
    env = runtime.published_env()
    if not env.get("DISPLAY"):
        return None
    return runtime.state_dir() / SOCKET_NAME, env


def _human_has_it() -> bool:
    try:
        from tools.bot_desktop import lease
        return lease.get().holder == lease.HUMAN
    except Exception:
        return False


def _look() -> tuple[str, str]:
    ""
    from . import gate
    bot = gate._bot()
    color = LEAD_COLOR if bot == "default" else ""
    state = gate._hub_state()
    try:
        look = json.loads((state / "bot-looks.json").read_text()).get(bot) or {}
        color = str(look.get("color") or "") or color
    except (OSError, ValueError, AttributeError):
        pass
    if not color:
        try:
            for e in json.loads((state / "bots.json").read_text()):
                if isinstance(e, dict) and e.get("id") == bot:
                    color = str(e.get("color") or "")
                    break
        except (OSError, ValueError, TypeError):
            pass
    return color or LEAD_COLOR, gate._bot_name(bot)


def _deliver(path: Path, data: bytes) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(data, str(path))
        return True
    except OSError:
        return False


def _start(path: Path, env: dict, data: bytes) -> None:
    ""
    key = str(path)
    with _lock:
        if key in _starting:
            return
        _starting.add(key)

    def run():
        try:
            child_env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "USER")}
            child_env.update({k: env[k] for k in ("DISPLAY", "XAUTHORITY") if env.get(k)})
            subprocess.Popen([sys.executable, str(HELPER), key], env=child_env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                             close_fds=True)
            deadline = time.monotonic() + START_SEC
            while time.monotonic() < deadline:
                time.sleep(0.1)
                if _deliver(path, data):
                    return
        except Exception:
            log.debug("the screen cursor did not start", exc_info=True)
        finally:
            with _lock:
                _starting.discard(key)
    threading.Thread(target=run, name="zip-cursor-start", daemon=True).start()


def show(x: float | None = None, y: float | None = None, press: bool = False, **where) -> None:
    ""

    screen = _screen()
    if screen is None:
        return
    path, env = screen
    if _human_has_it():
        hide()
        return
    color, name = _look()
    msg = {"op": "show", "press": bool(press), "color": color, "name": name, **where}
    if x is not None and y is not None:
        msg.update(x=int(round(x)), y=int(round(y)))
    data = json.dumps(msg).encode()
    _shown[str(path)] = time.monotonic()
    if not _deliver(path, data):
        _start(path, env, data)


def hide(wait: bool = False) -> None:
    ""
    try:
        screen = _screen()
    except Exception:
        return
    if screen is None:
        return
    path = str(screen[0])
    if time.monotonic() - _shown.get(path, -SHOWN_SEC) > SHOWN_SEC:
        return
    _shown.pop(path, None)
    if not wait:
        _deliver(Path(path), b'{"op": "hide"}')
        return
    name = f"zip-cursor-{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}"
    own = None
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        try:
            try:
                s.bind("\0" + name)
            except OSError:


                own = os.path.join("/tmp", f"zc-{os.getpid()}-{time.monotonic_ns() % 10**9}")
                s.bind(own)
            s.settimeout(ACK_SEC)
            s.sendto(b'{"op": "hide", "ack": true}', path)
            s.recv(16)
        except OSError:
            _deliver(Path(path), b'{"op": "hide"}')
        finally:
            if own:
                with contextlib.suppress(OSError):
                    os.unlink(own)


def safely(fn, *args, **kw) -> None:
    try:
        fn(*args, **kw)
    except Exception:
        log.debug("screen cursor step skipped", exc_info=True)




def _origin(page, task: str):
    ""
    hit = _origins.get(task)
    if hit and time.monotonic() - hit[0] < ORIGIN_SEC:
        return hit[1]
    raw = page._run("eval", [ORIGIN_JS]).get("result")
    try:
        sx, sy, dw, dh, w, h, scale = (float(v) for v in json.loads(raw))
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None


    out = (sx + dw / 2, sy + dh, w, h, scale if scale > 0 else 1.0)
    if len(_origins) > 64:
        _origins.clear()
    _origins[task] = (time.monotonic(), out)
    return out


def at_element(task: str, ref: str, press: bool = True) -> None:
    ""
    if _screen() is None or _human_has_it():
        return
    from . import link_click
    page = link_click.Page(task)
    box = page._run("get", ["box", link_click.at(ref)])
    box = box.get("box") if isinstance(box.get("box"), dict) else box
    try:
        cx = float(box["x"]) + float(box["width"]) / 2
        cy = float(box["y"]) + float(box["height"]) / 2
    except (KeyError, TypeError, ValueError):
        return
    origin = _origin(page, task)
    if origin is None:
        return
    left, top, w, h, scale = origin
    if not (0 <= cx <= w and 0 <= cy <= h):
        return
    show((left + cx) * scale, (top + cy) * scale, press=press)


def at_page(task: str) -> None:
    ""
    if _screen() is None or _human_has_it():
        return
    from . import link_click
    origin = _origin(link_click.Page(task), task)
    if origin is not None:
        left, top, w, h, scale = origin
        show((left + w / 2) * scale, (top + h / 2) * scale)


def before_browser_step(tool_name: str, args, task: str) -> None:
    ""
    if not isinstance(args, dict):
        return
    if tool_name in ("browser_click", "browser_type") and args.get("ref"):
        at_element(task or "default", str(args["ref"]), press=True)
    elif tool_name == "browser_scroll":
        at_page(task or "default")
    elif tool_name == "browser_press":
        show()


def browser_middleware(tool_name: str = "", args=None, next_call=None, **kw):
    ""

    if tool_name == "computer_use" and isinstance(args, dict):
        return around_computer_use(args, next_call, str(kw.get("session_id") or ""))
    if tool_name in ("browser_click", "browser_type", "browser_scroll", "browser_press"):
        safely(before_browser_step, tool_name, args, str(kw.get("task_id") or "default"))
    return next_call(args)




_SIZE = re.compile(r"capture mode=\w+ (\d+)x(\d+)")
_ELEMENT = re.compile(r"#(\d+) .*? @ \((-?\d+), (-?\d+), (\d+), (\d+)\)")


def _capture_text(out) -> str:
    if isinstance(out, dict):
        text = out.get("text_summary")
        if isinstance(text, str):
            return text
        parts = out.get("content")
        if isinstance(parts, list):
            return "\n".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
        return ""
    if isinstance(out, str):
        try:
            data = json.loads(out)
        except ValueError:
            return out
        if isinstance(data, dict) and isinstance(data.get("summary"), str):
            return data["summary"]
    return out if isinstance(out, str) else ""


def _learn(session: str, out) -> None:
    ""
    text = _capture_text(out)
    size = _SIZE.search(text)
    if not size:
        return
    elements = {int(m.group(1)): tuple(int(m.group(i)) for i in range(2, 6)) for m in _ELEMENT.finditer(text)}
    if len(_captures) > 32:
        _captures.clear()
    _captures[session] = {"shot": (int(size.group(1)), int(size.group(2))), "elements": elements}


def _active_window(session: str):
    try:
        from tools.computer_use import tool as cu
        backend = cu._backends.get(cu._scoped_sid(session))
        wid = getattr(backend, "_active_window_id", None)
        return int(wid) if wid else None
    except Exception:
        return None


def _cu_spot(action: str, args: dict, seen: dict, window) -> dict | None:
    ""
    element = args.get("to_element") if action == "drag" else args.get("element")
    point = args.get("to_coordinate") if action == "drag" else args.get("coordinate")
    box = (seen.get("elements") or {}).get(element) if isinstance(element, int) else None
    if box and box[2] > 0 and box[3] > 0:
        return {"x": box[0] + box[2] / 2, "y": box[1] + box[3] / 2}
    shot = seen.get("shot") or (0, 0)
    if window and isinstance(point, (list, tuple)) and len(point) == 2 and None not in point:
        return {"window": window, "x": float(point[0]), "y": float(point[1]), "shot_w": shot[0], "shot_h": shot[1]}
    if window and action == "scroll" and shot[0] and shot[1]:
        return {"window": window, "x": shot[0] / 2, "y": shot[1] / 2, "shot_w": shot[0], "shot_h": shot[1]}
    return None


def around_computer_use(args: dict, next_call, session: str):
    action = str(args.get("action") or "").strip().lower()
    seen = dict(_captures.get(session) or {})
    window = _active_window(session) if action in CU_POINTS else None
    was_shown = False
    try:
        screen = _screen()
        was_shown = screen is not None and time.monotonic() - _shown.get(str(screen[0]), -SHOWN_SEC) <= SHOWN_SEC
        if was_shown:
            hide(wait=True)
    except Exception:
        log.debug("screen cursor hide skipped", exc_info=True)
    out = next_call(args)
    try:
        spot = _cu_spot(action, args, seen, window) if action in CU_POINTS else None
        _learn(session, out)
        if spot is not None:
            show(press=action in CU_PRESSES, **spot)
        elif was_shown or action in ("type", "key", "set_value"):
            show()
    except Exception:
        log.debug("screen cursor step skipped", exc_info=True)
    return out


def turn_ended(**_kw) -> None:
    safely(hide)
