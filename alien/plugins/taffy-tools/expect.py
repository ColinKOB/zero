""













import re
import time

SETTLE_SEC = 1.5
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


_NOISE = re.compile(r"\bref=e\d+|^\s*-?\s*\[\d+\]|\bat \(-?\d+, -?\d+\)")

PARAM = {"type": "string", "description": (
    "What should change on the page after this click: the label of a count ('Cart', for the cart badge) or words that "
    "should appear ('Added to cart'). The result then says 'confirmed: ...' or 'nothing changed'. Use it on Add to cart "
    "and every other click whose effect you need.")}
HINT = ("Give expect on Add to cart and similar clicks; the result then says whether the page really changed.")


def _clean(line: str) -> str:
    return " ".join(_NOISE.sub("", line).split())


def _label_lines(text: str, label: str) -> list[str]:
    ""

    lines = [_clean(line) for line in (text or "").splitlines()]
    out = []
    for i, line in enumerate(lines):
        if label in line.casefold():
            if not _NUMBER.search(line) and i + 1 < len(lines):
                line = f"{line} {lines[i + 1]}"
            out.append(line)
    return out


def _counts(lines: list[str]) -> dict[str, list[str]]:
    ""
    out: dict[str, list[str]] = {}
    for line in lines:
        numbers = _NUMBER.findall(line)
        if numbers:
            out.setdefault(_NUMBER.sub("#", line), numbers)
    return out


def compare(before: str | None, after: str, expect: str) -> str:
    ""

    want = " ".join(str(expect or "").split())
    label = want.casefold()
    if not label or not after:
        return ""
    if before is None:
        return f'the page shows "{want}"' if label in after.casefold() else ""
    was, now = _counts(_label_lines(before, label)), _counts(_label_lines(after, label))
    for key, numbers in now.items():
        old = was.get(key)
        if old and old != numbers:
            changed = next((a, b) for a, b in zip(old, numbers) if a != b) if len(old) == len(numbers) else (old[0], numbers[0])
            return f'"{want}" went from {changed[0]} to {changed[1]}'



    old_first, new_first = next(iter(was.values()), None), next(iter(now.values()), None)
    if new_first and old_first != new_first:
        return f'"{want}" went from {old_first[0] if old_first else "none"} to {new_first[0]}'
    if after.casefold().count(label) > before.casefold().count(label):
        return f'"{want}" appeared'
    return ""


def verify(expect: str, before: str | None, after: str, reread, press_again, sleep=time.sleep) -> tuple[str, str]:
    ""


    seen = compare(before, after, expect)
    if seen:
        return f"confirmed: {seen}.", after
    sleep(SETTLE_SEC)
    after = reread() or after
    seen = compare(before, after, expect)
    if seen:
        return f"confirmed: {seen}.", after
    want = " ".join(str(expect).split())
    if press_again is None:
        return (f'nothing changed: "{want}" did not appear or change. It was not pressed again, because it can spend '
                "money, send or submit, or what it does is unknown. Read the page before you do anything else."), after
    if not press_again(after):
        return (f'nothing changed: "{want}" did not appear or change, and the element could not be found again to '
                "press it by position. Read the page before you try again."), after
    sleep(SETTLE_SEC)
    after = reread() or after
    seen = compare(before, after, expect)
    if seen:
        return f"confirmed after a second press at the element's center: {seen}.", after
    return (f'nothing changed: "{want}" did not appear or change, even after a second press at the element\'s center. '
            "Read the page and find out why before you press it again."), after
