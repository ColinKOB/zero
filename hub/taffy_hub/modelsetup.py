""

























import asyncio
import importlib.util
import logging
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import config, home, store

log = logging.getLogger("taffy.models")

_PATH = config.PLUGIN_DIR / "models.py"

_REPO_PATH = Path(__file__).resolve().parents[2] / "alien" / "plugins" / "taffy-tools" / "models.py"
if not _PATH.exists() and _REPO_PATH.exists():
    _PATH = _REPO_PATH
_spec = importlib.util.spec_from_file_location("taffy_models", _PATH)
M = importlib.util.module_from_spec(_spec)
sys.modules["taffy_models"] = M
_spec.loader.exec_module(M)


class ChoiceError(ValueError):
    pass


def current():
    ""
    return M.setup(home.get("models"))


def quick_model(bot_id: str | None = None):
    ""
    return M.resolve(current(), bot_id or M.LEAD, "quick")




PLANS_CACHE_SEC = 60
_plans: tuple[float, dict] | None = None


async def _claude_signed_in() -> bool:
    from . import accounts
    return (await accounts._claude_login()).get("state") == "signed_in"


async def _codex_signed_in() -> bool:
    from . import accounts
    return (await accounts._chatgpt()).get("state") == "signed_in"



SIGN_IN = {"claude": _claude_signed_in, "codex": _codex_signed_in}




KEY_FOLDER = "model-keys"


def key_path(plan_key: str) -> Path:
    ""

    return config.HUB_DIR / KEY_FOLDER / f"{plan_key}.key"


def _key(plan_key: str) -> str | None:
    try:
        key = key_path(plan_key).read_text().strip()
    except OSError:
        return None
    return key if key and "\n" not in key and "=" not in key else None


def key_ready(plan_key: str) -> bool:
    ""

    return _key(plan_key) is not None


def keyed_provider(provider: str | None) -> bool:
    ""
    return str(provider or "") in M.KEYED_PROVIDERS


def bills_by_use(bot_id: str) -> bool:
    ""
    from . import hermes
    return keyed_provider(hermes.primary_provider(bot_id))


def same_company(a: str, b: str) -> bool:
    ""
    return M.same_company(current(), a, b, _config_model)


async def signed_in() -> dict[str, bool]:
    ""

    global _plans
    if home.plan_kind() == "shared":
        return {k: True for k in M.PLANS}
    if _plans and time.time() - _plans[0] < PLANS_CACHE_SEC:
        return dict(_plans[1])
    plans = list(M.PLANS.values())
    states = await asyncio.gather(*(SIGN_IN[p.sign_in]() for p in plans))
    out = {p.key: bool(ok) for p, ok in zip(plans, states)}
    _plans = (time.time(), out)
    return dict(out)




def _zip(b, s, shown) -> dict:
    ""

    now = _now(b.id, s)
    m = M.CATALOG.get(now or "")
    chosen = M.chosen_effort(s, b.id)
    return {"id": b.id, "name": b.name, "pin": shown.zips.get(b.id), "now": now, "team": b.team,
            "effort": chosen, "effort_now": (chosen or m.default_effort) if m else None,
            "efforts": list(m.efforts) if m else []}


def _now(bot_id: str, s) -> str | None:
    ""
    m = M.resolve(s, bot_id, "everyday")
    if m is not None:
        return m.key
    found = M.BY_ID.get(_config_model(bot_id) or "")
    return found.key if found else None


def _config_model(bot_id: str) -> str | None:
    try:
        text = _config_path(bot_id).read_text()
    except OSError:
        return None
    m = re.search(r"^model:\s*\n(?:[ \t]+.*\n)*?[ \t]+default:\s*['\"]?([\w.\[\]-]+)", text, re.M)
    return m.group(1) if m else None


def _config_path(bot_id: str) -> Path:
    return config.HERMES_HOME / ("config.yaml" if bot_id == M.LEAD else f"profiles/{bot_id}/config.yaml")


async def payload() -> dict:
    plans = await signed_in()
    s = M.setup(home.get("models"))
    use = list(s.use) if s else (M.use_for(plans) or [next(iter(M.PLANS))])
    shown = s or M.setup(M.recommended(use))
    zips = [_zip(b, s, shown) for b in config.BOTS]
    return {
        "use": use,
        "pick": shown.pick,
        "jobs": {j: shown.jobs[j] for j in M.JOBS},
        "lead_job": shown.lead_job,
        "plans": [{"key": p.key, "name": p.name, "signed_in": plans.get(p.key, False),
                   "recommended": {j: p.recommended[j] for j in M.JOBS}, "lead_job": p.recommended["lead"]}
                  for p in M.PLANS.values()],
        "models": [{"key": m.key, "name": m.name, "plan": m.plan, "tier": m.tier, "suits": list(m.suits),
                    "efforts": list(m.efforts), "effort": m.default_effort,

                    "keyed": m.keyed, "available": not m.keyed or key_ready(m.plan)}
                   for m in M.CATALOG.values()],
        "keyed_plans": [{"key": p.key, "name": p.name, "ready": key_ready(p.key)} for p in M.KEYED.values()],
        "zips": zips,
    }




def clean(body: dict, plans: dict[str, bool], saved_efforts: dict | None = None) -> dict:
    ""


    if not isinstance(body, dict):
        raise ChoiceError("Check the model settings and try again.")
    use = list(M.plans_in(body.get("use")))
    if not use:
        raise ChoiceError("Choose the plans your Zips use.")
    missing = [k for k in use if not plans.get(k)]
    if missing:
        raise ChoiceError(f"Sign in to {M.PLANS[missing[0]].name} on your computer first.")
    ok = {m.key for m in M.allowed(use)}
    pick = body.get("pick") or "auto"
    if pick != "auto" and pick not in ok:
        pick = M.setup({"use": use, "pick": pick}).pick
    jobs = body.get("jobs") if isinstance(body.get("jobs"), dict) else {}
    jobs = {j: k for j, k in jobs.items() if j in M.JOBS and k in ok and j in M.CATALOG[k].suits}
    zips = body.get("zips") if isinstance(body.get("zips"), dict) else {}
    zips = {z: k for z, k in zips.items() if z in config.BOTS_BY_ID and (k in ok or _keyed_model(k))}
    choice = {"use": use, "pick": pick, "jobs": jobs, "zips": zips}
    efforts = body.get("efforts") if "efforts" in body else saved_efforts
    efforts = {z: e for z, e in (efforts if isinstance(efforts, dict) else {}).items() if z in config.BOTS_BY_ID}
    s = M.setup({**choice, "efforts": efforts})
    choice["efforts"] = {z: e for z, e in efforts.items() if M.chosen_effort(s, z)}
    return choice


def _keyed_model(key) -> bool:
    m = M.CATALOG.get(key) if isinstance(key, str) else None
    return bool(m and m.keyed)


async def save(body: dict) -> dict:
    await ensure_use()
    saved = home.get("models")
    choice = clean(body, await signed_in(), saved.get("efforts") if isinstance(saved, dict) else None)
    home.save({"models": choice}, whole=("models",))
    await asyncio.to_thread(apply)
    return await payload()


KEEP = object()


async def pin(bot_id: str, key: str | None | object = KEEP, effort: str | None | object = KEEP) -> dict:
    ""


    if bot_id not in config.BOTS_BY_ID:
        raise ChoiceError("That Zip isn't in your home anymore.")
    if effort is not KEEP and effort is not None and effort not in M.EFFORTS:
        raise ChoiceError("Choose how hard this Zip thinks from the list.")
    if key is not KEEP and _keyed_model(key) and not key_ready(M.CATALOG[key].plan):
        raise ChoiceError(f"{M.CATALOG[key].name} needs its key on your home first.")
    await ensure_use()
    raw = home.get("models")
    s = M.setup(raw)
    if s is None:

        use = M.use_for(await signed_in())
        if not use:
            raise ChoiceError("Sign in to Claude or ChatGPT on your computer first.")
        body = M.recommended(use)
    else:
        body = {"use": list(s.use), "pick": s.pick, "jobs": dict(raw.get("jobs") or {}), "zips": dict(s.zips)}
    body["efforts"] = dict(s.efforts) if s is not None else {}
    if key is not KEEP:
        body["zips"].pop(bot_id, None)
        if key is not None:
            body["zips"][bot_id] = key
    if effort is not KEEP:
        body["efforts"].pop(bot_id, None)
        if effort is not None:
            body["efforts"][bot_id] = effort
    return await save(body)




APPLY_EVERY_SEC = 10 * 60


async def run() -> None:
    ""

    while True:
        try:
            await ensure_use()
            await asyncio.to_thread(apply)
        except Exception:
            log.warning("could not apply the model choice", exc_info=True)
        await asyncio.sleep(APPLY_EVERY_SEC)


async def ensure_use() -> None:
    ""

    raw = home.get("models")
    raw = raw if isinstance(raw, dict) else {}
    if M.plans_in(raw.get("use")) or home.plan_kind() == "shared":
        return
    use = M.use_for(await signed_in())
    if use:
        home.save({"models": {**M.recommended(use), **raw, "use": use}}, whole=("models",))


_applying = threading.Lock()


def apply() -> list[str]:
    ""


    with _applying:
        return _apply()


def _apply() -> list[str]:
    s = current()
    if s is None:
        return []
    changed = []
    ids = [M.LEAD] + sorted(p.parent.name for p in (config.HERMES_HOME / "profiles").glob("*/config.yaml")
                            if not p.parent.name.startswith("."))
    trials = {e["id"]: e["trial"] for e in config._registry_entries()
              if e.get("sandbox") is True and isinstance(e.get("trial"), dict)}
    for bot_id in ids:
        block = _with_trial(M.hermes_block(s, bot_id), trials.get(bot_id))
        path = _config_path(bot_id)
        try:
            text = path.read_text()
        except OSError:
            continue
        new = set_blocks(text, block)
        if new != text:
            _write(path, new)
            changed.append(bot_id)
        try:
            if sync_key(bot_id, M.resolve(s, bot_id, "everyday")) and bot_id not in changed:
                changed.append(bot_id)
        except Exception:
            log.warning("could not set %s's model key", bot_id, exc_info=True)
        try:
            unpin_sessions(bot_id)
        except Exception:
            log.warning("could not unpin %s's sessions", bot_id, exc_info=True)
    if changed:
        log.info("models applied to %s", ", ".join(changed))
    return changed


def _env_path(bot_id: str) -> Path:
    return config.HERMES_HOME / (".env" if bot_id == M.LEAD else f"profiles/{bot_id}/.env")


_missing_reported: set[str] = set()


def sync_key(bot_id: str, m) -> bool:
    ""


    path = _env_path(bot_id)
    try:
        text = path.read_text()
    except OSError:
        return False
    want = M.plan_of(m.plan) if m is not None and m.keyed else None
    lines = text.splitlines()
    out = []
    for line in lines:
        name, _, value = line.partition("=")
        p = next((k for k in M.KEYED.values() if k.key_env == name), None)
        if p is not None and (want is None or p.key != want.key) and value.strip() == (_key(p.key) or "\0"):
            continue
        out.append(line)
    if want is not None:
        key = _key(want.key)
        if key is None:
            if bot_id not in _missing_reported:
                _missing_reported.add(bot_id)
                _report_missing_key(bot_id, want)
        else:
            _missing_reported.discard(bot_id)
            out = [l for l in out if not l.startswith(want.key_env + "=")] + [f"{want.key_env}={key}"]
    new = "\n".join(out) + "\n"
    if new == text:
        return False
    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=path.parent)
    with os.fdopen(fd, "w") as f:
        f.write(new)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return True


def _report_missing_key(bot_id: str, plan) -> None:
    try:
        from . import problems
        problems.report("default", {
            "kind": "break", "concerns_zip": bot_id, "source": "code", "where": "modelsetup.sync_key",
            "what_happened": f"{bot_id} is set to a model on {plan.name}, and the home has no {plan.name} key, so its "
                             "turns can't run.",
            "evidence": f"{KEY_FOLDER}/{key_path(plan.key).name} is missing or empty in the hub's folder.",
            "expected": f"The {plan.name} key installed on the server (docs/plans/2026-11-20-ember-zip.md), or the Zip "
                        "moved back to a plan's model."})
    except Exception:
        log.warning("could not report %s's missing model key", bot_id, exc_info=True)


def _with_trial(block: dict | None, trial: dict | None) -> dict | None:
    ""


    if not block or not trial:
        return block
    out = dict(block)
    for key, value in trial.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = {**out[key], **value}
        elif key in out and not isinstance(out[key], dict):
            out[key] = value
    return out


def _write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, path.stat().st_mode & 0o777)
    os.replace(tmp, path)


def _yaml_lines(key: str, value, pad: str = "") -> list[str]:
    ""

    if isinstance(value, dict):
        if not value:
            return [f"{pad}{key}: {{}}\n"]
        return [f"{pad}{key}:\n"] + [line for k, v in value.items() for line in _yaml_lines(k, v, pad + "  ")]
    if isinstance(value, list):
        if not value:
            return [f"{pad}{key}: []\n"]
        return [f"{pad}{key}:\n"] + [f"{pad}{'- ' if i == 0 else '  '}{k}: {v}\n"
                                     for item in value for i, (k, v) in enumerate(item.items())]
    if isinstance(value, bool):
        value = "true" if value else "false"
    return [f"{pad}{key}: {value}\n"]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _content(line: str) -> bool:
    return bool(line.strip()) and not line.lstrip().startswith("#")


def _end(lines: list[str], start: int, stop: int) -> int:
    ""


    own, last = _indent(lines[start]), start
    for j in range(start + 1, stop):
        line = lines[j]
        if _content(line):
            if _indent(line) < own or (_indent(line) == own and not line.lstrip().startswith("-")):
                break
            last = j
    return last


def _find(lines: list[str], key: str, lo: int, hi: int, pad: str) -> int | None:
    return next((i for i in range(lo, hi) if re.match(rf"{re.escape(pad)}{re.escape(key)}:(\s|$)", lines[i])), None)


def _set(lines: list[str], path: list[str], value, lo: int = 0, hi: int | None = None, pad: str = "") -> None:
    ""
    hi = len(lines) if hi is None else hi
    key, rest = path[0], path[1:]
    at = _find(lines, key, lo, hi, pad)
    if at is None:
        if hi and not lines[hi - 1].endswith("\n"):
            lines[hi - 1] += "\n"
        for p in reversed(rest):
            value = {p: value}
        lines[hi:hi] = _yaml_lines(key, value, pad)
        return
    end = _end(lines, at, hi)
    if not rest:
        lines[at:end + 1] = _yaml_lines(key, value, pad)
        return
    inline = lines[at].split(":", 1)[1].split("#", 1)[0].strip()
    if inline in ("{}", "null", "~"):
        lines[at] = f"{pad}{key}:\n"
    elif inline:
        return
    child = next((lines[j] for j in range(at + 1, end + 1) if _content(lines[j])), None)
    inner = child[:_indent(child)] if child else pad + "  "
    _set(lines, rest, value, at + 1, end + 1, inner)


def set_blocks(text: str, block: dict) -> str:
    ""



    lines = text.splitlines(keepends=True)
    for path, value in block.items():
        _set(lines, path.split("."), value)
    return "".join(lines)


def _state_db(bot_id: str) -> Path:
    return config.HERMES_HOME / ("state.db" if bot_id == M.LEAD else f"profiles/{bot_id}/state.db")


def unpin_sessions(bot_id: str) -> int:
    ""

    ids = [sid for key, sid in store.db().execute("SELECT bot_id, session_id FROM bot_sessions").fetchall()
           if sid and (key == bot_id or key.startswith(bot_id + "#"))]
    path = _state_db(bot_id)
    if not ids or not path.exists():
        return 0
    conn = sqlite3.connect(path, timeout=10)
    try:
        marks = ",".join("?" * len(ids))
        cur = conn.execute(f"UPDATE sessions SET model = NULL WHERE model IS NOT NULL AND id IN ({marks})", ids)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
