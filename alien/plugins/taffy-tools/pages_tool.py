""









import json
import re

from . import consent, home, mac

ACTIONS = ("publish", "list", "get", "take_down")


_DETAILS = (
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    re.compile(r"(?<![\d-])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\d-])"),
    re.compile(r"\b\d{1,5}\s+(?:[A-Z][a-z]+\.?\s+){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|"
               r"Court|Ct|Way|Place|Pl|Terrace|Parkway|Pkwy|Circle|Highway|Hwy)\b"),
)

SCHEMA = {
    "name": "share_page",
    "description": (
        "A web page you keep current at a private link Colin can share, like a trip plan, an event's details or a "
        "project's status. Use it when Colin asks for a page, a link he can send or show someone, or something to "
        "share; his ask is the permission, so do it without asking first. A schedule, a plan, amounts over time or a "
        "job's steps he only wants to see go on a card (the card tool), never on a page. action 'publish' with title "
        "and text puts "
        "a new page online and returns its link; tell Colin the link in one line. To change a page, call 'publish' "
        "with page (its id or title) and the whole new text: the words are replaced at the same link. When facts on "
        "a page you keep change later, update it and mention it in one line. 'list' shows your pages and links, "
        "'get' returns a page's current text, and 'take_down' removes a page when Colin says to (its link stops "
        "working). The page shows when it was last updated. Write text in simple Markdown (# headings, - lists, "
        "**bold**, [links](https://...)). Lead with what a reader needs at a glance: times, places, the next step. "
        "Anyone with the link can read it, so put on it only what the page is about: no private notes, emails, "
        "home addresses, account or card numbers, or other people's details Colin did not ask to share. Never send "
        "the link to anyone but Colin; sharing it is his choice."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "title": {"type": "string", "description": "publish: the page's title, short, like \"Maine trip, Nov 6 to 9\"."},
            "text": {"type": "string", "description": "publish: everything the page says, in simple Markdown. An update "
                                                      "replaces the whole page, so give all of it."},
            "page": {"type": "string", "description": "publish (to update), get and take_down: the page's id or title."},
        },
        "required": ["action"],
    },
}


def schema() -> dict:
    s = json.loads(json.dumps(SCHEMA))
    s["description"] = home.personalize(s["description"])
    for prop in s["parameters"]["properties"].values():
        if "description" in prop:
            prop["description"] = home.personalize(prop["description"])
    return s


def classify(args: dict):
    ""

    from . import tiers
    action = args.get("action")
    title = " ".join(str(args.get("title") or "").split())[:120]
    page = " ".join(str(args.get("page") or "").split())[:120]
    text = str(args.get("text") or "")[:2000]
    if action == "publish" and not page:
        return tiers.Action("write", f"Put a page online at a private link: \"{title or 'untitled'}\"", inputs=text,
                            extra={"request": title, "new_page": True})
    if action == "publish":
        return tiers.Action("write", f"Update the shared page \"{page}\"", inputs=text,
                            extra={"request": page, "page_update": True})
    if action == "take_down":
        return tiers.Action("write", f"Take down the shared page \"{page}\"", extra={"request": page})
    return None


def _details(text: str) -> set[str]:
    return {re.sub(r"\D", "", m)[-10:] if i == 1 else " ".join(m.lower().split())
            for i, pattern in enumerate(_DETAILS) for m in pattern.findall(text)}


def adds_details(args: dict) -> bool:
    ""



    new = _details(f'{args.get("title") or ""}\n{args.get("text") or ""}')
    if not new:
        return False
    reply = mac._call("get", {"page": str(args.get("page") or "")}, 30, area="pages")
    page = (reply.get("result") or {}) if reply.get("ok") else {}
    return bool(new - _details(f'{page.get("title") or ""}\n{page.get("text") or ""}'))


def share_page(args: dict, **kwargs) -> str:
    args = args if isinstance(args, dict) else {}
    action = args.get("action")
    if action not in ACTIONS:
        return json.dumps({"error": f"action must be one of {', '.join(ACTIONS)}."})
    if action in ("publish", "take_down") and consent.test_mode():
        return json.dumps({"test_mode": True, "would_be": action, "note": "Test mode: nothing went online or came down."})
    body = {k: args[k] for k in ("title", "text", "page") if isinstance(args.get(k), str)}
    reply = mac._call(action, body, 120, session_id=kwargs.get("session_id") or "", area="pages")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "That did not work."})
    return json.dumps({"result": reply.get("result")}, ensure_ascii=False)
