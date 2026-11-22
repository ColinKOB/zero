""





















import logging
import re
import time
from datetime import date

from . import config, mac

log = logging.getLogger("taffy.imessage")

ACTIONS = ("recent", "read", "search", "since", "lookup")
SEARCH_MAX = 50
MEMBERS_SEC = 10 * 60
LABEL_SEC = 6 * 3600
_members: dict[str, tuple[float, dict]] = {}
_labels: dict[str, tuple[float, str]] = {}


def known(conversation: str, now: float | None = None) -> tuple[dict | None, str]:
    ""

    now = time.time() if now is None else now
    hit = _members.get(conversation)
    members = hit[1] if hit and now - hit[0] < MEMBERS_SEC else None
    label = _labels.get(conversation)
    name = label[1] if label and now - label[0] < LABEL_SEC else ""
    return members, (members or {}).get("name") or name or _label_of(members)


def card_to(conversation: str, convo: dict) -> dict:
    ""
    people = [{"handle": str(p.get("handle") or ""), "name": str(p.get("name") or "")} for p in convo.get("people") or []
              if isinstance(p, dict) and p.get("handle")]
    return {"conversation": conversation, "name": str(convo.get("name") or ""), "people": people,
            "service": str(convo.get("service") or "")}


def _label_of(members: dict | None) -> str:
    people = (members or {}).get("people") or []
    return ", ".join(p.get("name") or p.get("handle") for p in people[:8])


def _remember(result: dict) -> None:
    now = time.time()
    convos = [result.get("conversation")] if isinstance(result.get("conversation"), dict) else []
    convos += [c for c in result.get("conversations") or [] if isinstance(c, dict)]
    for convo in convos:
        guid = str(convo.get("conversation") or "")
        if guid and convo.get("people"):
            _members[guid] = (now, card_to(guid, convo))
    for m in result.get("messages") or []:
        if isinstance(m, dict) and m.get("conversation") and m.get("with"):
            _labels[str(m["conversation"])] = (now, str(m["with"])[:120])
    if len(_labels) > 2000:
        for guid, _ in sorted(_labels.items(), key=lambda kv: kv[1][0])[:1000]:
            _labels.pop(guid, None)


async def read(bot_id: str, args: dict, timeout: float | None = None) -> dict:
    ""


    args = args if isinstance(args, dict) else {}
    action = str(args.get("action") or "")
    if action not in ACTIONS:
        raise mac.MacError("action must be recent, read or search.")
    try:
        limit = min(max(int(args.get("limit") or (30 if action == "read" else 15)), 1), 100)
    except (TypeError, ValueError):
        limit = 15
    payload: dict = {"action": action, "limit": limit}
    if action == "read":
        conversation = str(args.get("conversation") or "").strip()
        if not conversation:
            raise mac.MacError("Give the conversation from a recent or search result.")
        payload["conversation"] = conversation[:200]
        if _number(args.get("before")) is not None:
            payload["before"] = _number(args.get("before"))
    elif action == "search":
        query = str(args.get("query") or "").strip()
        for key in ("conversation", "person"):
            if str(args.get(key) or "").strip():
                payload[key] = str(args[key]).strip()[:200]
        for key in ("since", "until"):
            if str(args.get(key) or "").strip():
                payload[key] = _day(args[key])
        if payload.get("since") and payload.get("until") and payload["since"] > payload["until"]:
            raise mac.MacError("since is after until.")
        if not query and not any(k in payload for k in ("conversation", "person", "since", "until")):
            raise mac.MacError("Say what to search for.")
        if query:
            payload["query"] = query[:100]
        if _number(args.get("before")) is not None:
            payload["before"] = _number(args.get("before"))
        context = _number(args.get("context"))
        payload["context"] = min(context if context is not None else 2, 5)
        if payload["context"]:
            payload["limit"] = min(limit, SEARCH_MAX)
    elif action == "lookup":
        payload["handle"] = str(args.get("handle") or "").strip()[:200]
    elif action == "since" and _number(args.get("after")) is not None:
        payload["after"] = _number(args.get("after"))


    started = time.monotonic()
    try:
        reply = await mac._call("messages", payload, **({"timeout": timeout} if timeout else {}))
    except mac.MacError as e:
        log.warning("Texts %s by %s failed after %.1f s: %s", action, bot_id, time.monotonic() - started,
                    getattr(e, "code", "") or "refused")
        raise
    log.info("Texts %s read by %s in %.1f s", action, bot_id, time.monotonic() - started)
    result = reply.get("result") or {}
    if isinstance(result, dict):
        _remember(result)
    return result


def _day(value) -> str:
    text = str(value).strip()[:10] if re.match(r"^\d{4}-\d{2}-\d{2}(?:$|T| )", str(value).strip()) else ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise mac.MacError("since and until are days written like 2024-02-14.") from None


def _number(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None
