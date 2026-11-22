""







import json
import re

from . import choices, config, store

KIND = "result"
MEDIA_LENGTH = 32
MEDIA_ID = re.compile(rf"[0-9a-f]{{{MEDIA_LENGTH}}}")
KINDS = ("site", "file")


class ResultError(Exception):
    pass


def media_dir():
    return config.HUB_DIR / "media"


def offer(bot_id: str, args: dict, during_turn: bool) -> None:
    media = str(args.get("media") or "")
    if not MEDIA_ID.fullmatch(media) or not (media_dir() / f"{media}.file").is_file():
        raise ResultError("The file is not there to show.")
    kind = args.get("kind") if args.get("kind") in KINDS else "file"
    name = " ".join(str(args.get("name") or "file").split())[:120] or "file"
    card = {"state": "open", "media": media, "name": name, "kind": kind,
            "size": max(0, int(args.get("size") or 0)), "title": " ".join(str(args.get("title") or "").split())[:60],
            "preview": bool(args.get("preview")), "page": bool(args.get("page")) and kind == "site",
            "can_publish": bool(args.get("can_publish")) and kind == "site"}
    if card["preview"]:
        card.update(width=max(1, int(args.get("width") or 720)), height=max(1, int(args.get("height") or 480)))

    def publish() -> None:
        store.add_feed(KIND, bot_id, None, card["title"] or name, json.dumps(card),
                       written_by="zip", written_for="person")
    if during_turn:
        choices.defer(bot_id, publish)
    else:
        publish()


def path_of(media: str, part: str):
    ""
    if not MEDIA_ID.fullmatch(media):
        return None
    suffix = {"page": ".html", "file": ".file", "picture": ".jpg"}.get(part)
    if not suffix:
        return None
    path = media_dir() / f"{media}{suffix}"
    name = "file"
    try:
        name = str(json.loads((media_dir() / f"{media}.meta.json").read_text()).get("name") or "file")
    except (OSError, ValueError):
        pass
    return path, name


def title_for(card: dict) -> str:
    what = "Site ready" if card.get("kind") == "site" else "File ready"
    return f"{what}: {card.get('title') or card.get('name') or ''}".rstrip(": ")
