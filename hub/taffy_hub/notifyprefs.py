""

















from . import store

KINDS = ("needs", "done", "progress")
_SETTING = "notify_kinds_off"


def kinds_for(bot_id: str | None) -> dict[str, bool]:
    off = set(((store.get_setting(_SETTING, {}) or {}).get(bot_id or "default")) or [])
    return {k: k not in off for k in KINDS}


def set_kinds(bot_id: str, **changes: bool | None) -> dict[str, bool]:
    ""
    unknown = [k for k in changes if k not in KINDS]
    if unknown:
        raise ValueError(f"unknown kind {unknown[0]}")
    allv = store.get_setting(_SETTING, {}) or {}
    off = set(allv.get(bot_id) or [])
    for k, on in changes.items():
        if on is True:
            off.discard(k)
        elif on is False:
            off.add(k)
    if off:
        allv[bot_id] = sorted(off)
    else:
        allv.pop(bot_id, None)
    store.set_setting(_SETTING, allv)
    return kinds_for(bot_id)


def allows(bot_id: str | None, kind: str | None) -> bool:
    ""

    if not store.notify_on(bot_id):
        return False
    return kinds_for(bot_id).get(kind or "needs", True)
