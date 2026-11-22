""












import colorsys
import logging
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config

log = logging.getLogger("taffy.wallpapers")

ART_DIR = Path(__file__).parent / "wallpaper_art"
CONF = Path("xdg") / "xfce4" / "xfconf" / "xfce-perchannel-xml" / "xfce4-desktop.xml"
IMAGE_NAME = "wallpaper.jpg"
MONITOR = "monitorVNC-0"
ZOOMED = "5"
VERTICAL_GRADIENT = "2"
SOLID = "0"
NO_IMAGE = "0"


def art_for(bot_id: str) -> Path | None:
    p = ART_DIR / f"{bot_id}.jpg"
    return p if p.is_file() else None


def gradient(color: str) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    ""

    m = re.fullmatch(r"#?([0-9A-Fa-f]{6})", (color or "").strip())
    r, g, b = (int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4)) if m else (0.39, 0.45, 0.55)
    h, _, s = colorsys.rgb_to_hls(r, g, b)
    top = colorsys.hls_to_rgb(h, 0.22, min(s, 0.55))
    bottom = colorsys.hls_to_rgb(h, 0.09, min(s, 0.45))
    return top, bottom


def _rgba(parent: ET.Element, name: str, rgb: tuple[float, float, float]) -> None:
    for p in parent.findall("property"):
        if p.get("name") == name:
            parent.remove(p)
    arr = ET.SubElement(parent, "property", {"name": name, "type": "array"})
    for v in (*rgb, 1.0):
        ET.SubElement(arr, "value", {"type": "double", "value": f"{v:.3f}"})


def _prop(parent: ET.Element, name: str, ptype: str = "empty", value: str | None = None) -> ET.Element:
    for p in parent.findall("property"):
        if p.get("name") == name:
            if value is not None:
                p.set("type", ptype)
                p.set("value", value)
            return p
    p = ET.SubElement(parent, "property", {"name": name, "type": ptype})
    if value is not None:
        p.set("value", value)
    return p


def desktop_xml(existing: str | None, image: str | None, color: str) -> str:
    ""
    root = ET.fromstring(existing) if existing else ET.Element("channel", {"name": "xfce4-desktop", "version": "1.0"})
    node = root
    for name in ("backdrop", "screen0", MONITOR, "workspace0"):
        node = _prop(node, name)
    if image:
        _prop(node, "color-style", "int", SOLID)
        _prop(node, "image-style", "int", ZOOMED)
        _prop(node, "last-image", "string", image)
        _rgba(node, "rgba1", gradient(color)[1])
    else:
        top, bottom = gradient(color)
        _prop(node, "color-style", "int", VERTICAL_GRADIENT)
        _prop(node, "image-style", "int", NO_IMAGE)
        _rgba(node, "rgba1", top)
        _rgba(node, "rgba2", bottom)
        for p in node.findall("property"):
            if p.get("name") == "last-image":
                node.remove(p)
    icons = _prop(root, "desktop-icons")
    _prop(icons, "style", "int", "0")
    ET.indent(root, "  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def _write(path: Path, data: bytes) -> bool:
    ""
    try:
        if path.read_bytes() == data:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return True


def ensure(bot_id: str) -> str:
    ""

    from . import screen
    home = screen.profile_home(bot_id)
    desk = home / "bot-desktop"
    if not home.is_dir():
        return "no profile"
    if screen._socket_alive(desk / "rfb.sock"):
        return "running"
    bot = config.BOTS_BY_ID.get(bot_id)
    color = bot.color if bot else ""
    art = art_for(bot_id)
    image = desk / IMAGE_NAME
    changed = False
    if art:
        changed |= _write(image, art.read_bytes())
    elif image.exists():
        image.unlink()
        changed = True
    conf = desk / CONF
    old = conf.read_text(encoding="utf-8") if conf.exists() else None
    try:
        new = desktop_xml(old, str(image) if art else None, color)
    except ET.ParseError:
        new = desktop_xml(None, str(image) if art else None, color)
    changed |= _write(conf, new.encode("utf-8"))
    return "set" if changed else "current"


def ensure_all() -> dict[str, str]:
    ""
    out = {}
    for b in list(config.BOTS_BY_ID):
        try:
            out[b] = ensure(b)
        except Exception:
            log.warning("could not set %s's background", b, exc_info=True)
            out[b] = "failed"
    return out
