""





































from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

from . import config, facts, home, shortreply, store, who, ziplimits

log = logging.getLogger("taffy.notebooks")

ENABLED = False
ROOM = 0.80
TARGET = 0.60
EVERY_SEC = 900
STUCK_SEC = 26 * 3600
RETRY_SEC = 6 * 3600
HISTORY = ".history"
HISTORY_KEEP = 100
FACT_MAX = 380
WORDS_KEPT = 0.5
DELIM = "\n§\n"
SHELF = "Shelved in saved facts (use recall before this kind of work): "
MODELS = (("chatgpt", "sol"), ("claude", "opus"))
EFFORT = "medium"
TIMEOUT_SEC = 600
NOTEBOOK, PROFILE = "notebook", "profile"
NO_ANSWER = "no usable answer"

SYSTEM = "You tidy the notebook of an AI helper so it keeps what matters and has room again. Reply with JSON only."
PROMPT = """Today is {today}. Below is {what}. It is loaded into every conversation and holds {size:,} of its \
{limit:,} characters, so there is no room to add to it. Sort every entry so it has room again. Nothing is deleted: an \
entry that leaves is saved where it can be looked up.

For each entry choose what to do:
- "keep": it stays as written. {keep}
- "keep" with "text": it stays in fewer words. Use this when an entry repeats another or carries words that add \
nothing. The shorter wording keeps every name, number, date, amount, id, address, path, command and tool name of the \
original, written exactly as it was. It keeps every word that limits or turns the meaning ("never", "not", "no", \
"only", "unless", "except", "without", "until", "if", "or"), on the same thing. A statement stays a statement: do not \
turn "{first} expects X" into the command "do X".
- "merge" with "into": it says the same thing as another entry. Give that entry's number, and give that entry a \
"text" that holds every detail both said, including the ones only one of them has.
- "leave": it leaves, as one or more parts under "moved". Each part has "as", "subject" (two to four words) and \
"text": complete sentences under {fact_max} characters that make sense with nothing else beside them (say who and \
what, and write each date in full with its year). Keep every name, number, date and id. Say only what the entry \
says: add no later outcome, no "originally" or "at the time", and nothing learned from another entry. "as" is one of:
  - "rule": {rule}
  - "past": a dated item whose day is clearly over on today's date and that no other entry and no work still going \
on depends on: a deadline that passed, a meeting that happened, a one-day status, a note a later entry replaced.
  - "shelf": {shelf}
An entry that is partly over may stay with a "text" that drops the finished part and a "moved" list that holds it.

A second reader compares every new wording with the original and throws out any that adds, drops or changes \
anything, so a wording that only removes filler is the one that survives.

Aim for under {target:,} characters in all, but never by dropping something current. When unsure, keep.

Reply with JSON only, one object for every entry, in order:
{{"entries": [{{"n": 1, "do": "keep"}}, {{"n": 2, "do": "keep", "text": "..."}}, {{"n": 3, "do": "merge", "into": 2}}, \
{{"n": 4, "do": "leave", "moved": [{{"as": "rule", "subject": "...", "text": "..."}}]}}]}}

Entries:
{entries}"""
WORDS = {
    NOTEBOOK: {
        "what": "the private notebook of {name}, an AI helper working for {first}",
        "keep": "Choose this for the state of work that is still going on or still needed, for how-tos, access "
                "routes, ids, tool quirks and lessons the helper uses in its work, and for anything you are unsure "
                "about.",
        "rule": "a lasting rule, preference or decision of {first}'s (how {first} wants things done, what {first} "
                "decided, who someone is) that would hold whichever helper did the work. It moves to the team's saved "
                "facts, which reach every helper.",
        "shelf": "a how-to or reference detail needed for only one kind of job. It moves to this helper's own saved "
                 "facts, which arrive when a message shares their words. Use it only when keeping, merging and "
                 "shortening cannot reach the size below, and shelve what is needed least often first.",
    },
    PROFILE: {
        "what": "the shared profile of {first}: who {first} is and how {first} wants every AI helper on the team to "
                "work. Every helper has a copy",
        "keep": "Choose this for who {first} is and for every rule that applies to all helpers in all work. This "
                "file is where those rules live, so they stay.",
        "rule": "a detail about one subject, project, purchase, person or errand, which only some work needs. It "
                "moves to the team's saved facts, which reach the work they bear on.",
        "shelf": "not used for this file.",
    },
}


@store.schema
def _tables(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS notebook_tidy (file TEXT PRIMARY KEY, digest TEXT NOT NULL, "
              "at INTEGER NOT NULL, outcome TEXT NOT NULL, before INTEGER NOT NULL, after INTEGER NOT NULL, "
              "moved INTEGER NOT NULL, over_since INTEGER)")




def path_of(bot_id: str, kind: str = NOTEBOOK) -> Path:
    return ziplimits.home_of(bot_id) / "memories" / ("USER.md" if kind == PROFILE else "MEMORY.md")


def limit_of(bot_id: str, kind: str = NOTEBOOK) -> int:
    key, default = (("user_char_limit", ziplimits.PROFILE_LIMIT) if kind == PROFILE
                    else ("memory_char_limit", ziplimits.NOTEBOOK_LIMIT))
    return ziplimits._setting(ziplimits.home_of(bot_id), key, default)


def split(text: str) -> list[str]:
    return [e for e in (x.strip() for x in str(text or "").split(DELIM)) if e]


def join(entries: list[str]) -> str:
    return DELIM.join(entries)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _targets() -> list[tuple[str, str]]:
    ""
    out = [(b.id, NOTEBOOK) for b in config.BOTS if not config.is_sandbox(b.id)]
    return out + [(config.LEAD.id, PROFILE)]


def _private(bot_id: str) -> bool:
    ""
    from . import shortreply
    return shortreply.withheld(bot_id)




_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_@./:%$#&+~'’-]*")
_NAME = re.compile(r"(?<![.!?:]\s)(?<!^)(?<!\n)\b[A-Z][a-z]{2,}\b")


def specifics(text: str) -> set[str]:
    ""


    out = set()
    for tok in _TOKEN.findall(text or ""):
        tok = tok.strip(".,:;'’-")
        if len(tok) >= 2 and (re.search(r"[0-9/@_]", tok) or (tok.isupper() and tok.isalpha())):
            out.add(tok.lower())
        elif tok.isdigit():
            out.add(tok)
    out.update(m.group(0).lower() for m in _NAME.finditer(text or ""))
    return out


def _covered(original: str, *now: str) -> set[str]:
    ""


    have = " ".join(now)
    lost = {s for s in specifics(original) if s not in have.lower()}


    lost |= {f'the word "{w}"' for w in facts._turns(original) - facts._turns(have)}
    if not lost and facts._kept(original, have) < WORDS_KEPT:
        lost.add("most of its words")
    return lost


_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_WRITTEN = re.compile(r"\b(" + "|".join(_MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _all_over(text: str, today: date) -> bool:
    ""


    found = []
    for m in _WRITTEN.finditer(text or ""):
        found.append((int(m.group(3)), _MONTHS.index(m.group(1).lower()[:3]) + 1, int(m.group(2))))
    found += [tuple(map(int, m.groups())) for m in _ISO.finditer(text or "")]
    try:
        days = [date(*f) for f in found]
    except ValueError:
        return False
    return bool(days) and max(days) < today




def prompt(bot_id: str, kind: str, entries: list[str], limit: int, hidden: frozenset = frozenset()) -> str:
    bot = config.BOTS_BY_ID.get(bot_id)
    names = {"name": bot.name if bot else bot_id, "first": who.first()}
    words = {k: v.format(**names) for k, v in WORDS[kind].items()}
    today = datetime.fromtimestamp(time.time(), home.TZ).strftime("%A, %B %-d, %Y")
    return PROMPT.format(today=today, size=len(join(entries)), limit=limit, target=int(limit * TARGET),
                         fact_max=FACT_MAX, entries="\n\n".join(f"[{i}] {e}" for i, e in enumerate(entries, 1) if i not in hidden),
                         first=names["first"], **words)


async def ask(text: str, system: str = SYSTEM, models: tuple = MODELS) -> str:
    ""
    from . import modelsetup, shortreply
    M = modelsetup.M
    s = modelsetup.current()
    use = s.use if s is not None else tuple(M.PLANS)
    for plan, key in models:
        if plan not in use:
            continue
        try:
            answer = await shortreply.ask_helper(text, model=M.CATALOG[key], timeout=TIMEOUT_SEC, system=system,
                                                 max_tokens=32000, effort=EFFORT, fallback=False, origin="notebooks")
        except Exception:
            log.warning("could not tidy a notebook on %s", key, exc_info=True)
            continue
        if answer and answer.strip():
            return answer
    raise RuntimeError("no plan answered")


def parse(answer: str) -> dict[int, dict]:
    ""
    found = re.search(r"\{.*\}", str(answer or ""), re.S)
    try:
        rows = json.loads(found.group(0))["entries"] if found else []
    except (ValueError, KeyError, TypeError):
        return {}
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and isinstance(r.get("n"), int):
            out.setdefault(r["n"], r)
    return out


def check(entries: list[str], plan: dict[int, dict], kind: str = NOTEBOOK, now: float | None = None) -> dict:
    ""


    today = datetime.fromtimestamp(now or time.time(), home.TZ).date()
    refused: dict[int, str] = {}
    text_of: dict[int, str] = {}
    moved_of: dict[int, list[dict]] = {}
    merges: dict[int, int] = {}

    def parts(n: int, row: dict) -> list[dict] | None:
        out = []
        for p in row.get("moved") or []:
            text = " ".join(str((p or {}).get("text") or "").split())
            how = (p or {}).get("as")
            if how not in ("rule", "past", "shelf") or (how == "shelf" and kind == PROFILE):
                refused[n] = "a part leaves in a way this file does not allow"
                return None
            if not 8 <= len(text) <= 400:
                refused[n] = "a part that leaves is not one fact under 400 characters"
                return None
            if how == "past" and not _all_over(text, today):
                refused[n] = "a part marked past names no full date, or a date that is not over"
                return None
            out.append({"n": n, "as": how, "subject": " ".join(str(p.get("subject") or "").split())[:60], "text": text})
        return out

    for n, original in enumerate(entries, 1):
        row = plan.get(n) or {}
        do = row.get("do")
        if do == "merge" and isinstance(row.get("into"), int) and row["into"] != n:
            merges[n] = row["into"]
            continue
        leaving = parts(n, row) if row.get("moved") else []
        stays = " ".join(str(row.get("text") or "").split()) if do == "keep" and row.get("text") else None
        if leaving is None or do not in ("keep", "leave") or (do == "leave" and not leaving):
            refused.setdefault(n, "no usable answer")
            text_of[n] = original
            continue
        if do == "keep" and stays is None and not leaving:
            text_of[n] = original
            continue
        lost = _covered(original, stays or "", *(p["text"] for p in leaving)) if (stays or do == "leave") else set()
        if do == "keep" and stays is None:
            lost = {"an entry cannot shed a part and stay as written"}
        if lost or (stays is not None and len(stays) >= len(original) and not leaving):
            refused[n] = "the answer drops " + ", ".join(sorted(lost)[:6]) if lost else "the new wording is no shorter"
            text_of[n] = original
            continue
        if stays is not None:
            text_of[n] = stays
        moved_of[n] = leaving
    for n, into in merges.items():
        target = text_of.get(into)
        if target is None or into in merges or _covered(entries[n - 1], target):
            refused[n] = "the entry it merges into does not hold everything this one said"
            text_of[n] = entries[n - 1]
    kept = [(n, text_of[n]) for n in sorted(text_of)]
    moved = [p for n in sorted(moved_of) for p in moved_of[n]]
    return {"kept": kept, "moved": moved, "refused": refused,
            "merged": {n: into for n, into in merges.items() if n not in refused}}




JUDGE_SYSTEM = "You compare two wordings of a note and say whether they mean exactly the same. Reply with JSON only."
JUDGE = """Each numbered item shows what an AI helper's notebook says now (BEFORE) and what a tidy-up would have it \
say instead (AFTER). AFTER may be shorter, may be split into parts, may put a finished event in the past tense, and \
may write a date out in full with its year. Decide for each item whether AFTER says exactly what BEFORE says.

It is the same only when all of these hold:
- Every instruction points the same way. Nothing forbidden is now allowed or required, and nothing required or \
allowed is now forbidden. Each "never", "not", "no", "only", "without", "before", "after", "always" still applies to \
the same thing.
- Every number, amount, date, time, name, id, address, path and command is unchanged.
- Every condition, exception and limit ("unless", "except", "if", "until", "only when") is still there, on the same \
thing.
- AFTER states nothing BEFORE did not say.
- Nothing BEFORE says is missing from AFTER.
When you are not sure, it differs.

Reply with JSON only: {{"same": [item numbers], "differs": [item numbers]}}

{items}"""


async def judge(text: str) -> str:
    ""

    return await ask(text, JUDGE_SYSTEM, tuple(reversed(MODELS)))


def _restore(checked: dict, entries: list[str], bad: set[int], why: str) -> dict:
    ""

    bad = set(bad) | {n for n, into in checked["merged"].items() if into in bad}
    kept = dict(checked["kept"])
    for n in bad:
        kept[n] = entries[n - 1]
    return {"kept": sorted(kept.items()), "moved": [p for p in checked["moved"] if p["n"] not in bad],
            "refused": {**checked["refused"], **{n: why for n in bad}},
            "merged": {n: into for n, into in checked["merged"].items() if n not in bad}}


def _changes(entries: list[str], checked: dict) -> dict[int, tuple[list[int], str, str]]:
    ""


    kept = dict(checked["kept"])
    parts: dict[int, list[str]] = {}
    for p in checked["moved"]:
        parts.setdefault(p["n"], []).append(p["text"])
    out = {}
    for n, original in enumerate(entries, 1):
        if n in checked["merged"]:
            continue
        group = [n] + sorted(m for m, into in checked["merged"].items() if into == n)
        after = ([kept[n]] if n in kept else []) + [t for m in group for t in parts.get(m, [])]
        if group == [n] and after == [original]:
            continue
        out[n] = (group, "\n".join(entries[m - 1] for m in group), "\n".join(after))
    return out


async def confirm(entries: list[str], checked: dict) -> dict | None:
    ""

    changes = _changes(entries, checked)
    if not changes:
        return checked
    items = "\n\n".join(f"[{n}]\nBEFORE:\n{before}\nAFTER:\n{after}" for n, (_, before, after) in changes.items())
    try:
        answer = await judge(JUDGE.format(items=items))
    except Exception as e:
        log.warning("no judge for a notebook's new wordings: %s", type(e).__name__)
        answer = ""
    found = re.search(r"\{.*\}", str(answer or ""), re.S)
    try:
        verdict = json.loads(found.group(0)) if found else None
    except ValueError:
        verdict = None
    if not isinstance(verdict, dict) or not all(isinstance(verdict.get(k, []), list) for k in ("same", "differs")) \
            or not (verdict.get("same") or verdict.get("differs")):
        return None
    same = {n for n in verdict.get("same", []) if isinstance(n, int)}
    same -= {n for n in verdict.get("differs", []) if isinstance(n, int)}
    bad = {m for n, (group, _, _) in changes.items() if n not in same for m in group}
    return _restore(checked, entries, bad, "the judge did not confirm that the new wording means the same") if bad \
        else checked


def with_shelf(kept: list[str], moved: list[dict]) -> list[str]:
    ""
    subjects = list(dict.fromkeys(p["subject"] or p["text"][:40] for p in moved if p["as"] == "shelf"))
    old = next((e for e in kept if e.startswith(SHELF)), None)
    if not subjects:
        return kept
    if old:
        subjects = list(dict.fromkeys([*old[len(SHELF):].rstrip(".").split("; "), *subjects]))
    line = SHELF + "; ".join(subjects) + "."
    return [e for e in kept if e is not old] + [line]




def _save_moved(bot_id: str, part: dict, now: float) -> int | None:
    ""
    how = part["as"]



    if how == "rule" and (has := facts.repeats(part["text"], "preference", bot_id)):
        return has["id"]
    try:
        fid, _ = facts.save(bot_id, part["text"], subject=part["subject"] or "notebook",
                            kind="preference" if how == "rule" else "other", source="import", limits=False,
                            private=how != "rule", until=now if how == "past" else None, merge=False)
    except facts.FactError as e:
        log.warning("a notebook entry could not be saved as a fact (%s), so it stays", e)
        return None
    return fid or None


class _Lock:
    ""


    def __init__(self, path: Path):
        self.path = path.with_suffix(path.suffix + ".lock")

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(f".mem_{os.getpid()}_{path.name}")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _history_dir(path: Path) -> Path:
    return path.with_name(path.name + HISTORY)


def _keep_copy(path: Path, text: str) -> None:
    folder = _history_dir(path)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromtimestamp(time.time()).strftime("%Y%m%d-%H%M%S")
    name = folder / f"{path.name}.{stamp}"
    n = 1
    while name.exists():
        n += 1
        name = folder / f"{path.name}.{stamp}-{n}"
    name.write_text(text, encoding="utf-8")
    for old in sorted(folder.glob(f"{path.name}.*"))[:-HISTORY_KEEP]:
        old.unlink(missing_ok=True)


def _last_copy(path: Path) -> str | None:
    folder = _history_dir(path)
    copies = sorted(folder.glob(f"{path.name}.*")) if folder.is_dir() else []
    return _read(copies[-1]) if copies else None


def keep_history(bot_id: str) -> list[int]:
    ""


    from . import team
    path = path_of(bot_id)
    text = _read(path)
    if text is None:
        return []
    before = _last_copy(path)
    if before == text:
        return []
    _keep_copy(path, text)
    if before is None:
        return []
    now_entries = split(text)
    saved = []
    for gone in split(before):
        if gone in now_entries or gone.startswith(SHELF) or any(not _covered(gone, e) or facts._similar(gone, e) >= 0.5
                                                                 for e in now_entries):
            continue
        for part in team._fact_parts(gone):
            try:
                fid, _ = facts.save(bot_id, part, subject="notebook", kind="other", source="import", limits=False,
                                    private=True, merge=False)
            except facts.FactError as e:
                log.warning("%s's notebook lost an entry that could not be saved as a fact (%s)", bot_id, e)
                continue
            if fid:
                saved.append(fid)
    if saved:
        log.warning("%s's notebook lost entries outside a tidy; saved them as facts %s", bot_id, saved)
    return saved


def _note(file: str, digest: str, outcome: str, before: int, after: int, moved: int, over: bool) -> None:
    with store._lock:
        c = store.db()
        row = c.execute("SELECT over_since FROM notebook_tidy WHERE file=?", (file,)).fetchone()
        since = (row["over_since"] if row and row["over_since"] else int(time.time())) if over else None
        c.execute("INSERT OR REPLACE INTO notebook_tidy (file, digest, at, outcome, before, after, moved, over_since) "
                  "VALUES (?,?,?,?,?,?,?,?)", (file, digest, int(time.time()), outcome, before, after, moved, since))
        c.commit()


def _file_key(bot_id: str, kind: str) -> str:
    return f"{bot_id}:{kind}"


async def tidy(bot_id: str, kind: str = NOTEBOOK, force: bool = False) -> dict | None:
    ""
    path, limit = path_of(bot_id, kind), limit_of(bot_id, kind)
    text = _read(path)
    if text is None:
        return None
    entries = split(text)
    size = len(join(entries))
    key = _file_key(bot_id, kind)
    row = store.db().execute("SELECT digest, outcome, at, over_since FROM notebook_tidy WHERE file=?", (key,)).fetchone()
    if size < limit * ROOM and not force:
        if row and row["over_since"]:
            _note(key, _digest(text), "ok", size, size, 0, False)
        return None
    if _private(bot_id):
        if not row or row["digest"] != _digest(text):
            _note(key, _digest(text), "never read by the model", size, size, 0, True)
        return None
    if row and row["digest"] == _digest(text) and not force and (
            row["outcome"] != NO_ANSWER or time.time() - row["at"] < RETRY_SEC):
        return None



    from . import privatewords
    hidden = frozenset(n for n, e in enumerate(entries, 1) if privatewords.holds(e))
    try:
        with shortreply.reading((bot_id,)):
            plan = parse(await ask(prompt(bot_id, kind, entries, limit, hidden)))
    except Exception as e:
        log.warning("could not tidy %s's %s: %s", bot_id, kind, type(e).__name__)
        plan = {}
    plan = {n: r for n, r in plan.items() if n not in hidden and r.get("into") not in hidden}
    if not plan:
        _note(key, _digest(text), NO_ANSWER, size, size, 0, True)
        return {"outcome": NO_ANSWER, "before": size, "after": size}
    with shortreply.reading((bot_id,)):
        checked = await confirm(entries, check(entries, plan, kind))
    if checked is None:
        _note(key, _digest(text), NO_ANSWER, size, size, 0, True)
        return {"outcome": NO_ANSWER, "before": size, "after": size}
    result = await asyncio.to_thread(_apply, bot_id, kind, path, text, entries, checked, limit)


    _note(key, _digest(text), result["outcome"], size, result["after"], result["moved"],
          result["after"] >= limit * ROOM)
    log.info("tidied %s's %s: %d to %d characters, %d parts moved to facts, %d answers refused (%s)", bot_id, kind, size,
             result["after"], result["moved"], len(result["refused"]), result["outcome"])
    return result


def _apply(bot_id: str, kind: str, path: Path, text: str, entries: list[str], checked: dict, limit: int) -> dict:
    now = time.time()
    with _Lock(path):
        if _read(path) != text:
            return {"outcome": "changed meanwhile", "after": len(join(entries)), "moved": 0, "refused": {},
                    "digest": ""}
        left: dict[int, list[dict]] = {}
        for part in checked["moved"]:
            left.setdefault(part["n"], []).append(part)
        failed = {n for n, parts in left.items() if any(_save_moved(bot_id, p, now) is None for p in parts)}

        failed |= {n for n, into in checked.get("merged", {}).items() if into in failed}
        kept = [entries[n - 1] if n in failed else t for n, t in checked["kept"]]
        kept_ns = {n for n, _ in checked["kept"]}
        kept += [entries[n - 1] for n in sorted(failed - kept_ns)]
        moved = [p for p in checked["moved"] if p["n"] not in failed]
        new = join(list(dict.fromkeys(with_shelf(kept, moved))))
        if not kept or len(new) >= len(join(entries)) or len(new) > limit:
            return {"outcome": "nothing to gain", "after": len(join(entries)), "moved": 0,
                    "refused": checked["refused"], "digest": _digest(text)}
        if kind == NOTEBOOK and _last_copy(path) != text:
            _keep_copy(path, text)
        if kind == PROFILE:

            from . import team
            team._keep_user_history(path, text, path.stat().st_mtime)
        _write(path, new)
        if kind == NOTEBOOK:
            _keep_copy(path, new)
        else:


            team.note_user_version(path, new)
    return {"outcome": "tidied" if len(new) < limit * ROOM else "still nearly full", "after": len(new),
            "moved": len(moved), "refused": checked["refused"], "digest": _digest(new), "parts": moved}


def stuck(now: float | None = None) -> list[dict]:
    ""
    now = now or time.time()
    return [dict(r) for r in store.db().execute(
        "SELECT * FROM notebook_tidy WHERE over_since IS NOT NULL AND over_since < ?", (now - STUCK_SEC,))]


async def run_once() -> None:
    for bot_id, kind in _targets():
        try:
            if kind == NOTEBOOK:
                await asyncio.to_thread(keep_history, bot_id)
            if ENABLED:
                await tidy(bot_id, kind)
        except Exception:
            log.warning("looking after %s's %s failed", bot_id, kind, exc_info=True)


async def run() -> None:
    await asyncio.sleep(120)
    while True:
        await run_once()
        await asyncio.sleep(EVERY_SEC)
