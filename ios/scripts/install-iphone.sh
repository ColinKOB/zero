#!/bin/zsh
# Builds Taffy for one iPhone and installs it there, and only there.
#
#   ios/scripts/install-iphone.sh                      the default phone (Colin's, below)
#   ios/scripts/install-iphone.sh --device "<name>"    another phone, by its name as Finder shows it
#   ios/scripts/install-iphone.sh --device <id>        another phone, by its CoreDevice id or UDID
#
# Several family iPhones are on this developer account (two are iPhone 14 Pro), so the phone is always named
# exactly: a name must match one connected device and nothing else, or nothing is installed. Each install gets a
# new build number, shown in Settings.
set -euo pipefail

DEFAULT_PHONE="00000000-0000-0000-0000-000000000000"   # set this to your own iPhone's CoreDevice id
WANT="$DEFAULT_PHONE"
while (( $# > 0 )); do
  case "$1" in
    --device) WANT="${2:-}"; shift 2 ;;
    --device=*) WANT="${1#--device=}"; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1. Use --device \"<phone name or id>\"."; exit 2 ;;
  esac
done
[[ -n "$WANT" ]] || { echo "Say which phone: --device \"<phone name or id>\"."; exit 2; }
cd "$(dirname "$0")/.."

# Find exactly one connected phone whose name, CoreDevice id or UDID is the one asked for.
LIST=$(mktemp -t zero-devices)
trap 'rm -f "$LIST"' EXIT
xcrun devicectl list devices --json-output "$LIST" >/dev/null 2>&1 || true
PHONE=$(python3 -I - "$LIST" "$WANT" <<'PY'
import json, sys
path, want = sys.argv[1], sys.argv[2]
try:
    devices = json.load(open(path)).get("result", {}).get("devices", [])
except Exception:
    devices = []
hits = []
for d in devices:
    props, hw = d.get("deviceProperties", {}), d.get("hardwareProperties", {})
    ids = {d.get("identifier", ""), hw.get("udid", ""), props.get("name", "")}
    if want in ids:
        hits.append(d.get("identifier", ""))
print(hits[0] if len(hits) == 1 else ("MANY" if hits else ""))
PY
)
if [[ "$PHONE" == "MANY" ]]; then
  echo "More than one phone matches \"$WANT\". Use its CoreDevice id from: xcrun devicectl list devices"
  exit 1
fi
if [[ -z "$PHONE" ]] || ! xcrun devicectl list devices 2>/dev/null | grep "$PHONE" | grep -qwE "connected|available"; then
  echo "That iPhone isn't connected. Plug it in or put it on the same Wi-Fi, unlock it, and try again."
  exit 1
fi

BUILD=$(git rev-list --count HEAD)
xcodegen generate >/dev/null
xcodebuild -project Taffy.xcodeproj -scheme Taffy -configuration Debug \
  -destination "generic/platform=iOS" -derivedDataPath build/iphone \
  -allowProvisioningUpdates CURRENT_PROJECT_VERSION="$BUILD" build | grep -E "error:|BUILD (SUCCEEDED|FAILED)"

xcrun devicectl device install app --device "$PHONE" build/iphone/Build/Products/Debug-iphoneos/Taffy.app
echo "Installed build $BUILD on the iPhone $PHONE."
