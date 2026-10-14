#!/usr/bin/env bash
# zero-e2e.sh: one command that proves a stranger's path through Zero (docs/plans/2026-10-09-zero-for-anyone-plan.md,
# WP5). It brings up a home, drives the iPhone app's onboarding in a simulator, checks the home's state through the
# hub, checks isolation, tears down, and writes a short result file under docs/qa/.
#
# Modes:
#   --live            (default) the throwaway test home on alien (zero-test; ports from /srv/zero/homes.json):
#                     zero-home-test up --seed (our mail) -> ssh tunnel -> simulator onboarding -> hub-state
#                     assertions (and one chat turn with the test home's Inbox Zip) -> zero-home-check test ->
#                     isolation probes -> zero-home-test down. Needs the wizard's one-time steps for zero-test.
#   --dry             the same chain on this Mac with no test home: the real hub from this repo on a temporary
#                     home with a fake Hermes (hub/tests/harness/fixture_hub.py), plus the hub suite matrix.
#   --headless        --dry without the simulator (hub/tests/harness/simulate_app.py makes the app's calls).
#   --fixture         the UI test only, against the app's -fixture onboarding (no hub, no assertions).
#   --colin-readonly  read-only checks of Colin's live hub over an ssh tunnel: health, refusals without a token,
#                     and the loopback isolation probes. Never pairs, never starts a turn, changes nothing.
#
# Options:
#   --host NAME         ssh host for alien (default alien)
#   --keep              leave the test home up (live) or the temporary home in place (dry)
#   --keep-build        keep the build folder (default: deleted at the end)
#   --no-build          reuse an earlier build in the build folder
#   --no-sample         skip the sample-home run
#   --no-matrix         skip the hub suite matrix in --dry
#   --no-inbox          skip the chat turn that asks the Inbox Zip for the seeded mail
#   --device NAME       simulator model (default "iPhone 17 Pro"); --runtime ID (default newest iOS)
#   --result FILE       where to write the result (default docs/qa/zero-for-anyone-e2e-<mode>-<time>.md)
#
# Safety: ports are checked with lsof before anything listens; only processes this script started are stopped; the
# simulator it creates is deleted; a build folder it builds into (/tmp/wp5-build by default, ZERO_E2E_BUILD to
# change) is deleted unless --keep-build; it refuses to build with less than 6 GB free. In --live it touches only the test home through
# WP3's zero-home-* commands, and never pairs with, chats with or changes Colin's home. Tokens stay in a mode-700
# temporary folder and are never printed.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HARNESS="$REPO/hub/tests/harness"
IOS="$REPO/ios"
MODE=live
HOST=alien
KEEP=0; KEEP_BUILD=0; BUILD=1; SAMPLE=1; MATRIX=1; INBOX=1
DEVICE="iPhone 17 Pro"; RUNTIME=""
RESULT=""
BUILD_DIR="${ZERO_E2E_BUILD:-/tmp/wp5-build}"
TEST_HUB_PORT=8652
TEST_HERMES_PORT=8644
TEST_USER=zero-test
ZERO_BIN="${ZERO_BIN:-/srv/zero/bin}"
PERSONA="$HARNESS/persona.json"
COLIN_HUB_PORT=8650; COLIN_HERMES_PORT=8642; TALLY_PORT=18930
MIN_FREE_GB=6

while [ $# -gt 0 ]; do
  case "$1" in
    --live|--dry|--headless|--fixture|--colin-readonly) MODE="${1#--}" ;;
    --host) HOST="$2"; shift ;;
    --keep) KEEP=1 ;;
    --keep-build) KEEP_BUILD=1 ;;
    --no-build) BUILD=0; KEEP_BUILD=1 ;;
    --no-sample) SAMPLE=0 ;;
    --no-matrix) MATRIX=0 ;;
    --no-inbox) INBOX=0 ;;
    --device) DEVICE="$2"; shift ;;
    --runtime) RUNTIME="$2"; shift ;;
    --result) RESULT="$2"; shift ;;
    -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

STAMP="$(TZ=America/New_York date +%Y-%m-%d-%H%M)"
NOW_ET="$(TZ=America/New_York date '+%Y-%m-%d %H:%M %Z')"
RESULT="${RESULT:-$REPO/docs/qa/zero-for-anyone-e2e-$MODE-$STAMP.md}"
SHOTS="$REPO/docs/qa/zero-for-anyone-screens/$MODE"
WORK="$(mktemp -d /tmp/zero-e2e.XXXXXX)"; chmod 700 "$WORK"
TOKEN_FILE="$WORK/harness-token"
STEPS="$WORK/steps.md"; : > "$STEPS"
FAILED=0
PIDS=()
SIM=""; SIM_CREATED=0
BUILT=0
HOME_UP=0

say()  { printf '\n== %s\n' "$*"; }
step() {
  printf -- '- **%s** %s: %s\n' "$1" "$2" "$3" >> "$STEPS"
  printf '%-5s %s: %s\n' "$1" "$2" "$3"
  [ "$1" = FAIL ] && FAILED=1 || true
}

pick_python() {
  for p in "${ZERO_E2E_PYTHON:-}" python3.13 python3; do
    [ -n "$p" ] && command -v "$p" >/dev/null && "$p" -c 'import fastapi, httpx, uvicorn' 2>/dev/null && { echo "$p"; return; }
  done
  echo python3
}
PY="$(pick_python)"

port_free() { ! lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
free_port() { "$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }
local_port_for() { if port_free "$1"; then echo "$1"; else free_port; fi; }
free_gb() { df -g / | awk 'NR==2 {print $4}'; }
remote() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "bash -lc $(printf '%q' "$*")"; }

cleanup() {
  set +e
  for pid in "${PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null; done
  if [ "$MODE" = live ] && [ "$HOME_UP" = 1 ] && [ "$KEEP" = 0 ]; then
    say "Tearing down the test home"
    if remote "$ZERO_BIN/zero-home-test down" > "$WORK/down.log" 2>&1; then
      step PASS teardown "zero-home-test down finished; Colin removes the user with: sudo $ZERO_BIN/zero-home-sudo-prep remove-home test"
    else step FAIL teardown "zero-home-test down failed: $(tail -2 "$WORK/down.log" | tr '\n' ' ')"; fi
  fi
  if [ "$SIM_CREATED" = 1 ] && [ -n "$SIM" ]; then
    xcrun simctl shutdown "$SIM" >/dev/null 2>&1; xcrun simctl delete "$SIM" >/dev/null 2>&1
  fi
  if [ "$BUILT" = 1 ] && [ "$KEEP_BUILD" = 0 ] && [ "$BUILD_DIR" != "/" ] && [ -d "$BUILD_DIR" ]; then rm -rf "$BUILD_DIR"; fi
  write_result
  if [ "$KEEP" = 1 ] && [ "$MODE" = dry ]; then echo "kept the temporary home in $WORK/home"; else rm -rf "$WORK"; fi
}

write_result() {
  [ -f "$STEPS" ] || return 0
  mkdir -p "$(dirname "$RESULT")"
  {
    echo "# Zero for Anyone: End-to-End Run ($MODE)"
    echo
    echo "Run $NOW_ET by tools/zero-e2e.sh --$MODE from $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null) on $(git -C "$REPO" branch --show-current 2>/dev/null)."
    echo "Result: $([ "$FAILED" = 0 ] && echo passed || echo FAILED)."
    echo
    echo "## Steps"
    echo
    cat "$STEPS"
    for f in "$WORK"/*.result.md; do [ -f "$f" ] && { echo; cat "$f"; }; done
    if [ -d "$SHOTS" ]; then
      echo
      echo "## Screenshots"
      echo
      echo "In \`docs/qa/zero-for-anyone-screens/$MODE/\`:"
      for d in "$SHOTS"/*/; do [ -d "$d" ] && echo "- $(basename "$d"): $(ls "$d" | grep -c '\.png$') screens ($(ls "$d" | grep '\.png$' | sed 's/^[0-9]*-//; s/\.png$//' | paste -sd, - | sed 's/,/, /g'))"; done
    fi
  } > "$RESULT"
  echo; echo "Result written to ${RESULT#$REPO/}"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

section_from_json() {
  "$PY" - "$1" "$2" "$3" <<'PYEOF'
import json, sys
title, src, out = sys.argv[1:4]
try:
    data = json.load(open(src))
except (OSError, ValueError):
    sys.exit(0)
lines = [f"## {title}", ""]
for i in data.get("items", []):
    name = i.get("check") or f"{i.get('category')}/{i.get('check')}"
    lines.append(f"- {i['status']} {name}: {i.get('detail', '')}")
open(out, "w").write("\n".join(lines) + "\n")
PYEOF
}

# MARK: The simulator and the build
ensure_sim() {
  [ -n "$SIM" ] && return 0
  if [ -z "$RUNTIME" ]; then
    RUNTIME="$(xcrun simctl list runtimes available | awk '/^iOS/ {print $NF}' | tail -1)"
  fi
  SIM="$(xcrun simctl create "Zero E2E $STAMP" "$DEVICE" "$RUNTIME")"; SIM_CREATED=1
  xcrun simctl boot "$SIM"
  xcrun simctl bootstatus "$SIM" -b >/dev/null
  step PASS simulator "created and booted $DEVICE ($RUNTIME); deleted at the end"
}

build_tests() {
  if [ "$BUILD" = 0 ] && [ -d "$BUILD_DIR/Build/Products" ]; then step SKIP build "reusing $BUILD_DIR"; return 0; fi
  local free; free="$(free_gb)"
  if [ "$free" -lt "$MIN_FREE_GB" ]; then step FAIL build "only ${free} GB free; need ${MIN_FREE_GB} GB"; exit 1; fi
  say "Building the app and the UI tests (xcodegen, then xcodebuild build-for-testing)"
  BUILT=1
  (cd "$IOS" && xcodegen generate --quiet)
  if xcodebuild build-for-testing -project "$IOS/Taffy.xcodeproj" -scheme Taffy -destination "id=$SIM" \
      -derivedDataPath "$BUILD_DIR" COMPILER_INDEX_STORE_ENABLE=NO > "$WORK/build.log" 2>&1; then
    step PASS build "Taffy and TaffyUITests built for the simulator ($(free_gb) GB free after)"
  else
    step FAIL build "$(grep -m3 'error:' "$WORK/build.log" | tr '\n' ' ')"; exit 1
  fi
}

ui_test() {
  local name="$1" mode="$2" server="${3:-}" code="${4:-}" mailbox="${5:-0}"
  mkdir -p "$SHOTS"
  say "Simulator: $name ($mode)"
  if env TEST_RUNNER_ZERO_E2E_MODE="$mode" TEST_RUNNER_ZERO_E2E_SERVER="$server" TEST_RUNNER_ZERO_E2E_CODE="$code" \
      TEST_RUNNER_ZERO_E2E_PERSONA="$(cat "$PERSONA")" TEST_RUNNER_ZERO_E2E_SHOTS="$SHOTS" \
      TEST_RUNNER_ZERO_E2E_MAILBOX="$mailbox" \
      xcodebuild test-without-building -project "$IOS/Taffy.xcodeproj" -scheme Taffy -destination "id=$SIM" \
      -derivedDataPath "$BUILD_DIR" -only-testing:"TaffyUITests/OnboardingE2ETests/$name" \
      -resultBundlePath "$WORK/$name.xcresult" > "$WORK/$name.log" 2>&1; then
    step PASS "$name" "$(ui_summary "$name" "$mode")"
  else
    step FAIL "$name" "$(ui_summary "$name" "$mode"); $(grep -m2 -E 'error:|failed' "$WORK/$name.log" | tr '\n' ' ' | cut -c1-400)"
  fi
}

ui_summary() {
  local sub; case "$1" in testSampleHome) sub=sample-home ;; *) sub="$2" ;; esac
  "$PY" - "$SHOTS/$sub/ui-result.json" <<'PYEOF'
import json, sys
try:
    r = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    print("no result file from the UI test"); sys.exit(0)
s = f"screens: {', '.join(r.get('screens', [])) or 'none recorded'}"
if r.get("picked"): s += f"; picked {', '.join(r['picked'])}"
if r.get("notes"): s += f"; notes: {' / '.join(r['notes'])}"
print(s)
PYEOF
}

# MARK: Pairing as the harness
pair_harness() {
  "$PY" - "$HARNESS" "$1" "$2" "$TOKEN_FILE" <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from hubapi import Hub
hub = Hub(sys.argv[2])
hub.pair(sys.argv[3], "Zero E2E harness")
hub.save_token(sys.argv[4])
print(hub.new_code())
PYEOF
}

assertions() {
  local hub="$1"; shift
  say "Hub-state assertions"
  local args=(--hub "$hub" --token-file "$TOKEN_FILE" --json "$WORK/assert.json" --persona "$PERSONA")
  [ "$INBOX" = 1 ] && args+=(--ask-inbox)
  if "$PY" "$HARNESS/assert_state.py" "${args[@]}" "$@" > "$WORK/assert.log" 2>&1; then
    step PASS assertions "$(tail -1 "$WORK/assert.log")"
  else
    step FAIL assertions "$(tail -1 "$WORK/assert.log"); failing: $(grep '^FAIL' "$WORK/assert.log" | cut -c7- | paste -sd';' - | cut -c1-500)"
  fi
  section_from_json "Hub-State Assertions" "$WORK/assert.json" "$WORK/2-assert.result.md"
}

fresh_home_checks() {
  local hub="$1"; shift
  if "$PY" "$HARNESS/live_checks.py" --hub "$hub" --token-file "$TOKEN_FILE" --json "$WORK/fresh.json" "$@" \
      > "$WORK/fresh.log" 2>&1; then
    step PASS fresh_home "$(tail -1 "$WORK/fresh.log")"
  else
    step WARN fresh_home "$(tail -1 "$WORK/fresh.log"); $(grep '^FAIL' "$WORK/fresh.log" | cut -c7- | paste -sd';' - | cut -c1-400)"
  fi
  section_from_json "Fresh Home, Before Onboarding" "$WORK/fresh.json" "$WORK/1-fresh.result.md"
}

# MARK: Modes
run_fixture() {
  ensure_sim; build_tests
  ui_test testStrangerOnboarding fixture
  [ "$SAMPLE" = 1 ] && ui_test testSampleHome fixture
}

start_fixture_hub() {
  local port; port="$(local_port_for "$TEST_HUB_PORT")"
  mkdir -p "$WORK/home"
  "$PY" "$HARNESS/fixture_hub.py" --home "$WORK/home" --state "$WORK/hub.json" --port "$port" > "$WORK/hub.log" 2>&1 &
  PIDS+=("$!")
  for _ in $(seq 1 60); do [ -s "$WORK/hub.json" ] && break; sleep 0.5; done
  for _ in $(seq 1 40); do curl -fsS "http://127.0.0.1:$port/api/health" >/dev/null 2>&1 && break; sleep 0.5; done
  if ! curl -fsS "http://127.0.0.1:$port/api/health" >/dev/null 2>&1; then
    step FAIL fixture_hub "did not start: $(tail -3 "$WORK/hub.log" | tr '\n' ' ')"; exit 1
  fi
  HUB_URL="http://127.0.0.1:$port"
  HUB_CODE="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["code"])' "$WORK/hub.json")"
  step PASS fixture_hub "the hub from this repo on a temporary home (shape test), fake Hermes, port $port"
}

run_dry() {
  say "Dry mode: fixture hub on this Mac"
  if [ "$MATRIX" = 1 ]; then
    if "$PY" "$HARNESS/matrix.py" --json "$WORK/matrix.json" > "$WORK/matrix.log" 2>&1; then
      step PASS matrix "$(grep -E '^(none|colin|riley) ' "$WORK/matrix.log" | paste -sd';' - | sed 's/;/; /g')"
    else
      step FAIL matrix "$(tail -4 "$WORK/matrix.log" | tr '\n' ' ')"
    fi
  fi
  if "$PY" "$HARNESS/isolation_probe.py" --self-test > "$WORK/iso-self.log" 2>&1; then
    step PASS isolation_selftest "$(grep 'open home' "$WORK/iso-self.log")"
  else
    step FAIL isolation_selftest "$(tail -3 "$WORK/iso-self.log" | tr '\n' ' ')"
  fi
  start_fixture_hub
  local app_code; app_code="$(pair_harness "$HUB_URL" "$HUB_CODE")"
  step PASS pairing "the harness paired and minted a code for the app"
  fresh_home_checks "$HUB_URL" --home-json "$WORK/home/taffy/home.json" --probe-pairing
  if [ "$MODE" = headless ]; then
    if "$PY" "$HARNESS/simulate_app.py" --hub "$HUB_URL" --code "$app_code" > "$WORK/app.log" 2>&1; then
      step PASS headless_app "$(paste -sd';' "$WORK/app.log" | sed 's/;/; /g')"
    else
      step FAIL headless_app "$(tail -3 "$WORK/app.log" | tr '\n' ' ')"
    fi
  else
    ensure_sim; build_tests
    ui_test testStrangerOnboarding hub "$HUB_URL" "$app_code" 1
  fi
  assertions "$HUB_URL" --home-dir "$WORK/home" --expect-mailbox
  if "$PY" "$HARNESS/isolation_probe.py" --network-only --victim-hub "${HUB_URL##*:}" > "$WORK/iso.log" 2>&1; then
    step PASS isolation_network "the fixture hub refuses every probe without a token"
  else
    step FAIL isolation_network "$(grep '^FAIL' "$WORK/iso.log" | paste -sd';' -)"
  fi
  if [ "$MODE" = dry ] && [ "$SAMPLE" = 1 ]; then ui_test testSampleHome fixture; fi
}

run_live() {
  say "Live mode: the test home on $HOST"
  if ! remote "test -x $ZERO_BIN/zero-home-test && test -x $ZERO_BIN/zero-home-check && test -r /srv/zero/homes.json" > "$WORK/which.log" 2>&1; then
    step FAIL preflight "$ZERO_BIN/zero-home-test, zero-home-check or /srv/zero/homes.json is missing on $HOST (run the wizard's host and test-home steps first)"
    exit 1
  fi
  remote "cat /srv/zero/homes.json" > "$WORK/homes.json"
  read -r TEST_HUB_PORT TEST_HERMES_PORT TEST_PREVIEW TEST_USER < <("$PY" - "$WORK/homes.json" <<'PYEOF'
import json, sys
h = json.load(open(sys.argv[1])).get("homes", {}).get("test") or {}
p = h.get("ports", {})
print(p.get("hub", 8652), p.get("hermes_api", 8644), p.get("preview_base", 18500), h.get("user", "zero-test"))
PYEOF
)
  step PASS registry "the test home is $TEST_USER: hub $TEST_HUB_PORT, Hermes API $TEST_HERMES_PORT, previews from $TEST_PREVIEW"

  local seed_remote="/tmp/zero-e2e-seed-$STAMP.json"
  "$PY" -c 'import json,sys; m=json.load(open(sys.argv[1]))["messages"]; json.dump([{k: x[k] for k in ("from","subject","body")} for x in m], open(sys.argv[2],"w"), indent=1)' \
    "$HARNESS/greenmail/messages.json" "$WORK/seed.json"
  scp -q -o BatchMode=yes "$WORK/seed.json" "$HOST:$seed_remote" && ssh -o BatchMode=yes "$HOST" "chmod 644 $seed_remote"
  if ! remote "$ZERO_BIN/zero-home-test up --seed $seed_remote" > "$WORK/up.log" 2>&1; then
    HOME_UP=1
    step FAIL home_up "zero-home-test up failed: $(tail -3 "$WORK/up.log" | tr '\n' ' ')"; exit 1
  fi
  HOME_UP=1
  ssh -o BatchMode=yes "$HOST" "rm -f $seed_remote" || true
  local code imap smtp seeded
  code="$(grep -iE '^\s*code:' "$WORK/up.log" | tail -1 | grep -Eo '[A-HJ-NP-Z2-9]{4}-?[A-HJ-NP-Z2-9]{4}' | tail -1 || true)"
  imap="$(grep -Eo 'IMAP 127\.0\.0\.1:[0-9]+' "$WORK/up.log" | tail -1 | grep -Eo '[0-9]+$' || true)"
  smtp="$(grep -Eo 'SMTP 127\.0\.0\.1:[0-9]+' "$WORK/up.log" | tail -1 | grep -Eo '[0-9]+$' || true)"
  seeded="$(grep -Eo 'Seeded [0-9]+ messages' "$WORK/up.log" | tail -1 || true)"
  imap="${imap:-$((TEST_PREVIEW + 51))}"; smtp="${smtp:-$((TEST_PREVIEW + 50))}"
  if [ -z "$code" ]; then step FAIL home_up "no pairing code in zero-home-test up's output"; exit 1; fi
  step PASS home_up "zero-home-test up printed a pairing code; ${seeded:-mail was seeded on an earlier run}; GreenMail IMAP $imap, SMTP $smtp"
  "$PY" -c 'import json,sys; p=json.load(open(sys.argv[1])); p["mailbox"].update(imap_port=int(sys.argv[3]), smtp_port=int(sys.argv[4])); json.dump(p, open(sys.argv[2],"w"))' \
    "$HARNESS/persona.json" "$WORK/persona.json" "$imap" "$smtp"
  PERSONA="$WORK/persona.json"

  local lport; lport="$(local_port_for "$TEST_HUB_PORT")"
  ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -N -L "$lport:127.0.0.1:$TEST_HUB_PORT" "$HOST" & PIDS+=("$!")
  for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:$lport/api/health" >/dev/null 2>&1 && break; sleep 1; done
  HUB_URL="http://127.0.0.1:$lport"
  if ! curl -fsS "$HUB_URL/api/health" >/dev/null 2>&1; then step FAIL tunnel "the test home's hub does not answer through the tunnel"; exit 1; fi
  step PASS tunnel "ssh -L $lport:127.0.0.1:$TEST_HUB_PORT $HOST"

  local app_code; app_code="$(pair_harness "$HUB_URL" "$code")"
  step PASS pairing "the harness paired with the test home and minted a code for the app"
  fresh_home_checks "$HUB_URL" --home-shape test
  ensure_sim; build_tests
  ui_test testStrangerOnboarding hub "$HUB_URL" "$app_code" 1
  assertions "$HUB_URL" --home-shape test --expect-mailbox
  [ "$SAMPLE" = 1 ] && ui_test testSampleHome fixture

  say "zero-home-check test"
  if remote "$ZERO_BIN/zero-home-check test" > "$WORK/check.log" 2>&1; then
    step PASS zero_home_check "$(grep -cE '^\s*PASS' "$WORK/check.log") passed, $(grep -cE '^\s*SKIP' "$WORK/check.log") skipped"
  else
    step FAIL zero_home_check "$(grep -E '^\s*FAIL' "$WORK/check.log" | sed 's/^ *//' | paste -sd';' - | cut -c1-500)"
  fi
  { echo "## zero-home-check test"; echo; echo '```'; cat "$WORK/check.log"; echo '```'; } > "$WORK/3-check.result.md"
  isolation_live
}

isolation_live() {
  say "Isolation probes"
  if ssh -o BatchMode=yes "$HOST" "python3 - --victim-home /home/$TEST_USER --victim-hub $TEST_HUB_PORT --victim-hermes $TEST_HERMES_PORT" \
      < "$HARNESS/isolation_probe.py" > "$WORK/iso-colin.log" 2>&1; then
    step PASS isolation_colin_to_test "$(tail -1 "$WORK/iso-colin.log")"
  else
    step FAIL isolation_colin_to_test "$(grep '^FAIL' "$WORK/iso-colin.log" | paste -sd';' - | cut -c1-500)"
  fi
  if ssh -o BatchMode=yes "$HOST" "sudo -n -u $TEST_USER true" >/dev/null 2>&1; then
    if ssh -o BatchMode=yes "$HOST" "sudo -n -u $TEST_USER python3 - --victim-home /home/jdoe --victim-hub $COLIN_HUB_PORT --victim-hermes $COLIN_HERMES_PORT --tally $TALLY_PORT --victim-uid 1000" \
        < "$HARNESS/isolation_probe.py" > "$WORK/iso-test.log" 2>&1; then
      step PASS isolation_test_to_colin "$(tail -1 "$WORK/iso-test.log")"
    else
      step FAIL isolation_test_to_colin "$(grep '^FAIL' "$WORK/iso-test.log" | paste -sd';' - | cut -c1-500)"
    fi
  else
    step SKIP isolation_test_to_colin "no password-free way to run as $TEST_USER; zero-home-check covered the owner's .taffy, taffy, .config, sign-ins, Docker socket, hub and Hermes API from the home's side"
  fi
}

run_colin_readonly() {
  say "Read-only checks of Colin's live hub on $HOST"
  local lport; lport="$(free_port)"
  ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -N -L "$lport:127.0.0.1:$COLIN_HUB_PORT" "$HOST" & PIDS+=("$!")
  for _ in $(seq 1 20); do curl -fsS "http://127.0.0.1:$lport/api/health" >/dev/null 2>&1 && break; sleep 1; done
  if "$PY" "$HARNESS/live_checks.py" --hub "http://127.0.0.1:$lport" --home-shape colin --json "$WORK/colin.json" > "$WORK/colin.log" 2>&1; then
    step PASS colin_hub "$(tail -1 "$WORK/colin.log") (no token, GET only)"
  else
    step FAIL colin_hub "$(grep '^FAIL' "$WORK/colin.log" | paste -sd';' -)"
  fi
  section_from_json "Colin's Hub, Read-Only" "$WORK/colin.json" "$WORK/1-colin.result.md"
  if ssh -o BatchMode=yes "$HOST" "python3 - --network-only --victim-hub $COLIN_HUB_PORT --victim-hermes $COLIN_HERMES_PORT --tally $TALLY_PORT" \
      < "$HARNESS/isolation_probe.py" > "$WORK/iso.log" 2>&1; then
    step PASS loopback_refusals "$(tail -1 "$WORK/iso.log") on $HOST's loopback (hub, Hermes API, Tally; no credentials)"
  else
    step FAIL loopback_refusals "$(grep '^FAIL' "$WORK/iso.log" | paste -sd';' -)"
  fi
}

case "$MODE" in
  fixture) run_fixture ;;
  dry|headless) run_dry ;;
  live) run_live ;;
  colin-readonly) run_colin_readonly ;;
esac
[ "$FAILED" = 0 ] && say "Passed" || say "FAILED (see the result file)"
exit "$FAILED"
