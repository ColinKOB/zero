""









import base64
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from . import consent, gate, home, safe_files, sitepreview

FILE_MAX = 5 * 1024 * 1024
PAGES = {".html", ".htm"}

SCHEMA = {
    "name": "preview_screenshot",
    "description": (
        "See a page you are building, as a full-page picture only you see. target is an HTML file in your work "
        "folder, or the address of a site you serve on one of your own preview ports (http://127.0.0.1:<port>/...). "
        "Set phone true to see it at an iPhone's width. Use it after each change to a page, before you share it or "
        "call it done, instead of opening the file or 127.0.0.1 in your browser. The page cannot reach the internet "
        "while it is drawn, so outside fonts and pictures are missing."),
    "parameters": {"type": "object", "properties": {
        "target": {"type": "string", "description": "An .html file's path, or http://127.0.0.1:<your port>/path."},
        "phone": {"type": "boolean", "description": "Lay the page out at an iPhone's width (390 points)."},
    }, "required": ["target"]},
}

_ADDRESS = re.compile(r"(?i)^(?:https?://)?(?:127\.0\.0\.1|localhost)(?::\d+)?(?:[/?#].*)?$")


def _err(text: str) -> str:
    return json.dumps({"error": home.personalize(text)})


def own_ports(bot: str) -> frozenset:
    ""

    info = gate._container(bot)
    return frozenset(info["ports"]) if info is not None else home.preview_range()


def site_of(target: str, ports: frozenset) -> tuple[str, str] | str:
    ""
    u = target.strip()
    if not re.match(r"(?i)^https?://", u):
        u = "http://" + u
    try:
        parts = urlsplit(u)
        port = parts.port
    except ValueError:
        return "That address could not be read."
    if parts.scheme.lower() != "http" or (parts.hostname or "") not in ("127.0.0.1", "localhost") or port is None:
        return "Give one of your own preview ports on 127.0.0.1, as http://127.0.0.1:<port>/."
    if port not in ports:
        shown = f" Yours are {min(ports)} to {max(ports)}." if ports else ""
        return f"Port {port} is not one of your preview ports.{shown}"
    site = f"http://127.0.0.1:{port}/"
    rest = u[len(f"{parts.scheme}://{parts.netloc}"):] or "/"
    return site + rest.lstrip("/"), site


def _picture(path: Path, summary: dict) -> dict:
    data = path.read_bytes()
    try:
        from tools.vision_tools import _EMBED_MAX_DIMENSION, _resize_image_for_vision
        from tools.vision_tools_history_budget import resolve_embed_target_bytes
        url = _resize_image_for_vision(path, mime_type="image/jpeg", max_base64_bytes=resolve_embed_target_bytes(),
                                       max_dimension=_EMBED_MAX_DIMENSION, force_jpeg=True)
    except Exception:
        url = "data:image/jpeg;base64," + base64.b64encode(data).decode()
    text = json.dumps(summary)
    return {"_multimodal": True, "text_summary": text, "meta": {},
            "content": [{"type": "text", "text": text + "\n\nThe page is attached. Only you see it."},
                        {"type": "image_url", "image_url": {"url": url}}]}


def preview_screenshot(args: dict, **kwargs):
    target = str(args.get("target") or "").strip()
    phone = args.get("phone") is True
    if not target:
        return _err("Give target: an .html file in your work folder, or http://127.0.0.1:<your port>/.")
    bot = gate._bot()
    with tempfile.TemporaryDirectory(prefix="page-look-") as tmp:
        shot = Path(tmp) / "page.jpg"
        if _ADDRESS.match(target) or re.match(r"(?i)^https?://", target):
            found = site_of(target, own_ports(bot))
            if isinstance(found, str):
                return _err(found)
            address, site = found
            try:
                w, h = sitepreview.render(address, str(shot), site=site, phone=phone)
            except sitepreview.NoPreview as e:
                return _err(f"The page could not be drawn ({e}). Check that your server is running on that port.")
            shown = address
        else:
            from . import results
            real_home = str(consent.REAL_HOME)
            roots = safe_files.zip_roots(consent.REAL_HOME, bot)
            if Path(target).suffix.lower() not in PAGES:
                return _err("Give an .html file, or the address of your own site on 127.0.0.1.")
            try:
                real = safe_files.resolve(target, roots, real_home)
                with safe_files.open_file(real, roots, real_home) as f:
                    data = f.read(FILE_MAX + 1)
            except safe_files.Refused as e:
                return _err(f"Only pages in your own work folder or the shared folder can be drawn ({e}).")
            if len(data) > FILE_MAX:
                return _err("That page is larger than 5 MB.")
            text, _changed = results.bundle(data.decode("utf-8", "replace"), os.path.dirname(real), roots, real_home)
            page = Path(tmp) / "page.html"
            page.write_text(text)
            try:
                w, h = sitepreview.render(str(page), str(shot), phone=phone)
            except sitepreview.NoPreview as e:
                return _err(f"The page could not be drawn ({e}).")
            shown = target
        return _picture(shot, {"page": shown, "width": "phone" if phone else "desktop", "picture": f"{w}x{h}"})
