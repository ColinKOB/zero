""














import json
import re
from urllib.parse import urlsplit

from . import consent, home, mac, tiers

ASK_SCHEMA = {
    "name": "ask_for_api_key",
    "description": (
        "Ask Colin for a service's API key when a job needs that service's API. This is the right move for any "
        "service, even if you've never used it: never say you can't use API keys. He gets one card in this chat with "
        "a secure field for each value, and the key goes straight to Zero, so you never see it. Never ask for a key in "
        "the chat, never save one in your vault or as a login item, and never type one into a web page. The vault is "
        "for website logins. If he pastes a key in the chat anyway, don't use it: tell him to delete that message and "
        "make a new key, then call this. First tell him in one sentence what to make and on which page. Look that up "
        "in the service's own documentation first, so url, hosts and auth are right. For an app in Connect Apps, use "
        "ask_to_connect instead. After this call, stop and say nothing about the card. A note arrives when the key is "
        "added, and you carry on with service_api."
    ),
    "parameters": {"type": "object", "properties": {
        "service": {"type": "string", "description": "The service's name, like \"SendGrid\"."},
        "why": {"type": "string", "description": "What you need it for, in a few words, written to Colin as \"you\" "
                                                 "(he reads it on the card): \"send your launch emails\"."},
        "url": {"type": "string", "description": "The https page where he makes the key."},
        "make": {"type": "string", "description": "One sentence on what to make on that page, with the least access "
                                                  "the job needs: \"Create a key with Mail Send access and copy it.\""},
        "hosts": {"type": "array", "items": {"type": "string"},
                  "description": "The API's own hosts, like [\"api.sendgrid.com\"]. The key is sent to these and "
                                 "nowhere else, and they are shown on his card."},
        "auth": {"type": "string", "enum": ["bearer", "header", "query", "basic", "oauth1"],
                 "description": "How the API takes the key. bearer: Authorization: Bearer. header: a named header. "
                                "query: a named query parameter. basic: a username and password. oauth1: four signed "
                                "values."},
        "fields": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string", "description": "What he sees over the field, like \"API Key\"."},
            "name": {"type": "string", "description": "For header or query: the header or parameter the API reads."},
        }}, "description": "Each value he pastes. Leave out for one bearer key."},
        "check_url": {"type": "string", "description": "Optional: a free GET on the API that works with a good key and "
                                                       "answers 401 or 403 with a bad one."},
    }, "required": ["service", "why", "url", "make", "hosts", "auth"]},
}

CALL_SCHEMA = {
    "name": "service_api",
    "description": (
        "Call a service's API with the key Colin gave you. Zero adds the key, so you never see it or send it. The call "
        "goes only to the hosts on his card. GET and HEAD run right away. Other methods follow your usual rules: a "
        "send, a post or a delete gets his card first, so make the same call again, unchanged, once he approves. A "
        "charge or order is refused, because money moves only through the Wallet. If you have no key, this says so: "
        "call ask_for_api_key. A result that starts \"Not done\" means nothing went out. Any other failure may have "
        "gone through, so don't send it again. On X, post with post_to_x."
    ),
    "parameters": {"type": "object", "properties": {
        "service": {"type": "string", "description": "The service's name, as on ask_for_api_key."},
        "method": {"type": "string", "enum": ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
        "url": {"type": "string", "description": "The full https address on the API."},
        "query": {"type": "object", "description": "Query parameters."},
        "body": {"description": "A JSON body."},
        "form": {"type": "object", "description": "A form body, for APIs that take form fields instead of JSON."},
        "headers": {"type": "object", "description": "Other headers the API needs. Never a key."},
    }, "required": ["service", "url"]},
}

ASK_WAIT_SEC = 20
CALL_WAIT_SEC = 60

_PAY_WORDS = {"charge", "charges", "payment", "payments", "pay", "checkout", "checkouts", "order", "orders", "purchase",
              "purchases", "buy", "payout", "payouts", "transfer", "transfers", "refund", "refunds", "subscription",
              "subscriptions", "invoice", "invoices", "billing", "wallet"}
_PUBLISH_WORDS = {"post", "posts", "tweet", "tweets", "publish", "publishes", "deploy", "deploys", "deployment",
                  "deployments", "release", "releases", "status", "statuses", "broadcast", "broadcasts"}
_SEND_WORDS = {"send", "sends", "message", "messages", "mail", "mails", "email", "emails", "sms", "text", "texts",
               "notify", "notification", "notifications", "invite", "invites", "invitation", "invitations", "comment",
               "comments", "reply", "replies", "call", "calls", "campaign", "campaigns", "dm", "dms", "webhook", "webhooks"}
_MONEY_NEVER = ("Not done: money moves only through the Wallet, with Colin's yes. Tell him what you'd buy and where.")


def _words(text: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    return {w.lower() for w in re.split(r"[^A-Za-z0-9]+", spaced) if w}


def _plain(value) -> str:
    if value in (None, "", {}):
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def classify(args: dict):
    ""
    method = str((args or {}).get("method") or "GET").upper()
    if method in ("GET", "HEAD"):
        return None
    url = str(args.get("url") or "")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if consent.base(host) in tiers.X_SITES or str(args.get("service") or "").strip().lower() in ("x", "twitter"):
        return tiers.Action("hardline", f"{method} on X", site="x.com", why_never=tiers.X_POST_NEVER)
    inputs = "\n".join(p for p in (f"{method} {url}", _plain(args.get("query")), _plain(args.get("body")),
                                   _plain(args.get("form"))) if p)

    body = args.get("body") if isinstance(args.get("body"), dict) else {}
    words = _words(parts.path) | _words(str(body.get("query") or ""))
    service = " ".join(str(args.get("service") or host).split())[:40] or host
    if words & _PAY_WORDS:
        return tiers.Action("pay", f"Spend money through {service}", site=host, target=host, why_never=_MONEY_NEVER)
    if method == "DELETE":
        kind, say = "delete", f"Delete something on {service}"
    elif words & _PUBLISH_WORDS:
        kind, say = "publish", f"Publish on {service}"

    elif words & _SEND_WORDS or host.startswith("hooks."):
        kind, say = "send", f"Send through {service}"
    else:
        return tiers.Action("write", f"Change something on {service}", site=host, target=host, inputs=inputs[:4000],
                            scope="", extra={"request": inputs[:200]})
    if len(inputs) > tiers.X_MAX_CHARS or tiers._consent().scrub(inputs) != inputs:

        return tiers.Action("hardline", say, site=host, why_never=(
            "Not done: a card can show at most 4,000 characters, with nothing that looks like a password or key. "
            "Shorten it or take that part out."))
    return tiers.Action(kind, say, target=host, site=host, inputs=inputs, scope="")


def _answer(out: dict) -> str:
    if out.get("ok"):
        result = out.get("result")
        return json.dumps(result if isinstance(result, (dict, list)) else {"result": result}, ensure_ascii=False)
    return json.dumps({"error": str(out.get("error") or "Not done: the hub didn't answer.")}, ensure_ascii=False)


def ask_for_api_key(args: dict, **kw) -> str:
    return _answer(mac._call("ask", args or {}, ASK_WAIT_SEC, session_id=kw.get("session_id") or "",
                             area="service-keys"))


def service_api(args: dict, **kw) -> str:
    args = args or {}
    out = mac._call("call", args, CALL_WAIT_SEC, session_id=kw.get("session_id") or "", area="service-keys")
    error = str(out.get("error") or "")
    method = str(args.get("method") or "GET").upper()
    if not out.get("ok") and method not in ("GET", "HEAD") and "reach the hub: " in error \
            and "Connection refused" not in error:

        return json.dumps({"error": home.personalize("That call didn't confirm, so it may or may not have gone "
                                                     "through. Don't send it again. Check, and tell Colin.")})
    return _answer(out)
