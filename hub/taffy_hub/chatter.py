"""Messages that carry no news: a bare ".", "[SILENT]", "ok", "thanks", "still working on it".

Silence is a feature only when it is enforced (docs/research/2026-10-09-grokbot-dots-user-feedback.md): telling Zips
to "stay quiet" is a hint they can ignore (#79), and a bot that sends a bare "." instead of staying quiet sets off the
other bots (#78). So the hub decides, in code, what counts as saying nothing:

- empty(): nothing at all. Punctuation only, or Hermes's "[SILENT]".
- chatter(): empty, or a short acknowledgment, thanks or "still working" with no new information in it. A message
  with any word outside that small vocabulary (a name of a thing, a number, a date) is news and is never chatter.

Used by team.py (bot-to-bot messages are refused or dropped), watcher.py (a board update answered with nothing is not
posted or pushed), turns.py (a picked-up conversation that says nothing posts nothing; a chat turn that ends without a
reply says so plainly) and history.py (empty replies to the hub's own turns stay out of the chat).
"""

import re

SILENT = "[SILENT]"
MAX_WORDS = 12

_ACK = set("""
ok okay okey k kk alright all right sure yes yep yeah yup cool nice great good perfect awesome excellent fine
got it noted understood acknowledged ack received roger copy that will do on it sounds
thanks thank thx ty you so much very really appreciate appreciated cheers
for the heads up note info update message help quick let know me
still working keep keeping going progress making in hand underway away busy posted updated
i i'll ill im i'm am we we'll we're were are is it's its this that a an on with to of and now just
standing by here ready waiting wait hold holding
hey hi hello again
""".split())


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text or "").lower().replace("\u2019", "'"))


# Hermes's cancellation sentinel ("Operation interrupted: waiting for model response (2.7s elapsed).", also
# "Operation interrupted: handling API error (...)" and a bare "Operation interrupted."). Hermes's own desktop and
# gateway hide it; its API server returns it as the reply, so the hub hides it too (live convo QA, Oct 9: after
# Stop, To-Do's chat showed it as To-Do's answer).
INTERRUPTED = "Operation interrupted"


def interrupted(text: str | None) -> bool:
    """Hermes's note that a run was cut off, not words the Zip wrote."""
    return str(text or "").strip().startswith(INTERRUPTED)


# Hermes's turn-end "File-mutation verifier" footer: a "⚠️ File-mutation verifier:" line plus indented "•" bullets
# naming files and "Run `git status`" (hermes run_agent's turn finalizer, display.file_mutation_verifier). It is for
# Hermes's own terminal; in the app it put an emoji, tool names and file paths under Nova's reply (live convo QA 2,
# N9). The same pattern Hermes's speech cleanup drops (tools/tts_text_normalize.py).
_VERIFIER_FOOTER = re.compile(r"^\s*⚠️?\s*File-mutation verifier:.*(?:\n[ \t]+•.*)*", flags=re.MULTILINE)


_SHORT = re.compile(r"<short>\s*(.*?)\s*</short>", re.S | re.I)
_SHORT_TAG = re.compile(r"</?short>", re.I)


def split_short(text: str | None) -> tuple[str, str | None]:
    """(the reply without its short version, the short version or None)."""
    t = str(text or "")
    m = _SHORT.search(t)
    if not m:
        return (_SHORT_TAG.sub("", t).strip() if _SHORT_TAG.search(t) else t), None
    before, after = t[:m.start()].strip(), t[m.end():].strip()
    return "\n\n".join(p for p in (before, after) if p), (m.group(1).strip() or None)


def without_hermes_notes(text: str | None) -> str:
    """The Zip's own words, without Hermes's file-mutation verifier footer or the Zip's short-version tag."""
    t = str(text or "")
    if "<short>" in t.lower() or "</short>" in t.lower():
        t = split_short(t)[0]
    if "File-mutation verifier" not in t:
        return t
    return _VERIFIER_FOOTER.sub("", t).rstrip()


def empty(text: str | None) -> bool:
    """Nothing to show: no words at all, or only Hermes's [SILENT] marker."""
    t = str(text or "").strip()
    if not t or t.upper().startswith(SILENT):
        return True
    return not re.search(r"[A-Za-z0-9]", t)


def chatter(text: str | None, names: tuple[str, ...] | list[str] = ()) -> bool:
    """True for a message that only acknowledges, thanks or says work is still going, with no news (#78, #79).
    `names` are words that do not count as news (the Zips' names, the person's name)."""
    if empty(text):
        return True
    words = _words(text)
    if not words or len(words) > MAX_WORDS:
        return False
    skip = {w.lower() for n in names for w in _words(n)}
    rest = [w for w in words if w not in skip]
    return all(w in _ACK for w in rest)


def team_names() -> list[str]:
    """Every Zip's name and id, and the person's first name: words that are not news in a team message."""
    from . import config, who
    out = []
    for b in config.BOTS:
        out += [b.id, b.name]
    try:
        out.append(who.first())
    except Exception:
        pass
    return out + ["taffy", "zero", "team", "teammate"]
