""














import asyncio
import re
import time

POLL_SEC, SETTLE_SEC = 0.5, 3.0
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


_NOISE = re.compile(r"\bref=e\d+|^\s*-?\s*\[\d+\]|\bat \(-?\d+, -?\d+\)")


_NOT_TWICE = re.compile(
    r"\b(?:buy|order|pay|purchase|check ?out|place|submit|send|post|publish|share|delete|remove|confirm|book|reserve|"
    r"donate|subscribe|transfer|agree|accept|apply|sign|finish|complete|continue|next|proceed|save|upload|reply)",
    re.I)
_LINE = re.compile(r'^\s*\[(\d+)\] \w+ "([^"]*)"')
_AT = re.compile(r"\bat \((-?\d+), (-?\d+)\)")


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


def name_of(listing: str | None, number: int) -> str:
    ""
    for line in (listing or "").splitlines():
        match = _LINE.match(line)
        if match and int(match[1]) == number:
            return match[2].removesuffix(" (by its row)")
    return ""


def position(listing: str, name: str) -> tuple[int, int, str] | None:
    ""
    want = " ".join(name.split()).casefold()
    spots = []
    for line in (listing or "").splitlines():
        match, at = _LINE.match(line), _AT.search(line)
        if match and " ".join(match[2].removesuffix(" (by its row)").split()).casefold() == want:
            spots.append((int(at[1]), int(at[2]), line) if at else None)
    return spots[0] if len(spots) == 1 and spots[0] else None


def press_again_ok(args: dict, name: str) -> bool:
    return bool(name.strip()) and "text" not in args and args.get("action", "press") == "press" \
        and not _NOT_TWICE.search(name)


async def check(expect: str, args: dict, before: str | None, result, read, click,
                sleep=asyncio.sleep) -> tuple[str, bool]:
    ""


    want = " ".join(str(expect).split())
    delta = result.get("ui_delta") if isinstance(result, dict) else None
    if isinstance(delta, dict):
        seen = compare("\n".join(map(str, delta.get("removed") or [])), "\n".join(map(str, delta.get("added") or [])),
                       want)
        if seen:
            return f"confirmed: {seen}.", True
    if before is None:
        return (f'unchecked: "{want}" did not show in the press\'s own change summary, and there is no full mac_ui '
                "read from before a press by number to measure it against. Read the window to see if it worked."), True

    async def settle() -> str:
        until = time.monotonic() + SETTLE_SEC
        while True:
            await sleep(POLL_SEC)
            seen = compare(before, await read(""), want)
            if seen or time.monotonic() >= until:
                return seen

    seen = await settle()
    if seen:
        return f"confirmed: {seen}.", True
    name = str(args.get("name") or "").strip()
    if not press_again_ok(args, name):
        return (f'nothing changed: "{want}" did not appear or change. It was not pressed again, because it can spend '
                "money, send, submit or move on, or it filled a field. Read the window before you do anything else."), \
            False
    if isinstance(delta, dict) and (delta.get("window_changed") or delta.get("panels_changed")):

        return (f'nothing changed: "{want}" did not appear or change, but the window or its sheets did, so it was not '
                "pressed again. Read the window before you do anything else."), False
    spot = position(await read(name), name)
    if spot is None:
        return (f'nothing changed: "{want}" did not appear or change, and "{name}" could not be found exactly once '
                "to press it by position. Read the window before you try again."), False
    x, y, line = spot
    if not press_again_ok(args, _clean(line)):
        return (f'nothing changed: "{want}" did not appear or change. It was not pressed again, because the control '
                "now reads as one that can spend money, send or submit. Read the window before you do anything "
                "else."), False
    await click(x, y)
    seen = await settle()
    if seen:
        return f"confirmed after a second press at the control's position: {seen}.", True
    return (f'nothing changed: "{want}" did not appear or change, even after a second press at the control\'s '
            "position. Read the window and find out why before you press it again."), False
