"""Who the home belongs to, for every sentence the hub writes (to the person, to the Zips, to Haiku).

The name the person gave in onboarding wins; without one, the home's settings (home.json `person`). Nothing formats
the name at import time, so a rename reaches the next sentence. Sentences name the person instead of using
pronouns (plan 2026-10-09, section 2).
"""

from . import home


def full() -> str:
    """"Colin O'Brien"."""
    from . import onboarding
    return onboarding.person_name(full=True)


def first() -> str:
    """"Colin"."""
    from . import onboarding
    return onboarding.person_name(full=False)


def possessive(name: str | None = None) -> str:
    """"Colin's", "James'"."""
    n = name or first()
    return f"{n}'" if n.endswith("s") else f"{n}'s"


def hub_note() -> str:
    """The prefix of every quiet note the hub puts in a Zip's conversation. History classifies by `[Hub note`."""
    return f"[Hub note, not a message from {first()}]"


def mac() -> str:
    """"Colin's Mac"."""
    return f"{possessive()} Mac"


def tz() -> str:
    """"Eastern"."""
    return home.tz_word()


def to_you(text: str) -> str:
    """A Zip's words about the person, turned to the person ("see Colin's last order" reads "see your last order"),
    for anything the app shows them: card reasons are written by the Zip, which names the person in the third person
    in its own notes (Oct 9: "so Zero can see Colin's last contacts order and price"). Only the person's own name is
    changed; everything else stays as the Zip wrote it."""
    import re
    t = str(text or "")
    names = {n for n in (full(), first()) if n and n.strip()}
    for n in sorted(names, key=len, reverse=True):
        e = re.escape(n)
        t = re.sub(rf"\b{e}(?:'s|’s|'|’)(?=\s|$)", "your", t)
        t = re.sub(rf"\b{e}\b", "you", t)
    return t
