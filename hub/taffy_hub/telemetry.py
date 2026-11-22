""





















from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
import threading
import time
import uuid
from pathlib import Path

log = logging.getLogger("taffy.telemetry")



POSTHOG_KEY = "phc_yVWQXLbeQdUPiQuMsHDcWLTVbWWNB9BKP97NtMXzHy26"
POSTHOG_HOST = "https://us.i.posthog.com"
SENTRY_DSN = "https://1008ec92800ccbee7d405cb42fc185d3@o4512255649841152.ingest.us.sentry.io/4512255654690816"

FILE_NAME = "telemetry.json"
MAX_TEXT = 300

_lock = threading.RLock()
_client = None
_sentry_on = False
_dir_override: Path | None = None
_state_cache: dict | None = None






def use_dir(path: Path | str | None) -> None:
    ""
    global _dir_override, _state_cache
    _dir_override = Path(path) if path else None
    _state_cache = None


def _dir() -> Path:
    if _dir_override is not None:
        return _dir_override
    try:
        from . import config
        return config.HUB_DIR
    except ImportError:
        return Path(os.environ.get("ZERO_HUB_STATE") or ".")


def _read() -> dict:
    try:
        data = json.loads((_dir() / FILE_NAME).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    if _under_test() and _dir_override is None:
        return
    d = _dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (FILE_NAME + ".tmp")
        tmp.write_text(json.dumps(data, indent=1) + "\n")
        os.chmod(tmp, 0o600)
        tmp.replace(d / FILE_NAME)
    except OSError:
        log.debug("could not save %s", FILE_NAME)


def state() -> dict:
    ""
    global _state_cache
    with _lock:
        if _state_cache is not None:
            return _state_cache
        data = _read()
        try:
            uuid.UUID(str(data.get("id")))
        except ValueError:
            data["id"] = str(uuid.uuid4())
            data.setdefault("created", int(time.time()))
            _write(data)
        _state_cache = data
        return data


def _update(**changes) -> None:
    global _state_cache
    with _lock:
        data = dict(state())
        data.update(changes)
        _write(data)
        _state_cache = data


def install_id() -> str:
    return str(state()["id"])


def _env_off() -> bool:
    return os.environ.get("ZERO_SHARE_USAGE", "").strip().lower() in ("0", "no", "false", "off")


def _under_test() -> bool:
    return "pytest" in sys.modules and not os.environ.get("ZERO_TELEMETRY_IN_TESTS")


def sharing() -> bool:
    ""
    return not _env_off() and state().get("share", True) is not False


def enabled() -> bool:
    return not _under_test() and sharing()


def set_sharing(on: bool) -> bool:
    ""
    _update(share=bool(on))
    if on:
        start()
    else:
        stop()
    return sharing()






def version() -> str:
    ""

    try:
        b = json.loads((Path(__file__).parent / "build.json").read_text())
        v = str(b.get("version") or "0.1.0")
        extra = str(b.get("build") or b.get("commit") or "")
        return f"{v}+{extra}" if extra else v
    except (OSError, ValueError):
        return "0.1.0+dev"


def home_kind() -> str:
    return "mac" if sys.platform == "darwin" else "linux"


def owner_home() -> bool:
    ""

    try:
        from . import home
    except ImportError:
        return _owner_in_file(os.environ.get("ZERO_HOME"))
    try:
        return home.is_owner_home()
    except Exception:
        return False


def _owner_in_file(path: str | None) -> bool:
    ""
    try:
        data = json.loads(Path(str(path)).expanduser().read_text())
        plan = data.get("plan") or {}
        broker = any((plan.get(k) or {}).get("mode") == "broker" for k in ("claude", "chatgpt"))
        return data.get("owner_home") is True and not broker
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _os_version() -> str:
    if sys.platform == "darwin":
        return platform.mac_ver()[0] or platform.release()
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')[:60]
    except OSError:
        pass
    return platform.release()


def common() -> dict:
    return {"home_type": home_kind(), "home_version": version(), "os": platform.system().lower(),
            "os_version": _os_version(), "arch": platform.machine(), "python": platform.python_version(),
            "owner_home": owner_home(), "source": "hub"}






_HOME = str(Path.home())
_USER = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
_PATTERNS = [
    (re.compile(r"/(Users|home)/[^/\s\"']+"), r"/\1/~"),
    (re.compile(r"\b[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.ts\.net\b"), "<tailnet>"),
    (re.compile(r"\b[A-Za-z0-9\-]+\.local\b"), "<local-name>"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"(https?|wss?|zero)://([^/\s?#\"']+)[^\s\"']*"), r"\1://\2/…"),
    (re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"\b(sk|pk|rk|phc|phx|ghp|gho|xox[abp]|AIza|ya29)[-_A-Za-z0-9]{8,}"), "<secret>"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "<secret>"),
    (re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{32,}\b"), "<secret>"),
    (re.compile(r"\b(?:Bearer|token|password|code)[=:\s]+\S+", re.I), "<secret>"),
    (re.compile(r"\d{6,}"), "<number>"),
]


def scrub(text, limit: int = MAX_TEXT) -> str:
    ""

    s = str(text if text is not None else "")
    if len(_HOME) > 1:
        s = s.replace(_HOME, "~")
    for pattern, repl in _PATTERNS[:1]:
        s = pattern.sub(repl, s)
    if len(_USER) >= 3 and _USER.lower() not in ("root", "user", "admin", "ubuntu"):
        s = re.sub(r"\b" + re.escape(_USER) + r"\b", "~", s, flags=re.I)
    for pattern, repl in _PATTERNS[1:]:
        s = pattern.sub(repl, s)
    return s if len(s) <= limit else s[:limit] + "…"


def _clean_props(props: dict | None) -> dict:
    out = {}
    for k, v in (props or {}).items():
        if isinstance(v, bool) or isinstance(v, (int, float)) or v is None:
            out[k] = v
        elif isinstance(v, dict) and k in ("$set", "$set_once"):
            out[k] = _clean_props(v)
        else:
            out[k] = scrub(v)
    return out






def start() -> None:
    ""
    if not enabled():
        return
    _start_sentry()
    _start_posthog()


def _start_posthog() -> None:
    global _client
    with _lock:
        if _client is not None or not POSTHOG_KEY:
            return
        try:
            from posthog import Posthog
            _client = Posthog(POSTHOG_KEY, host=POSTHOG_HOST, flush_at=20, flush_interval=10, max_retries=2,
                              timeout=5, disable_geoip=True, enable_exception_autocapture=False)
        except Exception:
            log.info("usage counts are off: the posthog package is not installed")
            _client = None


def _start_sentry() -> None:
    global _sentry_on
    with _lock:
        if _sentry_on or not SENTRY_DSN:
            return
        try:
            import sentry_sdk
            from sentry_sdk.integrations.logging import LoggingIntegration
            sentry_sdk.init(
                dsn=SENTRY_DSN, release=f"zero-home@{version()}",
                environment=os.environ.get("ZERO_ENVIRONMENT") or ("owner" if owner_home() else "production"),
                send_default_pii=False, include_local_variables=False, max_request_body_size="never",
                server_name="zero-home", before_send=before_send, before_breadcrumb=_before_breadcrumb,


                integrations=[LoggingIntegration(level=None, event_level=logging.ERROR)],
                shutdown_timeout=2)
            sentry_sdk.set_user({"id": install_id()})
            sentry_sdk.set_tag("home_id", install_id())
            sentry_sdk.set_tag("home_type", home_kind())
            sentry_sdk.set_tag("owner_home", str(owner_home()).lower())
            _sentry_on = True
        except Exception:
            log.info("error reports are off: the sentry-sdk package is not installed or did not start")


def stop() -> None:
    ""
    global _client, _sentry_on
    with _lock:
        client, _client = _client, None
        sentry, _sentry_on = _sentry_on, False
    try:
        if client is not None:
            client.shutdown()
    except Exception:
        pass
    if sentry:
        try:
            import sentry_sdk
            sentry_sdk.get_client().close(timeout=2)
        except Exception:
            pass


def capture(event: str, properties: dict | None = None) -> None:
    ""
    if _client is None or not enabled():
        return
    try:
        _client.capture(event, distinct_id=install_id(), properties={**common(), **_clean_props(properties)})
    except Exception:
        log.debug("usage event %s was not queued", event, exc_info=True)


def report(message: str, *, level: str = "error", tags: dict | None = None, context: dict | None = None) -> None:
    ""
    if not _sentry_on or not enabled():
        return
    try:
        import sentry_sdk
        with sentry_sdk.new_scope() as scope:
            for k, v in (tags or {}).items():
                scope.set_tag(k, scrub(v, 200))
            if context:
                scope.set_context("detail", {k: scrub(v, 2000) for k, v in context.items()})
            sentry_sdk.capture_message(scrub(message), level=level)
    except Exception:
        pass


def before_send(event: dict, hint: dict | None = None) -> dict | None:
    ""

    if not enabled():
        return None
    try:
        event.pop("server_name", None)
        event["user"] = {"id": install_id()}
        req = event.get("request")
        if isinstance(req, dict):
            event["request"] = {"method": req.get("method"), "url": scrub(str(req.get("url") or "").split("?")[0])}
        le = event.get("logentry")
        if isinstance(le, dict):

            template = le.get("message") or ""
            event["logentry"] = {"message": scrub(template), "params": [], "formatted": scrub(template)}
        if isinstance(event.get("message"), str):
            event["message"] = scrub(event["message"])
        for v in (event.get("exception") or {}).get("values") or []:
            if v.get("value") is not None:
                v["value"] = scrub(v["value"])
            for f in (v.get("stacktrace") or {}).get("frames") or []:
                f.pop("vars", None)
                for k in ("abs_path", "filename", "module"):
                    if f.get(k):
                        f[k] = scrub(f[k], 500)
        for t in (event.get("threads") or {}).get("values") or []:
            for f in (t.get("stacktrace") or {}).get("frames") or []:
                f.pop("vars", None)
        crumbs = event.get("breadcrumbs")
        if isinstance(crumbs, dict):
            for b in crumbs.get("values") or []:
                b["message"] = scrub(b.get("message")) if b.get("message") else b.get("message")
                b.pop("data", None)
        if isinstance(event.get("extra"), dict):
            event["extra"] = {k: scrub(v) for k, v in event["extra"].items()}
        if isinstance(event.get("tags"), dict):
            event["tags"] = {k: scrub(v, 200) for k, v in event["tags"].items()}
        contexts = event.get("contexts")
        if isinstance(contexts, dict):
            contexts.pop("device", None)
            for c in contexts.values():
                if isinstance(c, dict):
                    c.pop("hostname", None)
        return event
    except Exception:
        return None


def _before_breadcrumb(crumb: dict, hint: dict | None = None) -> dict | None:
    if crumb.get("category") in ("httplib", "subprocess"):
        return None
    if crumb.get("message"):
        crumb["message"] = scrub(crumb["message"])
    crumb.pop("data", None)
    return crumb








ROUTES: dict[tuple[str, str], tuple[str, str | None, dict]] = {
    ("POST", "/api/bots/{bot_id}/messages"): ("message_sent", "message_failed", {}),
    ("POST", "/api/pair"): ("device_paired", "pairing_failed", {}),
    ("POST", "/api/pair/new-code"): ("pairing_code_made", None, {"surface": "app"}),
    ("POST", "/api/unpair"): ("device_unpaired", None, {}),
    ("POST", "/api/bots/{bot_id}/voice"): ("voice_call_started", "voice_call_failed", {"engine": "chatgpt"}),
    ("POST", "/api/bots/{bot_id}/voice/local"): ("voice_call_started", "voice_call_failed", {"engine": "on_device"}),
    ("POST", "/api/onboarding"): ("onboarding_saved", "onboarding_save_failed", {}),
    ("POST", "/api/plan/claude/start"): ("ai_signin_started", "ai_signin_failed", {"provider": "claude", "surface": "phone"}),
    ("POST", "/api/plan/claude/code"): ("ai_signin_completed", "ai_signin_failed", {"provider": "claude", "surface": "phone"}),
    ("POST", "/api/accounts/mailboxes"): ("connection_added", "connection_failed", {"kind": "mailbox"}),
    ("POST", "/api/accounts/calendars"): ("connection_added", "connection_failed", {"kind": "calendar"}),
    ("POST", "/api/accounts/google"): ("connection_added", "connection_failed", {"kind": "google"}),
    ("POST", "/api/apps/{app_id}/code"): ("connection_added", "connection_failed", {"kind": "{app_id}"}),
    ("POST", "/api/apps/{app_id}/start"): ("connection_started", "connection_failed", {"kind": "{app_id}"}),
    ("POST", "/connect/trello/finish"): ("connection_added", "connection_failed", {"kind": "trello"}),
    ("DELETE", "/api/accounts/mailboxes/{mailbox_id}"): ("connection_removed", None, {"kind": "mailbox"}),
    ("DELETE", "/api/accounts/calendars/{calendar_id}"): ("connection_removed", None, {"kind": "calendar"}),
    ("DELETE", "/api/accounts/google/{address}"): ("connection_removed", None, {"kind": "google"}),
    ("DELETE", "/api/apps/{app_id}"): ("connection_removed", None, {"kind": "{app_id}"}),
    ("POST", "/api/bots/{bot_id}/signed-in"): ("site_signin_done", None, {}),
    ("POST", "/api/logins"): ("login_saved", "login_save_failed", {}),
    ("DELETE", "/api/bots/{bot_id}"): ("zip_deleted", None, {}),
    ("POST", "/api/delete-everything"): ("delete_everything", "delete_everything_failed", {}),
}

_messages_today = 0


def route_event(method: str, route: str, status: int, path_params: dict | None = None) -> tuple[str, dict] | None:
    ""
    global _messages_today
    spec = ROUTES.get((method, route))
    if spec is None:
        if status >= 500:
            return "api_error", {"route": route, "method": method, "status": status}
        return None
    ok_event, fail_event, extra = spec
    props = {}
    for k, v in extra.items():
        props[k] = re.sub(r"\{(\w+)\}", lambda m: str((path_params or {}).get(m.group(1), "?"))[:40], v)
    if "bot_id" in (path_params or {}) and ok_event.startswith(("message", "voice")):
        props["zip"] = "lead" if path_params["bot_id"] == "default" else "other"
    if status < 400:
        if ok_event == "message_sent":
            _messages_today += 1
        return ok_event, props
    if fail_event is None:
        return None
    return fail_event, {**props, "status": status}


class UsageMiddleware:
    ""


    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or _client is None:
            return await self.app(scope, receive, send)

        async def watch(message):
            if message.get("type") == "http.response.start":
                try:
                    route = getattr(scope.get("route"), "path", None)
                    if route:
                        ev = route_event(scope.get("method", ""), route, int(message.get("status", 0)),
                                         scope.get("path_params"))
                        if ev:
                            capture(*ev)
                except Exception:
                    pass
            await send(message)

        return await self.app(scope, receive, watch)


def zips_changed(entries: list[dict]) -> None:
    ""


    if _client is None or not enabled():
        return
    try:
        now = {str(e.get("id")): str(e.get("template") or "custom") for e in entries if e.get("id")}
        seen = state().get("zips")
        _update(zips=sorted(now))
        if seen is None:
            return
        for zid in set(now) - set(seen):
            capture("zip_added", {"template": now[zid]})
        for _ in set(seen) - set(now):
            capture("zip_removed")
    except Exception:
        pass


def _summary() -> dict:
    ""
    out: dict = {"messages": _messages_today}
    try:
        from . import config, home, store
        out["zips"] = len(config.BOTS)
        out["custom_zips"] = sum(1 for b in config.BOTS if config.is_custom(b.id))
        out["brain"] = str(home.get("plan.brain") or home.get("plan.connectors") or "unknown")
        out["mailboxes"] = len(home.get("mail.accounts") or [])
        out["calendars"] = len(home.get("calendars") or [])
        try:
            out["devices"] = int(store.db().execute("SELECT COUNT(*) FROM devices").fetchone()[0])
        except Exception:
            pass
    except Exception:
        pass
    return out


def heartbeat(today: str | None = None) -> bool:
    ""

    global _messages_today
    if not enabled() or _client is None:
        return False
    if not state().get("first_seen"):


        capture("home_first_seen", {"$set_once": {"first_seen_version": version()}})
        _update(first_seen=int(time.time()))
    day = today or time.strftime("%Y-%m-%d")
    if state().get("last_day") == day:
        return False
    summary = _summary()
    capture("home_active_day", {**summary, "$set": {"home_type": home_kind(), "home_version": version(),
                                                    "brain": summary.get("brain"), "owner_home": owner_home()}})
    _messages_today = 0
    _update(last_day=day)
    return True


async def run() -> None:
    ""
    import asyncio
    capture("home_started", {"$set": {"home_type": home_kind(), "home_version": version(), "owner_home": owner_home()},
                             "$set_once": {"first_version": version()}})
    try:
        from . import config
        zips_changed([e for e in config._registry_entries() if e["id"] in config.BOTS_BY_ID])
    except Exception:
        pass
    while True:
        try:
            heartbeat()
        except Exception:
            pass
        await asyncio.sleep(3600)
