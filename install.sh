#!/usr/bin/env bash
# Zero home server installer. Run it as your own user (not root) on Ubuntu 24.04 or another Debian-like Linux,
# x86_64 or arm64, with sudo:
#
#   curl -fsSL https://raw.githubusercontent.com/ColinKOB/zero/main/install.sh | bash
#
#   bash install.sh             install, or repair and update an existing install (safe to run again)
#   bash install.sh pair        a new pairing link and QR code for the iPhone app
#   bash install.sh signin      sign this server in to your own Claude or ChatGPT plan
#   bash install.sh status      what is running
#   bash install.sh uninstall   stop and remove Zero (asks before deleting your Zips' memories and files)
#
# Answers can be given ahead of time, which also makes the run non-interactive:
#   ZERO_SOURCE    where the code comes from: a git URL, a .tar.gz URL, a local .tar.gz or a local folder
#   ZERO_REF       the branch or tag for a git URL (default main)
#   ZERO_NAME      your name                     ZERO_TIMEZONE  an IANA time zone, such as America/New_York
#   ZERO_AI        claude, chatgpt, both or skip (which plan's command line to sign in with)
#   ZERO_TAILSCALE yes or no (reach the server from anywhere through your own Tailscale account)
#   ZERO_SANDBOX   docker (each Zip works in its own container; the default) or local (the lead Zip works
#                  directly on this computer as you, and no Docker is installed)
#   ZERO_DELETE_DATA=yes   with uninstall: also delete the Zips' memories, chats and files
#
# Nothing here uses anyone else's accounts, keys or plans. The AI sign-in runs the official Claude Code and Codex
# command lines with your own account.
set -euo pipefail

# The public repository. The lead fills this in when the repository is public.
ZERO_SOURCE="${ZERO_SOURCE:-https://github.com/ColinKOB/zero.git}"
ZERO_REF="${ZERO_REF:-main}"

# The engine, pinned to the commits alien runs.
HERMES_REPO="https://github.com/NousResearch/hermes-agent.git"
HERMES_COMMIT="74943bb889b7978f9d6ff0aa77ae442547201cc2"
DIRECTSDK_REPO="https://github.com/NousResearch/hermes-plugin-claude-subscription-directsdk.git"
DIRECTSDK_COMMIT="31b591fd04737a7807183f3d7f3d389b19f94687"
DIRECTSDK_NAME="claude-subscription-directsdk-experimental"
PYTHON_VERSION="3.14"
SANDBOX_IMAGE="taffy/zip-sandbox:2026-10-08-v1"
HOME_ID="home"

ZDATA="$HOME/.local/share/zero"          # the installer's own files: the source copy and its state
STATE="$ZDATA/install.env"
SRC="$ZDATA/src"
TAFFY="$HOME/taffy"                      # the home: code, settings, hub state, work
HERMES_HOME="$HOME/.taffy"               # Hermes: config, SOUL, memories, chats
VENV="$TAFFY/.venv"
UNITS="$HOME/.config/systemd/user"
LOCALBIN="$HOME/.local/bin"
export PATH="$LOCALBIN:$PATH"

# ---------------------------------------------------------------------------------------------------------------
# Output and questions
# ---------------------------------------------------------------------------------------------------------------

if [ -t 1 ]; then B=$'\033[1m'; D=$'\033[2m'; R=$'\033[0m'; else B=""; D=""; R=""; fi
step() { printf '\n%s== %s%s\n' "$B" "$*" "$R"; }
say()  { printf '%s\n' "$*"; }
note() { printf '%s%s%s\n' "$D" "$*" "$R"; }
die()  { printf '\n%sZero could not finish:%s %s\n' "$B" "$R" "$*" >&2; exit 1; }

TTY=""
if { exec 3</dev/tty; } 2>/dev/null; then TTY=yes; fi

# ask VAR "question" default : uses $VAR when already set, else asks on the terminal, else takes the default.
ask() {
  local var=$1 q=$2 def=${3:-} ans=""
  if [ -n "${!var:-}" ]; then return 0; fi
  if [ -n "$TTY" ]; then
    printf '%s%s%s ' "$B" "$q" "$R" >&2
    [ -n "$def" ] && printf '[%s] ' "$def" >&2
    IFS= read -r ans <&3 || ans=""
  fi
  printf -v "$var" '%s' "${ans:-$def}"
}

save_state() {
  mkdir -p "$ZDATA"
  {
    echo "# Written by Zero's install.sh. Read again on every run, so a rerun keeps the same choices."
    for k in HUB_PORT API_PORT PREVIEW_BASE SANDBOX NETWORK SERVE_PORT PUBLIC_URL LINGER_BY_US DOCKER_BY_US \
             TAILSCALE_BY_US ROOTFUL_OFF_BY_US; do
      printf '%s=%q\n' "$k" "${!k:-}"
    done
  } > "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

# ---------------------------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------------------------

preflight() {
  [ "$(id -u)" != 0 ] || die "Run this as your own user, not as root. It asks for sudo when it needs it."
  [ "$(uname -s)" = Linux ] || die "This installer is for a Linux computer. Zero for Mac is a separate app."
  case "$(uname -m)" in x86_64|amd64) ARCH=x86_64 ;; aarch64|arm64) ARCH=aarch64 ;;
    *) die "This computer's processor ($(uname -m)) is not supported. Zero needs x86_64 or arm64." ;; esac
  [ -r /etc/os-release ] || die "This computer does not say which Linux it runs (/etc/os-release is missing)."
  # shellcheck disable=SC1091
  . /etc/os-release
  case " ${ID:-} ${ID_LIKE:-} " in *" debian "*|*" ubuntu "*) ;;
    *) die "Zero's installer supports Ubuntu and Debian-like systems. This one is ${PRETTY_NAME:-unknown}." ;; esac
  command -v systemctl >/dev/null || die "Zero needs systemd, which this computer does not have."
  command -v sudo >/dev/null || die "Zero needs sudo for a few steps. Install sudo, add yourself to it, and run this again."
  if ! sudo -n true 2>/dev/null; then
    say "Zero needs your password for sudo to install packages and keep its services running after you log out."
    if [ -n "$TTY" ]; then sudo -v <&3 || die "sudo did not accept the password."
    else sudo -v || die "sudo needs a password, and there is no terminal to type it in."; fi
  fi
}

port_free() { ! ss -Hltn "sport = :$1" 2>/dev/null | grep -q .; }

first_free_port() {   # first_free_port START END [EXCLUDE...]
  local p=$1 end=$2; shift 2
  while [ "$p" -le "$end" ]; do
    if port_free "$p" && [[ " $* " != *" $p "* ]]; then echo "$p"; return 0; fi
    p=$((p + 1))
  done
  return 1
}

range_free() { local p; for ((p=$1; p<$1+$2; p++)); do port_free "$p" || return 1; done; }

hub_url() { if [ "${NETWORK:-}" = lan ]; then echo "https://127.0.0.1:$HUB_PORT"; else echo "http://127.0.0.1:$HUB_PORT"; fi; }
# -k only here, on this computer's own loopback; the phone pins the certificate's fingerprint instead.
hub_answers() { curl -fsSk --max-time 3 "$(hub_url)/api/health" >/dev/null 2>&1; }

# The user's systemd manager. sudo or su sessions have no XDG_RUNTIME_DIR; lingering starts the manager.
user_bus() {
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
  local i
  for i in $(seq 1 30); do
    [ -S "$XDG_RUNTIME_DIR/bus" ] && systemctl --user show-environment >/dev/null 2>&1 && return 0
    sleep 1
  done
  die "Your user's systemd did not start. Log out and in again, then run this again."
}

# ---------------------------------------------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------------------------------------------

install_packages() {
  step "System packages"
  local want=(git curl ca-certificates openssl rsync python3 qrencode restic jq iproute2 util-linux iptables uidmap
              dbus-user-session)
  local missing=() p
  for p in "${want[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
  if [ ${#missing[@]} -eq 0 ]; then say "Everything needed is already installed."; return; fi
  say "Installing: ${missing[*]}"
  sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 apt-get install -y -qq "${missing[@]}" >/dev/null
}

fetch_source() {
  step "Zero's code"
  local tmp; tmp="$(mktemp -d)"
  if [ -d "$ZERO_SOURCE" ]; then
    say "From the folder $ZERO_SOURCE"
    rsync -a --delete --exclude .git "$ZERO_SOURCE/" "$tmp/src/"
  elif [ -f "$ZERO_SOURCE" ]; then
    say "From the archive $ZERO_SOURCE"
    mkdir -p "$tmp/src" && tar -xzf "$ZERO_SOURCE" -C "$tmp/src"
  elif [[ "$ZERO_SOURCE" == *.tar.gz || "$ZERO_SOURCE" == *.tgz ]]; then
    say "Downloading $ZERO_SOURCE"
    curl -fsSL "$ZERO_SOURCE" -o "$tmp/src.tar.gz" || die "Could not download $ZERO_SOURCE."
    mkdir -p "$tmp/src" && tar -xzf "$tmp/src.tar.gz" -C "$tmp/src"
  else
    say "Downloading $ZERO_SOURCE ($ZERO_REF)"
    git clone -q --depth 1 --branch "$ZERO_REF" "$ZERO_SOURCE" "$tmp/src" || die "Could not download $ZERO_SOURCE."
    rm -rf "$tmp/src/.git"
  fi
  # A GitHub archive puts everything in one top folder.
  if [ ! -f "$tmp/src/alien/bin/zero-home-lib.py" ]; then
    local inner; inner="$(find "$tmp/src" -mindepth 2 -maxdepth 4 -path '*/alien/bin/zero-home-lib.py' | head -1)"
    [ -n "$inner" ] || die "The download does not look like Zero (alien/bin/zero-home-lib.py is missing)."
    mv "$tmp/src" "$tmp/outer" && mv "$(dirname "$(dirname "$(dirname "$inner")")")" "$tmp/src"
  fi
  mkdir -p "$ZDATA"
  rsync -a --delete "$tmp/src/" "$SRC/"
  rm -rf "$tmp"
  install -m 0755 "$SRC/install.sh" "$ZDATA/install.sh" 2>/dev/null || true
  say "Zero's code is in $SRC."
}

install_uv_python() {
  step "Python and uv"
  if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 UV_INSTALL_DIR="$LOCALBIN" sh >/dev/null \
      || die "Could not install uv."
  fi
  uv python install -q "$PYTHON_VERSION" || die "Could not install Python $PYTHON_VERSION."
  if [ ! -x "$VENV/bin/python" ]; then
    mkdir -p "$TAFFY"
    uv venv -q --python "$PYTHON_VERSION" "$VENV" || die "Could not make the Python environment."
  fi
  say "uv $(uv --version | awk '{print $2}'), $("$VENV/bin/python" -V)"
}

# git_pin DIR REPO COMMIT : DIR holds REPO at COMMIT (fetches only that commit).
git_pin() {
  local dir=$1 repo=$2 commit=$3
  if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" = "$commit" ]; then return 0; fi
  if [ ! -d "$dir/.git" ]; then
    [ ! -e "$dir" ] || [ -z "$(ls -A "$dir" 2>/dev/null)" ] || mv "$dir" "$dir.before-zero-$(date +%s)"
    mkdir -p "$dir" && git -C "$dir" init -q && git -C "$dir" remote add origin "$repo"
  fi
  git -C "$dir" fetch -q --depth 1 origin "$commit" || die "Could not download $repo at $commit."
  git -C "$dir" checkout -q -f "$commit"
}

install_engine() {
  step "Hermes (the agent engine) at ${HERMES_COMMIT:0:10}"
  git_pin "$TAFFY/hermes" "$HERMES_REPO" "$HERMES_COMMIT"
  local p
  for p in "$SRC"/alien/patches/hermes-*.patch; do
    [ -f "$p" ] || continue
    if git -C "$TAFFY/hermes" apply --check "$p" 2>/dev/null; then
      git -C "$TAFFY/hermes" apply "$p" && note "applied $(basename "$p")"
    elif ! git -C "$TAFFY/hermes" apply --check -R "$p" 2>/dev/null; then
      note "skipped $(basename "$p"): it does not apply to this Hermes"
    fi
  done
  local req="$SRC/alien/engine/requirements.txt"
  if [ ! -f "$VENV/.zero-packages" ] || ! cmp -s "$req" "$VENV/.zero-packages"; then
    say "Installing Python packages (a few minutes the first time)"
    if ! uv pip install -q --python "$VENV/bin/python" -r "$req"; then
      note "The pinned package list did not install here; installing Hermes's own requirements instead."
      uv pip install -q --python "$VENV/bin/python" -e "$TAFFY/hermes" \
        "fastapi" "uvicorn[standard]" "httpx[http2]" "pyjwt[crypto]" "pyyaml" || die "Could not install Hermes."
    fi
    cp "$req" "$VENV/.zero-packages"
  fi
  if ! "$VENV/bin/python" -c "import hermes_cli" 2>/dev/null || [ ! -x "$VENV/bin/hermes" ]; then
    uv pip install -q --python "$VENV/bin/python" --no-deps -e "$TAFFY/hermes" || die "Could not install Hermes."
  fi
  "$VENV/bin/python" -c "import yaml" 2>/dev/null || uv pip install -q --python "$VENV/bin/python" pyyaml

  step "The Claude plan plugin at ${DIRECTSDK_COMMIT:0:10}"
  git_pin "$HERMES_HOME/plugins/$DIRECTSDK_NAME" "$DIRECTSDK_REPO" "$DIRECTSDK_COMMIT"
  chmod 700 "$HERMES_HOME"
}

install_zero_code() {
  step "The hub and Zero's tools"
  mkdir -p "$TAFFY"/{bin,hub-state,work/default,shared,owner,state,plugins} "$HERMES_HOME"/{plugins,profiles} "$LOCALBIN" \
    "$HOME/backups"
  chmod 700 "$TAFFY/hub-state" "$HERMES_HOME/profiles" "$HOME/backups"
  local changed
  changed=$(rsync -a --delete --itemize-changes --exclude __pycache__ --exclude tests "$SRC/hub/" "$TAFFY/hub-src/" | wc -l)
  changed=$((changed + $(rsync -a --delete --itemize-changes --exclude __pycache__ --exclude tests \
    "$SRC/alien/plugins/taffy-tools/" "$TAFFY/plugins/taffy-tools/" | wc -l)))
  CODE_CHANGED=$changed
  if [ ! -L "$HERMES_HOME/plugins/taffy-tools" ]; then
    [ ! -e "$HERMES_HOME/plugins/taffy-tools" ] || mv "$HERMES_HOME/plugins/taffy-tools" "$HERMES_HOME/plugins/taffy-tools.before-zero"
    ln -s "$TAFFY/plugins/taffy-tools" "$HERMES_HOME/plugins/taffy-tools"
  fi
  local t
  for t in taffy-backup zip-egress-rules zero-calls zip-test-cards set-toolsets.py; do
    install -m 0755 "$SRC/alien/bin/$t" "$TAFFY/bin/$t"
  done
  install -m 0755 "$SRC/alien/sandbox/zip-networks.sh" "$TAFFY/bin/zip-networks.sh"
  [ -f "$TAFFY/shared/zip-templates.json" ] || install -m 0644 "$SRC/alien/shared/zip-templates.json" "$TAFFY/shared/"
  cat > "$TAFFY/bin/taffy" <<'EOF'
#!/bin/bash
# Runs Hermes for this Zero home. Written by Zero's install.sh.
export HERMES_HOME="$HOME/.taffy"
export ZERO_HOME="$HOME/taffy/home.json"
export PATH="$HOME/taffy/bin:$HOME/.local/bin:$PATH"
exec "$HOME/taffy/.venv/bin/hermes" "$@"
EOF
  cat > "$TAFFY/bin/taffy-hub" <<'EOF'
#!/bin/bash
# The hub's command line for this Zero home (pair, restart, refresh-chats). Written by Zero's install.sh.
export HERMES_HOME="$HOME/.taffy"
export ZERO_HOME="$HOME/taffy/home.json"
export PYTHONPATH="$HOME/taffy/hub-src:$HOME/taffy/hermes"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
exec "$HOME/taffy/.venv/bin/python" -m taffy_hub.cli "$@"
EOF
  chmod 755 "$TAFFY/bin/taffy" "$TAFFY/bin/taffy-hub"
}

choose_ports() {
  if [ -z "${HUB_PORT:-}" ]; then
    HUB_PORT=$(first_free_port 8650 8699) || die "No free port for the hub between 8650 and 8699."
    API_PORT=$(first_free_port 8642 8649 || first_free_port 8700 8799 "$HUB_PORT") || die "No free port for Hermes."
    PREVIEW_BASE=18200
    until range_free "$PREVIEW_BASE" 60; do
      PREVIEW_BASE=$((PREVIEW_BASE + 100))
      [ "$PREVIEW_BASE" -lt 19900 ] || die "No free range of 60 ports for the Zips' previews."
    done
  elif ! hub_answers; then
    # Our own services may be stopped; a port taken by something else means pick again.
    if ! port_free "$HUB_PORT" && ! systemctl --user is-active -q taffy-hub 2>/dev/null; then
      die "Port $HUB_PORT, which Zero's hub uses, is now taken by another program. Stop it or remove $STATE and run this again."
    fi
  fi
  say "Ports: hub $HUB_PORT, Hermes $API_PORT, previews $PREVIEW_BASE to $((PREVIEW_BASE + 59)), all on this computer only."
}

ask_person() {
  step "About you"
  # A rerun keeps the answers already in home.json.
  local existing="" saved_tz=""
  if [ -f "$TAFFY/home.json" ]; then
    existing=$("$VENV/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["person"]["name"])' "$TAFFY/home.json" 2>/dev/null || true)
    saved_tz=$("$VENV/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["person"]["timezone"])' "$TAFFY/home.json" 2>/dev/null || true)
  fi
  if [ -n "$existing" ]; then ZERO_NAME="${ZERO_NAME:-$existing}"; fi
  if [ -n "$saved_tz" ]; then ZERO_TIMEZONE="${ZERO_TIMEZONE:-$saved_tz}"; fi
  local guess; guess=$(getent passwd "$USER" | cut -d: -f5 | cut -d, -f1)
  ask ZERO_NAME "What is your name?" "${guess:-$USER}"
  local tz; tz=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo UTC)
  [ "$tz" != "Etc/UTC" ] || tz=UTC
  ask ZERO_TIMEZONE "Your time zone?" "$tz"
  say "Zero is set up for $ZERO_NAME in $ZERO_TIMEZONE."
}

ask_sandbox() {
  if [ -z "${ZERO_SANDBOX:-}" ] && [ -n "${SANDBOX:-}" ]; then ZERO_SANDBOX=$SANDBOX; fi
  ZERO_SANDBOX="${ZERO_SANDBOX:-docker}"
  case "$ZERO_SANDBOX" in docker|local) ;; *) die "ZERO_SANDBOX is docker or local, not $ZERO_SANDBOX." ;; esac
  SANDBOX=$ZERO_SANDBOX
}

write_settings() {
  step "Your home's settings"
  ZERO_NAME="$ZERO_NAME" ZERO_TIMEZONE="$ZERO_TIMEZONE" HUB_PORT="$HUB_PORT" API_PORT="$API_PORT" \
  PREVIEW_BASE="$PREVIEW_BASE" PUBLIC_URL="$PUBLIC_URL" SANDBOX="$SANDBOX" HOME_ID="$HOME_ID" TLS_FP="${TLS_FP:-}" \
  ZERO_SRV="$ZDATA/srv" "$VENV/bin/python" - "$SRC/alien/bin/zero-home-lib.py" "$TAFFY/home.json" <<'PY'
import importlib.util, json, os, socket, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("zero_home_lib", sys.argv[1])
lib = importlib.util.module_from_spec(spec); spec.loader.exec_module(lib)
path = Path(sys.argv[2])
e = os.environ
lead = "local" if e["SANDBOX"] == "local" else "docker"
if path.exists():
    s = json.loads(path.read_text())
    before = json.dumps(s, sort_keys=True)
else:
    before = None
    ports = {"hub": int(e["HUB_PORT"]), "hermes_api": int(e["API_PORT"]), "serve_port": 443,
             "preview_base": int(e["PREVIEW_BASE"]), "docker_subnet_base": "172.30"}
    s = lib.new_home_settings(e["HOME_ID"], e["ZERO_NAME"], e["ZERO_TIMEZONE"], ports, public_host="localhost",
                              whisper_url=None, owner_name="", own_plan=True)
    s["backup"]["drop"] = None
    s["zips"] = {"max": 6, "container_memory_mb": 2048}
# What the installer owns; everything else in the file is the hub's and the app's.
s["person"]["name"] = e["ZERO_NAME"]
s["person"]["first_name"] = e["ZERO_NAME"].split()[0]
s["person"]["timezone"] = e["ZERO_TIMEZONE"]
s["ports"]["hub"], s["ports"]["hermes_api"] = int(e["HUB_PORT"]), int(e["API_PORT"])
s["ports"]["preview"]["base"] = int(e["PREVIEW_BASE"])
s["network"]["public_url"] = e["PUBLIC_URL"]
s["network"]["serve_port"] = None
s["network"]["tls_fingerprint"] = e.get("TLS_FP") or None   # the app pins this on the local network
s["host"].update(name=socket.gethostname(), kind="server", lead_backend=lead)
s["plan"]["claude"] = {"mode": "own", "command": "claude"}
s["plan"]["chatgpt"] = {"mode": "own", "codex_command": "~/.local/bin/codex", "codex_base_url": None}
s["plan"].pop("owner_name", None)
s["judge"] = {"enabled": False}
s["integrations"]["whisper_url"] = None
if before != json.dumps(s, sort_keys=True):
    path.write_text(json.dumps(s, indent=2) + "\n")
    print("Wrote " + str(path))
else:
    print(str(path) + " is already right.")
PY
}

render_home() {
  step "Hermes settings and services"
  "$VENV/bin/python" "$SRC/alien/bin/zero-home-render" "$HOME_ID" --home-dir "$HOME" --settings "$TAFFY/home.json" \
    | sed 's/^/  /'
  if ! grep -q platform_toolsets "$HERMES_HOME/config.yaml" 2>/dev/null; then
    HERMES_HOME="$HERMES_HOME" ZERO_HOME="$TAFFY/home.json" "$VENV/bin/python" "$TAFFY/bin/set-toolsets.py" >/dev/null \
      && say "  set the Zips' tool lists"
  fi
  # The hub listens on this computer only; Tailscale serve publishes it. Without Tailscale it listens on the
  # local network too, so a phone on the same Wi-Fi can reach it.
  local d="$UNITS/taffy-hub.service.d" host=127.0.0.1 tls=""
  if [ "$NETWORK" = lan ]; then
    host=0.0.0.0
    tls=" --ssl-certfile %h/taffy/tls/hub-cert.pem --ssl-keyfile %h/taffy/tls/hub-key.pem"
  fi
  mkdir -p "$d"
  cat > "$d/zero-listen.conf.new" <<EOF
# Written by Zero's install.sh: where the hub listens ($NETWORK). On the local network it serves HTTPS with its own
# self-signed certificate, which the app pins by fingerprint, so a device token never travels in plain text.
[Service]
ExecStart=
ExecStart=%h/taffy/.venv/bin/python -m uvicorn taffy_hub.app:app --host $host --port $HUB_PORT$tls --no-access-log --ws websockets
EOF
  if ! cmp -s "$d/zero-listen.conf.new" "$d/zero-listen.conf"; then mv "$d/zero-listen.conf.new" "$d/zero-listen.conf"; CODE_CHANGED=1
  else rm -f "$d/zero-listen.conf.new"; fi
  if [ ! -s "$HOME/.config/taffy-backup/restic-password" ]; then
    mkdir -p "$HOME/.config/taffy-backup" && chmod 700 "$HOME/.config/taffy-backup"
    (umask 077; head -c 32 /dev/urandom | base64 | tr -d '/+=' > "$HOME/.config/taffy-backup/restic-password")
    say "  made a password for the nightly encrypted backup (~/.config/taffy-backup/restic-password)"
  fi
}

enable_linger() {
  if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != yes ]; then
    sudo loginctl enable-linger "$USER"
    LINGER_BY_US=yes
    say "Zero's services now keep running after you log out (lingering is on for $USER)."
  fi
  user_bus
}

install_docker() {
  [ "$SANDBOX" = docker ] || { note "No Docker: the lead Zip works directly on this computer (ZERO_SANDBOX=local)."; return; }
  step "Docker for the Zips' sandboxes (rootless, as $USER)"
  if ! command -v dockerd-rootless-setuptool.sh >/dev/null; then
    local had_docker=no
    dpkg -s docker-ce >/dev/null 2>&1 && had_docker=yes
    # shellcheck disable=SC1091
    . /etc/os-release
    local distro=ubuntu codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
    [ "${ID:-}" = debian ] && { distro=debian; codename="${VERSION_CODENAME:-}"; }
    sudo install -m 0755 -d /etc/apt/keyrings
    sudo curl -fsSL "https://download.docker.com/linux/$distro/gpg" -o /etc/apt/keyrings/docker.asc
    sudo chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$distro $codename stable" \
      | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
      docker-ce-rootless-extras docker-buildx-plugin slirp4netns >/dev/null || die "Could not install Docker."
    DOCKER_BY_US=yes
    if [ "$had_docker" = no ]; then
      # Zero uses only your user's own Docker; the system-wide daemon the package starts is not needed.
      sudo systemctl disable --now docker.service docker.socket >/dev/null 2>&1 || true
      ROOTFUL_OFF_BY_US=yes
    fi
  fi
  # Ubuntu 24.04 limits user namespaces to programs AppArmor allows; rootlesskit needs one.
  if [ -d /etc/apparmor.d ] && [ "$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || echo 0)" = 1 ] \
     && [ ! -f /etc/apparmor.d/zero-rootlesskit ]; then
    sudo tee /etc/apparmor.d/zero-rootlesskit >/dev/null <<'EOF'
# Written by Zero's install.sh: lets rootless Docker make its user namespace.
abi <abi/4.0>,
include <tunables/global>
"/usr/bin/rootlesskit" flags=(unconfined) {
  userns,
  include if exists <local/zero-rootlesskit>
}
EOF
    sudo apparmor_parser -r /etc/apparmor.d/zero-rootlesskit || true
  fi
  if ! grep -q "^$USER:" /etc/subuid || ! grep -q "^$USER:" /etc/subgid; then
    local start; start=$(awk -F: 'BEGIN{m=100000} {if ($2+$3>m) m=$2+$3} END{print m}' /etc/subuid /etc/subgid)
    sudo usermod --add-subuids "$start-$((start + 65535))" --add-subgids "$start-$((start + 65535))" "$USER"
  fi
  export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"
  if [ ! -f "$UNITS/docker.service" ]; then
    mkdir -p "$HOME/.config/docker"
    [ -f "$HOME/.config/docker/daemon.json" ] || echo '{"dns": ["1.1.1.1", "8.8.8.8"], "log-driver": "local"}' > "$HOME/.config/docker/daemon.json"
    dockerd-rootless-setuptool.sh install --skip-iptables >/dev/null 2>&1 || dockerd-rootless-setuptool.sh install \
      || die "Rootless Docker did not start. See: journalctl --user -u docker"
    # Kept beside Docker's own settings (not in Zero's folder), so uninstall knows Zero set this daemon up even
    # after a reinstall.
    echo "Zero's install.sh set up this user's rootless Docker." > "$HOME/.config/docker/zero-set-this-up"
  fi
  systemctl --user daemon-reload
  systemctl --user enable -q --now docker.service
  local i; for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
  docker info >/dev/null 2>&1 || die "Rootless Docker is not answering. See: journalctl --user -u docker"
  if ! docker image inspect "$SANDBOX_IMAGE" >/dev/null 2>&1; then
    say "Building the Zips' sandbox image (5 to 10 minutes, once)"
    docker build -q -f "$SRC/alien/sandbox/zip-sandbox.Dockerfile" -t "$SANDBOX_IMAGE" "$SRC/alien/sandbox" >/dev/null \
      || die "The sandbox image did not build. Run this again; if it fails twice, try ZERO_SANDBOX=local."
  fi
  ZERO_HOME="$TAFFY/home.json" "$TAFFY/bin/zip-networks.sh" | sed 's/^/  /'
  say "Docker is ready: $(docker version -f '{{.Server.Version}}' 2>/dev/null), rootless."
}

start_services() {
  step "Starting Zero"
  systemctl --user daemon-reload
  local u
  for u in taffy-gateway.service taffy-hub.service taffy-backup.timer; do
    if ! systemctl --user is-enabled -q "$u" 2>/dev/null || ! systemctl --user is-active -q "$u"; then
      systemctl --user enable -q --now "$u"
    elif [ "${CODE_CHANGED:-0}" != 0 ] && [ "$u" != taffy-backup.timer ]; then
      systemctl --user restart "$u"
    fi
  done
  local i
  for i in $(seq 1 90); do hub_answers && break; sleep 1; done
  hub_answers || die "The hub did not answer on port $HUB_PORT. See: journalctl --user -u taffy-hub -n 50"
  say "The hub answers: $(curl -fsSk "$(hub_url)/api/health")"
  systemctl --user is-active -q taffy-gateway || note "The gateway is still starting. Check it with: systemctl --user status taffy-gateway"
}

# The hub's own certificate for the local network: self-signed, made once and kept (the phone pins it), key mode 600.
make_hub_cert() {
  local dir="$TAFFY/tls" ip=$1
  mkdir -p "$dir" && chmod 700 "$dir"
  if [ ! -s "$dir/hub-key.pem" ] || [ ! -s "$dir/hub-cert.pem" ]; then
    local host; host=$(hostname)
    (umask 077; openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
      -subj "/CN=Zero hub on $host" -addext "subjectAltName=IP:$ip,IP:127.0.0.1,DNS:$host,DNS:$host.local" \
      -addext "basicConstraints=critical,CA:FALSE" -addext "extendedKeyUsage=serverAuth" \
      -keyout "$dir/hub-key.pem" -out "$dir/hub-cert.pem" >/dev/null 2>&1) || die "Could not make the hub's certificate."
    say "Made the hub's own HTTPS certificate (~/taffy/tls)."
  fi
  chmod 600 "$dir/hub-key.pem"; chmod 644 "$dir/hub-cert.pem"
  TLS_FP=$(openssl x509 -in "$dir/hub-cert.pem" -outform der | sha256sum | cut -d" " -f1)
}

lan_address() { ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<NF;i++) if ($i=="src") {print $(i+1); exit}}'; }

setup_network() {
  step "Reaching Zero from your phone"
  TLS_FP=""
  if [ -z "${ZERO_TAILSCALE:-}" ] && [ "${NETWORK:-}" = tailscale ]; then ZERO_TAILSCALE=yes; fi
  if [ -z "${ZERO_TAILSCALE:-}" ]; then
    say "Tailscale lets your phone reach this computer from anywhere, privately and with HTTPS. It is free for"
    say "personal use, and you sign in with your own account. Without it, your phone can reach Zero only on this Wi-Fi."
    ask ZERO_TAILSCALE "Use Tailscale? (yes or no)" yes
  fi
  case "${ZERO_TAILSCALE,,}" in y|yes) setup_tailscale && return ;; esac
  NETWORK=lan
  local ip; ip=$(lan_address)
  [ -n "$ip" ] || die "This computer has no network address."
  make_hub_cert "$ip"
  PUBLIC_URL="https://$ip:$HUB_PORT"
  say "Your phone reaches Zero at $PUBLIC_URL while it is on the same network as this computer."
  note "Certificate fingerprint (SHA-256): $TLS_FP"
  if command -v ufw >/dev/null && sudo ufw status 2>/dev/null | grep -q "Status: active"; then
    local net; net=$(ip -4 -o addr show | awk -v ip="$ip" '$4 ~ "^"ip"/" {print $4; exit}')
    sudo ufw allow from "${net:-$ip/24}" to any port "$HUB_PORT" proto tcp comment "Zero hub" >/dev/null
    note "The firewall now lets your local network reach port $HUB_PORT."
  fi
}

setup_tailscale() {
  if ! command -v tailscale >/dev/null; then
    say "Installing Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh >/dev/null 2>&1 || { note "Tailscale did not install."; return 1; }
    TAILSCALE_BY_US=yes
  fi
  if ! tailscale status --json 2>/dev/null | jq -e '.BackendState == "Running"' >/dev/null; then
    if [ -z "$TTY" ]; then note "Tailscale needs you to sign in, and there is no terminal here. Using the local network."; return 1; fi
    say "Sign in to Tailscale with your own account. Open the link it prints, on any device."
    sudo tailscale up <&3 || { note "Tailscale sign-in did not finish."; return 1; }
  fi
  sudo tailscale set --operator="$USER" >/dev/null 2>&1 || true
  local name; name=$(tailscale status --json | jq -r '.Self.DNSName' | sed 's/\.$//')
  [ -n "$name" ] && [ "$name" != null ] || { note "Tailscale has no name for this computer."; return 1; }
  # 443 unless something else already uses it in this computer's serve settings.
  local serve; serve=$(tailscale serve status --json 2>/dev/null || echo '{}')
  local port="${SERVE_PORT:-}" p
  if [ -z "$port" ]; then
    for p in 443 8443 10443; do
      if echo "$serve" | jq -e --arg p "$p" '(.TCP // {})[$p] == null' >/dev/null 2>&1; then port=$p; break; fi
    done
    [ -n "$port" ] || { note "Tailscale serve already uses ports 443, 8443 and 10443 on this computer."; return 1; }
  fi
  if ! tailscale serve --bg --https="$port" "http://127.0.0.1:$HUB_PORT" >/dev/null 2>&1; then
    note "Tailscale could not publish Zero with HTTPS. Turn on HTTPS for your tailnet at"
    note "https://login.tailscale.com/admin/dns (HTTPS Certificates), then run this installer again."
    return 1
  fi
  SERVE_PORT=$port NETWORK=tailscale
  PUBLIC_URL="https://$name"; [ "$port" = 443 ] || PUBLIC_URL="$PUBLIC_URL:$port"
  say "Zero is on your tailnet at $PUBLIC_URL. Install Tailscale on your iPhone and sign in with the same account."
  return 0
}

# The official command lines are installed whatever the answer, so Sign In With Claude in the app works later. They
# are only programs; nothing is signed in until the person does it with their own account.
install_ai_clis() {
  if ! command -v claude >/dev/null; then
    say "Installing Claude Code (Anthropic's official command line)"
    curl -fsSL https://claude.ai/install.sh | bash >/dev/null 2>&1 || note "Claude Code did not install. Run this again later."
  fi
  if ! command -v codex >/dev/null; then
    say "Installing Codex (OpenAI's official command line, used for voice calls)"
    local tmp; tmp=$(mktemp -d)
    if curl -fsSL "https://github.com/openai/codex/releases/latest/download/codex-$ARCH-unknown-linux-musl.tar.gz" -o "$tmp/c.tgz" \
       && tar -xzf "$tmp/c.tgz" -C "$tmp"; then
      install -m 0755 "$(find "$tmp" -type f -name 'codex*' ! -name '*.tgz' | head -1)" "$LOCALBIN/codex"
    else
      note "Codex did not install. Run this again later."
    fi
    rm -rf "$tmp"
  fi
  command -v claude >/dev/null && say "  $(claude --version 2>/dev/null | head -1)"
  command -v codex >/dev/null && say "  $(codex --version 2>/dev/null | head -1)"
  true
}

ai_signin() {
  step "Your AI plan"
  say "Your Zips think with your own Claude or ChatGPT plan. This server signs in with the official command line"
  say "of the plan you pick, using your own account. Nobody else's plan or keys are used."
  install_ai_clis
  if [ -z "${ZERO_AI:-}" ] && [ -z "$TTY" ]; then
    ZERO_AI=skip; note "There is no terminal to sign in from, so this step is skipped."
  elif [ -z "${ZERO_AI:-}" ]; then
    say "  1. Claude (Pro or Max)   2. ChatGPT (Plus or Pro)   3. Both   4. Skip for now"
    ask ZERO_AI "Which plan?" 1
  fi
  case "${ZERO_AI,,}" in 1|claude) ZERO_AI=claude ;; 2|chatgpt|codex) ZERO_AI=chatgpt ;; 3|both) ZERO_AI=both ;;
    *) ZERO_AI=skip ;; esac
  if [ "$ZERO_AI" = skip ]; then
    say "Skipped. Sign in later with: bash $ZDATA/install.sh signin"
    say "You can also choose Sign In With Claude in the app after pairing."
    return 0
  fi
  if [[ "$ZERO_AI" == claude || "$ZERO_AI" == both ]] && command -v claude >/dev/null; then
    if claude auth status --json 2>/dev/null | grep -q '"loggedIn": *true'; then say "Claude is already signed in."
    else
      say "Signing in to Claude. Open the link it shows, sign in, and paste the code back here."
      claude auth login --claudeai <&3 || note "Claude sign-in did not finish. Try again with: bash $ZDATA/install.sh signin"
    fi
  fi
  if [[ "$ZERO_AI" == chatgpt || "$ZERO_AI" == both ]] && command -v codex >/dev/null; then
    if codex login status >/dev/null 2>&1; then say "ChatGPT is already signed in."
    else
      say "Signing in to ChatGPT. Open the link it shows on any device and enter the code."
      codex login --device-auth <&3 || note "ChatGPT sign-in did not finish. Try again with: bash $ZDATA/install.sh signin"
    fi
  fi
  if [ "$ZERO_AI" = chatgpt ]; then
    note "Zips think with Claude today. With ChatGPT only, Zero's voice calls work, and chats need a Claude plan."
  fi
  systemctl --user restart taffy-gateway 2>/dev/null || true
}

pair() {
  step "Pair your iPhone"
  local out code link
  out=$("$TAFFY/bin/taffy-hub" pair 1 2>&1) || die "No pairing code: $out"
  code=$(sed -n 's/^Pairing code: \([A-Z0-9-]*\).*/\1/p' <<<"$out")
  link=$(sed -n 's/^Link: //p' <<<"$out")
  [ -n "$code" ] && [ -n "$link" ] || die "No pairing code: $out"
  say "Open the Camera on your iPhone and point it at this code. It opens the Zero app with this server filled in."
  echo
  qrencode -t ansiutf8 -m 2 "$link" 2>/dev/null || true
  echo
  say "  Link:    $link"
  say "  Address: $PUBLIC_URL"
  say "  Code:    $code   (works once, for one hour)"
  echo
  say "If the camera is not handy, open Zero, choose I Have My Code, and type the address and the code."
  [ "$NETWORK" = tailscale ] && say "Your iPhone needs the Tailscale app, signed in with the same account, to reach this address."
  [ "$NETWORK" = lan ] && say "Your iPhone needs to be on the same Wi-Fi as this computer."
  say "For a new code later, run: bash $ZDATA/install.sh pair"
}

# ---------------------------------------------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------------------------------------------

load_state() { [ -f "$STATE" ] && . "$STATE"; true; }

do_install() {
  say "${B}Zero home server${R}: this sets up Zero on this computer for one person, as $USER."
  preflight
  load_state
  install_packages
  fetch_source
  install_uv_python
  install_engine
  install_zero_code
  choose_ports
  ask_person
  ask_sandbox
  enable_linger
  NETWORK="${NETWORK:-lan}"; PUBLIC_URL="${PUBLIC_URL:-http://127.0.0.1:$HUB_PORT}"
  setup_network
  save_state
  write_settings
  render_home
  install_docker
  save_state
  start_services
  ai_signin
  pair
  echo
  say "${B}Zero is running.${R} It starts by itself when this computer starts. To update or repair it, run the"
  say "same install command again. To remove it, run: bash $ZDATA/install.sh uninstall"
}

do_status() {
  load_state; user_bus
  local u
  for u in taffy-gateway taffy-hub taffy-backup.timer docker; do
    printf '%-16s %s\n' "$u" "$(systemctl --user is-active "$u" 2>/dev/null || true)"
  done
  [ -n "${HUB_PORT:-}" ] && curl -fsSk "$(hub_url)/api/health" && echo
  say "Address: ${PUBLIC_URL:-unknown} (${NETWORK:-not installed})"
}

do_uninstall() {
  [ "$(id -u)" != 0 ] || die "Run this as the user who installed Zero, not as root."
  load_state
  say "${B}Removing Zero${R} from this computer for $USER."
  user_bus
  local u
  for u in taffy-hub.service taffy-gateway.service taffy-backup.timer taffy-backup.service; do
    systemctl --user disable -q --now "$u" 2>/dev/null || true
  done
  rm -rf "$UNITS"/taffy-gateway.service "$UNITS"/taffy-hub.service "$UNITS"/taffy-backup.service \
    "$UNITS"/taffy-backup.timer "$UNITS"/taffy-gateway.service.d "$UNITS"/taffy-hub.service.d
  if [ -f "$UNITS/docker.service" ]; then
    export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"
    local ids; ids=$(docker ps -aq --filter "ancestor=$SANDBOX_IMAGE" 2>/dev/null || true)
    [ -z "$ids" ] || docker rm -f $ids >/dev/null 2>&1 || true
    docker network ls --format '{{.Name}}' 2>/dev/null | grep '^zip-' | xargs -r docker network rm >/dev/null 2>&1 || true
    docker image rm "$SANDBOX_IMAGE" >/dev/null 2>&1 || true
    if [ "${DOCKER_BY_US:-}" = yes ] || [ -f "$HOME/.config/docker/zero-set-this-up" ]; then
      dockerd-rootless-setuptool.sh uninstall >/dev/null 2>&1 || true
      rm -rf "$UNITS/docker.service.d" "$HOME/.config/docker/zero-set-this-up"
      say "Stopped your user's Docker. Its stored images remain in ~/.local/share/docker; remove it with: rootlesskit rm -rf ~/.local/share/docker"
    fi
  fi
  systemctl --user daemon-reload 2>/dev/null || true
  systemctl --user reset-failed 2>/dev/null || true
  if [ -n "${SERVE_PORT:-}" ] && command -v tailscale >/dev/null; then
    tailscale serve --https="$SERVE_PORT" off >/dev/null 2>&1 || sudo tailscale serve --https="$SERVE_PORT" off >/dev/null 2>&1 || true
    say "Tailscale no longer publishes Zero. Tailscale itself stays installed and signed in."
  fi
  if [ "${LINGER_BY_US:-}" = yes ]; then sudo loginctl disable-linger "$USER" || true; fi
  rm -rf "$TAFFY/hermes" "$TAFFY/hub-src" "$TAFFY/plugins" "$TAFFY/bin" "$TAFFY/.venv" \
    "$HERMES_HOME/plugins/$DIRECTSDK_NAME" "$HERMES_HOME/plugins/taffy-tools"
  local answer="${ZERO_DELETE_DATA:-}"
  if [ -z "$answer" ] && [ -n "$TTY" ]; then
    say "Your Zips' memories, chats, files and backups are still in ~/.taffy, ~/taffy and ~/backups."
    ask answer "Delete them too? This cannot be undone. Type delete to delete, or press Enter to keep them." ""
  fi
  if [ "$answer" = delete ] || [ "$answer" = yes ]; then
    rm -rf "$HERMES_HOME" "$TAFFY" "$HOME/backups/taffy-restic" "$HOME/.config/taffy-backup"
    say "Deleted your Zips' memories, chats and files."
  else
    say "Kept your Zips' memories, chats and files in ~/.taffy and ~/taffy. Installing again picks them up."
  fi
  rm -rf "$ZDATA"
  say "Zero is removed. Claude Code, Codex, uv and your sign-ins stay installed, since you may use them elsewhere."
}

case "${1:-install}" in
  install|repair|update) do_install ;;
  pair) load_state; user_bus; pair ;;
  signin) load_state; user_bus; ARCH=$(uname -m | sed 's/amd64/x86_64/;s/arm64/aarch64/'); ZERO_AI="${ZERO_AI:-}"; ai_signin ;;
  status) do_status ;;
  uninstall|remove) do_uninstall ;;
  *) say "Usage: bash install.sh [install | pair | signin | status | uninstall]"; exit 2 ;;
esac
