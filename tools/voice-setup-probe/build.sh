#!/bin/zsh
# Builds the test browser and the guide harness into /tmp/zero-guide-bin. The harness compiles Zero's real guide code.
set -e
here=${0:A:h}; ios=$here/../../ios; out=/tmp/zero-guide-bin
mkdir -p $out
xcrun swiftc -O -swift-version 5 -target arm64-apple-macos14.0 $here/testbrowser.swift -o $out/testbrowser
cp $here/harness.swift $out/main.swift
xcrun swiftc -O -swift-version 5 -target arm64-apple-macos14.0 $ios/Shared/VoiceGuide/*.swift $ios/MacBridge/VoiceGuide.swift $ios/MacBridge/GuideOverlayViews.swift $out/main.swift -o $out/harness
# The same browser as an app bundle, so the guide can open pages in it like it opens the person's browser.
app=$out/ZeroTestBrowser.app
mkdir -p $app/Contents/MacOS
cp $out/testbrowser $app/Contents/MacOS/ZeroTestBrowser
cat > $app/Contents/Info.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>ZeroTestBrowser</string>
<key>CFBundleIdentifier</key><string>com.example.zero.testbrowser</string>
<key>CFBundleName</key><string>Zero Test Browser</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>NSPrincipalClass</key><string>NSApplication</string>
</dict></plist>
PLIST
codesign -s - --force $app >/dev/null 2>&1
echo built $out
