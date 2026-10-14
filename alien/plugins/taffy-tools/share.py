"""share_screen: a bot sends Colin a picture in its chat in the Taffy app.

Either a capture of the bot's own screen on alien, or an image file it made. The picture goes to
the hub's media folder and an outbox note; the hub puts it in the bot's timeline. Never while Colin
holds control of that screen (he may be typing a password there).
"""

import json
import os
import time
import uuid
from pathlib import Path

from . import consent, gate, home, safe_files

MAX_WIDTH = 1600
MEDIA = OUTBOX = None


def _hub_state() -> Path:
    return home.path("hub_state", consent.REAL_HOME)

SCHEMA = {
    "name": "share_screen",
    "description": (
        "Send Colin a picture in your chat in the app. With no path, captures your own screen on alien "
        "(your browser and desktop) as it looks right now. With a path, shares an image file you made "
        "(PNG or JPEG in your own work folder or the shared folder). Use it when Colin asks to see something, or when a picture "
        "explains a result faster than words (a page you found, an error, a finished design). Always add a "
        "one-sentence caption. Do not share screens showing passwords, codes or full account numbers."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "caption": {"type": "string", "description": "One plain sentence for Colin saying what the picture shows, for example \"My browser on the Walmart cart.\" Never name servers or machines."},
            "path": {"type": "string", "description": "Optional image file to share instead of a screen capture."},
        },
        "required": ["caption"],
    },
}


def _err(msg: str) -> str:
    return json.dumps({"error": msg})


def _home(bot: str) -> Path:
    lead = home.path("hermes_home", consent.REAL_HOME)
    return lead if bot == "default" else lead / "profiles" / bot


def _capture(bot: str):
    from PIL import ImageGrab
    env_file = _home(bot) / "bot-desktop" / "env"
    if not env_file.exists():
        raise RuntimeError("Your screen is not running. Open a page in your browser first.")
    env = {}
    for line in env_file.read_text().splitlines():
        line = line.removeprefix("export ").strip()
        if "=" in line:
            k, v = line.split("=", 1)
            env[k] = v.strip().strip('"').strip("'")
    display = env.get("DISPLAY")
    if not display:
        raise RuntimeError("Could not find your screen.")
    import subprocess, sys, tempfile
    from PIL import Image
    out = Path(tempfile.mkstemp(suffix=".png")[1])
    try:
        p = subprocess.run([sys.executable, "-c",
                            "import sys; from PIL import ImageGrab; ImageGrab.grab(xdisplay=sys.argv[1]).save(sys.argv[2])",
                            display, str(out)],
                           env=dict(os.environ, **{k: v for k, v in env.items() if k in ("DISPLAY", "XAUTHORITY")}),
                           capture_output=True, timeout=20)
        if p.returncode != 0:
            raise RuntimeError("The screen could not be captured.")
        img = Image.open(out)
        img.load()
        return img
    finally:
        out.unlink(missing_ok=True)


def share(args: dict, session_id: str = "") -> str:
    from PIL import Image
    bot = gate._bot()
    caption = " ".join(str(args.get("caption") or "").split())[:300]
    if not caption:
        return _err("Add a one-sentence caption that says what the picture shows.")
    path = str(args.get("path") or "").strip()
    try:
        from tools.bot_desktop import lease
        l = lease.get(str(_home(bot)))
        if l.holder == lease.HUMAN:
            return _err(home.personalize("Colin is using your screen right now, so it cannot be captured. Try again "
                                         "after he hands it back."))
    except Exception:
        pass
    try:
        if path:
            try:
                f = safe_files.open_file(path, safe_files.zip_roots(consent.REAL_HOME, bot), str(consent.REAL_HOME))
            except safe_files.Refused as e:
                return _err(f"Only images in your own work folder or the shared folder can be shared ({e}).")
            with f:
                img = Image.open(f)
                img.load()
        else:
            img = _capture(bot)
    except Exception as e:
        return _err(f"Could not get the picture: {e}")

    img = img.convert("RGB")
    if img.width > MAX_WIDTH:
        img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)))
    media, outbox = MEDIA or _hub_state() / "media", OUTBOX or _hub_state() / "outbox"
    media.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    media_id = uuid.uuid4().hex
    img.save(media / f"{media_id}.jpg", quality=82)
    os.chmod(media / f"{media_id}.jpg", 0o600)
    note = {"bot": bot, "media": media_id, "caption": caption, "width": img.width, "height": img.height,
            "task_id": os.environ.get("HERMES_KANBAN_TASK") or None, "at": time.time()}
    tmp = outbox / f".{media_id}.json"
    tmp.write_text(json.dumps(note))
    tmp.replace(outbox / f"{int(time.time() * 1000)}-{media_id}.json")
    return json.dumps({"ok": True, "shared": home.personalize("The picture is in your chat in Colin's app.")})
