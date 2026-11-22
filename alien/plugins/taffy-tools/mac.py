""






import base64
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import consent, gate, home, tiers


def hub() -> str:
    ""
    return home.hub_url()


def key_file() -> Path:
    return home.path("hub_state", consent.REAL_HOME) / "internal-key"

RULES = (
    "Your own computer on alien is the default for browsing, except for a page it can't get past (a bot block, a "
    "refusal): open that page on his Mac. For a human check, call ask_to_sign_in with purpose='challenge', which sends "
    "you there first. Never press a human check on the Mac or anywhere else. Colin's Mac holds his projects, folders, Mail and "
    "apps. You may use its files and commands for your work without his words: on a team, in a scheduled job, on a "
    "board task, or when a teammate asks. That work uses mac_run (mdfind, ls, cat, git) and mac_copy, and when it "
    "came from Zero or him, mac_start says whether his screen and apps are open too: they are while he's away from "
    "the Mac, and a card asks him when he's on it. In your chat with him, use it when he asks for something on his Mac or his computer, or, if "
    "he set Zips to use his Mac automatically, when something he asked for needs it. Each chat turn says which he "
    "chose. Call mac_start first with the "
    "task (when his Mac is set to automatic, your first Mac step starts it for you). When you are answering him in "
    "chat, add his own words. He approves it on the Mac or his phone, or it "
    "starts at once when his Mac is set to automatic. Before you ask him for files, code or links, look on alien "
    "first: your work folder and its from-mac folder (from-mac/INDEX.md lists what was copied), the team folder and "
    "its from-mac folder, and the shared folder. If it isn't there and his Mac is on, find it there with mac_run and "
    "mdfind -name (or ls ~/Projects ~/Desktop ~/Documents), then mac_copy it so the next job doesn't need his Mac. "
    "For a git repo with a GitHub remote, cloning it from GitHub on alien works too, but mac_copy also brings "
    "uncommitted work. If his Mac isn't connected, doesn't answer or he says no, carry on with what you have: on a "
    "team, raise it with team_need; in a chat, tell him in one line. You work beside him with your own "
    "cursor: prefer mac_ui and mac_press, which never touch his mouse; mac_click and typing go to the app you are "
    "working in, not his. "


    "While he is at the Mac, ask 'Can I use your mouse for a moment?' before using his real mouse (mac_click with "
    "real true). Never type passwords (sign-ins use the saved-logins flow). Only the four stops wait for his yes, "
    "and the app's card asks him, so never ask \"should I?\" in chat as well. Call mac_done when finished. If a step is his before "
    "the task is finished, call hand_over with place mac instead of mac_done. When searching his "
    "files, never filter out /Library/: iCloud Drive is ~/Library/Mobile Documents/com~apple~CloudDocs and Pages, "
    "Numbers and Keynote documents are in ~/Library/Mobile Documents/com~apple~Pages (and ~Numbers, ~Keynote). "
    "Don't hide errors with 2>/dev/null. To gather files to copy or share, stage them in a folder from mktemp -d "
    "/tmp/zero-XXXXXX on his Mac, which you may clear without asking; never make a new dotfolder in his home "
    "folder. If a result has folders_macos_blocks, say plainly that macOS hasn't let "
    "Zero into those folders and how he can allow it, instead of saying the file isn't on his Mac. "
    "Finding files is fastest with mac_run and mdfind (mdfind -name halloween) rather than find in iCloud Drive. "
    "To read what an app on his Mac holds (notes, meetings, history), first read its own files with mac_run in "
    "~/Library/Application Support/<app>, or for App Store and Apple apps in ~/Library/Group Containers and "
    "~/Library/Containers (Apple Notes: ~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite), opening a "
    "database as sqlite3 \"file:<path>?mode=ro\"; use the screen only when that fails. "
    "To get several clicks and fields done at once, use mac_steps with plain steps; it stops and shows the screen "
    "when it is not sure. Use mac_open with front true only when foreground focus is required. It keeps the app "
    "in front until a saved-login fill finishes or fails, another app is opened, or the task ends. "
    "A window in another Space may still need Colin to move it. "
    "To use a saved login in Safari or Chrome on the Mac, use browser_vault_list to find its login handle, "
    "open the browser with front true, focus the "
    "password field, then call mac_fill_login. It fills privately under the same Allow rules as your own browser. "
    "Submit the form with mac_key, then reopen the browser with front true before mac_login_status. "
    "The status check returns focus afterward. A fill alone never proves sign-in succeeded. "
    "Only Zero reads private places: Zero can read every folder in his home on the Mac, dot-folders and Library "
    "included, so never say one of his coding chats is out of reach. A mac_run that names such a folder, or sets "
    "private_read true, can read everything but has no internet and changes no files, so read first and act in a "
    "second command. Keys and passwords come back as [removed]; leave them out of anything you send. To search his "
    "chats, use a few separate words, since he paraphrases. T3 Code's database is ~/.t3/userdata/statev2.sqlite. It is "
    "11 GB and in live use, so open it as sqlite3 \"file:$HOME/.t3/userdata/statev2.sqlite?mode=ro\" and never copy "
    "it. orchestration_v2_projection_threads has thread_id and title. orchestration_v2_projection_messages and "
    "orchestration_v2_projection_turn_items have thread_id and payload_json, so search with payload_json LIKE "
    "'%word%' AND payload_json LIKE '%other%', join to the threads for the title, and add LIMIT. Search the messages "
    "first, which takes seconds, then the turn items, which takes about a minute and holds what the messages leave "
    "out. Claude Code and Codex transcripts are in ~/.claude/projects and ~/.codex/sessions: "
    "rg -il 'word' those folders, then read the matches with rg. On alien too, Zero can read ~/.claude, ~/.codex and "
    "the Zips' settings with a plain read command, and any database there opens read-only."
)

SCHEMAS = {
    "mac_start": {
        "name": "mac_start",
        "description": "Start using Colin's Mac for one task. " + RULES,
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string", "description": "What you will do on the Mac, in one plain sentence."},
            "colin_said": {"type": "string", "description": "Colin's own words asking for it, quoted exactly. "
                                                            "Needed when you are answering him in your chat or on a "
                                                            "call. Leave it out for team work, scheduled jobs, board "
                                                            "tasks and teammates' questions."},
        }, "required": ["task"]},
    },
    "mac_open": {
        "name": "mac_open",
        "description": "Open a web address, a file path (~ allowed), an app by name, or a System Settings pane "
                       "(x-apple.systempreferences: links, for example "
                       "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility) on Colin's Mac. "
                       "A page already showing in a Safari window comes forward as it is, with what was filled in, so to get "
                       "back to a page in another window, open its address. A dark or black screenshot never means the Mac is off "
                       "or asleep: read the window with mac_ui and carry on. "
                       "When you are answering Colin in his chat, it comes to the front, since he asked to see it; set "
                       "front false to open it quietly. Other work opens quietly and moves one work window to a spare "
                       "display when possible. Set front true when "
                       "foreground focus is required, including saved-login fills. Focus returns to Colin's previous app "
                       "after the fill finishes or fails. If Colin switches "
                       "to another app, leave his choice alone. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string"},
            "front": {"type": "boolean", "description": "Keep the app in front when required, including through a saved-login fill. Defaults to true in your chat with Colin, false elsewhere."},
        }, "required": ["target"]},
    },
    "mac_run": {
        "name": "mac_run",




        "description": "Run a zsh command on Colin's Mac in his home folder and get its output. Put every read you "
                       "already know you need in one call, one per line, each after an echo naming it (echo '## "
                       "notes'), so their outputs come back together; wait for a result only when the next command "
                       "depends on it. Up to 120 s by "
                       "default. Needs mac_start first. sudo and disk erasing are blocked, and so are keychain and "
                       "key files, except in Zero's private_read. "
                       "In a folder his request names you may change his files in place (cat new > file, a script "
                       "that writes the file, or cp -X); the Mac copies each original to ~/Zero Work/Originals first. "
                       "When the result has originals, tell him in one line where the old version is. Moving, "
                       "renaming or deleting a file that was there stays refused. "
                       "railway commands are signed in for your own Railway projects when the command is railway "
                       "alone and names --project and --environment. "

                       "If a railway command signed in that way still says Unauthorized, or another tool on his Mac "
                       "says it isn't logged in, only Colin can log it in: ask him to run its login command (railway "
                       "login, gh auth login) in Terminal on his Mac. Never ask him to sign in to its website for "
                       "that, which signs in your own browser, not his Mac. "
                       "When Colin wants a file you found, link it in your reply as "
                       "[your paper](zero-file:<its full path>): his tap opens it in its own app on his Mac.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "number", "description": "Seconds, at most 600."},
            "private_read": {"type": "boolean", "description": "Zero only: read any folder in his home in this run, "
                                                               "with no internet and no file changes. Other Zips: "
                                                               "this does nothing."},
        }, "required": ["command"]},
    },
    "mac_claude": {
        "name": "mac_claude",
        "description": "Hand coding work in one folder under ~/Projects on Colin's Mac to Claude Code, and get its "
                       "report back. Colin sees the brief and the folder on a card and taps Start; no mac_start is "
                       "needed. Claude Code can read and change files and run commands (tests, builds) in that "
                       "folder only, with no internet, for up to 10 minutes. Write the brief so it stands alone: the "
                       "goal, the files that matter, how to check the work (the test command), and what to report. "
                       "Then tell Colin what it did in plain words, and check its claims yourself with mac_run when "
                       "it matters.",
        "parameters": {"type": "object", "properties": {
            "brief": {"type": "string", "description": "Self-contained instructions, at most 12,000 characters."},
            "folder": {"type": "string", "description": "The project folder, written as ~/Projects/<folder>."},
        }, "required": ["brief", "folder"]},
    },
    "mac_codex": {
        "name": "mac_codex",
        "description": "The first choice for doing a job in an app on Colin's Mac (Mail, Messages, Finder, Notes, Safari "
                       "with his sign-ins, any app). Terra is for computer use: it clicks and types in his apps' windows "
                       "from the background, so it never takes his screen, and it does the whole job in one run. Use it "
                       "before mac_ui and mac_click, which drive his screen one step at a time. "



                       "To show him a page or app he asked to see, open it directly with mac_open; Terra is for work in "
                       "an app. "


                       "When Colin wants ChatGPT or Codex on his Mac to do a job instead of a Zip, this is the route: "
                       "start a new run with the job itself as the brief, what to find and what to report. Terra is "
                       "that helper, so the run is the new chat and its answer comes back to you. Never brief it to "
                       "open or control the ChatGPT app. A web page that needs no sign-in of his stays in your own browser, and "
                       "a command stays with mac_run. Don't use it for coding: coding goes to mac_claude. Coding through "
                       "Terra is an absolute last resort and a worst case, only after mac_claude failed too, and tried "
                       "must say so. Needs mac_start first. Give brief and folder. It returns at once. When Terra finishes "
                       "or has a question, a note wakes you: call this tool with session to read it, and with session and "
                       "reply to answer it. Tell Colin the result in plain words. Terra also has that folder, the internet, "
                       "and Google Drive, Sheets and Docs in his personal Google account and anything shared with it. It "
                       "can't share or delete Drive files, commit with git, or open Keychain Access, Passwords or Terminal. "
                       "It hands final submits, purchases, sends to someone new, permanent deletes and other people's "
                       "private data back to you, and those still need Colin's okay. At a final submit it stops with "
                       "READY: and a picture of the page, and reading the run says how to ask him. Keep that run open, "
                       "and never press that step yourself with mac_open, mac_ui or mac_press. When his own message says yes to "
                       "the final submit or send, start or answer Terra in that same turn: the home server quotes his "
                       "message to Terra, and Terra presses that one button. A note that wakes you carries no words of "
                       "his, so a yes he gave earlier has to be asked again. Purchases always come back to him. "



                       "To brief Terra well: give the goal and every fact it needs, such as the site's address, the exact "
                       "items or values (course sections, the new address, sizes, his usual store), what is already true "
                       "(he is signed in, items already in the cart stay), and the stop line (stop before the final submit, "
                       "checkout or send, and report what that last screen says). Quote Colin's own words when he gave "
                       "permission, so Terra clicks through notices without asking. Don't add limits Colin didn't set: "
                       "Next, Continue and Save and continue on a form are steps to take, and only the final submit waits. "


                       "A site's notice, terms or privacy page on his own account is Terra's to accept: never tell Terra "
                       "to stop at one, and never ask him about one. "



                       "Fill his own details from saved facts and his own card in contacts (find his name), and start "
                       "Terra without asking him first. For answers only he can swear to, such as a jury "
                       "questionnaire's citizenship, convictions, pending charges or a condition that keeps him from "
                       "serving, give Terra the answer that fits what you know and keep a list of them: he checks them "
                       "on the review page before he presses. Ask before Terra starts only when the form can't go on "
                       "without a fact you can't find, such as a number only he holds. "


                       "Never put one of his saved "
                       "passwords in a brief: Terra uses the sign-ins already in his browsers. A number or code he handed "
                       "you for this job, such as the participant number and PIN printed on a letter or card he sent, "
                       "is not a saved password: put it in the brief so Terra signs in with it. When Terra reports BLOCKED or asks something "
                       "Colin's words or the facts you have already answer, reply on the same session instead of asking "
                       "Colin or starting a new run, since it keeps its window and its place. When a site makes a step "
                       "final on the spot (an add that enrolls at once, Place order), ask Colin one yes-or-no question. "
                       "Read the whole report for anything left undone, and tell Colin the result in one or two lines. "


                       "When reading a run shows needs_app, the home server adds that app for Terra by itself. Don't "
                       "ask him about it.",
        "parameters": {"type": "object", "properties": {
            "brief": {"type": "string", "description": "To start: self-contained instructions, at most 100,000 characters."},
            "folder": {"type": "string", "description": "To start: the folder it works in, written as ~/<folder>. "
                                                        "It is made if it isn't there. ~/Projects itself, ~/Documents, "
                                                        "~/Library and hidden folders are refused."},
            "tried": {"type": "string", "description": "To start, optional: what you already tried, if anything. Colin sees "
                                                       "this."},
            "model": {"type": "string", "enum": ["terra", "sol"], "description": (
                "To start, optional: sol runs it on Sol instead of Terra. Use sol when Colin asked for Sol for this "
                "kind of job; leave it out otherwise.")},
            "session": {"type": "string", "description": "The run's session, from starting it or from the note."},
            "reply": {"type": "string", "description": "With session: your answer to Terra, at most 100,000 characters."},
            "stop": {"type": "boolean", "description": "With session: end the run."},
            "allow_app": {"type": "boolean", "description": (
                "With session: add the app Terra was turned away from, and Terra carries on. No yes is needed; the "
                "home server usually does it first.")},
            "sign_in": {"type": "string", "description": (
                "With session, when reading the run says Terra is on a sign-in page: the handle of a login from "
                "browser_vault_list saved for exactly that site. The home server fills it into Terra's page; you and "
                "Terra never see the password.")},
        }},
    },
    "mac_screenshot": {
        "name": "mac_screenshot",
        "description": "See the app you are working in on the work window's display, drawn whole even where other "
                       "windows cover it; other apps' windows are left out. Returns image dimensions, screen point dimensions, and screen_origin_x "
                       "and screen_origin_y. Convert each image coordinate by its axis scale, then add that origin. "
                       "Origins can be negative on displays left of or above the main display. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {}},
    },
    "mac_ui": {
        "name": "mac_ui",
        "description": "List the buttons, fields, switches, rows, links and text in the app you are working in on "
                       "Colin's Mac, each with its center in screen points, a field's text and a switch's on or off. "
                       "Includes sheets and file pickers owned by that app, even when macOS runs them separately. "
                       "Lists only windows on the screen. "
                       "Faster and more exact than a screenshot. Give query to list only lines with that word. "
                       "Some apps (Signal, Slack, Discord, WhatsApp and other Electron apps) list only a few unnamed "
                       "buttons at first: wait about two seconds and call mac_ui again. Then press the search field "
                       "and the conversation rows by name with mac_press. If the list stays thin, use the keyboard "
                       "before coordinates: mac_key cmd+f (or cmd+k), mac_type the name, mac_key return. "
                       "Needs mac_start first.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Only lines containing this word, to keep the list short."},
        }},
    },
    "mac_press": {
        "name": "mac_press",
        "description": "Press an element without the mouse, by its number from the last mac_ui list or by its name "
                       "('Privacy & Security', 'Submit'). A number presses only while the app's front window is "
                       "the one that list read; otherwise nothing is pressed and you get a fresh list. "
                       "Rows in lists and sidebars are selected. Give text to fill a "
                       "field, or to choose that option in a pop-up menu. Action 'menu' opens its menu. It says what "
                       "the element reads afterwards and returns a short UI change summary, including focus and new sheets. "
                       "Use mac_screenshot only when that state is not enough. Prefer mac_steps for routine sequences. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {
            "element": {"type": "integer"},
            "name": {"type": "string", "description": "The element's words, when you don't have its number."},
            "text": {"type": "string"},
            "action": {"type": "string", "enum": ["press", "menu"]},
            "expect": {"type": "string", "description": (
                "What should change in the window after this press: the label of a count ('Cart', for the cart "
                "badge) or words that should appear ('Added to cart'). The result then says 'confirmed: ...' or "
                "'nothing changed', and a safe button is pressed once more when nothing did. Use it on Add to cart "
                "and every other press whose effect you need.")},
        }},
    },
    "mac_click": {
        "name": "mac_click",
        "description": "Click at global screen points in the chosen app with your own cursor. A point another "
                       "window covers is refused, and the refusal names that window and where it is. "
                       "In apps with a thin mac_ui list, press by name or use the keyboard first. "
                       "The click does not move his mouse. Use count 2 for a double click, button 'right' for a right click, "
                       "or to_x and to_y to drag. Set real true after asking Colin to lend you his mouse while he is at the Mac. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "number"}, "y": {"type": "number"},
            "button": {"type": "string", "enum": ["left", "right"]},
            "count": {"type": "integer"},
            "to_x": {"type": "number"}, "to_y": {"type": "number"},
            "real": {"type": "boolean"},
        }, "required": ["x", "y"]},
    },
    "mac_type": {
        "name": "mac_type",
        "description": "Type text into the focused field of the app you chose on Colin's Mac. Refused in password "
                       "fields. The field is read back, and it fails when the text didn't arrive while that app is "
                       "in the background. Returns a short UI change summary with focus and sheets. Use mac_screenshot only when that state is not enough. "
                       "Prefer mac_steps for routine sequences. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    },
    "mac_fill_login": {
        "name": "mac_fill_login",
        "description": "Fill a saved login in the frontmost Safari or Chrome page on Colin's Mac. Give only its "
                       "login handle from browser_vault_list. The site's own password field must be focused. The password "
                       "goes privately to that field, never through you or the clipboard. Uses the same Allow "
                       "rules as browser_vault_fill. Submit with mac_key, then call mac_login_status. Needs mac_start.",
        "parameters": {"type": "object", "properties": {
            "handle": {"type": "string", "description": "The saved login's handle, never its password."},
        }, "required": ["handle"], "additionalProperties": False},
    },
    "mac_upload": {
        "name": "mac_upload",
        "description": "Attach one file from its path on the Mac while an Open file picker is open. "
                       "It finds the app's own picker, enters the path, chooses the file, and checks whether the "
                       "picker closed and the page shows the file. It reports an unverified attachment plainly. "
                       "Use this after Browse files instead of a sequence of screenshots and shortcuts. "
                       "Copies for upload belong in a Zero temporary folder, never Downloads. The helper keeps "
                       "its staged copy through the task and removes it when mac_done ends the task. "
                       "Needs mac_start first. Attaching a file does not submit the page.",
        "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}},
                       "required": ["file_path"], "additionalProperties": False},
    },
    "mac_login_status": {
        "name": "mac_login_status",
        "description": "Check the Mac sign-in result without exposing filled details. Reports a two-factor "
                       "prompt, a bot check, a remaining login form, a Sign Out control, a changed page, or an unknown result. "
                       "A changed page alone does not prove sign-in succeeded. Needs mac_start.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "mac_key": {
        "name": "mac_key",
        "description": "Press a key or shortcut on Colin's Mac, such as 'return', 'cmd+l' or 'cmd+shift+t'. Returns a "
                       "short UI change summary with focus and sheets. Use mac_screenshot only when that state is not "
                       "enough. Prefer mac_steps for routine sequences. Needs mac_start first.",
        "parameters": {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]},
    },
    "mac_scroll": {
        "name": "mac_scroll",
        "description": "Scroll on Colin's Mac at a point. dy is lines: negative scrolls down, positive scrolls up. "
                       "If the point is refused or the wheel moves nothing, it presses Page Down or Page Up in the "
                       "app instead and says so; check with mac_screenshot that the right pane moved. "
                       "Needs mac_start first.",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "number"}, "y": {"type": "number"}, "dy": {"type": "integer"},
        }, "required": ["dy"]},
    },
    "mac_steps": {
        "name": "mac_steps",
        "description": "Prefer this for routine Mac sequences. Run 1 to 15 actions in one call: open, press by name "
                       "or element number, type, key, click, wait for text or a named element, or ui with a query. "
                       "Each object has action plus the matching mac tool's fields. For example: "
                       "[{action:'press',name:'Browse files'},{action:'wait',text:'Open',timeout:5}]. "
                       "Short sentences such as 'click Browse files' still work. It stops at the first failure and "
                       "reports the step. Each action keeps the normal safeguards. A screenshot is returned only on "
                       "failure or with include_screenshot true. Never send, buy, delete or submit without the "
                       "person's confirmation. Needs mac_start first; call mac_done after the task.",
        "parameters": {"type": "object", "properties": {
            "steps": {"type": "array", "minItems": 1, "maxItems": 15, "items": {}},
            "include_screenshot": {"type": "boolean"},
        }, "required": ["steps"]},
    },
    "mac_copy": {
        "name": "mac_copy",
        "description": "Copy a folder or file from Colin's Mac to alien, so later work doesn't need his Mac. It lands "
                       "in your work folder's from-mac folder, or the team folder's in team work, and replaces an "
                       "older copy of the same name. A git repo keeps its history, branches and uncommitted work. "
                       "Folders a build or install makes again (node_modules, build, dist, .venv and the like) are "
                       "left out. Up to about 2 GB. Find the path first with mac_run and mdfind -name. Needs "
                       "mac_start first.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "The folder or file on the Mac, from ~/ or /."},
        }, "required": ["path"]},
    },
    "mac_done": {
        "name": "mac_done",
        "description": "Hand Colin's Mac back as soon as the task is finished. If he has a step to do first, use "
                       "hand_over instead. Tick your checklist first: while a step is still in progress, the task "
                       "stays open.",
        "parameters": {"type": "object", "properties": {}},
    },
}

_step_schemas = []
for _action in ("open", "press", "type", "key", "click", "ui"):
    _params = SCHEMAS["mac_" + _action]["parameters"]
    _step_schemas.append({"type": "object", "properties": {
        **{k: v for k, v in _params["properties"].items() if k != "action"},
        "action": {"type": "string", "enum": [_action]},
    }, "required": ["action", *_params.get("required", [])], "additionalProperties": False})
_step_schemas.append({"type": "object", "properties": {
    "action": {"type": "string", "enum": ["wait"]}, "text": {"type": "string"}, "name": {"type": "string"},
    "timeout": {"type": "number", "minimum": 0, "maximum": 10},
}, "required": ["action"], "additionalProperties": False})
SCHEMAS["mac_steps"]["parameters"]["properties"]["steps"]["items"] = {
    "anyOf": [{"type": "string"}, *_step_schemas],
}


def _call(op: str, args: dict, timeout: float = 60, session_id: str = "", area: str = "mac") -> dict:
    try:
        key = key_file().read_text().strip()
    except OSError:
        return {"ok": False, "error": "The hub is not set up for Mac use yet."}
    body = {"bot_id": gate._bot(), "session_id": session_id, "args": args}
    origin_task = os.environ.get("HERMES_KANBAN_TASK")






    if (area == "mac" or (area, op) in (("team", "ask"), ("team", "context"), ("chat", "offer_choices"), ("chat", "ask_sign_in"),
                                        ("chat", "ask_wake_mac"), ("accounts", "ask_app"), ("chat", "mail_card"),
                                        ("errands", "wait"), ("chat", "code_session"), ("chat", "block_check"),
                                        ("chat", "build_check"))) \
            and origin_task and not gate._session_env("HERMES_CRON_SESSION"):
        body["origin_task"] = origin_task
    req = urllib.request.Request(f"{hub()}{area}/{op}", method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json", "x-taffy-internal": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"The hub refused that ({e.code})."}
    except Exception as e:
        if isinstance(e, TimeoutError) or (isinstance(e, urllib.error.URLError) and isinstance(e.reason, TimeoutError)):
            return {"ok": False, "error": "Could not reach the hub: timed out."}
        return {"ok": False, "error": f"Could not reach the hub: {e}"}


def _screenshot_result(result: dict):
    ""
    data = base64.b64decode(result.pop("jpeg_base64", "") or b"")
    summary = json.dumps({"screenshot": "attached", **result})

    with tempfile.TemporaryDirectory(prefix="mac-screen-") as tmp:
        path = Path(tmp) / "screen.jpg"
        path.touch(mode=0o600)
        path.write_bytes(data)
        try:
            from tools.vision_tools import _EMBED_MAX_DIMENSION, _resize_image_for_vision
            from tools.vision_tools_history_budget import resolve_embed_target_bytes
            url = _resize_image_for_vision(path, mime_type="image/jpeg", max_base64_bytes=resolve_embed_target_bytes(),
                                           max_dimension=_EMBED_MAX_DIMENSION, force_jpeg=True)
        except Exception:
            url = "data:image/jpeg;base64," + base64.b64encode(data).decode()
    return {"_multimodal": True, "text_summary": summary, "meta": {},
            "content": [{"type": "text", "text": summary + home.personalize("\n\nColin's Mac screen is attached.")},
                        {"type": "image_url", "image_url": {"url": url}}]}


TIMEOUT_RECOVERY = (
    "Retry only a read-only step, once, on your own without asking permission, while the existing Mac consent "
    "remains valid. If the second attempt fails, leave the Mac for now and keep going another way: the web, his "
    "connected accounts, a teammate, or what you already read. Then tell Colin the answer you found. Never repeat a "
    "send, purchase, deletion or other change whose outcome is unknown. Never renew or bypass expired or revoked "
    "consent."
)


def handler(op: str):
    timeouts = {"start": 150, "run": 700, "claude": 700, "screenshot": 45, "steps": 100, "upload": 100, "copy": 1500}

    def run(args: dict, **kwargs):
        if op == "fill_login":
            return _fill_login(args or {}, **kwargs)
        if op == "codex":

            args = {k: v for k, v in (args or {}).items() if k not in SIGN_IN_STEPS}
            if not saved_logins_on():
                args.pop("sign_in", None)
            if args.get("sign_in") and args.get("session"):
                return _codex_sign_in(args, **kwargs)
        reply = _call(op, args or {}, timeouts.get(op, 60), session_id=kwargs.get("session_id") or "")
        if not reply.get("ok"):
            error = reply.get("error") or "That did not work."
            if op not in ("start", "done") and any(t in error.lower() for t in
                                                  ("timed out", "timeout", "did not answer in time")):
                error += " " + home.personalize(TIMEOUT_RECOVERY)
            return json.dumps({"error": error})
        result = reply.get("result")
        if op == "run" and isinstance(result, dict) and result.get("timed_out") is True:
            result = {**result, "recovery": home.personalize(TIMEOUT_RECOVERY)}
        if isinstance(result, dict):
            picture = result if op == "screenshot" else result.get("screenshot")
            if isinstance(picture, dict) and picture.get("jpeg_base64"):
                if op == "screenshot":
                    return _screenshot_result(picture)
                image = _screenshot_result(dict(picture))
                metadata = {k: v for k, v in picture.items() if k != "jpeg_base64"}
                summary = json.dumps({"result": {**result, "screenshot": {"attached": True, **metadata}}}, ensure_ascii=False)
                image["text_summary"] = summary
                image["content"][0]["text"] = summary + home.personalize("\n\nColin's Mac screen is attached.")
                return image
        return json.dumps({"result": result}, ensure_ascii=False)
    return run


SIGN_IN_STEPS = ("sign_in_prepare", "sign_in_fill", "sign_in_cancel")


def saved_logins_on() -> bool:
    ""


    try:
        bot = gate._bot()
        if gate._sandboxed(bot):
            return gate.practice_logins(bot)
        if home.load().get("terra_saved_logins") is True:
            return True
    except Exception:
        return False


    try:
        from . import login_notes
        return login_notes.any_mac_only()
    except Exception:
        return False


def _codex_sign_in(args: dict, **kwargs) -> str:
    ""

    session = kwargs.get("session_id") or ""
    run = str(args.get("session") or "")
    prepared = _call("codex", {"session": run, "sign_in_prepare": str(args.get("sign_in") or "")}, session_id=session)
    if not prepared.get("ok"):
        return json.dumps({"error": prepared.get("error") or "The Mac could not find the sign-in page."})
    context = prepared.get("result") or {}
    ticket, site = context.get("ticket"), context.get("site")
    if not isinstance(ticket, str) or not isinstance(site, str) or not site:
        return json.dumps({"error": "The Mac could not verify the sign-in page."})
    filled = False
    try:
        if consent.site_of(site) == tiers.GOOGLE_HOST:
            return json.dumps({"error": "Not done: " + home.personalize(tiers.GOOGLE_TYPE_NEVER)})
        root = gate.lineage.root(session) if session else ""
        kanban = gate._session_env("HERMES_KANBAN_TASK") or ""
        cron = bool(gate._session_env("HERMES_CRON_SESSION")) or gate._lane(root) == "work"
        blocked = gate._check(gate._bot(), site, root, kanban, cron, record=True,
                              verb="Not signed in", task_id=kwargs.get("task_id") or "")
        if blocked:
            return json.dumps({"error": blocked.get("message") or "Sign-in was not allowed."})
        reply = _call("codex", {"session": run, "sign_in_fill": ticket}, 150, session_id=session)
        filled = reply.get("ok") is True
        return json.dumps({"result": reply.get("result")} if filled else
                          {"error": reply.get("error") or "The Mac could not confirm the fill."})
    finally:
        if not filled:
            _call("codex", {"session": run, "sign_in_cancel": ticket}, session_id=session)


def _fill_login(args: dict, **kwargs) -> str:
    ""
    session = kwargs.get("session_id") or ""
    prepared = _call("login_context", {"handle": str(args.get("handle") or "")}, session_id=session)
    if not prepared.get("ok"):
        return json.dumps({"error": prepared.get("error") or "The Mac could not verify a password field."})
    context = prepared.get("result") or {}
    ticket, site = context.get("ticket"), context.get("site")
    if not isinstance(ticket, str) or not isinstance(site, str) or not site:
        return json.dumps({"error": "The Mac could not verify the sign-in page."})
    try:
        if consent.site_of(site) == tiers.GOOGLE_HOST:

            return json.dumps({"error": "Not done: " + home.personalize(tiers.GOOGLE_TYPE_NEVER)})
        root = gate.lineage.root(session) if session else ""
        kanban = gate._session_env("HERMES_KANBAN_TASK") or ""
        cron = bool(gate._session_env("HERMES_CRON_SESSION")) or gate._lane(root) == "work"
        blocked = gate._check(gate._bot(), site, root, kanban, cron, record=True,
                              verb="Not signed in", task_id=kwargs.get("task_id") or "")
        if blocked:
            return json.dumps({"error": blocked.get("message") or "Sign-in was not allowed."})
        reply = _call("fill_login", {"ticket": ticket}, session_id=session)
        return json.dumps({"result": reply.get("result")} if reply.get("ok") else
                          {"error": reply.get("error") or "The Mac could not confirm the fill."})
    finally:
        _call("login_cancel", {"ticket": ticket}, session_id=session)


TOOLS = {name: name.removeprefix("mac_") for name in SCHEMAS}


def schema(name: str) -> dict:
    ""

    s = json.loads(json.dumps(SCHEMAS[name]))
    if name == "mac_codex" and not saved_logins_on():
        s["parameters"]["properties"].pop("sign_in", None)
    if name == "mac_start" and not home.is_owner_home():
        params = s["parameters"]
        params["properties"]["person_said"] = params["properties"].pop("colin_said")
        params["required"] = ["person_said" if r == "colin_said" else r for r in params["required"]]
    return s
