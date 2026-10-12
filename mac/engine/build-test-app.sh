#!/bin/zsh
# Builds the Mac app from this checkout, signed for this Mac's developer account, and puts it at
# "/Users/Shared/Zero Test.app" so another macOS account on this Mac (a fresh test user) can open it.
# It never touches /Applications/Zero.app or the running copy.
#   mac/engine/build-test-app.sh
set -uo pipefail
cd "$(dirname "$0")/../../ios"
xcodegen generate >/dev/null
log=$(mktemp)
if ! xcodebuild -project Taffy.xcodeproj -scheme Taffy -destination 'platform=macOS,variant=Mac Catalyst' \
     -derivedDataPath build/test-app -configuration Release -allowProvisioningUpdates build >"$log" 2>&1; then
  grep -E "error:" "$log" | head -5; echo "BUILD FAILED"; rm -f "$log"; exit 1
fi
rm -f "$log"
app=build/test-app/Build/Products/Release-maccatalyst/Taffy.app
dest="/Users/Shared/Zero Test.app"
rm -rf "$dest"
ditto "$app" "$dest" && chmod -R a+rX "$dest" && echo "Built $dest. Open it from the test account."
