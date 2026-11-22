""





import secrets
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Selection:
    app: str
    id: str
    label: str = ""
    creating: bool = False
    bot_id: str = ""
    set_default: bool = False


_selected: ContextVar[Selection | None] = ContextVar("app_connection", default=None)
_pending: dict[str, tuple[Selection, float]] = {}


def supported(app_id: str) -> bool:
    from . import keys, oauth
    return app_id in keys.BY_ID or app_id in oauth.APPS


def document(raw: dict) -> dict:
    if raw.get("version") == 2:
        return raw
    return {"version": 2, "default": "legacy" if raw else "", "bindings": {},
            "connections": {"legacy": raw} if raw else {}}


def capture() -> dict | None:
    ref = _selected.get()
    return asdict(ref) if ref else None


@contextmanager
def using(selection: dict | None):
    token = _selected.set(Selection(**selection) if selection else None)
    try:
        yield
    finally:
        _selected.reset(token)


@contextmanager
def for_bot(app_id: str, bot_id: str):
    from . import apps
    doc = document(apps._document(app_id)) if supported(app_id) else {}
    cid = doc.get("bindings", {}).get(bot_id, doc.get("default"))
    with using({"app": app_id, "id": cid} if cid else None):
        yield


def current(app_id: str) -> dict | None:
    from . import apps
    ref = _selected.get()
    if ref and ref.app == app_id:
        return asdict(ref)
    doc = document(apps._document(app_id))
    return asdict(Selection(app_id, doc["default"])) if doc["default"] else None


def record(app_id: str, raw: dict) -> dict:
    ref = _selected.get()
    if raw.get("version") != 2 and (not ref or ref.app != app_id):
        return raw
    doc = document(raw)
    cid = ref.id if ref and ref.app == app_id else doc["default"]
    return doc["connections"].get(cid, {})


def saved(app_id: str, raw: dict, data: dict) -> dict:
    ref = _selected.get()
    if ref and ref.app != app_id:
        ref = None
    doc = document(raw)
    if ref and not ref.creating and ref.id not in doc["connections"]:
        from . import apps
        raise apps.AppError("That connection was removed. Choose another one.", connected=False)
    if raw.get("version") != 2 and (not ref or ref.id == "legacy"):
        return data
    first = not doc["connections"]
    unselected = ref is None and not doc["default"]
    cid = ref.id if ref else doc["default"] or secrets.token_hex(12)
    old = doc["connections"].get(cid, {})
    label = ref.label if ref and ref.app == app_id and ref.label else old.get("label", "")
    doc["connections"][cid] = {**data, "label": label}
    if unselected or (ref and ref.set_default) or (first and not doc["default"] and not (ref and ref.bot_id)):
        doc["default"] = cid
    if ref and ref.bot_id:
        doc["bindings"][ref.bot_id] = cid
    return doc


def removed(app_id: str, raw: dict) -> dict | None:
    ref = _selected.get()
    if not ref or ref.app != app_id:
        return None
    doc = document(raw)
    doc["connections"].pop(ref.id, None)
    if doc["default"] == ref.id:
        doc["default"] = ""
    return doc


def _label(value) -> str:
    from . import apps
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
        raise apps.AppError("Name this connection in 80 characters or fewer.")
    return " ".join(value.split())


def begin(app_id: str, body: dict) -> dict | None:
    from . import apps, config
    if not supported(app_id):
        if body.get("new_connection") or body.get("connection_id"):
            raise apps.AppError("Manage this app's accounts in its existing settings.")
        return None
    if body.get("new_connection"):
        return asdict(Selection(app_id, secrets.token_hex(12), _label(body.get("label")), creating=True))
    cid = body.get("connection_id")
    if cid:
        doc = document(apps._document(app_id))
        if not isinstance(cid, str) or cid not in doc["connections"]:
            raise apps.AppError("That connection was removed. Choose another one.")
        return asdict(Selection(app_id, cid))
    bot_id = body.get("bot_id")
    if bot_id is not None and (not isinstance(bot_id, str) or bot_id not in config.BOTS_BY_ID):
        raise apps.AppError("Choose a Zip from this home.")
    raw = apps._document(app_id)
    if raw.get("version") == 2 and not raw["default"]:
        return asdict(Selection(app_id, secrets.token_hex(12), creating=True,
                                bot_id=bot_id or "", set_default=not bool(bot_id)))
    return None


def key_start(selection: dict) -> str:
    now = time.time()
    for state, (_, exp) in list(_pending.items()):
        if exp < now:
            _pending.pop(state, None)
    state = secrets.token_urlsafe(24)
    _pending[state] = (Selection(**selection), now + 900)
    return state


def key_finish(app_id: str, state: str) -> dict:
    from . import apps
    entry = _pending.get(state)
    if not entry or entry[0].app != app_id or entry[1] < time.time():
        raise apps.AppError("This connection expired. Tap Add Connection and try again.")
    return asdict(entry[0])


def key_done(state: str) -> None:
    _pending.pop(state, None)


def payload(app_id: str) -> dict:
    from . import apps, config
    if not supported(app_id):
        raise apps.AppError("Manage this app's accounts in its existing settings.")
    doc = document(apps._document(app_id))
    rows = []
    for cid, data in doc["connections"].items():
        if not data.get("token"):
            continue
        rows.append({"id": cid, "label": data.get("label") or data.get("account") or "Existing Connection",
                     "account": data.get("account") or "", "projects": data.get("projects") or [],
                     "state": data.get("state") or apps.CONNECTED, "is_default": cid == doc["default"],
                     "bot_ids": [b for b, chosen in doc["bindings"].items() if chosen == cid]})
    return {"connections": rows, "bots": [{"id": b.id, "name": b.name} for b in config.BOTS]}


def update(app_id: str, cid: str, body: dict) -> dict:
    from . import apps, config
    if not supported(app_id):
        raise apps.AppError("Manage this app's accounts in its existing settings.")
    label = _label(body.get("label"))
    bots = body.get("bot_ids")
    if not isinstance(bots, list) or any(not isinstance(b, str) or b not in config.BOTS_BY_ID for b in bots):
        raise apps.AppError("Choose Zips from this home.")
    if not isinstance(body.get("is_default"), bool):
        raise apps.AppError("Choose whether other Zips use this connection.")
    with apps._lock:
        doc = document(apps._document(app_id))
        if cid not in doc["connections"]:
            raise apps.AppError("That connection was removed. Choose another one.")
        doc["connections"][cid]["label"] = label
        doc["bindings"] = {b: chosen for b, chosen in doc["bindings"].items() if chosen != cid}
        doc["bindings"].update({b: cid for b in bots})
        if body["is_default"]:
            doc["default"] = cid
        elif doc["default"] == cid:
            doc["default"] = ""
        apps._write_document(app_id, doc)
    return {"ok": True, **payload(app_id)}
