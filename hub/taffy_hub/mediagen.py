""







import base64
import json
import os
import re
import time
import uuid
from io import BytesIO
from pathlib import Path

import httpx

from . import config, home

TIMEOUT_SEC = 90
API = "https://api.openai.com/v1"
NOT_SET_UP = "Pictures aren't set up on this home yet."
_BOT = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class MediaError(RuntimeError):
    ""


def _file_env(file: str) -> dict:
    out = {}
    try:
        text = Path(file).expanduser().read_text()
    except OSError:
        return {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _pick(name: str, file_vals: dict, default: str = "") -> str:
    for key in (f"ZERO_{name}", name):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value
    return str(file_vals.get(name) or "").strip() or default


def settings() -> dict | None:
    ""
    spec = home.get("integrations.media")
    file_vals = _file_env(str(spec.get("env_file"))) if isinstance(spec, dict) and spec.get("env_file") else {}
    key = _pick("OPENAI_API_KEY", file_vals)
    if not key:
        return None
    api = _pick("OPENAI_BASE_URL", file_vals, API).rstrip("/")
    return {"key": key, "api": api or API,
            "image_model": _pick("OPENAI_IMAGE_MODEL", file_vals, "gpt-image-1")}


def _send(method: str, url: str, key: str, body: dict | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {key}"}
    return httpx.request(method, url, headers=headers, json=body, timeout=TIMEOUT_SEC)


def _prompt(args: dict) -> str:
    text = args.get("prompt")
    if not isinstance(text, str):
        raise MediaError("prompt must be text.")
    text = " ".join(text.split())
    if not text:
        raise MediaError("prompt is empty.")
    if len(text) > 1000:
        raise MediaError("prompt is too long.")
    return text


def _fail(response: httpx.Response, key: str, what: str) -> None:
    if response.status_code in (401, 403):
        raise MediaError(f"{what} didn't accept the key. Check the key on the server.")
    message = ""
    try:
        err = response.json().get("error")
        if isinstance(err, dict):
            message = str(err.get("message") or "")
    except ValueError:
        message = ""
    if key and key in message:
        message = ""
    message = " ".join(message.split())[:240]
    raise MediaError(f"{what} didn't work just now." + (f" {message}" if message else ""))


def _jpeg(raw: bytes) -> bytes:
    if len(raw) > 12_000_000:
        raise MediaError("The picture was too large to save.")
    from PIL import Image
    img = Image.open(BytesIO(raw)).convert("RGB")
    if max(img.size) > 2048:
        img.thumbnail((2048, 2048))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _caption(prompt: str) -> str:
    return prompt[:80]


def _share(bot_id: str, jpeg: bytes, caption: str) -> str:
    ""
    media, outbox = config.HUB_DIR / "media", config.HUB_DIR / "outbox"
    media.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    media_id = uuid.uuid4().hex
    dest = media / f"{media_id}.jpg"
    dest.write_bytes(jpeg)
    os.chmod(dest, 0o600)
    from PIL import Image
    with Image.open(dest) as img:
        width, height = img.size
    note = {"bot": bot_id, "media": media_id, "caption": caption, "width": width, "height": height, "at": time.time()}
    tmp = outbox / f".{media_id}.json"
    tmp.write_text(json.dumps(note))
    tmp.replace(outbox / f"{int(time.time() * 1000)}-{media_id}.json")
    return media_id


def image(bot_id: str, args: dict, cfg: dict) -> dict:
    prompt = _prompt(args)
    url = cfg["api"] + "/images/generations"
    body = {"model": cfg["image_model"], "prompt": prompt, "size": "1024x1024", "n": 1}
    try:
        r = _send("POST", url, cfg["key"], body)
    except httpx.HTTPError:
        raise MediaError("The picture service didn't answer just now.") from None
    if r.status_code >= 400:
        _fail(r, cfg["key"], "The picture service")
    try:
        row = (r.json().get("data") or [{}])[0]
    except ValueError:
        row = {}
    b64 = row.get("b64_json") if isinstance(row, dict) else None
    if not isinstance(b64, str) or not b64:
        raise MediaError("The picture service didn't return a picture.")
    try:
        raw = base64.b64decode(b64, validate=False)
    except (ValueError, TypeError):
        raise MediaError("The picture service didn't return a picture.") from None
    media_id = _share(bot_id, _jpeg(raw), _caption(prompt))
    return {"made": "image", "media": media_id,
            "note": "The picture is in the chat. Say one short line about it."}


def run(op: str, bot_id: str, args: dict) -> dict:
    if op != "image":
        raise MediaError("action must be image. For video, use the make-video skill and share_result.")
    if not _BOT.match(bot_id or ""):
        raise MediaError("That isn't a Zip on this home.")
    cfg = settings()
    if not cfg:
        raise MediaError(NOT_SET_UP)
    return image(bot_id, args if isinstance(args, dict) else {}, cfg)
