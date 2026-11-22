""

CONNECTORS = {
    "name": "connectors",
    "description": (
        "Use Colin's connected accounts: his Trello board (\"@BOARD@\"), Google Calendar, "
        "Google Drive, Sheets, Docs and Slides (including the household finance sheet), Gmail search and reading, and any "
        "other account connected in his Claude account, such as Notion, Slack or Linear (service is its name in "
        "lowercase with underscores, for example notion, slack, linear or hugging_face). "
        "Describe the whole job in one plain-English request, including exact names, dates and times "
        "(America/New_York), for example: 'Move the card \"Form A\" to Doing and put \"Peer replies\" in Next' "
        "or 'List calendar events from Oct 8 to Oct 11'. The request runs with only that service's tools and "
        "returns what was done or found. Only the fields reach anyone or remove anything: send_to (who a message or "
        "post goes to), share_with, delete and publish, and guests and notify_guests for the calendar. Without them a "
        "change is made, and you mention it to Colin in one line; with them Colin gets a card first. Words in request "
        "never send, share, delete or publish. It never pays, "
        "refunds, charges, transfers money or changes billing. Say a change plainly ('Close ENG-12'), because a "
        "request that only reads can't change anything. Each call takes about 10 to 30 seconds, so "
        "batch related requests into one call. If the account isn't connected, Colin gets a card in your chat to "
        "connect it and the result tells you to stop; a note arrives when it's connected. If there is no such "
        "account, the result lists the ones there are."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "service": {"type": "string",
                        "description": "Which connected account to use: gmail, calendar, drive, trello, or another "
                                       "connected account by name (notion, slack, linear and so on). Google "
                                       "Sheets, Docs and Slides live under drive, which reads and edits them."},
            "request": {"type": "string", "description": "The complete request in plain English."},
            "guests": {"type": "array", "items": {"type": "string"},
                       "description": "calendar only: email addresses to invite. Each one gets an invitation from "
                                      "Colin's account. Only these fields invite anyone: a name in request is part of "
                                      "the event's title or notes, and the run can't add a guest that isn't here."},
            "notify_guests": {"type": "boolean",
                              "description": "calendar only: email the event's current guests about this change."},
            "send_to": {"type": "array", "items": {"type": "string"},
                        "description": "Who this request sends or posts to, in Colin's name: email addresses for "
                                       "gmail, or the channel or person for another account (Slack, a comment). "
                                       "The run can send only to these. Leave it out for reads, searches and drafts."},
            "share_with": {"type": "array", "items": {"type": "string"},
                           "description": "Who gets access to a file, board or page (email addresses for Drive)."},
            "delete": {"type": "boolean",
                       "description": "true when the request deletes something for good. Archiving, moving and "
                                      "labeling are not deletes."},
            "publish": {"type": "boolean",
                        "description": "true when the request publishes, deploys, merges or releases."},
        },
        "required": ["service", "request"],
    },
}


TALLY = {
    "name": "tally",
    "description": (
        "Read Colin and Becky's bank and card transactions (Plaid). It has no balances: never state a balance "
        "from it, and use the bank tool's accounts action for balances, available credit and pending charges. "
        "action 'purchases' returns purchases and income, newest first, optionally limited to a date range; "
        "'status' returns the linked banks and accounts (names and last four digits, no balances), which bank needs "
        "Colin to sign in again, and the last sync time; "
        "'refresh' asks the banks for new transactions first (at most once a minute) and then returns status. "
        "Amounts use the bank's sign: money out is positive, refunds are negative. Pending items can post later "
        "with a new id; count each purchase once."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["purchases", "status", "refresh"]},
            "start_date": {"type": "string", "description": "YYYY-MM-DD, inclusive (purchases only)."},
            "end_date": {"type": "string", "description": "YYYY-MM-DD, inclusive (purchases only)."},
        },
        "required": ["action"],
    },
}


def connectors() -> dict:
    ""

    from . import home
    s = home.personalize_schema(CONNECTORS)
    text = s["description"]
    board = home.trello()
    template = home.personalize('his Trello board ("@BOARD@"), ')

    named = str((board or {}).get("spoken_name") or (board or {}).get("name") or "")
    if not board:
        text = text.replace(template, "")
    elif named:
        text = text.replace(template, home.personalize("his Trello board (") + f'"{named}"), ')
    else:
        text = text.replace(template, home.personalize("his Trello board, "))
    if home.connectors() == "chatgpt":
        text = text.replace("Claude account", "ChatGPT account")
    s["description"] = text
    return s


def tally() -> dict:
    ""
    from . import home
    s = home.personalize_schema(TALLY)
    if not home.is_owner_home():
        s["description"] = s["description"].replace(f"{home.first_name()} and Becky's",
                                                    f"{home.first_name()}'s household")
    return s
