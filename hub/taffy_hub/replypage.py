""



























import asyncio
import hashlib
import json
import logging
import os
import re
import time

from . import pages, resultcard, shortreply

log = logging.getLogger("taffy.replypage")


PAGE_WORDS = 300

_HERE = re.compile(r"\bone[- ]line\b|\bin (?:the |this )?chat\b|\bright here\b|\bpaste\b|\bno page\b", re.I)
_SPLIT_NUMBERS = re.compile(r"(?m)^(\d{1,2}[.)] [^\n]*)\n[ \t]*\n(?=\d{1,2}[.)] )")
_HEADING = re.compile(r"(?![-*•#\d])[^.!?,;]{1,60}:?")
LEAD_WORDS = 60
TITLE_SEC = 8
TITLE_MAX = 60
TITLE_SYSTEM = "You title a page for the person who will read it. Answer with the title only."
TITLE_PROMPT = """Title this page in 2 to 5 plain words, in Title Case. Say what the page is, such as "Three-Month Money \
Plan" or "November Plan". No punctuation at the end, no dates unless the page is about one, and no one's name.

What the person asked:
<message>
{asked}
</message>

The page:
<page>
{text}
</page>"""


def wanted(text: str, asked: str = "") -> bool:
    ""
    body = shortreply._split_questions(shortreply._ATTACHMENT.sub("", text or ""))[0]
    return (shortreply.enabled() and shortreply.words_in(body) >= PAGE_WORDS and not shortreply.has_work(text)
            and not _HERE.search(asked or ""))


def lead(reply: str, asked: str = "") -> str | None:
    ""

    whole = shortreply._ATTACHMENT.sub("", reply or "").strip()
    body, asks = shortreply._split_questions(whole)
    kept: list[str] = []
    for sentence in shortreply._SENTENCE_GAP.split(next(iter(shortreply._paragraphs(body)), "")):
        if shortreply.words_in(" ".join([*kept, sentence])) > LEAD_WORDS:
            break
        kept.append(sentence)
    short = "\n\n".join(p for p in (" ".join(kept), asks) if p)
    return short if kept and not shortreply.faithful(whole, short, asked) else None


def body_of(reply: str) -> str:
    ""

    text = shortreply.PILL.sub(r"\1", shortreply._ATTACHMENT.sub("", reply or "")).strip()
    text = _SPLIT_NUMBERS.sub(r"\1\n", text)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        bare = line.strip()
        if (i + 1 < len(lines) and lines[i + 1].strip() and (i == 0 or not lines[i - 1].strip())
                and _HEADING.fullmatch(bare)):
            lines[i] = "## " + bare.rstrip(":")
    return "\n".join(lines)


def _media_id(bot_id: str, reply: str) -> str:
    ""
    return hashlib.sha256(f"{bot_id}\n{reply}".encode()).hexdigest()[:resultcard.MEDIA_LENGTH]


def _fallback_title() -> str:
    from datetime import datetime
    from . import home
    return "Details, " + datetime.now(home.zone()).strftime("%b %-d")


def _clean_title(text: str) -> str:
    ""
    first = next((line for line in str(text or "").splitlines() if line.strip()), "")
    title = re.sub(r"[\[\]()*_#`<>|\"“”]", "", first).strip(" .:")
    return title if 0 < len(title) <= TITLE_MAX else ""


async def _title(bot_id: str, reply: str, asked: str, ask=None) -> str:
    prompt = TITLE_PROMPT.format(asked=(asked or "(no message)").strip()[:600], text=body_of(reply)[:1500])
    try:
        ask_now = ask or shortreply.ask_quick
        with shortreply.reading_text(prompt, (bot_id,)):
            said = await asyncio.wait_for(ask_now(prompt, timeout=TITLE_SEC, system=TITLE_SYSTEM, max_tokens=30,
                                                  effort="low", bot_id=bot_id), TITLE_SEC + 1)
    except Exception as e:
        log.info("no title for %s's page (%s)", bot_id, type(e).__name__)
        said = ""
    return _clean_title(said) or _fallback_title()


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "page"


def _write(media: str, title: str, reply: str) -> dict:
    ""
    folder = resultcard.media_dir()
    folder.mkdir(parents=True, exist_ok=True)
    data = pages.render(title, body_of(reply), time.time()).encode()
    name = f"{_slug(title)}.html"
    for suffix, content in ((".file", data), (".html", data), (".meta.json", json.dumps({"name": name}).encode())):
        path = folder / f"{media}{suffix}"
        path.write_bytes(content)
        os.chmod(path, 0o600)
    card = {"media": media, "name": name, "size": len(data), "title": title, "kind": "site", "page": True,
            "preview": False, "can_publish": False}
    from . import made
    try:
        render = made._page_renderer()
        if render is not None:
            width, height = render(str(folder / f"{media}.html"), str(folder / f"{media}.jpg"))
            card.update(preview=True, width=width, height=height)
    except Exception as e:
        log.info("no picture of %s's page (%s)", media, type(e).__name__)
    return card


async def make(bot_id: str, reply: str, asked: str = "", ask=None) -> dict | None:
    ""

    media = _media_id(bot_id, reply)
    try:
        if (resultcard.media_dir() / f"{media}.file").is_file():
            name = json.loads((resultcard.media_dir() / f"{media}.meta.json").read_text()).get("name")
            return {"media": media, "title": str(name or "page").removesuffix(".html").replace("-", " ").title()}
        title = await _title(bot_id, reply, asked, ask)
        return {**await asyncio.to_thread(_write, media, title, reply), "media_new": True}
    except Exception:
        log.warning("could not make %s's page", bot_id, exc_info=True)
        return None


def discard(card: dict | None) -> None:
    ""
    if card and card.get("media_new"):
        for suffix in (".file", ".html", ".jpg", ".meta.json"):
            (resultcard.media_dir() / f"{card['media']}{suffix}").unlink(missing_ok=True)


def show(bot_id: str, short: str, card: dict) -> str:
    ""

    from . import made
    if card.get("media_new"):
        resultcard.offer(bot_id, {k: v for k, v in card.items() if k != "media_new"}, True)
    media = shortreply._attachments(short)
    prose, asks = shortreply._split_questions(shortreply._ATTACHMENT.sub("", short).strip())
    link = f"It's all here: [{card['title']}](zero-item:{made.item_id(bot_id, 'result', card['media'])})."
    return "\n\n".join(p for p in (prose, link, asks, *media) if p)


def linked(bot_id: str, reply: str, short: str) -> bool:
    ""
    from . import made
    return f"(zero-item:{made.item_id(bot_id, 'result', _media_id(bot_id, reply))})" in (short or "")
