"""Tools for using Colin's Mac, only when he explicitly asks and after he approves the task on the Mac.

The hub (taffy_hub/mac.py) holds the grant and the Mac's connection; these tools just call it on
127.0.0.1 with the internal key. Every rule that matters is enforced there, not here.
"""

import base64
import json
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import consent, gate, home


def hub() -> str:
    """The hub's internal address (ports.hub in the home's settings)."""
    return home.hub_url()


def key_file() -> Path:
    return home.path("hub_state", consent.REAL_HOME) / "internal-key"

RULES = (
    "Your own computer on alien is the default for browsing and work. Use Colin's Mac only when he explicitly "
    "asks for something on his Mac or his computer in this conversation. Call mac_start first with the task and "
    "his own words; he approves it on the Mac. You work beside him with your own cursor: prefer mac_ui and "
    "mac_press, which never touch his mouse; mac_click and typing go to the app you are working in, not his. "
    "Only use his real mouse (mac_click with real true) after he says yes to 'Can I use your mouse for a moment?'. "
    "Never type passwords (sign-ins use the saved-logins flow), and anything that sends, buys, deletes or "
    "changes his accounts still needs his confirmation in chat. Call mac_done when finished. When searching his "
    "files, never filter out /Library/: iCloud Drive is ~/Library/Mobile Documents/com~apple~CloudDocs and Pages, "
    "Numbers and Keynote documents are in ~/Library/Mobile Documents/com~apple~Pages (and ~Numbers, ~Keynote). "
    "Don't hide errors with 2>/dev/null. If a result has folders_macos_blocks, say plainly that macOS hasn't let "
    "Zero into those folders and how he can allow it, instead of saying the file isn't on his Mac. "
    "Finding files is fastest with mac_run and mdfind (mdfind -name halloween) rather than find in iCloud Drive. "
    "To get several clicks and fields done at once, use mac_steps with plain steps; it stops and shows the screen "
    "when it is not sure. If mac_ui says an app has no window on this screen, mac_open it with front true. "
    "Colin can allow a task on the Mac or on his phone."
)

SCHEMAS = {
    "mac_start": {
        "name": "mac_start",
        "description": "Ask Colin to let you use his Mac for one task. " + RULES,
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string", "description": "What you will do on the Mac, in one plain sentence."},
            "colin_said": {"type": "string", "description": "Colin's own words asking for it, quoted exactly."},
        }, "required": ["task", "colin_said"]},
    },
    "mac_open": {
        "name": "mac_open",
        "description": "Open a web address, a file path (~ allowed), an app by name, or a System Settings pane "
                       "(x-apple.systempreferences: links, for example "
                       "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility) on Colin's Mac. "
                       "It opens in the background; set front true to bring the app onto the screen when its windows "
                       "are full screen or in another Space. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string"},
            "front": {"type": "boolean", "description": "Bring the app in front of what Colin sees. Only when needed."},
        }, "required": ["target"]},
    },
    "mac_run": {
        "name": "mac_run",
        "description": "Run a zsh command on Colin's Mac in his home folder and get its output. Up to 120 s by "
                       "default. Needs mac_start first. sudo, disk erasing, keychain and key files are blocked.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "number", "description": "Seconds, at most 600."},
        }, "required": ["command"]},
    },
    "mac_screenshot": {
        "name": "mac_screenshot",
        "description": "See Colin's Mac screen. Returns the image plus the scale from image pixels to screen points "
                       "for clicking. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {}},
    },
    "mac_ui": {
        "name": "mac_ui",
        "description": "List the buttons, fields, switches, rows, links and text in the app you are working in on "
                       "Colin's Mac, each with its center in screen points, a field's text and a switch's on or off. "
                       "Faster and more exact than a screenshot. Give query to list only lines with that word. "
                       "Needs mac_start first.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Only lines containing this word, to keep the list short."},
        }},
    },
    "mac_press": {
        "name": "mac_press",
        "description": "Press an element without the mouse, by its number from the last mac_ui list or by its name "
                       "('Privacy & Security', 'Submit'). Rows in lists and sidebars are selected. Give text to fill a "
                       "field, or to choose that option in a pop-up menu. Action 'menu' opens its menu. It says what "
                       "the element reads afterwards. The best way to act on Colin's Mac. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {
            "element": {"type": "integer"},
            "name": {"type": "string", "description": "The element's words, when you don't have its number."},
            "text": {"type": "string"},
            "action": {"type": "string", "enum": ["press", "menu"]},
        }},
    },
    "mac_click": {
        "name": "mac_click",
        "description": "Click at screen points on Colin's Mac with your own cursor; the click goes to the app under that "
                       "point without moving his mouse. Use count 2 for a double click, button 'right' for a right click, "
                       "or to_x and to_y to drag. Set real true only after Colin agreed to lend you his mouse. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "number"}, "y": {"type": "number"},
            "button": {"type": "string", "enum": ["left", "right"]},
            "count": {"type": "integer"},
            "to_x": {"type": "number"}, "to_y": {"type": "number"},
            "real": {"type": "boolean"},
        }, "required": ["x", "y"]},
    },
    "mac_type": {
        "name": "mac_type",
        "description": "Type text into whatever is focused on Colin's Mac. Refused in password fields. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    "mac_key": {
        "name": "mac_key",
        "description": "Press a key or shortcut on Colin's Mac, such as 'return', 'cmd+l' or 'cmd+shift+t'. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]},
    },
    "mac_scroll": {
        "name": "mac_scroll",
        "description": "Scroll on Colin's Mac at a point. dy is lines: negative scrolls down, positive scrolls up. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "number"}, "y": {"type": "number"}, "dy": {"type": "integer"},
        }, "required": ["dy"]},
    },
    "mac_steps": {
        "name": "mac_steps",
        "description": "Do a few plain steps on Colin's Mac in one call, without the mouse, each found by name on the "
                       "screen as it is then: \"click Privacy & Security\", \"type 'Sam' into Full name\", "
                       "\"choose Green in Favorite color\", \"press return\". Up to 15 steps. It stops at the first step "
                       "it is not sure about and shows the screen's controls. Never use it to send, buy, delete or submit "
                       "unless Colin confirmed that. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {
            "steps": {"type": "array", "items": {"type": "string"}},
        }, "required": ["steps"]},
    },
    "mac_done": {
        "name": "mac_done",
        "description": "Hand Colin's Mac back as soon as the task is finished.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _call(op: str, args: dict, timeout: float = 60, session_id: str = "", area: str = "mac") -> dict:
    try:
        key = key_file().read_text().strip()
    except OSError:
        return {"ok": False, "error": "The hub is not set up for Mac use yet."}
    req = urllib.request.Request(f"{hub()}{area}/{op}", method="POST",
                                 data=json.dumps({"bot_id": gate._bot(), "session_id": session_id,
                                                  "args": args}).encode(),
                                 headers={"content-type": "application/json", "x-taffy-internal": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"The hub refused that ({e.code})."}
    except Exception as e:
        return {"ok": False, "error": f"Could not reach the hub: {e}"}


def _screenshot_result(result: dict):
    """Attach the screenshot as an image the model can see, like the browser tools do."""
    data = base64.b64decode(result.pop("jpeg_base64", "") or b"")
    summary = json.dumps({"screenshot": "attached", **result})
    with tempfile.TemporaryDirectory(prefix="mac-screen-") as tmp:
        path = Path(tmp) / "screen.jpg"
        path.touch(mode=0o600)
        path.write_bytes(data)
        try:
            from tools.vision_tools import _EMBED_MAX_DIMENSION, _resize_image_for_vision
            from tools.vision_tools_history_budget import resolve_embed_target_bytes
            url = _resize_image_for_vision(path, mime_type="image/jpeg", max_base64_bytes=resolve_embed_target_bytes(),
                                           max_dimension=_EMBED_MAX_DIMENSION, force_jpeg=True)
        except Exception:
            url = "data:image/jpeg;base64," + base64.b64encode(data).decode()
    return {"_multimodal": True, "text_summary": summary, "meta": {},
            "content": [{"type": "text", "text": summary + home.personalize("\n\nColin's Mac screen is attached.")},
                        {"type": "image_url", "image_url": {"url": url}}]}


def handler(op: str):
    timeouts = {"start": 150, "run": 700, "screenshot": 45, "steps": 100}

    def run(args: dict, **kwargs):
        reply = _call(op, args or {}, timeouts.get(op, 60), session_id=kwargs.get("session_id") or "")
        if not reply.get("ok"):
            return json.dumps({"error": reply.get("error") or "That did not work."})
        result = reply.get("result")
        if op == "screenshot" and isinstance(result, dict) and result.get("jpeg_base64"):
            return _screenshot_result(result)
        return json.dumps({"result": result}, ensure_ascii=False)
    return run


TOOLS = {name: name.removeprefix("mac_") for name in SCHEMAS}


def schema(name: str) -> dict:
    """A Mac tool's schema for this home. Colin's home keeps `colin_said` exactly as before; every other home's
    mac_start asks for `person_said` instead (the hub reads either)."""
    s = json.loads(json.dumps(SCHEMAS[name]))
    if name == "mac_start" and not home.is_colin():
        params = s["parameters"]
        params["properties"]["person_said"] = params["properties"].pop("colin_said")
        params["required"] = ["person_said" if r == "colin_said" else r for r in params["required"]]
    return s
