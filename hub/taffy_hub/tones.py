"""Each Zip's message tone (Colin, Oct 11: "can we make personalized message tones for each of the agents?" and "can
we give the user the ability to change the sounds if they'd prefer a different one?").

The tones are short sounds bundled in the app (ios/scripts/make-tones.py writes zero-<name>.caf). Every Zip gets
one by default, the lead the chime and the others in order, so the person can tell who wrote without looking. The
person can pick another for any Zip, "default" (the phone's own sound), "none" (no sound, the notification still
shows), or "custom": a sound of their own the app saved on that device as zero-custom-<zip>.caf. A device without
that file plays the phone's own sound instead, which iOS does by itself.

Only choices the person made are stored (setting "notify_tones": {zip: name}), so a new Zip gets its own default.
apns.payload_for asks `sound_for` for every notification.
"""

from . import config, store

TONES = ("chime", "marimba", "pluck", "bell", "glass", "harp", "ding", "knock", "bubble", "pop")
SPECIAL = ("default", "none", "custom")
_SETTING = "notify_tones"
LEAD = "default"   # Zero, the lead Zip


def default_for(bot_id: str | None) -> str:
    """The tone a Zip has until the person picks one: the lead the chime, the rest the others in their order."""
    bot_id = bot_id or LEAD
    if bot_id == LEAD:
        return TONES[0]
    others = [b.id for b in config.BOTS if b.id != LEAD]
    i = others.index(bot_id) if bot_id in others else sum(map(ord, bot_id))
    return TONES[1 + i % (len(TONES) - 1)]


def tone_for(bot_id: str | None) -> str:
    chosen = (store.get_setting(_SETTING, {}) or {}).get(bot_id or LEAD)
    return chosen if chosen in TONES or chosen in SPECIAL else default_for(bot_id)


def set_tone(bot_id: str, tone: str) -> str:
    if tone not in TONES and tone not in SPECIAL:
        raise ValueError(f"unknown tone {tone}")
    allv = store.get_setting(_SETTING, {}) or {}
    allv[bot_id] = tone
    store.set_setting(_SETTING, allv)
    return tone


def sound_for(bot_id: str | None) -> str | None:
    """The payload's sound: a bundled tone's file, the person's own file, "default", or None for no sound."""
    tone = tone_for(bot_id)
    if tone == "none":
        return None
    if tone == "default":
        return "default"
    if tone == "custom":
        return f"zero-custom-{bot_id or LEAD}.caf"
    return f"zero-{tone}.caf"
