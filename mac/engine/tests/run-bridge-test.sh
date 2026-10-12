#!/bin/bash
# Runs the engine through the Mac bridge, as the app does, in a temporary HOME with test LaunchAgent labels, so a
# real install on this Mac is never touched:
#   mac/engine/tests/run-bridge-test.sh <TaffyMacBridge.bundle> <home> <action> [args...]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
bundle="$1"; home="$2"; shift 2
out=/tmp/zero-bridge-harness
mkdir -p "$out"
if [ ! -x "$out/bridge-harness" ] || [ "$here/bridge-harness.swift" -nt "$out/bridge-harness" ]; then
  xcrun swiftc -O -target arm64-apple-macos14.0 "$here/bridge-harness.swift" -o "$out/bridge-harness"
fi
"$here/../stage-engine.sh" "$out/Engine" >/dev/null
cd "$home"
HOME="$home" ZERO_LABEL="${ZERO_LABEL:-tech.zerotest.bridge}" exec "$out/bridge-harness" "$bundle" "$@"
