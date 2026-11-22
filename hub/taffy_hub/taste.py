""







import json
from pathlib import Path

from . import config

DIR: Path = config.SHARED_DIR / "taste"
CORE = "core.md"
CHECKS = "checks.json"


class CheckError(ValueError):
    ""


def present() -> bool:
    return (DIR / CORE).is_file() or (DIR / CHECKS).is_file()


def core_text() -> str:
    try:
        return (DIR / CORE).read_text().strip()
    except OSError:
        return ""


def checks() -> dict[str, list[str]]:
    ""
    try:
        data = json.loads((DIR / CHECKS).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): [str(q) for q in v] for k, v in data.items() if isinstance(v, list)}


def validate(check) -> dict | None:
    ""


    kinds = checks()
    if not kinds:
        return None
    if not isinstance(check, dict):
        raise CheckError("Mark a milestone done with a check: {type, answers: [{q, yes, note}]}, where type is one of "
                         + ", ".join(sorted(kinds)) + ". Answer each question in the taste kit for that type.")
    kind = str(check.get("type") or "").strip()
    if kind not in kinds:
        raise CheckError(f"Unknown check type {kind!r}. Use one of: {', '.join(sorted(kinds))}.")
    raw = check.get("answers")
    if not isinstance(raw, list) or not raw:
        raise CheckError(f"Answer the {kind} questions: " + "; ".join(kinds[kind]))
    answers = []
    for a in raw:
        if not isinstance(a, dict):
            raise CheckError("Each answer is {q, yes, note}.")
        q = " ".join(str(a.get("q") or "").split())[:300]
        yes = bool(a.get("yes"))
        note = " ".join(str(a.get("note") or "").split())[:300]
        if not q:
            raise CheckError("Each answer names its question in q.")
        if not yes and not note:
            raise CheckError(f"\"{q}\" is a no without a note. Say what is missing and why it is fine, or fix it first.")
        answers.append({"q": q, "yes": yes, "note": note})
    asked = {_norm(a["q"]) for a in answers}
    missing = [q for q in kinds[kind] if _norm(q) not in asked]
    if missing:
        raise CheckError(f"The {kind} check also asks: " + "; ".join(missing) + ". Answer each one.")
    return {"type": kind, "answers": answers}


def _norm(q: str) -> str:
    return " ".join(q.lower().split()).rstrip("?.! ")


def soul_lines() -> str:
    ""
    core = core_text()
    if not core:
        return (f"If the folder `{DIR}` exists, read every file in it before you make anything a person or a customer "
                "will see, and follow it.")
    return (f"{core}\n\nThe full guides (brand, web, sales, social, operator) and the checks are in `{DIR}`; read the one "
            "for what you are making before you make it.")
