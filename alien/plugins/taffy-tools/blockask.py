""







import re

ASK_MAX_WORDS = 40
OPTION_MAX_WORDS = 7
_OPTIONS = re.compile(r"^\s*Options:\s*(.+?)\s*$", re.I | re.M)


_BACKGROUND = re.compile(r"^\s*Background:\s*(.+?)\s*$", re.I | re.M)



QUIET_KINDS = ("capability", "transient")




HUMAN_CHECK = ("When a page stops you with a human check (a CAPTCHA, \"Verify you are human\", a press and hold), don't "
               "wait and don't write about it: call ask_to_sign_in at once with purpose='challenge', that page's address "
               "and site_name, in a chat or a board task. It sends you to Colin's own browser on his Mac first, and "
               "raises his one-tap card if the Mac can't. Never press, solve or get around the check yourself, there or "
               "anywhere. Never ask him for it in a sentence, and never put it in a block with another question.")


HUMAN_CHECK_SHORT = "Human checks: call ask_to_sign_in purpose='challenge' at once, never press."

CHECK_WAIT = "Finish a human check on this page."
NO_ASK_KINDS = ("capability", "transient", "dependency")


def signs_in(reason: str, title: str = "") -> bool:
    ""
    return any(str(t or "").lower().startswith("sign in") for t in (title, reason))




_SIGN_IN_WORDS = re.compile(r"\b(?:sign|log)[\s-]?(?:into|in|onto|on|back in)\b|\bsignin\b|\blogin\b|\bre-?authenticat", re.I)




MAC_LOGIN = ("If a command-line tool on Colin's Mac needs him to log in there (it says Unauthorized, not logged in, or "
             "to run a login command), signing in on your screen can't fix it. Open the reason with one question that "
             "names the one command for him to run in Terminal on his Mac, such as \"Can you run railway login in "
             "Terminal on your Mac?\" On the next line say why and what you'll do once he has, then add "
             "\"Options: Done | Not Now\".")
SIGN_IN_FORM = ("Not blocked yet: for a website you will use in your own browser, start the reason with \"Sign in to "
                "<site>\" and put the site's sign-in page address (https://...) in it, so Colin gets one card with Sign "
                "In and the task picks up when he's done. " + MAC_LOGIN + " If he doesn't have to sign in himself, ask "
                "him one question instead.")


def maybe_sign_in(reason: str, title: str = "") -> bool:
    ""


    reason = str(reason or "")
    return bool(_SIGN_IN_WORDS.search(reason)) and not signs_in(reason, title) and parse(reason) is None


def parse(reason: str) -> dict | None:
    ""

    lines = [s.strip() for s in str(reason or "").strip().split("\n")]
    first = lines[0] if lines else ""
    if not first.endswith("?") or len(first.split()) > ASK_MAX_WORDS:
        return None
    rest = "\n".join(lines[1:])
    found = _OPTIONS.search(rest)
    options = [o.strip() for o in found.group(1).split("|") if o.strip()] if found else []
    if not 2 <= len(options) <= 4 or any(len(o.split()) > OPTION_MAX_WORDS for o in options):
        options = []
    background = _BACKGROUND.search(rest)
    details = _BACKGROUND.sub("", _OPTIONS.sub("", rest)).strip()
    return {"ask": first, "options": options, "details": details,
            "background": " ".join(background.group(1).split()) if background else ""}


def quiet(reason: str, kind: str | None, title: str = "") -> bool:
    ""
    return kind in QUIET_KINDS and not signs_in(reason, title) and parse(reason) is None


def asks_nothing(reason: str, kind: str | None, title: str) -> bool:
    ""



    return not (kind in NO_ASK_KINDS or signs_in(reason, title) or parse(reason))







GENERIC_WAKES = ("Tools and rules may have changed since it stopped you",
                 "The date you parked this for has arrived",
                 "Nothing is set to bring this task back",
                 "without a question, so there was nothing to answer",
                 "Before this goes to",
                 "This task was parked by mistake",
                 "without your turn on")
_RESUMES = ("unblocked", "specified", "promoted")


def generic_wake(note: str) -> bool:
    ""
    return any(g in str(note or "") for g in GENERIC_WAKES)


def settled_times(events, comments, first: str) -> list[float]:
    ""



    out, notes = [], []
    for c in comments or []:
        body, at = str(c.get("body") or ""), float(c.get("created_at") or 0)
        if first and (str(c.get("author") or "").strip() == first or f"{first} answered in the app:" in body):
            out.append(at)
        notes.append((at, body))
    for e in events or []:
        if e.get("kind") not in _RESUMES:
            continue
        at = float(e.get("created_at") or 0)
        if not any(at - 5 <= n_at <= at + 1 and generic_wake(body) for n_at, body in notes):
            out.append(at)
    return out


def standing_question(blocks, settled, title: str = "", hub_word=None) -> tuple[str, float] | None:
    ""




    for reason, at in reversed(list(blocks or [])):
        word = hub_word(reason, at) if hub_word else ""
        if parse(reason) is None and word != "person":
            continue
        if word == "elsewhere" or signs_in(reason, title):
            return None
        return None if any(s >= float(at or 0) for s in settled or []) else (reason, float(at or 0))
    return None
