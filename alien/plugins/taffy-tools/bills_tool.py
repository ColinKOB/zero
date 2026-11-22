""








import json

from . import home, mac

SCHEMA = {"name": "bills", "description": (
    "The one record of each bill Colin owes: who it is to, the amount, the due date and how that date is known, where "
    "each fact came from, and whether it is open, paid or settled by Colin. Read it with list before you state, "
    "summarize or warn about any bill; a note, memory or email that disagrees with it is older. Each time you read a "
    "bill (a statement, a due notice, Colin's words), call record with what it says: the hub matches it to the bill "
    "already on record, keeps a notice about a payment Colin settled from reopening it, and opens the next payment "
    "when a new one is due. due_from is statement (printed on the bill or statement), said (Colin's words) or "
    "estimated (worked out, such as last month's date plus a month); say an estimated date as an estimate. When "
    "Colin says a bill is paid, handled, deferred or replaced, call settle with state paid or settled and his words "
    "in said. A bill settled or paid is never raised again by an older notice. It cannot pay or move money."),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["list", "record", "settle"]},
        "payee": {"type": "string", "description": "record: who the bill is to, like \"Maple Loans\"."},
        "what": {"type": "string", "description": "record, optional: a few words, like \"student loan\"."},
        "amount": {"type": "number", "description": "record: the amount due, in dollars."},
        "due": {"type": "string", "description": "record: the due date, like 2026-11-18."},
        "due_from": {"type": "string", "enum": ["statement", "said", "estimated"],
                     "description": "record: how the due date is known."},
        "source": {"type": "string", "description": "record: where this came from in a few words, like "
                                                    "\"Maple Loans email of Nov 3\" or \"Colin in chat\"."},
        "seen": {"type": "string", "description": "record, optional: the date the source was written, like 2026-11-03."},
        "reference": {"type": "string", "description": "record, optional: the last four characters of the account "
                                                       "or statement reference. Never a whole account number."},
        "autopay": {"type": "boolean", "description": "record, optional: true when the bill pays itself."},
        "bill": {"type": "integer", "description": "settle: the bill's number from list or record."},
        "state": {"type": "string", "enum": ["paid", "settled", "open"],
                  "description": "settle: paid, settled (Colin handled, deferred or replaced it), or open again."},
        "said": {"type": "string", "description": "settle: what Colin said, in his words, and when if known."}},
        "required": ["action"]}}


def schema() -> dict:
    return home.personalize_schema(SCHEMA)


def bills(args: dict, **kwargs) -> str:
    args = args if isinstance(args, dict) else {}
    if args.get("action", "list") not in ("list", "record", "settle"):
        return json.dumps({"error": "action must be list, record or settle."})
    reply = mac._call("bills", args, 60, session_id=kwargs.get("session_id") or "", area="team")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "Could not reach the bills."})
    return json.dumps({"result": reply.get("result")})
