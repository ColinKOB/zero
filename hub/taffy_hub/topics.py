""


















import re

TAG = re.compile(r"\s*<topic\s*/>\s*", re.I)
_OPEN = "<topic/>"



MAX_TOPICS = 3

GUIDE = ("Each chat message holds one topic, so the person can answer each one on its own. Keep everything about one "
         "topic together, however many paragraphs it takes, with its question and next step; never split one topic. "
         "Most replies are one topic. Only when a reply also raises something separate that the person would answer "
         "or act on by itself (a different task, question or piece of news), write <topic/> on a line of its own "
         "before it, and it shows as its own message. Use it at most twice. A list, a side note or a closing line "
         "stays with the topic it belongs to. Never put <topic/> inside a draft or a short version. Keep each "
         "topic short.")


def split(text: str | None) -> list[str]:
    ""

    parts = [p for p in (p.strip() for p in TAG.split(str(text or ""))) if p] or [""]
    return parts[:MAX_TOPICS - 1] + ["\n\n".join(parts[MAX_TOPICS - 1:])] if len(parts) > MAX_TOPICS else parts


def join(text: str | None) -> str:
    ""
    t = str(text or "")
    return "\n\n".join(split(t)) if TAG.search(t) else t


def partial(text: str) -> str:
    ""
    text = join(text)
    for n in range(min(len(text), len(_OPEN) - 1), 0, -1):
        if _OPEN.startswith(text[-n:].lower()):
            return text[:-n]
    return text


def expand(msgs: list[dict]) -> list[dict]:
    ""
    out = []
    for m in msgs:
        parts = m.get("topics") or []
        if len(parts) < 2 or m.get("class") not in ("bot", "board_reply"):
            out.append({k: v for k, v in m.items() if k != "topics"})
            continue
        base = {k: v for k, v in m.items() if k not in ("topics", "display", "full", "backup")}
        for i, part in enumerate(parts):
            item = {**base, "text": part, "display": part}
            if i:


                item.pop("turn", None)
                item["id"] = f"{m.get('id')}.{i + 1}"
            if i == len(parts) - 1 and m.get("backup"):
                item["backup"] = True
            out.append(item)
    return out
