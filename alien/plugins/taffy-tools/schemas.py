"""Tool schemas for the Taffy team."""

CONNECTORS = {
    "name": "connectors",
    "description": (
        "Use Colin's connected accounts: his Trello board (\"Colin School and Projects\"), Google Calendar, "
        "Google Drive and Google Sheets (including the household finance sheet), Gmail search and reading, and any "
        "other account connected in his Claude account, such as Notion, Slack or Linear (service is its name in "
        "lowercase with underscores, for example notion, slack, linear or hugging_face). "
        "Describe the whole job in one plain-English request, including exact names, dates and times "
        "(America/New_York), for example: 'Move the card \"Form A\" to Doing and put \"Peer replies\" in Next' "
        "or 'List calendar events from Oct 8 to Oct 11'. The request runs with only that service's tools and "
        "returns what was done or found. It cannot send email. Sending, posting, sharing, inviting, deleting, "
        "deploying and merging in other accounts need Colin's okay, which the request asks for. It never pays, "
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
                                       "connected account by name (notion, slack, linear and so on). Sheets live "
                                       "under drive."},
            "request": {"type": "string", "description": "The complete request in plain English."},
        },
        "required": ["service", "request"],
    },
}


TALLY = {
    "name": "tally",
    "description": (
        "Read Colin and Riley's bank and card transactions (Plaid). It has no balances: never state a balance "
        "from it. "
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
    """CONNECTORS said for this home: its board (or none), the plan whose connectors it uses, its person and time
    zone."""
    from . import home
    s = home.personalize_schema(CONNECTORS)
    text = s["description"]
    board = home.trello()
    colin_board = home.personalize('his Trello board ("Colin School and Projects"), ')
    if not board:
        text = text.replace(colin_board, "")
    elif board.get("name") and board.get("board_id") != "AbCd1234":
        text = text.replace(colin_board, home.personalize("his Trello board (") + f'"{board["name"]}"), ')
    if home.connectors() == "chatgpt":
        text = text.replace("Claude account", "ChatGPT account")
    s["description"] = text
    return s


def tally() -> dict:
    """TALLY said for this home. Tally is set up only in Colin's home today."""
    from . import home
    s = home.personalize_schema(TALLY)
    if not home.is_colin():
        s["description"] = s["description"].replace(f"{home.first_name()} and Riley's",
                                                    f"{home.first_name()}'s household")
    return s
