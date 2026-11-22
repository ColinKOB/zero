""
















import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime
from urllib.parse import urlsplit

from . import config, logins, store, who

log = logging.getLogger("taffy.wallet")

KINDS = ("login", "card", "address", "identity")
VAULT_KIND = {"card": "payment", "address": "address", "identity": "identity"}


DEFAULT_LIMITS = {"per_month": 300}
LIMIT_MAX = 10000
ID_TYPES = ("drivers_license", "passport", "ssn", "dob", "other")
ID_NAMES = {"drivers_license": "Driver's license", "passport": "Passport", "ssn": "Social Security number",
            "dob": "Date of birth", "other": "ID number"}
REQUEST_KIND = "wallet_request"
_lock = threading.RLock()


class WalletError(Exception):
    ""


class NotFound(WalletError):
    pass


class NeedsSecret(WalletError):
    ""




def _path():
    return config.HUB_DIR / "wallet.json"


def _load() -> dict:
    ""

    try:
        data = json.loads(_path().read_text())
    except FileNotFoundError:
        return {"items": {}}
    except (OSError, ValueError) as e:
        raise WalletError("The Wallet couldn't be read. Try again in a minute.") from e
    if not (isinstance(data, dict) and isinstance(data.get("items"), dict)):
        raise WalletError("The Wallet couldn't be read. Try again in a minute.")
    return data


def _save(data: dict) -> None:
    config.HUB_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(_path())


def _save_or_undo(data: dict, new: dict) -> None:
    ""

    try:
        _save(data)
    except Exception as e:
        log.warning("could not save the wallet (%s); removing the copies just made", type(e).__name__)
        _remove(new)
        raise WalletError("That couldn't be saved. Try again in a minute.") from e


def set_meta(item_id: str, **meta) -> None:
    ""
    with _lock:
        data = _load()
        item = data["items"].get(item_id)
        if item is None:
            return
        item["meta"] = {**(item.get("meta") or {}), **meta}
        _save(data)


def stored(item_id: str) -> dict | None:
    ""
    return _load()["items"].get(item_id)


def public(item: dict) -> dict:
    ""
    out = {k: v for k, v in item.items() if k not in ("handles", "stuck", "sites")}
    if item.get("limits"):
        out["limits"] = {"per_month": item["limits"]["per_month"]}
    out["paused"] = bool(item.get("paused"))
    if item.get("stuck"):
        out["not_removed"] = sorted(item["stuck"])
    return out




def _luhn(number: str) -> bool:
    total = 0
    for i, d in enumerate(reversed(number)):
        n = int(d) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def brand(number: str) -> str:
    if number.startswith("4"):
        return "Visa"
    if number[:2] in ("34", "37"):
        return "Amex"
    if 51 <= int(number[:2]) <= 55 or 2221 <= int(number[:4]) <= 2720:
        return "Mastercard"
    if number.startswith(("6011", "65")) or 644 <= int(number[:3]) <= 649:
        return "Discover"
    return "Card"


def _text(secret: dict, key: str, limit: int = 120) -> str:
    v = " ".join(str(secret.get(key) or "").split())
    if len(v) > limit:
        raise WalletError("One of the fields is too long.")
    return v


def card_values(secret: dict) -> tuple[dict, dict]:
    ""
    number = re.sub(r"[\s-]", "", str(secret.get("card_number") or ""))
    if not number.isdigit() or not 12 <= len(number) <= 19 or not _luhn(number):
        raise WalletError("That card number doesn't look right. Check it and try again.")
    cvc = str(secret.get("cvc") or "").strip()
    if not cvc.isdigit() or len(cvc) not in (3, 4):
        raise WalletError("Enter the 3 or 4 digit security code.")
    try:
        month, year = int(str(secret.get("exp_month") or "").strip()), int(str(secret.get("exp_year") or "").strip())
    except ValueError:
        raise WalletError("Enter the expiration month and year.")
    year = year + 2000 if year < 100 else year
    now = datetime.now()
    if not 1 <= month <= 12 or year > now.year + 30:
        raise WalletError("Enter the expiration month and year.")
    if (year, month) < (now.year, now.month):
        raise WalletError("That card has expired.")
    name, postal = _text(secret, "cardholder_name"), _text(secret, "postal_code", 12)
    vault = {"card_number": number, "cvc": cvc, "exp_month": str(month), "exp_year": str(year),
             "cardholder_name": name, "billing_postal_code": postal}
    meta = {"brand": brand(number), "last4": number[-4:], "exp_month": month, "exp_year": year, "name_on_card": name}
    return {k: v for k, v in vault.items() if v}, meta


def address_values(secret: dict) -> tuple[dict, dict]:
    v = {k: _text(secret, k, 200) for k in ("name", "line1", "line2", "city", "region", "postal_code", "country",
                                           "phone", "email")}
    if not (v["line1"] and v["city"] and v["postal_code"] and v["country"]):
        raise WalletError("Enter the street, city, ZIP code and country.")
    vault = {"name": v["name"], "address_line1": v["line1"], "address_line2": v["line2"], "city": v["city"],
             "state": v["region"], "postal_code": v["postal_code"], "country": v["country"], "phone": v["phone"],
             "email": v["email"]}
    meta = {"name": v["name"], "city": v["city"], "region": v["region"], "country": v["country"]}
    return {k: x for k, x in vault.items() if x}, meta


def identity_values(secret: dict) -> tuple[dict, dict, str | None]:
    ""
    value = _text(secret, "value", 64)
    id_type = str(secret.get("id_type") or "").strip()
    if not value:
        raise WalletError("Enter the number.")
    if id_type not in ID_TYPES:
        raise WalletError("Pick what kind of ID this is.")
    issuer = _text(secret, "issuer", 80)
    origin = origin_of(str(secret.get("origin") or "")) if secret.get("origin") else None
    meta = {"type": id_type, "issuer": issuer}
    digits = re.sub(r"[\s-]", "", value)
    if id_type != "dob" and len(digits) >= 4:
        meta["last4"] = digits[-4:]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(secret.get("expires") or "")):
        meta["expires"] = secret["expires"]
    if origin:
        meta["origin"] = origin
    return {k: x for k, x in {"value": value, "id_type": id_type, "issuer": issuer}.items() if x}, meta, origin


def values(kind: str, secret: dict) -> tuple[dict, dict, str | None]:
    if not isinstance(secret, dict):
        raise WalletError("Fill in the fields first.")
    if kind == "card":
        return (*card_values(secret), None)
    if kind == "address":
        return (*address_values(secret), None)
    if kind == "identity":
        return identity_values(secret)
    raise WalletError("That kind of item can't be saved here.")


def default_label(kind: str, meta: dict) -> str:
    if kind == "card":
        return f"{meta['brand']} ending {meta['last4']}"
    if kind == "identity":
        return ID_NAMES.get(meta.get("type") or "", "ID number")
    if any(i.get("kind") == "address" for i in _load()["items"].values()):
        return meta.get("city") or "Address"
    return "Home"


def origin_of(site: str) -> str:
    ""
    s = str(site or "").strip()
    if not s:
        raise WalletError("Give the site.")
    if "://" not in s:
        s = "https://" + s
    p = urlsplit(s)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or not host or "." not in host:
        raise WalletError("Give the site's secure address, starting with https://.")
    return f"https://{host}" + (f":{p.port}" if p.port and p.port != 443 else "")


def same_site(a: str, b: str) -> bool:
    ""
    def host(o):
        return (urlsplit(o).netloc or "").lower().removeprefix("www.")
    return bool(a and b) and host(a) == host(b)


def site_name(origin: str) -> str:
    ""
    return (urlsplit(origin).hostname or origin or "").lower().removeprefix("www.")


def _zips(zips) -> list[str]:
    if zips is None:
        return [b.id for b in config.BOTS]
    picked = [z for z in dict.fromkeys(str(z) for z in zips) if z in config.BOTS_BY_ID]
    if not picked:
        raise WalletError("Pick at least one Zip.")
    return picked




def _vault(bot_id: str):
    return logins._store(bot_id)


def _push(kind: str, label: str, secret: dict, origin: str | None, bots: list[str]) -> tuple[dict, list[str]]:
    ""
    handles, failed = {}, []
    for bot in bots:
        try:
            handles[bot] = _vault(bot).add_item(VAULT_KIND[kind], label, secret, origin=origin).id
        except Exception as e:
            log.warning("could not add a %s to %s's vault (%s)", kind, bot, type(e).__name__)
            failed.append(bot)
    return handles, failed


def _remove(handles: dict) -> tuple[list[str], list[str]]:
    ""
    removed, failed = [], []
    for bot, handle in handles.items():
        try:
            _vault(bot).remove_item(handle)
            removed.append(bot)
        except Exception as e:
            log.warning("could not remove a wallet copy from %s's vault (%s)", bot, type(e).__name__)
            failed.append(bot)
    return removed, failed


def _clear_stuck(item: dict, bots: list[str]) -> list[str]:
    ""


    stuck = item.get("stuck") or {}
    back = {b: stuck.pop(b) for b in bots if b in stuck}
    _, still = _remove(back)
    stuck.update({b: back[b] for b in still})
    item["stuck"] = stuck
    return still


def _copy_address(item: dict, to_bots: list[str]) -> tuple[dict, list[str]]:
    ""

    holder = next((b for b in item["zips"] if item["handles"].get(b)), None)
    if holder is None:
        return {}, list(to_bots)
    try:
        secret = _vault(holder).resolve_secret(item["handles"][holder])
    except Exception:
        return {}, list(to_bots)
    try:
        return _push("address", item["label"], secret, None, to_bots)
    finally:
        del secret




def _login_id(key: tuple[str, str]) -> str:
    return "w_l" + hashlib.sha256(f"{key[0]}|{key[1]}".encode()).hexdigest()[:16]


def _login_groups() -> dict[str, dict]:
    ""
    from . import signin_access
    out: dict[str, dict] = {}
    try:
        rows = logins.list_all()
    except Exception:
        log.warning("could not read the saved logins", exc_info=True)
        return out
    signin_access.migrate_passwords(rows)
    for r in rows:
        if signin_access.password_revoked(r["bot"], r["id"]):
            continue
        key = logins._key(r.get("origin"), r.get("identifier"))
        if not key[0]:
            key = (str(r.get("id")), "")
        item_id = _login_id(key)
        item = out.setdefault(item_id, {
            "id": item_id, "kind": "login", "label": r.get("label") or site_name(str(r.get("origin") or "")),
            "zips": [], "meta": {"origin": r.get("origin"), "username": r.get("identifier")},
            "limits": None, "paused": False, "_rows": {}, "_made": {}})
        item["_rows"][r["bot"]] = r["id"]
        item["_made"][r["bot"]] = str(r.get("created_at") or "")
    for item in out.values():
        item["zips"] = [b.id for b in config.BOTS if signin_access.allowed(b.id, item["meta"]["origin"] or "")]
    return out


def _login_public(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ("_rows", "_made")}


def list_items() -> list[dict]:
    items = [public(i) for i in _load()["items"].values()]
    items += [_login_public(i) for i in _login_groups().values()]
    order = {k: n for n, k in enumerate(KINDS)}
    return sorted(items, key=lambda i: (order.get(i["kind"], 9), i.get("label", "").lower()))


def for_bot(bot_id: str) -> dict:
    ""
    out = []
    for i in _load()["items"].values():
        if bot_id in i["zips"] and i["handles"].get(bot_id):
            out.append({**public(i), "handle": i["handles"][bot_id]})
    notes = []
    if not any(i["kind"] == "address" for i in out):


        notes.append(f"You have no saved address. When you already know {who.possessive()} address and "
                     f"{who.first()} said yes to using it here, type it in yourself; ask_for_wallet_item an address "
                     "only when you have none.")
    if any(i["kind"] == "card" for i in out):
        return {"items": out, "note": " ".join(notes)} if notes else {"items": out}



    return {"items": out, "note": " ".join(["You have no saved card. " + no_card_steps(), *notes])}


def no_card_steps() -> str:
    first = who.first()
    return ("Take the checkout to the page with the total and the pay button, then stop and call hand_over with kind "
            f"final and a job name, saying what is in the cart, the total the page shows and the one button {first} "
            f"presses. Ask for a card with ask_for_wallet_item only once {first} asks you to pay yourself next time.")


def by_handle(bot_id: str, handle: str) -> dict | None:
    ""
    for i in _load()["items"].values():
        if i["handles"].get(bot_id) == handle or (i.get("stuck") or {}).get(bot_id) == handle:
            return i
    return None


def find_for_bot(bot_id: str, ref: str, kind: str) -> dict:
    ""
    ref = " ".join(str(ref or "").split()).lower()
    mine = [i for i in _load()["items"].values() if i["kind"] == kind and bot_id in i["zips"]]
    hit = [i for i in mine if i["id"].lower() == ref] or [i for i in mine if i["label"].lower() == ref] or \
        [i for i in mine if ref and (ref in i["label"].lower() or ref == str(i["meta"].get("last4") or ""))]
    if len(hit) == 1:
        return hit[0]
    if not mine:
        raise NotFound("You have no saved card. " + no_card_steps() if kind == "card" else
                       "You have no saved ID. Ask for one with ask_for_wallet_item.")
    names = ", ".join(i["label"] for i in mine)
    raise NotFound(f"Name one of your saved items exactly: {names}.")




def add(kind: str, secret: dict, label: str | None = None, zips=None, limits: dict | None = None) -> dict:
    ""

    if kind == "login":
        return _add_login(secret, label, zips)
    vault, meta, origin = values(kind, secret)
    label = " ".join(str(label or "").split())[:80] or default_label(kind, meta)
    bots = _zips(zips)
    with _lock:
        data = _load()
        handles, failed = _push(kind, label, vault, origin, bots)
        del vault
        if not handles:
            raise WalletError("That couldn't be saved. Try again in a minute.")
        item = {"id": "w_" + secrets.token_hex(6), "kind": kind, "label": label, "zips": list(handles), "meta": meta,
                "limits": _limits(limits) if kind == "card" else None, "paused": False,
                "handles": handles, "created_at": time.time()}
        data["items"][item["id"]] = item
        _save_or_undo(data, handles)
    return {**public(item), "failed": failed}


def _add_login(secret: dict, label: str | None, zips) -> dict:
    if not isinstance(secret, dict):
        raise WalletError("Fill in the fields first.")
    url = str(secret.get("origin") or "").strip()
    username = " ".join(str(secret.get("username") or "").split())
    password = str(secret.get("password") or "")
    if not username or not password or len(password) > 1024 or len(username) > 320:
        raise WalletError("Enter the username and password.")
    origin = origin_of(url)
    id_type = str(secret.get("identifier_type") or "") or identifier_type(username)
    otp = str(secret.get("otp_secret") or "").strip() or None
    try:
        saved, failed = logins.save_reporting(_zips(zips), " ".join(str(label or "").split())[:120] or
                                              site_name(origin), url if "://" in url else origin, username,
                                              id_type, password, otp)
    except logins.SaveFailed as e:
        names = ", ".join(config.BOTS_BY_ID[b].name if b in config.BOTS_BY_ID else b for b in e.stuck)
        raise WalletError(f"That login couldn't be saved, and part of it is left with {names}. Remove it there "
                          "and try again.")
    except Exception as e:
        log.info("login save failed: %s", type(e).__name__)
        raise WalletError("That login couldn't be saved. Check the site address.")
    item = login_item(saved[0]["origin"], username) if saved else None
    if item is None:
        raise WalletError("That login couldn't be saved. Try again in a minute.")
    return {**item, "failed": failed}


def identifier_type(username: str) -> str:
    return "email" if "@" in username else ("phone" if re.fullmatch(r"\+?[\d\s().-]{7,}", username) else "username")


def login_item(origin: str, username: str) -> dict | None:
    ""
    item = _login_groups().get(_login_id(logins._key(origin, username)))
    return _login_public(item) if item else None


def _limits(limits: dict | None, current: dict | None = None) -> dict:
    out = {"per_month": (current or DEFAULT_LIMITS)["per_month"]}
    if limits and limits.get("per_month") is not None:
        try:
            v = round(float(limits["per_month"]), 2)
        except (TypeError, ValueError):
            raise WalletError("Limits are dollar amounts.")
        if not 0 <= v <= LIMIT_MAX:
            raise WalletError(f"Limits go up to ${LIMIT_MAX:,}.")
        out["per_month"] = int(v) if v == int(v) else v
    return out


def update(item_id: str, label=None, zips=None, limits=None, paused=None) -> dict:
    ""

    if item_id.startswith("w_l"):
        return _update_login(item_id, label, zips, limits, paused)
    with _lock:
        item = stored(item_id)
        if item is None:
            raise NotFound("That item is gone.")
        new_zips = _zips(zips) if zips is not None else list(item["zips"])
        added = [b for b in new_zips if b not in item["zips"]]
        if added and item["kind"] in ("card", "identity"):
            raise NeedsSecret("Confirm with Face ID to add it.")
        if item["kind"] != "card" and limits is not None:
            raise WalletError("Only cards have limits.")
        if paused is not None and item["kind"] != "card":
            raise WalletError("Only cards can be paused.")
        if label is not None:
            label = " ".join(str(label).split())[:80]
            if not label:
                raise WalletError("Give it a name.")
            item["label"] = label
        if limits is not None:
            item["limits"] = _limits(limits, item.get("limits"))
        if paused is not None:
            item["paused"] = bool(paused)
        still = _clear_stuck(item, added)
        added = [b for b in added if b not in still]
        new_zips = [b for b in new_zips if b not in still]


        copied, not_copied = _copy_address(item, added) if added else ({}, [])
        if added and not copied and not any(b in new_zips for b in item["zips"]):
            new_zips = list(item["zips"])
        gone = {b: item["handles"][b] for b in item["zips"] if b not in new_zips and item["handles"].get(b)}
        gone.update({b: h for b, h in (item.get("stuck") or {}).items() if b not in new_zips})



        for b in gone:
            item["handles"].pop(b, None)
        item["stuck"] = dict(gone)
        item["handles"].update(copied)
        item["zips"] = [b for b in new_zips if b not in not_copied]
        data = _load()
        data["items"][item_id] = item
        _save_or_undo(data, copied)
        removed, failed = _remove(gone)
        item["stuck"] = {b: gone[b] for b in failed}
        data = _load()
        data["items"][item_id] = item
        try:
            _save(data)
        except Exception:
            log.warning("could not save the wallet after removing copies", exc_info=True)
    return {**public(item), "removed": removed, "failed": list(dict.fromkeys(failed + not_copied + still))}


def _remove_login(bot: str, row_id: str) -> bool:
    ""
    try:
        logins.remove(bot, row_id)
        return True
    except Exception as e:
        log.warning("could not remove a login from %s's vault (%s)", bot, type(e).__name__)
        return False


def _update_login(item_id, label, zips, limits, paused) -> dict:
    from . import signin_access
    with signin_access.lock:
        return _update_login_locked(item_id, label, zips, limits, paused)


def _update_login_locked(item_id, label, zips, limits, paused) -> dict:
    item = _login_groups().get(item_id)
    if item is None:
        raise NotFound("That login is gone.")
    if label is not None or limits is not None or paused is not None:
        raise WalletError("To change a login, save it again.")
    if zips is None:
        return _login_public(item) | {"removed": [], "failed": []}
    new_zips = _zips(zips)
    removed, failed = [], []



    holder = max(item["_rows"], key=lambda b: item["_made"].get(b, ""), default=None)
    adding = [b for b in new_zips if b not in item["zips"]]
    for bot in adding:
        if holder is None or not logins.copy_to(holder, item["_rows"][holder], bot, owner_grant=True):
            failed.append(bot)
    if not any(b in item["zips"] for b in new_zips) and len(failed) == len(adding):
        return _login_public(item) | {"removed": [], "failed": failed}
    from . import signin_access
    try:
        effective = [b for b in new_zips if b not in failed]
        if set(effective) != set(item["zips"]):
            signin_access.set_access(item["meta"]["origin"], {"mode": "selected", "zips": effective})
    except ValueError as exc:
        raise WalletError(str(exc)) from exc
    for bot in [b for b in item["zips"] if b not in new_zips]:
        (removed if bot not in item["_rows"] or _remove_login(bot, item["_rows"][bot]) else failed).append(bot)
    now = _login_groups().get(item_id) or {**item, "zips": []}
    signin_access.reconcile(item["meta"]["origin"])
    return _login_public(now) | {"removed": removed, "failed": failed}


def push(item_id: str, secret: dict, zips) -> dict:
    ""


    with _lock:
        item = stored(item_id)
        if item is None:
            raise NotFound("That item is gone.")
        vault, meta, origin = values(item["kind"], secret)
        if item["kind"] == "card" and (meta["last4"], meta["brand"]) != (item["meta"].get("last4"),
                                                                        item["meta"].get("brand")):
            raise WalletError("That's a different card. Add it as a new card instead.")
        if item["kind"] == "identity" and meta["type"] != item["meta"].get("type"):
            raise WalletError("That's a different kind of ID. Add it as a new item instead.")
        want = _zips(zips)
        new = [b for b in want if b not in item["zips"]]
        still = _clear_stuck(item, new)
        handles, failed = _push(item["kind"], item["label"], vault, origin or item["meta"].get("origin"),
                                [b for b in new if b not in still])
        failed += still
        del vault
        if item["kind"] == "card":
            item["meta"].update({k: meta[k] for k in ("exp_month", "exp_year", "name_on_card")})
        item["handles"].update(handles)
        item["zips"] += list(handles)
        data = _load()
        data["items"][item_id] = item
        _save_or_undo(data, handles)
    keep = [b for b in want if b in item["zips"]]
    if not keep:
        raise WalletError("That couldn't be saved for the Zips you picked. Try again in a minute.")
    out = update(item_id, zips=keep) if len(keep) < len(item["zips"]) else \
        {**public(item), "removed": [], "failed": []}
    return {**out, "failed": failed + out["failed"]}


def delete(item_id: str) -> dict:
    ""

    if item_id.startswith("w_l"):
        item = _login_groups().get(item_id)
        if item is None:
            raise NotFound("That login is gone.")
        removed, failed = [], []
        for bot, row_id in item["_rows"].items():
            (removed if _remove_login(bot, row_id) else failed).append(bot)
        return {"removed": removed, "failed": failed}
    with _lock:
        item = stored(item_id)
        if item is None:
            raise NotFound("That item is gone.")
        copies = {**{b: h for b, h in item["handles"].items()}, **(item.get("stuck") or {})}


        item.update(zips=[], handles={}, stuck=dict(copies))
        data = _load()
        data["items"][item_id] = item
        try:
            _save(data)
        except OSError as e:
            raise WalletError("That couldn't be removed. Try again in a minute.") from e
        removed, failed = _remove(copies)
        data = _load()
        if failed:
            item.update(zips=[], handles={}, stuck={b: copies[b] for b in failed})
            data["items"][item_id] = item
        else:
            data["items"].pop(item_id, None)
        _save(data)
    return {"removed": removed, "failed": failed}


def keep_from_zip(bot_id: str) -> None:
    ""


    with _lock:
        data = _load()
        for item in data["items"].values():
            holders = [b for b in item["zips"] if item["handles"].get(b)]
            if item["kind"] != "address" or holders != [bot_id] or bot_id == config.LEAD.id:
                continue


            copied, _ = _copy_address(item, [config.LEAD.id]) if not _clear_stuck(item, [config.LEAD.id]) else ({}, [])
            if not copied:
                raise WalletError(f"{item['label']} is saved only for this Zip, and it couldn't be moved to "
                                  f"{config.LEAD.name}. Try again in a minute.")
            item["handles"].update(copied)
            item["zips"].append(config.LEAD.id)
            _save_or_undo(data, copied)


def zip_deleted(bot_id: str) -> None:
    ""

    with _lock:
        try:
            data = _load()
            for item in data["items"].values():
                item["zips"] = [b for b in item["zips"] if b != bot_id]
                item["handles"].pop(bot_id, None)
                (item.get("stuck") or {}).pop(bot_id, None)
            _save(data)
        except Exception:
            log.warning("could not take a deleted Zip off the wallet", exc_info=True)


def set_paused(item_id: str, paused: bool) -> dict | None:
    with _lock:
        data = _load()
        item = data["items"].get(item_id)
        if item is None:
            return None
        item["paused"] = bool(paused)
        _save(data)
        return item


def clear_all() -> tuple[int, int]:
    ""

    gone = left = 0
    for item_id in list(_load()["items"]):
        try:
            out = delete(item_id)
        except NotFound:
            continue
        if out["failed"]:
            left += 1
        else:
            gone += 1
    if not left:
        _path().unlink(missing_ok=True)
    return gone, left




ASK_NAMES = {"card": "a card", "address": "an address", "identity": "an ID number", "login": "a login"}


ID_ASK_NAMES = {"drivers_license": "your driver's license", "passport": "your passport",
                "ssn": "your Social Security number", "dob": "your date of birth"}


def ask_name(kind: str, id_type: str = "") -> str:
    return ID_ASK_NAMES.get(id_type, ASK_NAMES[kind]) if kind == "identity" else ASK_NAMES[kind]


def _request_row(request_id: str):
    try:
        feed_id = int(request_id)
    except (TypeError, ValueError):
        return None
    return store.db().execute("SELECT * FROM feed WHERE id=? AND kind=?", (feed_id, REQUEST_KIND)).fetchone()





_WANTS_CARD = re.compile(r"\b(?:save|add|store|keep|put|use|give)\b[^.?!]{0,40}\b(?:card|visa|mastercard|amex|debit|"
                         r"wallet)\b|\b(?:next time|from now on|going forward)\b", re.I)


def card_wanted(bot_id: str) -> bool:
    ""

    from . import turns
    turn = turns.current(bot_id)
    if turn is None or turn.done:
        return False
    return any(_WANTS_CARD.search(t.own_words) for t in turns.colin_messages(bot_id, turn.started))


def ask(bot_id: str, kind: str, why: str, session_id: str | None, id_type: str = "") -> tuple[int | None, str]:
    ""

    if kind not in ASK_NAMES:
        raise WalletError("Ask for a card, an address, an ID number or a login.")
    if kind == "card" and not card_wanted(bot_id):
        raise WalletError(f"Don't ask {who.first()} for a card. " + no_card_steps())
    why = who.to_you(" ".join(str(why or "").split())[:160])
    id_type = id_type if kind == "identity" and id_type in ID_ASK_NAMES else ""
    held = "address" if kind == "address" else "birthday" if id_type == "dob" else None
    if held:
        from . import yourinfo
        have = yourinfo.values().get(held)
        if have:
            shown = yourinfo.address_text(have) if held == "address" else have
            raise WalletError(f"It's on {who.possessive()} Your Info already: {shown}. Type it into the form yourself.")
    name = ask_name(kind, id_type)
    for row in store.db().execute("SELECT * FROM feed WHERE kind=? AND bot_id=? ORDER BY id DESC LIMIT 20",
                                  (REQUEST_KIND, bot_id)).fetchall():
        card = json.loads(row["body"] or "{}")
        if card.get("state") == "open" and card.get("item_kind") == kind and (card.get("id_type") or "") == id_type:
            card.update(why=why or card.get("why") or "", session_id=session_id or card.get("session_id"))
            store.update_feed(row["id"], row["title"], json.dumps(card))
            store.touch_feed(row["id"])
            return None, (f"{who.possessive()} card to add {name} is at the bottom of your chat again. "
                          "Stop here. " + who.quiet_card() + " A note arrives here when it's saved.")
    title = f"Add {name}"
    body = {"item_kind": kind, "why": why, "bot": bot_id, "state": "open", "session_id": session_id}
    if id_type:
        body["id_type"] = id_type
    feed_id = store.add_feed(REQUEST_KIND, bot_id, None, title, json.dumps(body),
                             written_by="zip", written_for="person")
    store.update_feed(feed_id, title, json.dumps({"request_id": str(feed_id), **body}))
    return feed_id, (f"{who.first()} now has a card in your chat to add {name}, and a notification. The "
                     "values go straight to your saved items and you never see them. Stop here. " + who.quiet_card() +
                     " A note arrives here when it is saved.")


_saving: set[str] = set()


def _open_request(request_id: str):
    row = _request_row(request_id)
    if row is None:
        raise NotFound("That card is gone.")
    if json.loads(row["body"] or "{}").get("state") != "open" or request_id in _saving:
        raise WalletError("That card was already answered.")
    return row


def _close_request(request_id: str, state: str, **extra) -> tuple[dict, dict]:
    with _lock:
        row = _open_request(request_id)
        card = json.loads(row["body"] or "{}")
        card.update(state=state, at=time.time(), **extra)
        store.update_feed(row["id"], row["title"], json.dumps(card))
        return dict(row), card


def answer_request(request_id: str, kind: str, secret: dict, label: str | None = None,
                   zips=None) -> tuple[dict, str, str, str | None]:
    ""


    with _lock:
        row = _open_request(request_id)
        _saving.add(request_id)
    if plain := _your_info(kind, secret):
        return _answer_with_your_info(request_id, row, secret, *plain)
    try:

        item = add(kind, secret, label, zips if zips is not None else [row["bot_id"]])
    except BaseException:
        with _lock:
            _saving.discard(request_id)
        raise


    with _lock:
        _saving.discard(request_id)
        _, card = _close_request(request_id, "saved", item_id=item["id"], label=item["label"])
    bot = row["bot_id"]
    if bot in item["zips"]:
        note = (f"{who.hub_note()} {who.first()} saved {item['label']} for you. Carry on with what you were doing. "
                + ("Use it with use_card." if kind == "card" else "Use it with use_id." if kind == "identity" else
                   "Fill it with browser_vault_fill; see wallet_list for its handle." if kind == "address" else
                   "Sign in with browser_vault_fill."))
    else:
        note = (f"{who.hub_note()} {who.first()} saved {item['label']}, but not for you. Finish what you can without "
                f"it and tell {who.first()} in a sentence what's left.")
    return item, bot, note, card.get("session_id")


def _your_info(kind: str, secret: dict) -> tuple[str, object] | None:
    ""

    secret = secret if isinstance(secret, dict) else {}
    if kind == "address":
        return "address", {"line1": secret.get("line1"), "line2": secret.get("line2"), "city": secret.get("city"),
                           "region": secret.get("region"), "postal_code": secret.get("postal_code"),
                           "country": secret.get("country")}
    if kind == "identity" and str(secret.get("id_type") or "") == "dob":
        return "birthday", secret.get("value")
    return None




_CARD_EXTRAS = (("name", "legal_name"), ("phone", "phone"), ("email", "email"))


def _answer_with_your_info(request_id: str, row, secret: dict, field: str, value) -> tuple[None, str, str, str | None]:
    ""


    from . import yourinfo
    secret = secret if isinstance(secret, dict) else {}
    kind = "address" if field == "address" else "identity"
    try:
        typed = yourinfo.clean(field, value)
        extras = {to: yourinfo.clean(to, secret.get(key)) for key, to in _CARD_EXTRAS
                  if kind == "address" and str(secret.get(key) or "").strip()}
        if typed is None:
            raise yourinfo.InfoError("Add the street and the city." if field == "address" else "Enter the date.")
        have = yourinfo.set_field(field, typed, "you", fill_only=True)
        for to, v in extras.items():
            have = yourinfo.set_field(to, v, "you", fill_only=True)
        carried = have.get(field) == typed and all(have.get(to) == v for to, v in extras.items())
        kept = None if carried else add(kind, secret, None, [row["bot_id"]])
    except (yourinfo.InfoError, WalletError) as e:
        with _lock:
            _saving.discard(request_id)
        raise WalletError(str(e)) from None
    label = "your home address" if field == "address" else "your date of birth"
    with _lock:
        _saving.discard(request_id)
        _, card = _close_request(request_id, "saved", label=label, **({"item_id": kept["id"]} if kept else {}))
    if field == "address":
        parts = [yourinfo.address_text(typed)] + [f"{yourinfo.LABELS[to].lower()} {v}" for to, v in extras.items()]
        shown = "; ".join(parts)
    else:
        shown = typed
    note = (f"{who.hub_note()} {who.first()} typed this on the card: {shown}. Type it into the form yourself and carry "
            f"on with what you were doing.")
    if kept:
        note += (f" Your Info holds something different for part of it, so the card's copy is kept in the wallet for "
                 f"you too; use what {who.first()} typed on the card for this form.")
    return None, row["bot_id"], note, card.get("session_id")


def dismiss_request(request_id: str) -> tuple[str, str, str | None]:
    ""
    row, card = _close_request(request_id, "dismissed")
    note = (f"{who.hub_note()} {who.first()} closed the card without saving anything. Don't ask again for this. "
            f"Finish what you can without it and tell {who.first()} in a sentence what's left.")
    return row["bot_id"], note, card.get("session_id")
