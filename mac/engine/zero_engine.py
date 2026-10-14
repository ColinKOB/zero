"""The Python half of zero-engine (mac/engine/zero-engine): renders the home on this Mac, reports the AI sign-ins,
makes pairing codes and reports status. Run by the engine's own Python (the venv the installer made), with -I.

    zero_engine.py --root R --work W --label L --agents A render --shared-src S
    zero_engine.py ... status | ai-status | configure-ai | pair [minutes]

A Mac home is the same shape as a server home (docs/plans/2026-10-09-zero-for-anyone-plan.md, section 4): one
home.json that the hub and the taffy-tools plugin read, a Hermes home, the hub's state, a work folder. Every path in
home.json is absolute, because the hub and the plugin expand `~` from the password database, not from $HOME.

Zero never reads Claude's credentials: it runs `claude auth status --json` and keeps only "signed in" and the plan
kind. For ChatGPT it runs `codex login status`. When Codex is the brain, Hermes's own Codex sign-in is filled from
Codex's, through Hermes's own import code, the same thing `hermes auth add openai-codex` offers.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

HUB_PORT = 8650
API_PORT = 8642
LAN_PORT = 8653
HOME_ID = "mac"
MAC_OFF = {"terminal", "code_execution", "computer_use", "browser"}
SEARCH_DIRS = ["~/.local/bin", "~/.claude/local", "/opt/homebrew/bin", "/usr/local/bin", "~/.npm-global/bin",
               "~/.bun/bin", "~/.volta/bin", "/usr/bin"]


class Ctx:
    def __init__(self, a):
        self.root = Path(a.root)
        self.work = Path(a.work)
        self.label = a.label
        self.agents = Path(a.agents)
        self.home = Path(os.environ.get("HOME") or Path.home())
        self.engine = self.root / "engine"
        self.hermes_src = self.engine / "hermes"
        self.hub_src = self.engine / "hub-src"
        self.plugin = self.engine / "plugins" / "taffy-tools"
        self.hermes_home = self.root / "hermes"
        self.hub_state = self.root / "hub-state"
        self.shared = self.root / "shared"
        self.settings_file = self.root / "home.json"
        self.engine_file = self.root / "engine.json"
        self.venv = self.root / "venv"
        self.logs = self.root / "logs"


def lib(ctx: Ctx):
    spec = importlib.util.spec_from_file_location("zero_home_lib", ctx.engine / "lib" / "zero-home-lib.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def toolsets(ctx: Ctx):
    """alien/bin/set-toolsets.py, for the tool lists, memory sizes and compaction every home shares."""
    os.environ.setdefault("ZERO_HOME", str(ctx.settings_file))
    spec = importlib.util.spec_from_file_location("zero_set_toolsets", ctx.engine / "lib" / "set-toolsets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def port_free(port: int) -> bool:
    for host in ("127.0.0.1", "0.0.0.0"):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, port))
        except OSError:
            return False
        finally:
            s.close()
    return True


def pick_port(preferred: int, taken: set[int]) -> int:
    for p in [preferred] + list(range(preferred + 100, preferred + 300)):
        if p not in taken and port_free(p):
            return p
    raise SystemExit("no free port")


def local_host_name() -> str:
    try:
        name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    return f"{name}.local" if name else socket.gethostname()


def lan_ip() -> str:
    for iface in ("en0", "en1", "en2"):
        try:
            ip = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True,
                                timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            ip = ""
        if ip:
            return ip
    return ""


def firewall_on() -> bool:
    """macOS's own firewall. When it is on, macOS asks once whether Zero's engine may accept connections from the
    Wi-Fi, and the phone can reach this Mac only after the person allows it."""
    try:
        out = subprocess.run(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "enabled" in out.lower()


def timezone() -> str:
    try:
        target = os.readlink("/etc/localtime")
        m = re.search(r"zoneinfo/(.+)$", target)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "America/New_York"


def full_name() -> str:
    try:
        return subprocess.run(["id", "-F"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def find_tool(name: str, home: Path) -> str | None:
    for d in SEARCH_DIRS:
        p = Path(d.replace("~", str(home), 1)) / name
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return shutil.which(name)


def ai_status(home: Path) -> dict:
    """Claude Code and Codex: installed, signed in, and the plan kind. No email, no token, nothing else is kept."""
    out = {"claude": {"installed": False, "signed_in": False}, "codex": {"installed": False, "signed_in": False}}
    env = dict(os.environ, HOME=str(home), NO_COLOR="1")
    claude = find_tool("claude", home)
    if claude:
        out["claude"].update(installed=True, path=claude)
        try:
            p = subprocess.run([claude, "auth", "status", "--json"], capture_output=True, text=True, timeout=30,
                               env=env, cwd=str(home))
            data = json.loads(p.stdout or "{}")
            out["claude"]["signed_in"] = bool(data.get("loggedIn"))
            plan = data.get("subscriptionType") or data.get("authMethod")
            if plan and plan != "none":
                out["claude"]["plan"] = str(plan)[:40]
        except (OSError, subprocess.SubprocessError, ValueError):
            out["claude"]["error"] = "Claude Code did not answer."
    codex = find_tool("codex", home)
    if codex:
        out["codex"].update(installed=True, path=codex)
        try:
            p = subprocess.run([codex, "login", "status"], capture_output=True, text=True, timeout=30, env=env,
                               cwd=str(home))
            said = (p.stdout + p.stderr).lower()
            out["codex"]["signed_in"] = p.returncode == 0 and "logged in" in said and "not logged in" not in said
            if "chatgpt" in said:
                out["codex"]["plan"] = "chatgpt"
            elif "api key" in said:
                out["codex"]["plan"] = "api_key"
        except (OSError, subprocess.SubprocessError):
            out["codex"]["error"] = "Codex did not answer."
    out["ready"] = out["claude"]["signed_in"] or out["codex"]["signed_in"]
    return out


def brain(ai: dict) -> str:
    """Which plan the Zips think with: Claude when Claude Code is signed in, else ChatGPT through Codex."""
    if ai["claude"]["signed_in"]:
        return "claude"
    if ai["codex"]["signed_in"]:
        return "chatgpt"
    return "none"


def import_codex_tokens(ctx: Ctx) -> bool:
    """Fills Hermes's own Codex sign-in from Codex's, with Hermes's import code. Nothing is printed."""
    code = (
        "import sys\n"
        "from hermes_cli import auth\n"
        "from hermes_cli.auth_codex import _import_codex_cli_tokens\n"
        "t = _import_codex_cli_tokens()\n"
        "if not t: sys.exit(3)\n"
        "auth._save_codex_tokens(t)\n"
    )
    env = dict(os.environ, HERMES_HOME=str(ctx.hermes_home), HOME=str(ctx.home),
               PYTHONPATH=str(ctx.hermes_src))
    try:
        p = subprocess.run([str(ctx.venv / "bin" / "python"), "-c", code], env=env, capture_output=True, text=True,
                           timeout=60, cwd=str(ctx.root))
    except (OSError, subprocess.SubprocessError):
        return False
    if p.returncode != 0:
        print(f"Codex sign-in was not copied (exit {p.returncode}).")
    return p.returncode == 0


def hermes_has_codex(ctx: Ctx) -> bool:
    data = read_json(ctx.hermes_home / "auth.json")
    return bool((data.get("providers") or {}).get("openai-codex") or (data.get("credential_pool") or {}).get("openai-codex"))


def settings_for(ctx: Ctx, ports: dict, ai: dict, existing: dict) -> dict:
    """home.json for this Mac. Paths, ports, host and plan are the engine's and are rewritten on every install; the
    person, locale, mail, calendars and anything the app or the hub saved are kept."""
    person = existing.get("person") or {}
    name = person.get("name") or full_name()
    first = person.get("first_name") or (name.split()[0] if name else "")
    which = brain(ai)
    claude_cmd = ai["claude"].get("path") or "claude"
    codex_cmd = ai["codex"].get("path") or "codex"
    out = dict(existing)
    out.update({
        "version": 1,
        "home_id": HOME_ID,
        "person": {**{"timezone": timezone(), "quiet_hours": {"start": 23, "end": 7}}, **person,
                   "name": name, "first_name": first, "pronoun_free": True},
        "locale": existing.get("locale") or {"units": "us", "spelling": "american", "clock": "12h"},
        "paths": {"hermes_home": str(ctx.hermes_home), "hermes_bin": str(ctx.root / "bin" / "zero-hermes"),
                  "hub_state": str(ctx.hub_state), "work": str(ctx.work), "shared": str(ctx.shared),
                  "plugin": str(ctx.plugin), "owner": str(ctx.root / "owner"), "background_docs": []},
        "ports": {"hub": ports["hub"], "hermes_api": ports["hermes_api"], "lan": ports.get("lan"), "broker_http": 0,
                  "preview": {"base": 18200, "per_zip": 10, "count": 6}, "docker_subnet_base": "172.30"},
        "network": {"public_url": f"https://{local_host_name()}:{ports.get('lan') or ports['hub']}",
                    "serve_port": ports.get("lan") or ports["hub"],
                    "listen": "lan"},
        "host": {"name": "mac", "kind": "mac", "sandbox_image": None, "lead_backend": "local"},
        "plan": {"claude": {"mode": "own", "command": claude_cmd},
                 "chatgpt": {"mode": "own", "codex_command": codex_cmd, "codex_base_url": None},
                 "connectors": "claude" if which == "claude" else ("chatgpt" if which == "chatgpt" else "none"),
                 "frugal": True, "brain": which},
        "integrations": {"tally": None, "trello": None, "whisper_url": None},
        "judge": {"enabled": False},
        "push": {"key_dir": str(ctx.root / "push"), "team_id": None, "topic": "com.example.Taffy"},
        "backup": {"repo": str(ctx.root / "backups"), "drop": None, "mac_copy_expected": False},
        "zips": {"max": 4, "container_memory_mb": None},
    })
    out.setdefault("mail", {"accounts": []})
    out.setdefault("calendars", [])
    return out


def model_block(which: str, ai: dict) -> dict:
    """The lead's model, fallback and side models for the plan that is signed in, as alien's own-plan homes have."""
    if which == "chatgpt":
        return {"model": {"provider": "openai-codex", "default": "gpt-6.1-sol"},
                "fallback_providers": [], "auxiliary": None}
    fallback = [{"provider": "openai-codex", "model": "gpt-6.1-sol"}] if ai["codex"]["signed_in"] else []
    return {"model": {"provider": "claude-subscription-directsdk-experimental", "default": "opus"},
            "fallback_providers": fallback,
            "auxiliary": {"compression": {"provider": "claude-subscription-directsdk-experimental",
                                          "model": "claude-haiku-5-5"}}}


def render_config(ctx: Ctx, L, settings: dict, ai: dict) -> None:
    path = ctx.hermes_home / "config.yaml"
    existing = L.load_yaml(path.read_text()) if path.exists() else None
    cfg = L.render_profile_config(settings, ctx.home, "default", None, False, existing or None)
    T = toolsets(ctx)
    cfg["terminal"] = {"backend": "local", "home_mode": "real", "cwd": str(ctx.work / "default")}
    cfg.pop("bot_desktop", None)
    cfg["browser"] = {"headed": False, "restrict_evaluate": True, "allow_private_urls": False}
    lists = T.lists(True, "default")
    cfg["platform_toolsets"] = {k: [t for t in v if t not in MAC_OFF] for k, v in lists.items()}
    which = brain(ai)
    m = model_block(which, ai)
    cfg["model"] = m["model"]
    cfg["fallback_providers"] = m["fallback_providers"]
    if m["auxiliary"] is None:
        cfg.pop("auxiliary", None)
    else:
        cfg["auxiliary"] = m["auxiliary"]
    cfg.setdefault("kanban", {}).update(T.KANBAN)
    cfg.setdefault("compression", {}).update(T.COMPRESSION)
    mem = cfg.setdefault("memory", {})
    mem["memory_char_limit"] = T.LEAD_MEMORY_CHARS
    mem["user_char_limit"] = T.USER_CHARS
    write(path, L.emit_yaml(cfg), 0o600)


HERMES_WRAPPER = """#!/bin/bash
# Runs Hermes for Zero's home on this Mac. Written by zero-engine.
export HERMES_HOME="{hermes_home}"
export ZERO_HOME="{settings}"
export XDG_STATE_HOME="{state}"
export ZERO_USER_HOME="$HOME"
export PATH="{path}"
exec "{venv}/bin/hermes" "$@"
"""


def agent_path(ctx: Ctx, ai: dict) -> str:
    dirs = [str(ctx.root / "bin"), str(ctx.venv / "bin")]
    for tool in ("claude", "codex"):
        p = ai[tool].get("path")
        if p:
            dirs.append(str(Path(p).parent))
    dirs += [str(ctx.home / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
             "/usr/sbin", "/sbin"]
    seen = []
    for d in dirs:
        if d not in seen:
            seen.append(d)
    return ":".join(seen)


def plist(ctx: Ctx, name: str, args: list[str], env: dict) -> bytes:
    return plistlib.dumps({
        "Label": f"{ctx.label}.{name}",
        "ProgramArguments": args,
        "EnvironmentVariables": env,
        "WorkingDirectory": str(ctx.root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Interactive" if name == "hub" else "Standard",
        "StandardOutPath": str(ctx.logs / f"{name}.log"),
        "StandardErrorPath": str(ctx.logs / f"{name}.log"),
    })


def render_agents(ctx: Ctx, settings: dict, ai: dict) -> None:
    # XDG_STATE_HOME keeps Hermes's per-user gateway lock inside Zero's folder, so a Hermes the person runs on
    # their own never collides with Zero's, and removing Zero leaves nothing behind in ~/.local.
    env = {"HOME": str(ctx.home), "HERMES_HOME": str(ctx.hermes_home), "ZERO_HOME": str(ctx.settings_file),
           "XDG_STATE_HOME": str(ctx.root / "state"), "ZERO_USER_HOME": str(ctx.home),
           "PATH": agent_path(ctx, ai), "PYTHONUNBUFFERED": "1", "LANG": "en_US.UTF-8"}
    gw = plist(ctx, "gateway", [str(ctx.venv / "bin" / "hermes"), "gateway", "run"], env)
    hub_env = dict(env, PYTHONPATH=f"{ctx.hub_src}:{ctx.hermes_src}")
    hub = plist(ctx, "hub", [str(ctx.venv / "bin" / "python"), "-m", "uvicorn", "taffy_hub.app:app",
                             "--host", "127.0.0.1", "--port", str(settings["ports"]["hub"]), "--no-access-log",
                             "--ws", "websockets"], hub_env)
    cert, key = cert_paths(ctx)
    lan = plist(ctx, "lan", [str(ctx.venv / "bin" / "python"), "-I", str(ctx.engine / "lib" / "tls_proxy.py"),
                             "--listen", f"0.0.0.0:{settings['ports'].get('lan')}",
                             "--to", f"127.0.0.1:{settings['ports']['hub']}", "--cert", str(cert), "--key", str(key)],
                env)
    ctx.agents.mkdir(parents=True, exist_ok=True)
    (ctx.agents / f"{ctx.label}.gateway.plist").write_bytes(gw)
    (ctx.agents / f"{ctx.label}.hub.plist").write_bytes(hub)
    (ctx.agents / f"{ctx.label}.lan.plist").write_bytes(lan)


def cmd_render(ctx: Ctx, shared_src: Path) -> int:
    L = lib(ctx)
    for d, mode in ((ctx.hermes_home, 0o700), (ctx.hermes_home / "profiles", 0o700),
                    (ctx.hermes_home / "plugins", 0o755), (ctx.hub_state, 0o700), (ctx.shared, 0o755),
                    (ctx.root / "owner", 0o755), (ctx.root / "bin", 0o755), (ctx.logs, 0o755),
                    (ctx.work, 0o755), (ctx.work / "default", 0o755)):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, mode)
    ai = ai_status(ctx.home)
    engine = read_json(ctx.engine_file)
    ports = engine.get("ports") or {}
    if not ports or not port_free(ports.get("hub", 0)) and not hub_alive(ports.get("hub", 0)):
        hub = pick_port(HUB_PORT, set())
        ports = {"hub": hub, "hermes_api": pick_port(API_PORT, {hub})}
    if not ports.get("lan") or (not port_free(ports["lan"]) and not lan_alive(ports["lan"])):
        ports["lan"] = pick_port(LAN_PORT, {ports["hub"], ports["hermes_api"]})
    fingerprint = ensure_certificate(ctx)
    existing = read_json(ctx.settings_file)
    settings = settings_for(ctx, ports, ai, existing)
    write(ctx.settings_file, json.dumps(settings, indent=2) + "\n", 0o644)
    os.environ["ZERO_HOME"] = str(ctx.settings_file)

    for name in ("taffy-tools", "claude-subscription-directsdk-experimental"):
        link = ctx.hermes_home / "plugins" / name
        target = ctx.engine / "plugins" / name
        if link.is_symlink() or link.is_file():
            link.unlink()
        elif link.is_dir():
            shutil.rmtree(link)
        link.symlink_to(target)
    templates = shared_src / "zip-templates.json"
    if templates.exists() and not (ctx.shared / "zip-templates.json").exists():
        shutil.copy2(templates, ctx.shared / "zip-templates.json")
    write(ctx.root / "bin" / "zero-hermes", HERMES_WRAPPER.format(
        hermes_home=ctx.hermes_home, settings=ctx.settings_file, state=ctx.root / "state", path=agent_path(ctx, ai),
        venv=ctx.venv), 0o755)

    render_config(ctx, L, settings, ai)
    env_path = ctx.hermes_home / ".env"
    current = env_path.read_text() if env_path.exists() else ""
    write(env_path, L.render_env(settings, current, lead=True, broker_secret=None), 0o600)
    soul = ctx.hermes_home / "SOUL.md"
    if not soul.exists():
        write(soul, L.render_lead_soul(settings, ctx.home), 0o644)
        remember_soul_names(ctx, settings)
    if brain(ai) == "chatgpt" or ai["codex"]["signed_in"]:
        if not hermes_has_codex(ctx):
            import_codex_tokens(ctx)

    render_agents(ctx, settings, ai)
    write(ctx.engine_file, json.dumps({
        "version": 1, "root": str(ctx.root), "label": ctx.label, "ports": ports,
        "hermes_commit": (ctx.hermes_src / ".zero-commit").read_text().strip()
        if (ctx.hermes_src / ".zero-commit").exists() else None,
        "brain": brain(ai), "rendered_at": int(time.time()),
    }, indent=2) + "\n", 0o644)
    print(f"rendered the home: hub on port {ports['hub']}, gateway API on {ports['hermes_api']}, brain {brain(ai)}")
    return 0


SOUL_NAMES = "zero-soul-names.json"


def remember_soul_names(ctx: Ctx, settings: dict) -> None:
    p = settings.get("person") or {}
    write(ctx.root / "state" / SOUL_NAMES, json.dumps({"name": p.get("name") or "",
                                                       "first_name": p.get("first_name") or ""}) + "\n", 0o644)


def cmd_soul(ctx: Ctx) -> int:
    """After the onboarding: the lead's SOUL names the person the onboarding met, not the Mac account's name it was
    first written with. Only the names change; everything the hub added to the SOUL stays. The hub picks the change
    up on the next turn, so nothing restarts."""
    soul = ctx.hermes_home / "SOUL.md"
    settings = read_json(ctx.settings_file)
    person = settings.get("person") or {}
    new_name, new_first = str(person.get("name") or "").strip(), str(person.get("first_name") or "").strip()
    old = read_json(ctx.root / "state" / SOUL_NAMES)
    if not soul.exists() or not new_name or not new_first:
        print(json.dumps({"changed": False}))
        return 0
    text = soul.read_text()
    out = text
    if old.get("name") and old["name"] != new_name:
        out = out.replace(old["name"], new_name)
    if old.get("first_name") and old["first_name"] != new_first:
        out = re.sub(rf"\b{re.escape(old['first_name'])}\b", new_first, out)
    if out != text:
        write(soul, out, 0o644)
    remember_soul_names(ctx, settings)
    print(json.dumps({"changed": out != text}))
    return 0


def cert_paths(ctx: Ctx) -> tuple[Path, Path]:
    return ctx.root / "tls" / "cert.pem", ctx.root / "tls" / "key.pem"


def ensure_certificate(ctx: Ctx) -> str:
    """The Mac home's own certificate for the Wi-Fi: self-signed, made once at install, the key kept in Zero's folder
    (mode 600). Returns its SHA-256 fingerprint, which the pairing link carries and the phone pins."""
    cert, key = cert_paths(ctx)
    if not (cert.exists() and key.exists()):
        import datetime as dt
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        k = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Zero on this Mac")])
        now = dt.datetime.now(dt.timezone.utc)
        host = local_host_name()
        c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
             .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
             .not_valid_after(now + dt.timedelta(days=3650))
             .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
             .sign(k, hashes.SHA256()))
        (ctx.root / "tls").mkdir(parents=True, exist_ok=True)
        os.chmod(ctx.root / "tls", 0o700)
        write(key, k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()).decode(), 0o600)
        write(cert, c.public_bytes(serialization.Encoding.PEM).decode(), 0o644)
    os.chmod(key, 0o600)
    return fingerprint(cert)


def fingerprint(cert: Path) -> str:
    import hashlib
    import ssl
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()


def lan_alive(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def hub_alive(port: int) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2):
            return True
    except Exception:
        return False


def cmd_configure_ai(ctx: Ctx) -> int:
    """After the person signs in to Claude Code or Codex: point the Zips at the plan that is signed in now."""
    L = lib(ctx)
    ai = ai_status(ctx.home)
    engine = read_json(ctx.engine_file)
    if not engine.get("ports"):
        print("Zero is not set up on this Mac.")
        return 1
    os.environ["ZERO_HOME"] = str(ctx.settings_file)
    settings = settings_for(ctx, engine["ports"], ai, read_json(ctx.settings_file))
    write(ctx.settings_file, json.dumps(settings, indent=2) + "\n", 0o644)
    render_config(ctx, L, settings, ai)
    if ai["codex"]["signed_in"] and not hermes_has_codex(ctx):
        import_codex_tokens(ctx)
    render_agents(ctx, settings, ai)
    engine["brain"] = brain(ai)
    write(ctx.engine_file, json.dumps(engine, indent=2) + "\n", 0o644)
    print(json.dumps({"brain": brain(ai), "ready": ai["ready"]}))
    return 0


def cmd_status(ctx: Ctx) -> int:
    engine = read_json(ctx.engine_file)
    ports = engine.get("ports") or {}
    loaded = {}
    for name in ("hub", "gateway", "lan"):
        p = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{ctx.label}.{name}"], capture_output=True,
                           text=True)
        loaded[name] = p.returncode == 0
    out = {
        "installed": bool(ports) and (ctx.agents / f"{ctx.label}.hub.plist").exists(),
        "running": bool(ports) and hub_alive(ports.get("hub", 0)),
        "agents": loaded,
        "ports": ports,
        "brain": engine.get("brain"),
        "lan": {"host": local_host_name(), "ip": lan_ip()},
        "firewall": firewall_on(),
    }
    print(json.dumps(out))
    return 0


def cmd_pair(ctx: Ctx, minutes: float) -> int:
    """A one-time pairing code from the hub's own store, and the link the phone opens on the same Wi-Fi."""
    engine = read_json(ctx.engine_file)
    port = (engine.get("ports") or {}).get("hub")
    if not port:
        print(json.dumps({"error": "Zero is not set up on this Mac."}))
        return 1
    env = dict(os.environ, HERMES_HOME=str(ctx.hermes_home), ZERO_HOME=str(ctx.settings_file),
               PYTHONPATH=f"{ctx.hub_src}:{ctx.hermes_src}")
    code_py = "from taffy_hub import store; import sys; print(store.new_pairing_code(int(sys.argv[1])))"
    p = subprocess.run([str(ctx.venv / "bin" / "python"), "-c", code_py, str(int(minutes * 60))], env=env,
                       capture_output=True, text=True, timeout=60, cwd=str(ctx.root))
    code = (p.stdout.strip().splitlines() or [""])[-1]
    if p.returncode != 0 or not re.fullmatch(r"[A-Z0-9]{6,12}", code):
        print(json.dumps({"error": "Zero could not make a pairing code.", "detail": p.stderr[-300:]}))
        return 1
    from urllib.parse import urlencode
    lan_port = (engine.get("ports") or {}).get("lan")
    cert, _ = cert_paths(ctx)
    if not lan_port or not cert.exists():
        print(json.dumps({"error": "Zero is not set up for the Wi-Fi yet. Set it up again."}))
        return 1
    fp = fingerprint(cert)
    lan = f"https://{local_host_name()}:{lan_port}"
    ip = lan_ip()
    print(json.dumps({
        "code": code, "expires_in": int(minutes * 60),
        "local": f"http://127.0.0.1:{port}",
        "server": lan, "server_ip": f"https://{ip}:{lan_port}" if ip else None, "fingerprint": fp,
        "link": "zero://pair?" + urlencode({"server": lan, "code": code, "fp": fp}),
        "firewall": firewall_on(),
    }))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="zero_engine.py")
    ap.add_argument("--root", default=str(Path.home() / "Library" / "Application Support" / "Zero"))
    ap.add_argument("--work", default=str(Path.home() / "Zero Work"))
    ap.add_argument("--label", default="com.example.Zero")
    ap.add_argument("--agents", default=str(Path.home() / "Library" / "LaunchAgents"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--shared-src", required=True)
    sub.add_parser("status")
    sub.add_parser("ai-status")
    sub.add_parser("configure-ai")
    sub.add_parser("soul")
    p = sub.add_parser("pair")
    p.add_argument("minutes", nargs="?", type=float, default=15)
    a = ap.parse_args(argv)
    ctx = Ctx(a)
    if a.cmd == "render":
        return cmd_render(ctx, Path(a.shared_src))
    if a.cmd == "status":
        return cmd_status(ctx)
    if a.cmd == "ai-status":
        print(json.dumps(ai_status(ctx.home)))
        return 0
    if a.cmd == "configure-ai":
        return cmd_configure_ai(ctx)
    if a.cmd == "soul":
        return cmd_soul(ctx)
    if a.cmd == "pair":
        return cmd_pair(ctx, a.minutes)
    return 2


if __name__ == "__main__":
    sys.exit(main())
