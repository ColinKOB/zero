""

























import concurrent.futures
import hashlib
import json
import logging
import math
import re
import secrets
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, store, who

log = logging.getLogger("taffy.kit")

KIND = "kit"
IMAGE_DIR_NAME = "kit-images"


MAX_BLOCKS = 30
MAX_CHARS = 40_000
TITLE_MAX = 60
HEADING_MAX = 120
TEXT_MAX = 3000
LINE_MAX = 200
NOTE_MAX = 300
PRICE_MAX = 24
CELL_MAX = 120
LABEL_MAX = 40
URL_MAX = 2000
IMAGES_PER_BLOCK = 10
IMAGES_PER_CARD = 12
CHECK_MAX = 60
TABLE_COLS = 6
TABLE_ROWS = 40
WIDE_CELL_MAX = 32
BUTTONS_MAX = 4
OPTIONS_MIN, OPTIONS_MAX = 2, 4
WEEK_DAYS = 7
WEEK_ITEMS = 60
TIME_LABEL_MAX = 12
BAR_LABEL_MAX = 12
BARS_MAX = 31
STEPS_MAX = 30
TIMELINE_ITEMS = 30
IMAGE_BYTES_MAX = 6 * 1024 * 1024
IMAGE_WORKERS = 6
REPEAT_SEC = 600

VISUAL_TYPES = ("week", "bars", "progress", "timeline")
BLOCK_TYPES = ("heading", "text", "images", "checklist", "table", "question", "date", "link", "buttons", *VISUAL_TYPES)
WEEK_KINDS = ("due", "event", "task", "suggested")
STEP_STATES = ("done", "doing", "waiting", "next")
_MAGIC = ((b"\x89PNG", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF8", "gif"))
_NAME = re.compile(r"^[0-9a-f]{32}\.(?:png|jpg|gif|webp)$")
_URL = re.compile(r"^https?://[^\s]+$", re.I)
_CARD_ID = re.compile(r"^[0-9a-f]{6}$")
_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?$")
_DUE_WORD = re.compile(r"^due\b[\s:,-]*", re.I)
_SUGGESTED_WORD = re.compile(r"^suggested\b[\s:,.-]*", re.I)


_unpublished: dict[tuple[str, str], dict] = {}


class KitError(Exception):
    pass


def image_dir() -> Path:
    return config.HUB_DIR / IMAGE_DIR_NAME




def _line(value, limit: int, what: str, required: bool = True) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise KitError(f"Give the {what}.")
    if len(text) > limit:
        raise KitError(f"Keep the {what} under {limit} characters.")
    return text


def _para(value, limit: int, what: str) -> str:
    text = "\n".join(" ".join(part.split()) for part in str(value or "").strip().splitlines()).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        raise KitError(f"Give the {what}.")
    if len(text) > limit:
        raise KitError(f"Keep the {what} under {limit} characters, or split it into more blocks.")
    return text


def _url(value, what: str) -> str:
    url = str(value or "").strip()
    if not _URL.match(url) or len(url) > URL_MAX:
        raise KitError(f"The {what} must be a full web address starting with https://.")
    return url


def _list(value, what: str, lo: int, hi: int) -> list:
    if not isinstance(value, list) or not lo <= len(value) <= hi:
        raise KitError(f"Give {lo} to {hi} {what}.")
    return value


def _block(raw, seq, bot_id: str = "") -> dict:
    ""
    if not isinstance(raw, dict):
        raise KitError("Each block is an object with a type.")
    kind = str(raw.get("type") or "").strip().lower()
    if kind not in BLOCK_TYPES:
        raise KitError(f'"{kind or "?"}" is not a block type. Use one of: {", ".join(BLOCK_TYPES)}.')
    b = {"id": seq("b"), "type": kind}
    if kind == "heading":
        b["text"] = _line(_plain_words(raw.get("text")), HEADING_MAX, "heading")
    elif kind == "text":
        b["text"] = _para(_plain_words(raw.get("text")), TEXT_MAX, "text")
    elif kind == "images":
        items = []
        for item in _list(raw.get("items"), "pictures in an images block", 1, IMAGES_PER_BLOCK):
            if not isinstance(item, dict):
                raise KitError("Each picture is an object with an image and a title.")
            out = {"title": _line(item.get("title"), LINE_MAX, "picture's title")}
            src = str(item.get("image") or "").strip()
            if src:
                out["source"] = src
            for key, limit in (("price", PRICE_MAX), ("note", NOTE_MAX)):
                if item.get(key):
                    out[key] = _line(item[key], limit, key)
            if item.get("link"):
                out["link"] = _url(item["link"], "picture's link")
            items.append(out)
        b["items"] = items
    elif kind == "checklist":
        if raw.get("title"):
            b["title"] = _line(raw["title"], HEADING_MAX, "checklist's title")
        b["items"] = [_check_item(item, seq) for item in _list(raw.get("items"), "checklist items", 1, CHECK_MAX)]
    elif kind == "table":
        cols = [_line(c, CELL_MAX, "column name") for c in _list(raw.get("columns"), "columns", 1, TABLE_COLS)]
        rows = []
        for row in _list(raw.get("rows"), "table rows", 1, TABLE_ROWS):
            if not isinstance(row, list) or len(row) != len(cols):
                raise KitError(f"Give each table row {len(cols)} cells, one per column.")
            rows.append([_line(c, CELL_MAX, "cell", required=False) for c in row])


        if len(cols) > 3 and any(len(c) > WIDE_CELL_MAX for r in rows for c in r):
            raise KitError(f"A table with more than three columns takes short cells, {WIDE_CELL_MAX} characters at most. "
                           "Put longer notes in a text block, and use an images block for things to buy or compare "
                           "with a picture, price and note each.")
        b.update(columns=cols, rows=rows)
    elif kind == "question":
        b["question"] = _line(raw.get("question"), LINE_MAX, "question")
        if raw.get("title"):
            b["title"] = _line(raw["title"], TITLE_MAX, "question's title")
        options, typed = raw.get("options"), raw.get("typed") is True
        if typed == bool(options):
            raise KitError("Give a question two to four options, or set typed to true for a short typed answer.")
        if options:
            opts = [_line(o, 80, "option") for o in _list(options, "options", OPTIONS_MIN, OPTIONS_MAX)]
            if len({o.casefold() for o in opts}) != len(opts):
                raise KitError("Give different options.")
            b["options"] = opts
        else:
            b["typed"] = True
    elif kind == "date":
        when = str(raw.get("date") or "").strip()
        try:
            datetime.fromisoformat(when)
        except ValueError:
            raise KitError('Write the date as 2026-11-05, or with a time as 2026-11-05T15:30.') from None
        b["date"] = when
        if raw.get("label"):
            b["label"] = _line(raw["label"], 80, "date's label")
    elif kind == "link":
        b["url"] = _url(raw.get("url"), "link")
        if raw.get("title"):
            b["title"] = _line(raw["title"], LINE_MAX, "link's title")
    elif kind == "buttons":
        buttons = []
        for raw_button in _list(raw.get("buttons"), "buttons", 1, BUTTONS_MAX):
            if not isinstance(raw_button, dict):
                raise KitError("Each button is an object with a label.")
            button = {"id": seq("k"), "label": _line(raw_button.get("label"), LABEL_MAX, "button's label")}
            if raw_button.get("link") and raw_button.get("submit"):
                raise KitError("A button either opens a link or submits the answers, not both.")
            if raw_button.get("link"):
                button["link"] = _url(raw_button["link"], "button's link")
            elif raw_button.get("submit") is True:
                button["submit"] = True
            buttons.append(button)
        b["buttons"] = buttons
    elif kind == "week":
        _week(b, raw, bot_id)
    elif kind == "bars":
        _bars(b, raw)
    elif kind == "progress":
        _progress(b, raw)
    elif kind == "timeline":
        _timeline(b, raw)
    return b




def _today() -> date:
    from . import home
    return datetime.now(home.zone()).date()


def _when(value, what: str) -> tuple[date, str]:
    ""

    text = str(value or "").strip()
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise KitError(f"Write the {what} as 2026-11-05, or with a time as 2026-11-05T15:30.") from None
    if moment.tzinfo is not None:
        from . import home
        moment = moment.astimezone(home.zone())
    return moment.date(), moment.strftime("%H:%M") if "T" in text or " " in text else ""


def _clock_label(hhmm: str) -> str:
    ""
    hour, minute = int(hhmm[:2]), int(hhmm[3:5])
    if (hour, minute) == (12, 0):
        return "Noon"
    twelve = hour % 12 or 12
    return f"{twelve}{f':{minute:02d}' if minute else ''} {'AM' if hour < 12 else 'PM'}"


def _time_label(value) -> str:
    ""
    text = " ".join(str(value or "").split())
    clock = _CLOCK.match(text)
    if clock:
        return _clock_label(f"{clock[1]}:{clock[2]}")
    if len(text) > TIME_LABEL_MAX:
        raise KitError(f'Keep the time "{text}" under {TIME_LABEL_MAX} characters, like "7 PM" or "Tonight".')
    return text


def _short_date(day: date) -> str:
    return f"{day:%b} {day.day}"


def _span(start: date) -> str:
    ""
    end = start + timedelta(days=WEEK_DAYS - 1)
    return f"{_short_date(start)} to {end.day if end.month == start.month else _short_date(end)}"


def _target(out: dict, raw: dict, what: str) -> None:
    ""
    if raw.get("link"):
        out["link"] = _url(raw["link"], f"{what}'s link")
    if raw.get("task"):
        task = str(raw["task"]).strip()
        if not _TASK_ID.match(task):
            raise KitError(f"Give the {what}'s task as the board task's id.")
        out["task"] = task
    if raw.get("calendar") is True:
        out["calendar"] = True


def _zip_of(value, bot_id: str) -> tuple[str, str]:
    ""
    wanted = " ".join(str(value or "").split())
    if not wanted:
        bot = config.BOTS_BY_ID.get(bot_id)
        return (bot.id, bot.name) if bot else ("", "")
    fold = wanted.casefold()
    for bot in config.BOTS_BY_ID.values():
        if fold in (bot.id.casefold(), bot.name.casefold()):
            return bot.id, bot.name
    return "", _line(wanted, LABEL_MAX, "Zip's name")


def _week(b: dict, raw: dict, bot_id: str) -> None:
    start = _when(raw["start"], "week's start")[0] if raw.get("start") else _today()
    last = start + timedelta(days=WEEK_DAYS - 1)
    raw_items = raw.get("items") if raw.get("items") is not None else []
    if not isinstance(raw_items, list) or len(raw_items) > WEEK_ITEMS:
        raise KitError(f"Give a week at most {WEEK_ITEMS} items.")
    items = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise KitError("Each item of a week is an object with a day, a title and a kind.")
        day, clock = _when(item.get("day"), "item's day")
        if not start <= day <= last:
            raise KitError(f'"{item.get("title") or "An item"}" is on {day.isoformat()}, outside the week of '
                           f"{start.isoformat()} to {last.isoformat()}. Move the start, or leave the item out.")
        kind = str(item.get("kind") or "event").strip().lower()
        if kind not in WEEK_KINDS:
            raise KitError(f'"{kind}" is not a kind of item. Use one of: {", ".join(WEEK_KINDS)}.')
        out = {"day": day.isoformat(), "title": _line(_plain_words(item.get("title")), LINE_MAX, "item's title"),
               "kind": kind}
        given = item.get("time")
        label = _time_label(given) if given else (_clock_label(clock) if clock else "")
        note = _line(_plain_words(item.get("note")), NOTE_MAX, "item's note", required=False)
        if kind == "due":
            label = _DUE_WORD.sub("", label).strip()
            label = label[:1].upper() + label[1:]
            note = _DUE_WORD.sub("", note).strip()
            note = note[:1].upper() + note[1:]
        if kind == "suggested":
            note = _SUGGESTED_WORD.sub("", note).strip()
            note = note[:1].upper() + note[1:]
        if label:
            out["time"] = label
        if note:
            out["note"] = note
        if kind == "task" or item.get("zip"):
            zip_id, zip_name = _zip_of(item.get("zip"), bot_id)
            if zip_id:
                out["zip"] = zip_id
            if zip_name:
                out["zip_name"] = zip_name
        _target(out, item, "item")
        items.append(out)
    items.sort(key=lambda i: i["day"])
    b.update(start=start.isoformat(), range=_span(start), items=items)
    if raw.get("title"):
        b["title"] = _line(_plain_words(raw["title"]), TITLE_MAX, "week's title")


def _number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise KitError(f"Give each {what} as a number, zero or more.")
    return value


def _amount(value: float, unit: str) -> str:
    ""
    text = f"{value:,.2f}" if isinstance(value, float) and not value.is_integer() else f"{int(value):,}"
    return f"{unit}{text}" if unit in ("$", "€", "£") else " ".join(x for x in (text, unit) if x)


def _bars(b: dict, raw: dict) -> None:
    unit = _line(raw.get("unit"), 12, "unit", required=False)
    bars = []
    for bar in _list(raw.get("bars"), "bars", 1, BARS_MAX):
        if not isinstance(bar, dict):
            raise KitError("Each bar is an object with a label and a value.")
        value = _number(bar.get("value"), "bar's value")
        bars.append({"label": _line(bar.get("label"), BAR_LABEL_MAX, "bar's label"), "value": value,
                     "shown": _amount(value, unit)})
    b["bars"] = bars
    if unit:
        b["unit"] = unit
    if raw.get("limit") is not None:
        b["limit"] = _number(raw["limit"], "limit")
        b["limit_shown"] = _amount(b["limit"], unit)
    _heading(b, raw)


def _heading(b: dict, raw: dict) -> None:
    ""
    if raw.get("title"):
        b["title"] = _line(_plain_words(raw["title"]), TITLE_MAX, "block's title")
    summary = _line(_plain_words(raw.get("summary")), LINE_MAX, "summary", required=False)
    if summary:
        b["summary"] = summary


def _progress(b: dict, raw: dict) -> None:
    steps = []
    for step in _list(raw.get("steps"), "steps", 1, STEPS_MAX):
        if isinstance(step, str):
            step = {"title": step}
        if not isinstance(step, dict):
            raise KitError("Each step is its title, or an object with a title and a state.")
        state = str(step.get("state") or "next").strip().lower()
        if state not in STEP_STATES:
            raise KitError(f'"{state}" is not a step\'s state. Use one of: {", ".join(STEP_STATES)}.')
        out = {"title": _line(_plain_words(step.get("title")), LINE_MAX, "step's title"), "state": state}
        note = _line(_plain_words(step.get("note")), LABEL_MAX, "step's note", required=False)
        if note:
            out["note"] = note
        steps.append(out)
    b["steps"] = steps
    _heading(b, raw)
    b.setdefault("summary", f"{sum(s['state'] == 'done' for s in steps)} of {len(steps)} steps done.")


def _timeline(b: dict, raw: dict) -> None:
    items = []
    for item in _list(raw.get("items"), "times in a timeline", 1, TIMELINE_ITEMS):
        if not isinstance(item, dict):
            raise KitError("Each time in a timeline is an object with a time and a title.")
        out = {"title": _line(_plain_words(item.get("title")), LINE_MAX, "timeline item's title")}
        label = _time_label(item.get("time"))
        if label:
            out["time"] = label
        note = _line(_plain_words(item.get("note")), NOTE_MAX, "timeline item's note", required=False)
        if note:
            out["note"] = note
        _target(out, item, "timeline item")
        items.append(out)
    b["items"] = items
    _heading(b, raw)
    if raw.get("day"):
        day = _when(raw["day"], "timeline's day")[0]
        b["day"] = day.isoformat()
        b.setdefault("title", f"{day:%A}")


def _check_item(item, seq) -> dict:
    if isinstance(item, str):
        item = {"text": item}
    if not isinstance(item, dict):
        raise KitError("Each checklist item is its text, or an object with text and checked.")
    return {"id": seq("i"), "text": _line(item.get("text"), LINE_MAX, "checklist item"),
            "checked": item.get("checked") is True}


def clean(title, blocks, bot_id: str = "") -> dict:
    ""

    blocks = _list(blocks, "blocks", 1, MAX_BLOCKS)
    if len(json.dumps(blocks)) > MAX_CHARS:
        raise KitError("That card is too big. Split it into two cards, or link to a page for the long part.")
    n = 0

    def seq(prefix: str) -> str:
        nonlocal n
        n += 1
        return f"{prefix}{n}"

    cleaned = [_block(raw, seq, bot_id) for raw in blocks]
    if sum(1 for b in cleaned if b["type"] == "images" for i in b["items"] if i.get("source")) > IMAGES_PER_CARD:
        raise KitError(f"Use at most {IMAGES_PER_CARD} pictures on one card.")
    questions = [b for b in cleaned if b["type"] == "question"]
    submits = [k for b in cleaned if b["type"] == "buttons" for k in b["buttons"] if k.get("submit")]
    if len(submits) > 1:
        raise KitError("Give a card one Submit button.")
    if submits and not questions:
        raise KitError("A Submit button sends the card's answers, and this card asks no question.")
    if len(questions) > 1 and not submits:

        cleaned.append({"id": seq("b"), "type": "buttons", "buttons": [{"id": seq("k"), "label": "Submit",
                                                                       "submit": True}]})
    named = _line(_plain_words(title), TITLE_MAX, "title", required=False)
    for b in cleaned:
        if named and b["type"] in VISUAL_TYPES:
            b.setdefault("title", named)
    title = named or _fallback_title(cleaned)
    card = {"title": title, "blocks": cleaned, "seq": n, "state": "open", "rev": 1}
    card["digest"] = hashlib.sha256(json.dumps([title, blocks], sort_keys=True).encode()).hexdigest()[:16]
    return card


def _plain_words(value):
    ""
    from . import receipts
    return receipts.without_refs(value) if isinstance(value, str) else value


def _fallback_title(blocks: list[dict]) -> str:
    for b in blocks:
        text = b.get("text") or b.get("title") or b.get("question") or (_week_name(b) if b["type"] == "week" else "")
        if text:
            first = text.splitlines()[0]
            return first if len(first) <= TITLE_MAX else first[:TITLE_MAX - 3].rsplit(" ", 1)[0] + "..."
    return "A card"


def _week_name(b: dict) -> str:
    ""
    start = date.fromisoformat(b["start"])
    return "This Week" if start <= _today() <= start + timedelta(days=WEEK_DAYS - 1) else f"Week of {_short_date(start)}"






def _week_item(i: dict) -> str:
    label = i.get("time", "")
    if i["kind"] == "due":
        return f"{i['title']}, due{f' {label}' if label else ''}"
    if i["kind"] == "suggested":
        return f"{i['title']}, suggested{f' for {label}' if label else ''}"
    who_with = f"with {i['zip_name']}" if i["kind"] == "task" and i.get("zip_name") else ""
    return ", ".join(x for x in (i["title"], label, who_with) if x)


def _week_plain(b: dict) -> str:
    head = f"{b.get('title') or _week_name(b)}, {b['range']}."
    if not b["items"]:
        return f"{head} Nothing is scheduled."
    lines = [head]
    for day in sorted({i["day"] for i in b["items"]}):
        when = date.fromisoformat(day)
        said = ". ".join(_week_item(i) for i in b["items"] if i["day"] == day)
        lines.append(f"{when:%A}, {_short_date(when)}: {said}.")
    return "\n".join(lines)


def _titled(b: dict) -> str:
    return ". ".join(x.rstrip(".") for x in (b.get("title"), b.get("summary")) if x) + "."


def _bars_plain(b: dict) -> str:
    limit = b.get("limit")
    over = f" (over the {b.get('limit_shown')} limit)"
    said = ", ".join(f"{x['label']} {x['shown']}{over if limit is not None and x['value'] > limit else ''}"
                     for x in b["bars"])
    return f"{_titled(b)}\n{said}."


def _step_plain(s: dict) -> str:
    word = {"done": "done", "doing": "in progress", "waiting": "waiting", "next": "not started"}[s["state"]]
    return f"{s['title']}: {s.get('note') or word}."


def plain(b: dict) -> str:
    kind = b["type"]
    if kind == "week":
        return _week_plain(b)
    if kind == "bars":
        return _bars_plain(b)
    if kind == "progress":
        return "\n".join([_titled(b), *(_step_plain(s) for s in b["steps"])])
    if kind == "timeline":
        rows = (" ".join(x for x in (i.get("time"), i["title"]) if x) + (f" ({i['note']})" if i.get("note") else "")
                for i in b["items"])
        return "\n".join([_titled(b), *rows])
    if kind in ("heading", "text"):
        return b["text"]
    if kind == "images":
        return "\n".join(" - ".join(x for x in (i["title"], i.get("price"), i.get("note"), i.get("link")) if x)
                         for i in b["items"])
    if kind == "checklist":
        head = [b["title"]] if b.get("title") else []
        return "\n".join(head + [f"[{'x' if i['checked'] else ' '}] {i['text']}" for i in b["items"]])
    if kind == "table":
        return "\n".join(" | ".join(r) for r in [b["columns"]] + b["rows"])
    if kind == "question":
        return b["question"] + (f" ({' / '.join(b['options'])})" if b.get("options") else "")
    if kind == "date":
        return " ".join(x for x in (b.get("label"), b["date"]) if x)
    if kind == "link":
        return " ".join(x for x in (b.get("title"), b["url"]) if x)
    if kind == "buttons":
        return " / ".join(k["label"] for k in b["buttons"])
    return ""


def finish(card: dict) -> dict:
    for b in card["blocks"]:
        b["plain"] = plain(b)
    return card




def _kind_of(data: bytes) -> str | None:
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    return "webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None


def _keep(data: bytes) -> str | None:
    ""
    ext = _kind_of(data)
    if not ext or len(data) > IMAGE_BYTES_MAX:
        return None
    name = f"{hashlib.sha256(data).hexdigest()[:32]}.{ext}"
    folder = image_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if not path.exists():
        tmp = folder / f".{name}.{secrets.token_hex(4)}"
        tmp.write_bytes(data)
        tmp.replace(path)
    return name


def _roots(bot_id: str) -> list[Path]:
    ""
    from . import teams
    return [config.WORK_DIR if bot_id == "default" else config.WORK_DIR / bot_id,
            config.SHARED_DIR, config.HERMES_HOME / "kanban" / "attachments", *teams.extra_folders(bot_id)]


def _fetch(src: str, bot_id: str) -> str | None:
    if src.lower().startswith(("https://", "http://")):
        from . import siteicon
        got = siteicon._get(src, IMAGE_BYTES_MAX)
        return _keep(got[1]) if got and got[0] == 200 and len(got[1]) <= IMAGE_BYTES_MAX else None
    from . import safe_files
    try:
        with safe_files.open_file(src, [str(r) for r in _roots(bot_id)]) as f:
            data = f.read(IMAGE_BYTES_MAX + 1)
    except (OSError, safe_files.Refused):
        return None
    return _keep(data)


def load_images(card: dict, bot_id: str) -> list[str]:
    ""

    items = [i for b in card["blocks"] if b["type"] == "images" for i in b["items"] if i.get("source")]
    if not items:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as pool:
        names = list(pool.map(lambda i: _safe_fetch(i["source"], bot_id), items))
    missed = []
    for item, name in zip(items, names):
        item.pop("source")
        if name:
            item["image"] = name
        else:
            missed.append(item["title"])
    return missed


def _safe_fetch(src: str, bot_id: str) -> str | None:
    try:
        return _fetch(src, bot_id)
    except Exception:
        log.warning("a card's picture could not be fetched", exc_info=True)
        return None


def image_path(name: str) -> Path | None:
    if not _NAME.match(name or ""):
        return None
    path = image_dir() / name
    return path if path.is_file() else None




def prepare(bot_id: str, title, blocks) -> tuple[dict, list[str]] | str:
    ""

    card = clean(title, blocks, bot_id)
    again = _repeat(bot_id, card["digest"])
    if again:
        return again
    missed = load_images(card, bot_id)
    card["created"] = time.time()
    return finish(card), missed


def put_up(bot_id: str, prepared: tuple[dict, list[str]] | str, during_turn: bool) -> dict:
    ""

    if isinstance(prepared, str):
        return {"card": prepared, "result": f"That card is already in {who.possessive()} chat as card {prepared}. "
                                            "Change it with action update instead of sending it again."}
    card, missed = prepared
    card["card_id"] = _new_id(bot_id)
    if during_turn:
        from . import choices
        _unpublished[(bot_id, card["card_id"])] = card
        choices.defer(bot_id, lambda: _publish(bot_id, card))
        where = (f"{who.first()} will see it under your reply. Keep the reply short and don't repeat what the card "
                 "shows.")
    else:
        _publish(bot_id, card)
        where = f"It is in {who.possessive()} chat."
    note = (f" These pictures couldn't be loaded, so their items show without one: {'; '.join(missed)}."
            if missed else "")
    return {"card": card["card_id"], "result": f"Card {card['card_id']} is made. {where}{note}"}


def _publish(bot_id: str, card: dict) -> None:
    _unpublished.pop((bot_id, card["card_id"]), None)
    if card["state"] == "closed":
        return
    store.add_feed(KIND, bot_id, None, card["title"], json.dumps(card), written_by="zip", written_for="person")


def _new_id(bot_id: str) -> str:
    while True:
        cid = secrets.token_hex(3)
        if (bot_id, cid) not in _unpublished and find(bot_id, cid) is None:
            return cid


def _repeat(bot_id: str, digest: str) -> str | None:
    for card in [c for (b, _), c in _unpublished.items() if b == bot_id]:
        if card.get("digest") == digest and card["state"] == "open":
            return card["card_id"]
    row = store.db().execute(
        "SELECT body FROM feed WHERE kind=? AND bot_id=? AND created_at>=? AND json_valid(body) "
        "AND json_extract(body, '$.digest')=? AND json_extract(body, '$.state')='open' ORDER BY id DESC LIMIT 1",
        (KIND, bot_id, time.time() - REPEAT_SEC, digest)).fetchone()
    return json.loads(row["body"])["card_id"] if row else None


def find(bot_id: str, card_id: str):
    ""
    if not _CARD_ID.match(str(card_id or "")):
        return None
    return store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? AND json_valid(body) "
                              "AND json_extract(body, '$.card_id')=?", (KIND, bot_id, card_id)).fetchone()







def _card_of(bot_id: str, card_id: str) -> tuple[dict, dict | None]:
    ""
    waiting = _unpublished.get((bot_id, str(card_id or "")))
    if waiting is not None:
        return waiting, None
    row = find(bot_id, card_id)
    if row is None:
        raise KitError(f"Card {card_id} isn't one of your cards. Give the id the card tool returned.")
    return json.loads(row["body"]), dict(row)


def _save(card: dict, row: dict | None) -> None:
    card["rev"] = card.get("rev", 1) + 1
    finish(card)
    if row is not None:
        store.update_feed(row["id"], card["title"], json.dumps(card))


def _pick_block(card: dict, number, kinds: tuple[str, ...]) -> dict:
    ""
    if number is not None:
        try:
            b = card["blocks"][int(number) - 1]
        except (TypeError, ValueError, IndexError):
            raise KitError(f"Block {number} isn't on that card. Number blocks from the top, starting at 1.") from None
        if b["type"] not in kinds:
            raise KitError(f"Block {number} is a {b['type']} block; that change needs a {' or '.join(kinds)} block.")
        return b
    found = [b for b in card["blocks"] if b["type"] in kinds]
    if len(found) != 1:
        raise KitError(f"That card has {len(found)} {' or '.join(kinds)} blocks. Say which with block (1 is the top).")
    return found[0]


def _item(checklist: dict, ref) -> dict:
    items = checklist["items"]
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        n = int(ref)
        if 1 <= n <= len(items):
            return items[n - 1]
    else:
        want = " ".join(str(ref or "").split()).casefold()
        exact = [i for i in items if i["text"].casefold() == want]
        loose = [i for i in items if want and want in i["text"].casefold()]
        if exact or len(loose) == 1:
            return (exact or loose)[0]
    raise KitError(f'"{ref}" isn\'t an item on that checklist. Give its text or its number (1 is the first).')


def update(bot_id: str, card_id: str, args: dict) -> dict:
    ""

    with store._lock:
        card, row = _card_of(bot_id, card_id)
        if card["state"] == "closed":
            raise KitError(f"Card {card_id} is closed. Post a new card if it's still needed.")
        check, uncheck, add, text = (args.get(k) for k in ("check", "uncheck", "add", "text"))
        if not any(x not in (None, [], "") for x in (check, uncheck, add, text)):
            raise KitError("Say what to change: check, uncheck, add, or text.")
        if check or uncheck or add:
            checklist = _pick_block(card, args.get("block"), ("checklist",))
            for ref in _as_list(check):
                _item(checklist, ref)["checked"] = True
            for ref in _as_list(uncheck):
                _item(checklist, ref)["checked"] = False
            if add:
                n = card["seq"]
                for raw in _as_list(add):
                    n += 1
                    checklist["items"].append(_check_item(raw, lambda prefix: f"{prefix}{n}"))
                card["seq"] = n
                if len(checklist["items"]) > CHECK_MAX:
                    raise KitError(f"A checklist holds at most {CHECK_MAX} items. Start a second checklist block.")
        if text not in (None, ""):
            b = _pick_block(card, args.get("block"), ("heading", "text"))
            b["text"] = (_line(text, HEADING_MAX, "heading") if b["type"] == "heading"
                         else _para(text, TEXT_MAX, "text"))
        _save(card, row)
    left = [i["text"] for b in card["blocks"] if b["type"] == "checklist" for i in b["items"] if not i["checked"]]
    return {"card": card_id, "result": f"Card {card_id} is changed in place, with no new message."
                                       + (f" Still unchecked: {', '.join(left)}." if left else "")}


def _as_list(value) -> list:
    return [] if value in (None, "") else value if isinstance(value, list) else [value]


def read(bot_id: str, card_id: str) -> dict:
    ""
    card, _ = _card_of(bot_id, card_id)
    lines = [f"Card {card_id}: {card['title']} ({card['state']})"]
    lines += [f"{n}. {b['type']}: {b['plain']}" for n, b in enumerate(card["blocks"], 1)]
    if card.get("sent"):
        lines.append(f"{who.first()} answered: {card['sent']}")
    return {"card": card_id, "result": "\n".join(lines)}


def close(bot_id: str, card_id: str, why: str = "") -> dict:
    ""
    with store._lock:
        card, row = _card_of(bot_id, card_id)
        if card["state"] == "closed":
            return {"card": card_id, "result": f"Card {card_id} was already closed."}
        card.update(state="closed", closed_at=time.time())
        why = " ".join(str(why or "").split())[:200]
        if why:
            card["closed_why"] = why
        _save(card, row)
    return {"card": card_id, "result": f"Card {card_id} is closed. Nothing was sent, and {who.first()} wasn't told."}




def check(bot_id: str, feed_id: int, item_id: str, checked: bool) -> dict:
    ""

    with store._lock:
        row = _row(bot_id, feed_id)
        card = json.loads(row["body"])
        if card["state"] == "closed":
            raise KitError("That card is closed.")
        item = next((i for b in card["blocks"] if b["type"] == "checklist" for i in b["items"] if i["id"] == item_id),
                    None)
        if item is None:
            raise KitError("That item is gone from the card.")
        if item["checked"] == checked:
            return card
        item["checked"] = checked
        _save(card, dict(row))
    store.add_card_note(bot_id, f'{who.first()} {"checked" if checked else "unchecked"} "{item["text"]}" on your '
                                f'card {card["card_id"]} ("{card["title"]}").', TAP_NOTES, TAP_NOTES_MAX)
    return card


TAP_NOTES = "kit_tap_notes"
TAP_NOTES_MAX = 30


def _row(bot_id: str, feed_id: int):
    row = store.db().execute("SELECT * FROM feed WHERE id=? AND kind=? AND bot_id=?", (feed_id, KIND, bot_id)).fetchone()
    if not row:
        raise KitError("That card is gone.")
    return row


def answer(bot_id: str, feed_id: int, button: str | None, answers: dict | None) -> tuple[dict, str | None]:
    ""






    with store._lock:
        row = _row(bot_id, feed_id)
        card = json.loads(row["body"])
        send, typed = _message(card, button, answers or {})
        if card["state"] != "open":
            if card.get("sent") == send:
                return card, None
            raise KitError("That card was already answered." if card["state"] == "answered" else "That card is closed.")
        card.update(state="answered", sent=send, own="\n".join(typed), answered_at=time.time(),
                    answers={k: " ".join(str(v).split())[:500] for k, v in (answers or {}).items()},
                    **({"pressed": button} if button else {}))
        card["rev"] = card.get("rev", 1) + 1
        changed = store.db().execute("UPDATE feed SET body=?, changed_at=? WHERE id=? AND body=?",
                                     (json.dumps(card), time.time(), feed_id, row["body"])).rowcount
        store.db().commit()
    if not changed:
        raise KitError("That card was answered elsewhere. Reload it.")
    from . import firstline
    firstline.note_card_answer(send, bot_id)
    return card, send


def _message(card: dict, button: str | None, answers: dict) -> tuple[str, list[str]]:
    ""
    questions = [b for b in card["blocks"] if b["type"] == "question"]
    pressed = None
    if button is not None:
        pressed = next((k for b in card["blocks"] if b["type"] == "buttons" for k in b["buttons"] if k["id"] == button),
                       None)
        if pressed is None or pressed.get("link"):
            raise KitError("That button doesn't answer the card.")
    if pressed is None or pressed.get("submit"):
        if pressed is None and (len(questions) != 1 or any(k.get("submit") for b in card["blocks"]
                                                           if b["type"] == "buttons" for k in b["buttons"])):
            raise KitError("Answer the questions, then tap Submit.")
        lines, typed = [], []
        for q in questions:
            given = " ".join(str(answers.get(q["id"]) or "").split())
            if not given or len(given) > 500:
                raise KitError(f'Answer "{q["question"]}" first. Keep it under 500 characters.')


            if q.get("typed") or (q.get("options") and given not in q["options"]):
                typed.append(given)
            lines.append((given, q))
        if len(lines) == 1:
            given, q = lines[0]
            return f'About your question "{q["question"]}" on your card "{card["title"]}": {given}', typed
        return (f'My answers on your card "{card["title"]}":\n'
                + "\n".join(f'{q.get("title") or q["question"]} ({q["question"]}): {given}' for given, q in lines),
                typed)
    return f'On your card "{card["title"]}": {pressed["label"]}', []


def card_answers() -> dict[str, str]:
    ""

    out = {}
    for row in store.db().execute(f"SELECT body FROM feed WHERE kind='{KIND}' AND body LIKE '%\"sent\":%'"):
        card = json.loads(row["body"] or "{}")
        if card.get("sent"):
            out[" ".join(card["sent"].split())] = card.get("own") or ""
    return out
