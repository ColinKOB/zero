""

import json
from urllib.parse import urlsplit

PUBLIC_WEB = ("Read public pages with web_search and web_extract first. Open the browser only for signing in, "
              "clicking, forms, or when extraction failed or returned incomplete content.")








REAL_PAGES = ("A web_search result is only a preview of a page. Before you give a price, availability, a date, hours "
              "or a rule, open the page that states it and check that it covers the dates asked about. web_extract "
              "leaves out a site's menu, so if the pages you opened don't state it, open the site's home page in the "
              "browser and follow its menu. Name that page as a link, such as [their rates "
              "page](https://example.com/rates). If no page you opened confirms a fact, say so instead of guessing.")


TEXTS = ("If you have the messages tool, call it before you say you can't read a text, since an earlier refusal may no "
         "longer hold. If it says a switch is off, name the switch.")


def public_web_pre_llm_call(**kw):
    ""
    return {"context": PUBLIC_WEB + " " + REAL_PAGES + " " + TEXTS}






PREVIEW_CHARS = 500
PREVIEWS = ("Each preview is a few lines the search engine took from that page. It can be an old copy or cover other "
            "dates. Open the page before you use a price, availability, a date, hours or a rule from it.")



MOVED = ("This address didn't load, and pages often move. Open the site's home page, {home}, in the browser and follow "
         "its menu to the page you need before you say you couldn't get it.")
HOME_FAILED = "This page didn't load. Open it in the browser before you say you couldn't get it."


def _preview(text: str) -> str:
    if len(text) <= PREVIEW_CHARS:
        return text
    cut = text[:PREVIEW_CHARS]
    return cut[:cut.rfind(" ")].rstrip() + " …" if " " in cut else cut + "…"


def mark_web_result(tool_name: str, result):
    ""

    if tool_name not in ("web_search", "web_extract") or not isinstance(result, str):
        return None
    try:
        data = json.loads(result)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    changed = False
    if tool_name == "web_search":
        groups = data.get("data") if isinstance(data.get("data"), dict) else {}
        for hits in groups.values():
            for i, hit in enumerate(hits if isinstance(hits, list) else ()):
                if isinstance(hit, dict) and isinstance(hit.get("description"), str):
                    hits[i] = {("preview" if k == "description" else k): (_preview(v) if k == "description" else v)
                               for k, v in hit.items()}
                    changed = True
        if changed:
            data = {"about_previews": PREVIEWS, **data}
    else:
        for page in data.get("results") or ():
            if not isinstance(page, dict) or not page.get("error") or page.get("content") \
                    or str(page.get("error")).startswith("Blocked"):
                continue
            parts = urlsplit(str(page.get("url") or ""))
            if parts.scheme not in ("http", "https") or not parts.netloc:
                continue
            home = f"{parts.scheme}://{parts.netloc}/"
            page["next_step"] = HOME_FAILED if parts.path in ("", "/") and not parts.query else MOVED.format(home=home)
            changed = True
    return json.dumps(data, indent=2, ensure_ascii=False) if changed else None


AFTER = ("- After a sign-in: use app tools before websites. Use `ask_to_connect` if needed. For browser logins, "
         "open the page again and check. Omit past blockers from unrelated replies.")
PROGRESS = ("- Show your steps: for longer work, give a brief update when something useful changes. Skip routine "
            "step narration and repeated plans. Report completion only after verifying it. Never send filler "
            "such as \"still working\".")






FINISHING = ("- Finishing: lead with what you found and show the finished work itself. After you make a "
             "change you were asked for (a sheet, doc, event, page or file), read the change back, name the thing as a link (\"I added "
             "your hours to the [tracker](https://...)\") and send a picture of the change with `share_screen`. Open "
             "the thing in your browser first when it isn't open. If a site stopped you, "
             "use `share_screen` when it helps and briefly name the blocker and a verified next step, if known. "
             "Never call something running when it stopped. Don't add a recap of every check or limitation.")
HONESTY = "These are truth constraints, not required disclaimers. Be brief about uncertainty when it matters."
HONESTY_RULE = ("Be honest about what was actually verified. Preparation is not submission, a draft is not a sent "
                "email, a preview is not production, and a past balance is not safe cash. Claim completion only "
                "after verifying it, and keep completed steps closed unless new evidence reopens them. Don't take "
                "credit for a teammate's work; name them only when ownership matters. " + HONESTY)



OWN_START = ("- Starting on your own: when you begin something Colin did not just ask for (a routine's follow-up, a "
             "check you decided was needed), give no progress updates. Tell him only a result he needs.")
STYLE_RULES = (("- Show your steps:", PROGRESS), ("- Finishing:", FINISHING), ("- Starting on your own:", OWN_START))


SMALL_TALK = ("## Small talk\n"
              "- When Colin chats, thanks you, or tells you how his day is going without asking for anything, answer like "
              "a friend who knows him, in one or two short sentences from what you already know. Don't look anything "
              "up, check the board, the calendar or the date, or report status unless he asks. What he tells you is "
              "saved for the team without a tool.")




TECHNICAL = ("- Technical work: Colin doesn't code. Say what changes for users, plainly. No progress updates; come "
             "to him once, when it's ready to ship.")

RULES = (("- After a sign-in:", AFTER), ("- Technical work:", TECHNICAL))





LEAD_TEAM = ("Handing work to a teammate: put one line in the task body saying when Colin should have the result, "
             "such as \"Result by: 2027-01-15 12:00\". Use the time he named, or judge one from the request; the "
             "same day is the default. The work stays yours until he has it. When that time passes the hub wakes you: "
             "look, push the teammate or take the next step, and tell him in one line if it is late or stuck on him.\n"
             "Before you say what a teammate is doing or has found, read its task with kanban_show or ask it with "
             "ask_teammate in this same turn, and say only what that shows. One read is enough. If you haven't looked, "
             "say \"I'll check\" and check. Give a figure only from a record you read in this turn. Otherwise say in "
             "the first sentence that it is an estimate and what it rests on.")


def style_line(line: str) -> str:
    for prefix, replacement in STYLE_RULES:
        if line.startswith(prefix):
            return replacement
    if line.startswith(("- Be honest about", "Be honest about")):
        return ("- " if line.startswith("- ") else "") + HONESTY_RULE
    line = line.replace("Relay what matters to Colin in plain words: what was done, what was verified, what needs him.",
                        "Relay the result and any needed next step. Include verification details only when they affect trust.")
    return line


def update_text(text: str) -> str:
    lines, seen, anchor, team = [], set(), None, False
    for line in text.split("\n"):
        if "<!-- team:start -->" in line:
            team = True
        if not team:
            line = style_line(line)
            if line.startswith("- Tools:") and anchor is None:
                anchor = len(lines)
            if line.startswith("- After a sign-in:"):
                line = AFTER
            for prefix, rule in RULES:
                if line.startswith(prefix):
                    line = rule
                    seen.add(prefix)
                    break
        lines.append(line)
        if "<!-- team:end -->" in line:
            team = False
    missing = [rule for prefix, rule in RULES if prefix not in seen]
    if missing:
        if anchor is None:
            anchor = next((i + 1 for i, line in enumerate(lines) if line.startswith("# ")), 0)
        lines[anchor:anchor] = missing
    return "\n".join(lines)
