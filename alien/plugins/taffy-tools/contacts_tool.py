""
















import json
import re

from . import consent, home

ACTIONS = ("find", "add")
NAME_MAX = 100
_PHONE = re.compile(r"[0-9+\-(). ]+")
_EMAIL = re.compile(r"[^@\s]+@[^@\s.][^@\s]*\.[^@\s.]+")

SCHEMA = {
    "name": "contacts",
    "description": (
        "Colin's Contacts, the Apple address book that syncs to his iPhone. action 'find' looks up one person or "
        "company by query: a name, a phone number or an email. It answers with up to 10 matches and their phones, "
        "emails, addresses and birthday. Use it before texting or emailing someone he names, or when he asks for "
        "someone's number or address. action 'add' saves a new contact when he asks you to: given_name, family_name "
        "or organization, and optional phone and email. A new contact goes through at once; mention it in one line. "
        "If he already has a contact by that name, nothing is added: tell him what you would change, since changing "
        "or deleting a contact is his call. Zero must be open on his Mac."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "query": {"type": "string", "description": "find: a name, phone number or email."},
            "given_name": {"type": "string", "description": "add: first name."},
            "family_name": {"type": "string", "description": "add: last name."},
            "organization": {"type": "string", "description": "add: company, or the name for a business contact."},
            "phone": {"type": "string", "description": "add: phone number, as Colin gave it."},
            "email": {"type": "string", "description": "add: email address."},
        },
        "required": ["action"],
    },
}


class Invalid(ValueError):
    ""


def schema() -> dict:
    s = json.loads(json.dumps(SCHEMA))
    s["description"] = home.personalize(s["description"])
    for prop in s["parameters"]["properties"].values():
        if "description" in prop:
            prop["description"] = home.personalize(prop["description"])
    return s


def _text(args: dict, key: str, limit: int) -> str:
    v = args.get(key)
    if v is None:
        return ""
    if not isinstance(v, str):
        raise Invalid(f"{key} must be text.")
    if len(v.strip()) > limit:
        raise Invalid(f"{key} is too long (at most {limit} characters).")
    return v.strip()


def name_of(m: dict) -> str:
    ""
    person = " ".join(x for x in (m.get("given_name"), m.get("family_name")) if x)
    return person or m.get("organization") or ""


def parse(args: dict) -> dict:
    ""
    args = args if isinstance(args, dict) else {}
    action = args.get("action")
    if action not in ACTIONS:
        raise Invalid(f"action must be one of {', '.join(ACTIONS)}.")
    m = {"action": action}
    if action == "find":
        m["query"] = _text(args, "query", 100)
        if len(m["query"]) < 2:
            raise Invalid("find needs a name, phone number or email in query.")
        return m
    m.update(given_name=_text(args, "given_name", NAME_MAX), family_name=_text(args, "family_name", NAME_MAX),
             organization=_text(args, "organization", NAME_MAX), phone=_text(args, "phone", 40),
             email=_text(args, "email", 254))
    if not name_of(m):
        raise Invalid("add needs a given_name, family_name or organization.")
    if m["phone"] and not (_PHONE.fullmatch(m["phone"]) and 7 <= sum(c.isdigit() for c in m["phone"]) <= 15):
        raise Invalid(f"{m['phone']} is not a phone number.")
    if m["email"] and not _EMAIL.fullmatch(m["email"]):
        raise Invalid(f"{m['email']} is not an email address.")
    return m


def classify(args: dict):
    ""


    from . import tiers
    try:
        m = parse(args)
    except Invalid:
        return None
    if m["action"] == "find":
        return None
    return tiers.Action("write", f"Add {name_of(m)} to your Contacts",
                        inputs="\n".join(x for x in (name_of(m), m["phone"], m["email"]) if x),
                        extra={"request": name_of(m)})


def contacts_tool(args: dict, **kwargs) -> str:
    try:
        m = parse(args)
    except Invalid as e:
        return json.dumps({"error": str(e)})
    if m["action"] == "add" and consent.test_mode():
        return json.dumps({"test_mode": True, "would_be": "added", "contact": m,
                           "note": "Test mode: nothing was added."}, ensure_ascii=False)
    from . import mac
    reply = mac._call("contacts", m, 40, session_id=kwargs.get("session_id") or "")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "Contacts didn't answer."}, ensure_ascii=False)
    result = reply.get("result")
    if isinstance(result, dict) and result.get("added") is False:

        return json.dumps({"error": result.get("note") or "A contact by that name exists; nothing was added.",
                           "existing": result.get("existing")}, ensure_ascii=False)
    return json.dumps({"result": result}, ensure_ascii=False)
