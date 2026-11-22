""





import json
import re
import time
from dataclasses import dataclass

from . import config, store, who


@store.schema
def _tables(c):
    c.execute("""CREATE TABLE IF NOT EXISTS ask_waiters (
        source TEXT NOT NULL, ask_id INTEGER NOT NULL, bot_id TEXT NOT NULL,
        PRIMARY KEY (source, ask_id, bot_id))""")


@dataclass(frozen=True)
class Ask:
    source: str
    id: int
    bot: str
    text: str
    match: str
    shareable: bool = True
    questions: tuple[str, ...] = ()


STOP = set("a an the what which are is do does can could would should will you your me my i we our please "
           "need needs send share tell provide give get for to of on in and or with it this that these those "
           "have has how where when want use connect sign login account accounts".split())
APPROVAL = re.compile(r"\b(approv\w*|publish\w*|post\w*|sign.off|permission|allow\w*|pay\w*|buy\w*)\b", re.I)


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.casefold()) if w not in STOP}


def _names(text: str) -> set[str]:
    return words(" ".join(re.findall(r"\b[A-Z][A-Za-z0-9]*\b", text))) - {"x", "instagram", "twitter"}


def _visible(viewer: str, owner: str) -> bool:
    a, b = config.BOTS_BY_ID.get(viewer), config.BOTS_BY_ID.get(owner)
    return bool(a and b and (viewer == owner if a.sandbox or b.sandbox else True))


def open_for(bot_id: str) -> list[Ask]:
    from . import teams
    teams._db()
    out = []
    rows = store.db().execute("SELECT n.* FROM team_needs n JOIN teams t ON t.id=n.team_id "
                              "WHERE n.state='open' AND n.kind IN ('decide','do') "
                              "AND t.state NOT IN ('deleting','deleted','merged') ORDER BY n.id").fetchall()
    for r in rows:
        if _visible(bot_id, r["from_bot"]):
            out.append(Ask("need", r["id"], r["from_bot"], r["title"], r["title"], not bool(r["about"])))
    for r in store.db().execute("SELECT * FROM feed WHERE kind IN ('choices','login','account') ORDER BY id"):
        if not _visible(bot_id, r["bot_id"]):
            continue
        try:
            card = json.loads(r["body"] or "{}")
        except ValueError:
            continue
        if not isinstance(card, dict):
            continue
        if card.get("state") != "open" or card.get("notice") or card.get("steps"):
            continue
        text = card.get("question") or r["title"]
        questions = tuple(q["question"] for q in card.get("questions", []))
        match = " ".join(questions) if questions else text


        waiting = [a["name"] for a in card.get("apps", []) if not a.get("connected")] if card.get("setup") else []
        if waiting:
            text = f"{text}: {', '.join(waiting)}"

        out.append(Ask(r["kind"], r["id"], r["bot_id"], text, match, not bool(card.get("about")), questions))
    return out


def wait_on(bot_id: str, text: str, questions: tuple[str, ...] = ()) -> Ask | None:
    ""



    if APPROVAL.search(text):
        return None
    new = words(text)
    for ask in open_for(bot_id):
        old = words(ask.match)
        if ask.bot == bot_id or not ask.shareable or APPROVAL.search(ask.match):
            continue
        if questions or ask.questions:
            if len(questions) != len(ask.questions) or any(words(a) != words(b) for a, b in zip(questions, ask.questions)):
                continue
        names, other_names = _names(text), _names(ask.match)
        if names and other_names and names.isdisjoint(other_names):
            continue
        if len(new) < 2 or new != old:
            continue
        store.db().execute("INSERT OR IGNORE INTO ask_waiters VALUES (?,?,?)", (ask.source, ask.id, bot_id))
        store.db().commit()
        return ask
    return None






COVERS_SEC = 3600
STEP = re.compile(r"\b(deploy|ship|launch|releas|merg|put (?:it )?live|go(?:es)? live|roll(?:ed|ing)? (?:it )?out)", re.I)
SENSITIVE = re.compile(r"\b(send|e-?mail|text|messag|repl(?:y|ies)|invit|delet|cancel|refund|charg)\w*", re.I)
NO = re.compile(r"^\W*(no|not|don'?t|do not|keep|skip|leave|hold|wait|stop|cancel|decline|never|later)\b", re.I)


YES = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok(?:ay)?|go ahead|do it|ship it|deploy it|sounds good|approved?|"
                 r"please do|let'?s do it)\b", re.I)

ENV = re.compile(r"\b(staging|stage|prod|production|preview|dev|development|test|testing|beta|sandbox)\b", re.I)
SITE = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:app|com|io|dev|net|org|co|ai|me|so|xyz)\b", re.I)


def _steps(text: str) -> set[str]:
    return {m.group(1).split()[0].casefold() for m in STEP.finditer(text)}


def _target(text: str) -> set[str]:
    return {w for w in words(text) if not STEP.match(w)}


def _yes(answer: str, options: list) -> bool:
    if NO.match(answer):
        return False
    return answer in options or bool(YES.match(answer))


def _same_place(text: str, said: str) -> bool:
    envs = {m.casefold() for m in ENV.findall(text)}
    sites = {m.casefold() for m in SITE.findall(text)}
    return envs == {m.casefold() for m in ENV.findall(said)} and sites <= {m.casefold() for m in SITE.findall(said)}


def covered(bot_id: str, text: str) -> str:
    ""

    steps = _steps(text)
    if not steps or APPROVAL.search(text) or SENSITIVE.search(text):
        return ""
    since = time.time() - COVERS_SEC
    asked = [(r["title"], f"{r['title']} {r['detail']}", r["answer"] or "", r["answered_at"],
              json.loads(r["options"] or "[]")) for r in store.db().execute(
        "SELECT title, detail, answer, answered_at, options FROM team_needs WHERE from_bot=? AND kind='decide' AND "
        "state='answered' AND answered_at>=? ORDER BY answered_at DESC", (bot_id, since))
        if not str(r["answer"] or "").startswith("Closed by ")]
    for r in store.db().execute("SELECT body FROM feed WHERE kind='choices' AND bot_id=? AND "
                                "json_extract(body, '$.state')='picked' AND json_extract(body, '$.at')>=?",
                                (bot_id, since)):
        card = json.loads(r["body"] or "{}")
        asked.append((card["question"], f"{card['question']} {card.get('reasoning') or ''}", card.get("picked") or "",
                      card["at"], card.get("options") or []))
    for title, said, answer, at, options in sorted(asked, key=lambda a: -a[3]):
        if not steps & _steps(said) or len(_target(text) & _target(said)) < 2 or not _same_place(text, said):
            continue
        if not _yes(answer, options):
            return ""
        first, detail = who.first(), said[len(title):].strip()[:300]
        return (f'{first} already said yes to this. You asked "{title}"' + (f', which said "{detail}"' if detail else "")
                + f', and {first} answered "{answer[:200]}". That answer is {who.possessive()} yes for this step, so do '
                "not ask again. Do it now, or hand it to the teammate who can, and tell "
                f"{first} in one line when it is done.")
    return ""


def _shared(text: str) -> str:
    ""
    from . import team
    return team._redact(text)[0]


def waiting(ask: Ask) -> str:
    name = config.BOTS_BY_ID[ask.bot].name
    return (f'{name} already asked: "{_shared(ask.text)}". Do not ask again or repeat it in your reply. '
            "I recorded that you need the answer too. It will reach you when that request is settled. "
            "Continue any work that does not need it.")


def context(bot_id: str) -> str:

    others = [a for a in open_for(bot_id) if a.bot != bot_id]
    if not others:
        return ""
    lines = "\n".join(f'- {config.BOTS_BY_ID[a.bot].name}: {_shared(a.text)}' for a in others)
    return (f"[Requests waiting on {who.first()} across chats and teams]\n" + (lines + "\n" if lines else "") +
            "Do not ask again for the same thing in another chat. Use team_need or offer_choices for requests, "
            "even when the person must type the answer. Those tools record who else needs the answer. "
            "Do not repeat a card's request in prose. Sign-in and posting permissions still apply separately.")


def answered(source: str, ask_id: int, owner: str, question: str, answer: str, *, expired: bool = False,
             closed: bool = False) -> None:
    ""

    from . import team, teams
    answer, _ = team._redact(answer)
    parts = [answer]
    if len(answer) > 700:
        parts = []
        for line in answer.splitlines(keepends=True):
            parts.extend(line[i:i + 700] for i in range(0, len(line), 700))
    with store._lock:
        rows = store.db().execute("SELECT bot_id FROM ask_waiters WHERE source=? AND ask_id=?",
                                  (source, ask_id)).fetchall()
        for r in rows:
            if _visible(owner, r["bot_id"]) and r["bot_id"] != owner:
                membership = teams.member_of(r["bot_id"])
                for index, part in enumerate(parts, 1):
                    position = f" (part {index} of {len(parts)})" if len(parts) > 1 else ""
                    event = ("the request expired without an answer" if expired else
                             "the question closed without an answer because it no longer applies" if closed else
                             f"{who.first()} responded")
                    note = (f'About "{_shared(question)[:200]}", {event}{position}: {part}\n'
                            "This does not approve sending, posting, spending or account access.")
                    team.tell(owner, r["bot_id"], note, None, from_hub=True, shown=False)
                    if membership:
                        teams.scheduler.wake(membership["id"], r["bot_id"], note, kind="answer")
            store.db().execute("DELETE FROM ask_waiters WHERE source=? AND ask_id=? AND bot_id=?",
                               (source, ask_id, r["bot_id"]))
            store.db().commit()



    membership = teams.member_of(owner) if source in ("login", "account") else None
    if membership and membership["state"] != "deleting" and owner in config.BOTS_BY_ID:
        teams._unblock(membership["id"], f"{config.BOTS_BY_ID[owner].name}'s card \"{question[:80]}\" was just settled")
