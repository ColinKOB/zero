"""Which kinds of news from each Zip reach the person's phone (fluid app, Oct 9).

Builds on the per-Zip "Notify Me When It Finishes or Needs Me" switch (store.notify_on), which stays the master:
off means nothing from that Zip notifies. With it on, the person picks the kinds, the levels Grok Bot users asked
for (research rows #75 to #77: "stuck / question needing input-approval / something important / status updates"):

- "needs": decisions and blocks. A card waiting for an okay, a sign-in, a blocked or failed task, a reply the
  person is waiting on.
- "done": finished work. A task the Zip finished, a result of something the person asked for.
- "progress": routine progress. Check-ins from scheduled jobs and other updates that ask nothing.

Every kind starts on, so nothing changes until the person turns one off. Only the kinds turned off are stored
(setting "notify_kinds_off": {bot: [kind, ...]}), so a new Zip behaves like the rest.

The push path decides the kind of each notification. watcher.push takes a `kind` and checks `allows(bot, kind)`
for each Zip it names; see docs/qa/2026-10-09-fluid-app.md, "Hub fields for fluid-chat".
"""

from . import store

KINDS = ("needs", "done", "progress")
_SETTING = "notify_kinds_off"


def kinds_for(bot_id: str | None) -> dict[str, bool]:
    off = set(((store.get_setting(_SETTING, {}) or {}).get(bot_id or "default")) or [])
    return {k: k not in off for k in KINDS}


def set_kinds(bot_id: str, **changes: bool | None) -> dict[str, bool]:
    """Changes any of the kinds given (None leaves one as it is) and returns all of them."""
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
    """Whether a notification of this kind from this Zip may go out. The master switch comes first; a push with no
    kind (older callers) counts as "needs", the kind that always mattered most."""
    if not store.notify_on(bot_id):
        return False
    return kinds_for(bot_id).get(kind or "needs", True)
