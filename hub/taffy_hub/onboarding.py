"""What the person told the app during onboarding: the agent's name and look (a body and an outfit), their own name, a few answers
about their life, which sources the agent may look around in, and the agent's voice.

Kept in the hub's settings table as one record; the lead Zip is renamed by the endpoint in app.py.
"""

import json
import logging
import re
import time

from . import config, home, store

log = logging.getLogger("taffy.onboarding")

ABOUT = ("work", "home", "age", "family")
SOURCES = ("files", "apps", "contact", "calendar", "email", "picked_files")
SETTING = "onboarding"


class OnboardingError(ValueError):
    pass


def _text(value, limit: int, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise OnboardingError(f"The {field} should be text.")
    v = " ".join(value.split())
    if len(v) > limit:
        raise OnboardingError(f"The {field} is too long.")
    return v


def clean(body: dict) -> dict:
    """Only known fields, trimmed and capped. Unknown about-you questions and sources are refused, so nothing
    unexpected reaches the agent's notes."""
    about = body.get("about") or {}
    sources = body.get("sources") or {}
    look = body.get("look") or {}
    if not isinstance(about, dict) or not isinstance(sources, dict) or not isinstance(look, dict):
        raise OnboardingError("Check the answers and try again.")
    if extra := set(about) - set(ABOUT):
        raise OnboardingError(f"Unknown question: {sorted(extra)[0]}.")
    if extra := set(sources) - set(SOURCES):
        raise OnboardingError(f"Unknown source: {sorted(extra)[0]}.")
    if any(not isinstance(v, bool) for v in sources.values()):
        raise OnboardingError("Each source is on or off.")
    if not isinstance(body.get("no_computer", False), bool):
        raise OnboardingError("Say whether there is a computer.")
    return {
        "agent_name": _text(body.get("agent_name"), 24, "agent's name"),
        "person_name": _text(body.get("person_name"), 60, "name"),
        "about": {k: _text(about.get(k), 300, k) for k in ABOUT if _text(about.get(k), 300, k)},
        "sources": {k: bool(sources.get(k, False)) for k in SOURCES},
        "voice": _text(body.get("voice"), 40, "voice"),
        "no_computer": body.get("no_computer", False),
        "look": {"body": _text(look.get("body"), 40, "body"), "outfit": _text(look.get("outfit"), 40, "outfit")},
    }


def save(answers: dict) -> dict:
    record = {**answers, "saved_at": time.time()}
    store.set_setting(SETTING, record)
    return record


def load() -> dict | None:
    return store.get_setting(SETTING)


PERSON_FILE = config.HUB_DIR / "person.json"
ACK_SETTING = "onboarding_ack"
ABOUT_START, ABOUT_END = "<!-- onboarding:start -->", "<!-- onboarding:end -->"
LABELS = {"work": "Work", "home": "Home", "age": "Age", "family": "Family"}
# Hermes refuses to add notes about the person once USER.md passes its user_char_limit (1,375 by default).
# alien/bin/set-toolsets.py raises the limit to this for every Zip; the About block is kept small enough to
# leave room for the Zips' own notes under it (Oct 8 QA round 2, L7).
USER_CHAR_LIMIT = 5000
NOTES_HEADROOM = 600
ENTRY_DELIMITER = "\n§\n"   # how Hermes separates USER.md entries


def person_name(full: bool = True) -> str:
    """The person's name from onboarding, or the home's settings (home.json `person`) when nobody has onboarded."""
    try:
        name = ((load() or {}).get("person_name") or "").strip()
    except Exception:
        name = ""
    if not name:
        return home.person_name(full)
    return name if full else name.split()[0]


_IMPORT_PERSON_FILE = PERSON_FILE


def _person_file():
    """PERSON_FILE when something set it, else the person file in the hub's folder as it is now (a test that moved
    config.HUB_DIR never writes the live one)."""
    return PERSON_FILE if PERSON_FILE != _IMPORT_PERSON_FILE else config.HUB_DIR / "person.json"


def _write_person_file(name: str) -> None:
    f = _person_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"name": name, "first_name": name.split()[0] if name else ""}))
    tmp.replace(f)


def _name_in_settings(name: str) -> None:
    """The plugin reads the name from home.json first, so a home that has the file gets the new name there too. Only
    for the home this hub serves: never when the hub's folder was moved (a test) and never by creating the file."""
    if not name or not home.exists() or config.HUB_DIR.resolve() != home.path_of("hub_state").resolve():
        return
    try:
        home.save({"person": {"name": name, "first_name": name.split()[0]}})
    except (OSError, home.HomeError):
        log.warning("could not save the name in the home's settings", exc_info=True)


def set_person_name(name: str) -> None:
    """A new name from Settings (PUT /api/home-settings): the onboarding record, when there is one, and the person
    file the plugin reads, so every reader agrees with home.json."""
    record = load()
    if record and record.get("person_name"):
        store.set_setting(SETTING, {**record, "person_name": name})
    _write_person_file(name)


def _about_block(answers: dict, room: int | None = None) -> str | None:
    """The About entry, in at most `room` characters: the full header when it fits, then a short one, then each
    answer cut at a word. None when not even the name fits."""
    name = answers.get("person_name") or "the person"
    about = {k: v for k, v in (answers.get("about") or {}).items() if v}

    def build(header: str, cap: int | None) -> str:
        lines = [f"- Name: {name}"] + [f"- {LABELS[k]}: {_cut(v, cap)}" for k, v in about.items()]
        return f"{ABOUT_START}\n{header}\n" + "\n".join(lines) + f"\n{ABOUT_END}"

    full = build(f"About {name}, from onboarding ({time.strftime('%Y-%m-%d')}): what they told the app about "
                 "themselves.", None)
    if room is None or len(full) <= room:
        return full
    for cap in (None, 200, 120, 80, 40):
        block = build(f"About {name} (onboarding):", cap)
        if len(block) <= room:
            return block
    about = {}
    block = build(f"About {name} (onboarding):", None)
    return block if len(block) <= room else None


def _cut(text: str, cap: int | None) -> str:
    if cap is None or len(text) <= cap:
        return text
    return text[:cap].rsplit(" ", 1)[0].rstrip(",;:") + "."


def _user_files() -> list:
    files = [config.HERMES_HOME / "memories" / "USER.md"]
    profiles = config.HERMES_HOME / "profiles"
    if profiles.is_dir():
        files += [p / "memories" / "USER.md" for p in sorted(profiles.iterdir())
                  if p.is_dir() and not p.name.startswith(".")]
    return files


def apply(answers: dict) -> list[str]:
    """Puts the answers where the Zips read them: an About entry in every Zip's USER.md (the lead's and each
    specialist's copy; replaced in place, or added after the existing notes), the person's name for the hub and
    the plugin, and a note so the lead's next chat turn mentions one answer. The entry is shortened to keep the
    file under Hermes's limit with room for new notes. Returns the USER.md files written."""
    _write_person_file(answers.get("person_name") or "")
    _name_in_settings(answers.get("person_name") or "")
    written = []
    for path in _user_files():
        try:
            text = path.read_text() if path.exists() else ""
        except OSError:
            continue
        if ABOUT_START in text and ABOUT_END in text:
            before, after = text[:text.index(ABOUT_START)], text[text.index(ABOUT_END) + len(ABOUT_END):]
        else:
            before, after = (text.rstrip() + ENTRY_DELIMITER) if text.strip() else "", "\n"
        room = USER_CHAR_LIMIT - NOTES_HEADROOM - len((before + after).strip())
        block = _about_block(answers, room)
        if block is None:
            log.warning("no room for the About entry in %s", path)
            continue
        text = before + block + after
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            written.append(str(path))
        except OSError:
            continue
    about = answers.get("about") or {}
    pick = next(((k, about[k]) for k in ("family", "work", "home", "age") if about.get(k)), None)
    if pick:
        store.set_setting(ACK_SETTING, {"field": LABELS[pick[0]].lower(), "answer": pick[1], "at": time.time()})
    return written


def take_ack() -> str | None:
    """The one-time note for the lead's first chat turn after onboarding, or None."""
    ack = store.get_setting(ACK_SETTING)
    if not ack:
        return None
    store.set_setting(ACK_SETTING, None)
    if time.time() - float(ack.get("at") or 0) > 7 * 86400:
        return None
    answer = re.sub(r"\s+", " ", str(ack.get("answer") or ""))[:200]
    return (f"[Onboarding, not a message from the person: they just set you up and told you about their "
            f"{ack.get('field')}: \"{answer}\". In this reply, mention that one thing naturally, in a sentence, "
            "so they know you heard it. Do not list the other answers.]")
