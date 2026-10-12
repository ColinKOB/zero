"""Each Zip's look, chosen in the app's Change Look.

Two kinds, both kept readable:
- Rendered (current): {"body": <body id>, "outfit": <outfit id or "none">}. Bodies are plush ("star") or flat
  ("flat-star"). Every plush body has outfits; flat bodies have none, so they are saved with outfit "none".
- Drawn (before Oct 2026): shape, color, texture, eyes and parts. The app now uses only the color, to pick the
  closest rendered body; zip-templates.json and older saved looks still use this kind.

Stored in hub-state/bot-looks.json by bot id. A Zip with no saved look keeps its shape and color from the
team list. Every value is from a fixed list, so the app only ever draws what it knows.
"""

import json
import re

from . import config

PARTS = {
    "shape": sorted(config.SHAPES | {"taffy"}),
    "texture": ["flat", "felt"],
    "eyes": ["calm", "curious", "bold", "dots", "sleepy", "wide"],
    "eyewear": ["none", "round", "square", "shades"],
    "top": ["none", "beanie", "cap", "headphones", "sprout", "crown", "antenna"],
    "neck": ["none", "scarf", "bowtie", "bandana"],
}

# The rendered characters (ios/Taffy/Views/Agent): bodies and their outfits.
BODIES = ["o", "star", "moon", "bolt", "drop", "pebble", "pill", "hex", "gem", "squircle", "mushroom", "bean", "arch",
          "flower", "dome", "leaf", "bell", "plus", "cube", "gumdrop"]
# Flat bodies: vector shapes drawn by the app (FlatFigure.swift). They have no outfits.
FLAT = ["o", "star", "moon", "bolt", "drop", "pill", "hex", "gem", "squircle", "mushroom", "bean", "arch", "flower",
        "dome", "leaf", "bell", "plus", "pebble", "acorn", "kite", "book", "calendar", "briefcase", "coin", "suitcase",
        "receipt"]
BODIES += ["flat-" + f for f in FLAT]
OUTFITS = ["classic", "cozy", "studio", "sporty", "plain"]


class LookError(ValueError):
    pass


def all_looks() -> dict[str, dict]:
    try:
        data = json.loads(config.LOOKS.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def for_bot(bot_id: str) -> dict | None:
    return all_looks().get(bot_id)


def clean(look: dict) -> dict:
    if "body" in look:
        return clean_rendered(look)
    out = {}
    for part, allowed in PARTS.items():
        value = str(look.get(part) or "").strip()
        if value not in allowed:
            raise LookError(f"Pick a {part} from the list.")
        out[part] = value
    color = str(look.get("color") or "").strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise LookError("Pick a color.")
    out["color"] = color.upper()
    out["seam"] = bool(look.get("seam"))
    return out


def clean_rendered(look: dict) -> dict:
    body = str(look.get("body") or "").strip()
    if body not in BODIES:
        raise LookError("Pick a body from the list.")
    outfit = str(look.get("outfit") or "none").strip()
    if body.startswith("flat-"):
        if outfit not in ("none", "plain"):
            raise LookError("Flat Zips wear no outfits.")
        outfit = "none"
    elif outfit == "none":   # plush looks saved before every body had outfits
        outfit = "plain"
    elif outfit not in OUTFITS:
        raise LookError("Pick an outfit from the list.")
    return {"body": body, "outfit": outfit}


def save(bot_id: str, look: dict) -> dict:
    look = clean(look)
    data = all_looks()
    data[bot_id] = look
    config.HUB_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.LOOKS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(config.LOOKS)
    return look


def forget(bot_id: str) -> None:
    data = all_looks()
    if data.pop(bot_id, None) is not None:
        tmp = config.LOOKS.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(config.LOOKS)
