""



















ROUTES = [
    ("accounts", "ask", "KIND", 1, "zip", "person", "a Zip asking the person to connect an account, with its reason"),
    ("accounts", "ask_app", "KIND", 1, "zip", "person", "a Zip asking the person to connect an app"),
    ("accounts", "ask_mailbox", "KIND", 1, "zip", "person", "a Zip asking the person to connect a mailbox"),
    ("app", "_decide_signin", '"signin_decision"', 4, "hub", "record", "a receipt of the person's Allow, Decline or sign-in answer"),
    ("app", "_after_x_post", '"signin_decision"', 1, "hub", "record", "a receipt of a post going out after the person's okay"),
    ("app", "_close_draft", '"signin_decision"', 1, "hub", "record", "a receipt that a Zip closed a draft unsent"),
    ("chatquestions", "settle", '"signin_decision"', 1, "hub", "record", "a receipt that the person's chat words answered a card"),
    ("choices", "_publish_new", '"choices"', 1, "zip", "person", "a Zip's question card"),
    ("claudeaccounts", "_tell", '"checkin"', 1, "hub", "person", "hub notice about a Claude account"),
    ("donecard", "_post", "KIND", 1, "hub", "record", "the To-Do card after a finished to-do: what finished and what is left"),
    ("donecard", "_say", '"message"', 1, "model", "person", "Zero's one line over a to-do card for something that finished by itself"),
    ("events", "routine_created", "KIND", 1, "hub", "record", "a scheduled job a Zip added"),
    ("events", "memory_updated", "KIND", 1, "hub", "record", "a Zip saved a memory"),
    ("events", "stopped", "KIND", 1, "hub", "record", "a stopped turn"),
    ("events", "mail_sent", "KIND", 1, "hub", "record", "an email a Zip sent"),
    ("gotdone", "_open_row", '"message"', 1, "model", "person", "the recap of what got done, written as the Zip"),
    ("gotdone", "post_recap", '"message"', 1, "model", "person", "the recap of what got done, written as the Zip"),
    ("kit", "_publish", "KIND", 1, "zip", "person", "a Zip's checklist or form card"),
    ("mac", "_card", '"mac_ask"', 1, "zip", "person", "a Zip asking to use the person's Mac"),
    ("mac", "_save", '"mac"', 1, "hub", "record", "what a Zip did on the person's Mac"),
    ("maccodex", "_line", '"mac"', 1, "hub", "record", "what a coding session did on the person's Mac"),
    ("macqueue", "wait", "CARD_KIND", 1, "hub", "person", "the Waiting for Your Mac card, with Go Ahead"),
    ("mailcard", "publish", "KIND", 1, "zip", "person", "a Zip's email draft card"),
    ("newzip", "first_turn", '"message_local"', 1, "zip", "person", "a new Zip's first hello"),
    ("nightly", "_post", '"message"', 1, "model", "person", "the night report, written as Zero"),
    ("notices", "publish", '"choices"', 1, "model", "person", "a question the hub's model raises as Zero"),
    ("notices", "post_brief", '"checkin"', 1, "hub", "person", "template: the Open Questions paragraph"),
    ("prepared", "send_approved", '"message"', 1, "zip", "person", "a Zip's reply after a prepared send"),
    ("problems", "tell", '"checkin"', 1, "hub", "person", "hub notice about a problem report"),
    ("problems", "tell_fixed", '"checkin"', 1, "hub", "person", "a quiet gray line: \"Fixed: <the sentence>\""),
    ("project", "publish", '"project"', 1, "hub", "person", "a project card the hub put together"),
    ("purchases", "_pay_card", "PAY_KIND", 1, "zip", "person", "a Zip asking the person to pay"),
    ("purchases", "_receipt", "RECEIPT_KIND", 1, "hub", "record", "a purchase receipt"),
    ("recheck", "_settle_card", '"signin_decision"', 1, "hub", "record", "a receipt that a draft went unsent"),
    ("resultcard", "publish", "KIND", 1, "zip", "person", "a file a Zip made for the person"),
    ("reviewcard", "answered_line", '"signin_decision"', 1, "hub", "record", "the person's answer on a review card"),
    ("reviewcard", "answered_words", '"signin_decision"', 1, "person", "record", "the person's own words on a review card"),
    ("service_keys", "ask", "accounts.KIND", 1, "zip", "person", "a Zip asking for a service key"),
    ("signin", "ask", "KIND", 1, "zip", "person", "a Zip asking the person to sign in"),
    ("signin", "park", "KIND", 1, "zip", "person", "a step a Zip parked for the person"),
    ("team", "_feed", '"team"', 1, "zip", "zip", "a message between two Zips"),
    ("team", "_feed", '"team"', 1, "zip", "person", "a Zip's message to or from the person in a team exchange"),
    ("teams", "propose", '"team_proposal"', 1, "zip", "person", "Zero proposing a team"),
    ("teams", "post", '"team"', 1, "zip", "zip", "a message in a Zip Team's thread"),
    ("teams", "post", '"team"', 1, "zip", "person", "a team member's ask of the person, in its thread"),
    ("teams", "_changed", "kind", 1, "hub", "record", "a signal that a team's plan or needs changed"),
    ("textcard", "publish", "KIND", 1, "zip", "person", "a Zip's text message draft card"),
    ("turns", "_run_measured", '"message_local"', 1, "zip", "person", "the Zip's reply to the person's typed message"),
    ("turns", "follow_up", '"message"', 1, "zip", "person", "the Zip's reply from a background turn"),
    ("turns", "wake", '"message"', 1, "zip", "person", "the Zip's reply from a wake"),
    ("turns", "say_stopped", '"message_local"', 1, "zip", "person", "what the Zip had written before he stopped it"),
    ("twofactor", "ask", "KIND", 1, "zip", "person", "a code a site sent the person, for a Zip's sign-in"),
    ("voice", "_after_hang_up", '"checkin"', 2, "zip", "person", "the call's wrap-up reply"),
    ("voice", "_record", '"call"', 1, "hub", "record", "a call's record and summary"),
    ("wallet", "ask", "REQUEST_KIND", 1, "zip", "person", "a Zip asking for a card"),
    ("watcher", "raise_parked", '"task_blocked"', 1, "hub", "record", "a record that a block's card went up"),
    ("board", "_reopen_rows", '"task_blocked"', 1, "hub", "record", "a withdrawn card's row, back when its task still waits"),
    ("watcher", "relay_watch", '"message"', 1, "zip", "person", "the Zip's report on a watch"),
    ("watcher", "relay_watch", '"checkin"', 1, "hub", "person", "what a watch found, when its Zip couldn't tell it"),
    ("watcher", "scan_cron", '"message"', 1, "zip", "person", "a scheduled job's report, in the Zip's words"),
    ("watcher", "scan_cron", '"checkin"', 2, "zip", "person", "a scheduled job's report, or its one buzz line"),
    ("watcher", "scan_jobs", '"checkin"', 3, "hub", "record", "template: a scheduled job added, changed, paused or removed"),
    ("watcher", "scan_outbox", '"image"', 1, "zip", "person", "a picture a Zip shared"),
    ("watcher", "scan_consent", '"signin_request"', 2, "zip", "person", "a Zip asking for the person's okay, with its reason"),
    ("watcher", "scan_consent", '"checkin"', 1, "hub", "record", "a sign-in line"),
    ("watcher", "scan_consent", '"action"', 1, "hub", "record", "a receipt of an okayed action"),
    ("watcher", "tell_expired", '"signin_decision"', 1, "hub", "record", "a card that ran out of time"),
    ("watcher", "tell_plainly", '"message"', 1, "model", "person", "board news rewritten as Zero"),
    ("watcher", "_team_update", '"message"', 1, "zip", "person", "Zero's reply to a team update"),
    ("watcher", "board_event", "kind", 1, "zip", "hub", "a board task's title and the Zip's note on it"),
    ("watcher", "board_event", "kind", 1, "hub", "record", "a record that a parked block's card went up"),
    ("watcher", "board_event", "kind", 1, "model", "person", "a finished task's one plain sentence for the person"),
    ("watcher", "board_event", "kind", 1, "hub", "person", "\"I finished <task>.\" when no sentence was written"),
    ("watcher", "board_event", "kind", 1, "zip", "person", "a failed task's notice, as the board gave it"),
    ("wayforward", "_say_stopped", '"message"', 1, "model", "person", "a stopped task's one line, written as the Zip"),
    ("passcard", "open_cards", "KIND", 2, "hub", "person", "the hand-off card in each Zip's chat: the topic, its folded note and how it stands"),
    ("withdrawn", "settle", '"message"', 1, "hub", "person", "a quiet gray line: \"No longer needed: <question>\""),
    ("zipapps", "setup", "KIND", 1, "hub", "person", "the start card listing apps to connect"),
]

BY = ("zip", "model", "hub", "person")
FOR = ("person", "hub", "zip", "record")


def as_markdown() -> str:
    ""
    rows = ["| Module | Function | Kind | Calls | Written by | Written for | The words |", "|---|---|---|---|---|---|---|"]
    for module, function, kind, calls, by, for_, words in ROUTES:
        rows.append(f"| {module} | {function} | {kind.strip(chr(34))} | {calls} | {by} | {for_} | {words} |")
    return "\n".join(rows)
