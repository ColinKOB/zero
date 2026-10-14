#!/bin/zsh
set -uo pipefail
cd "$(dirname "$0")/.."
xcodegen generate >/dev/null
log=$(mktemp)
if ! xcodebuild -project Taffy.xcodeproj -scheme Taffy -destination 'platform=macOS,variant=Mac Catalyst' \
     -derivedDataPath build -configuration Release -allowProvisioningUpdates build >"$log" 2>&1; then
  grep -E "error:" "$log" | head -5; echo "BUILD FAILED: the installed Mac app was left as it was."; rm -f "$log"; exit 1
fi
rm -f "$log"
app=build/Build/Products/Release-maccatalyst/Taffy.app
[ -d "$app" ] || { echo "No built app found; the installed Mac app was left as it was."; exit 1; }
# Quit the running copy, or the old app keeps running and the new one never opens (Oct 10: a copy from 3:47 a.m.
# ran all day through five installs). By bundle id, since the app's name is Zero; then wait, then make sure.
bin="/Applications/(Zero|Taffy).app/Contents/MacOS/Taffy"
osascript -e 'tell application id "com.example.Taffy" to quit' 2>/dev/null
for _ in {1..20}; do pgrep -f "$bin" >/dev/null || break; sleep 0.5; done
if pgrep -f "$bin" >/dev/null; then pkill -TERM -f "$bin"; sleep 2; fi
if pgrep -f "$bin" >/dev/null; then echo "The running Mac app would not quit; quit it and run this again."; exit 1; fi
rm -rf /Applications/Zero.app.previous
[ -d /Applications/Zero.app ] && mv /Applications/Zero.app /Applications/Zero.app.previous
[ -d /Applications/Taffy.app ] && mv /Applications/Taffy.app /Applications/Zero.app.previous.taffy
ditto "$app" /Applications/Zero.app && open /Applications/Zero.app \
  && rm -rf /Applications/Zero.app.previous /Applications/Zero.app.previous.taffy && echo "Installed the Mac app."
