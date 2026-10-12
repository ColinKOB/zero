#!/bin/zsh
# Builds a release of Zero from this checkout, with a new build number:
#   1. iPhone: archive, export for App Store Connect, upload, then (appstore.py) add the build to the
#      "Public Preview" TestFlight group with its public link and submit it for Beta App Review.
#   2. Mac: archive the Mac Catalyst app, export it signed with Developer ID and the hardened runtime, notarize and
#      staple it, wrap it in a DMG with an Applications shortcut, notarize and staple the DMG, and check both with
#      Gatekeeper. The DMG ends up at release/Zero.dmg (gitignored).
#
#   ios/scripts/release.sh               both
#   ios/scripts/release.sh --ios-only    only the iPhone build and TestFlight
#   ios/scripts/release.sh --mac-only    only the Mac DMG
#   ios/scripts/release.sh --no-upload   build everything, but send nothing to Apple (no upload, no notarization)
#
# Exports sign manually with profiles that appstore.py makes through the API, because Xcode's automatic export wants
# cloud signing, which this key is not allowed to use. Everything signs in with the App Store Connect API key in ~/.appstoreconnect/private_keys, so no Apple ID or 2FA.
# The iPhone upload needs the App Store Connect app record for com.example.Taffy, which only the website can
# create. Logs go to ios/build/release-logs/.
set -euo pipefail

DO_IOS=1; DO_MAC=1; UPLOAD=1
for arg in "$@"; do
  case "$arg" in
    --ios-only) DO_MAC=0 ;;
    --mac-only) DO_IOS=0 ;;
    --no-upload) UPLOAD=0 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."            # ios/
IOS_DIR=$PWD
REPO=${IOS_DIR:h}
TEAM=YOUR_TEAM_ID
KEY_ID=YOUR_ASC_KEY_ID
ISSUER=00000000-0000-0000-0000-000000000001
KEY_PATH="$HOME/.appstoreconnect/private_keys/AuthKey_${KEY_ID}.p8"
[[ -f "$KEY_PATH" ]] || { echo "The App Store Connect API key is missing: $KEY_PATH"; exit 1; }
AUTH=(-allowProvisioningUpdates -authenticationKeyPath "$KEY_PATH" -authenticationKeyID "$KEY_ID"
      -authenticationKeyIssuerID "$ISSUER")
NOTARY=(--key "$KEY_PATH" --key-id "$KEY_ID" --issuer "$ISSUER")
# The Developer ID certificate's SHA-1. Its name alone is ambiguous: the keychain lists the same certificate twice.
DEVID=$(security find-identity -v -p codesigning | awk '/Developer ID Application: .*\('"$TEAM"'\)/ {print $2; exit}')

# A new build number every run, increasing over time: the Eastern time, to the minute (e.g. 202610121405).
BUILD=$(TZ=America/New_York date +%Y%m%d%H%M)
OUT="$IOS_DIR/build/release-$BUILD"
LOGS="$IOS_DIR/build/release-logs"
mkdir -p "$OUT" "$LOGS"
echo "Release build $BUILD from $(git -C "$REPO" rev-parse --short HEAD) on $(git -C "$REPO" branch --show-current)."

xcodegen generate >/dev/null

run() {   # run <log name> <command...>: quiet unless it fails
  local log="$LOGS/$BUILD-$1.log"; shift
  if ! "$@" >"$log" 2>&1; then
    grep -E "error:|Error:|FAILED" "$log" | sort -u | head -15 || true
    echo "Failed. Full log: $log"
    exit 1
  fi
}

IOS_STATUS="skipped"
if (( DO_IOS )); then
  echo "iPhone: archiving..."
  run ios-archive xcodebuild -project Taffy.xcodeproj -scheme Taffy -configuration Release \
    -destination 'generic/platform=iOS' -derivedDataPath build/release-dd -archivePath "$OUT/Zero-ios.xcarchive" \
    "${AUTH[@]}" CURRENT_PROJECT_VERSION="$BUILD" archive
  IOS_PROFILES=$(python3 scripts/appstore.py profiles ios)
  cat >"$OUT/ios-export.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>export</string>
  <key>teamID</key><string>$TEAM</string>
  <key>signingStyle</key><string>manual</string>
  <key>signingCertificate</key><string>Apple Distribution</string>
  <key>provisioningProfiles</key><dict>
$IOS_PROFILES
  </dict>
  <key>uploadSymbols</key><true/>
  <key>manageAppVersionAndBuildNumber</key><false/>
</dict></plist>
EOF
  echo "iPhone: exporting for App Store Connect..."
  run ios-export xcodebuild -exportArchive -archivePath "$OUT/Zero-ios.xcarchive" \
    -exportOptionsPlist "$OUT/ios-export.plist" -exportPath "$OUT/ios"
  IPA=$(ls "$OUT"/ios/*.ipa | head -1)
  if (( ! UPLOAD )); then
    IOS_STATUS="exported $IPA (not uploaded)"
  elif ! python3 scripts/appstore.py app-id >/dev/null; then
    IOS_STATUS="NOT UPLOADED: App Store Connect has no app record for com.example.Taffy yet. Create it at
  appstoreconnect.apple.com (Apps, +, New App: iOS, name Zero, bundle ID com.example.Taffy, SKU zero-ios),
  then upload this build with:
  xcrun altool --upload-app -f '$IPA' -t ios --apiKey $KEY_ID --apiIssuer $ISSUER && python3 ios/scripts/appstore.py publish $BUILD"
  else
    echo "iPhone: uploading build $BUILD..."
    run ios-upload xcrun altool --upload-app -f "$IPA" -t ios --apiKey "$KEY_ID" --apiIssuer "$ISSUER"
    echo "iPhone: setting up TestFlight (Apple takes a while to process the build)..."
    python3 scripts/appstore.py publish "$BUILD" | tee "$LOGS/$BUILD-testflight.log"
    IOS_STATUS="uploaded build $BUILD; see TestFlight lines above"
  fi
fi

MAC_STATUS="skipped"
if (( DO_MAC )); then
  [[ -n "$DEVID" ]] || { echo "No Developer ID Application identity for team $TEAM in the keychain."; exit 1; }
  echo "Mac: archiving..."
  run mac-archive xcodebuild -project Taffy.xcodeproj -scheme Taffy -configuration Release \
    -destination 'generic/platform=macOS,variant=Mac Catalyst' -derivedDataPath build/release-mac-dd \
    -archivePath "$OUT/Zero-mac.xcarchive" "${AUTH[@]}" CURRENT_PROJECT_VERSION="$BUILD" ENABLE_HARDENED_RUNTIME=YES archive
  MAC_PROFILES=$(python3 scripts/appstore.py profiles mac)
  cat >"$OUT/mac-export.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>method</key><string>developer-id</string>
  <key>destination</key><string>export</string>
  <key>teamID</key><string>$TEAM</string>
  <key>signingStyle</key><string>manual</string>
  <key>signingCertificate</key><string>Developer ID Application</string>
  <key>provisioningProfiles</key><dict>
$MAC_PROFILES
  </dict>
</dict></plist>
EOF
  echo "Mac: exporting with Developer ID..."
  run mac-export xcodebuild -exportArchive -archivePath "$OUT/Zero-mac.xcarchive" \
    -exportOptionsPlist "$OUT/mac-export.plist" -exportPath "$OUT/mac"
  # The product is Taffy.app; people get it as Zero.app (the bundle's folder name is not part of the signature).
  STAGE="$OUT/dmg"
  rm -rf "$STAGE"; mkdir -p "$STAGE"
  ditto "$OUT/mac/Taffy.app" "$STAGE/Zero.app"
  ln -s /Applications "$STAGE/Applications"
  codesign --verify --deep --strict "$STAGE/Zero.app"
  if (( UPLOAD )); then
    echo "Mac: notarizing the app..."
    ditto -c -k --keepParent "$STAGE/Zero.app" "$OUT/Zero-app.zip"
    run mac-notarize-app xcrun notarytool submit "$OUT/Zero-app.zip" "${NOTARY[@]}" --wait
    grep -q "status: Accepted" "$LOGS/$BUILD-mac-notarize-app.log" || {
      cat "$LOGS/$BUILD-mac-notarize-app.log"; echo "Apple did not accept the app. See: xcrun notarytool log <id>"; exit 1; }
    run mac-staple-app xcrun stapler staple "$STAGE/Zero.app"
  fi
  echo "Mac: making the DMG..."
  DMG="$OUT/Zero.dmg"
  rm -f "$DMG"
  run mac-dmg hdiutil create -volname Zero -srcfolder "$STAGE" -fs HFS+ -format UDZO -ov "$DMG"
  run mac-sign-dmg codesign --force --sign "$DEVID" --timestamp "$DMG"
  if (( UPLOAD )); then
    echo "Mac: notarizing the DMG..."
    run mac-notarize-dmg xcrun notarytool submit "$DMG" "${NOTARY[@]}" --wait
    grep -q "status: Accepted" "$LOGS/$BUILD-mac-notarize-dmg.log" || {
      cat "$LOGS/$BUILD-mac-notarize-dmg.log"; echo "Apple did not accept the DMG."; exit 1; }
    run mac-staple-dmg xcrun stapler staple "$DMG"
    echo "Mac: verifying..."
    spctl -a -vvv -t install "$DMG"
    xcrun stapler validate "$DMG"
    MNT=$(mktemp -d)
    hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$DMG" >/dev/null
    spctl -a -vvv -t exec "$MNT/Zero.app"; xcrun stapler validate "$MNT/Zero.app"
    hdiutil detach "$MNT" >/dev/null
  fi
  mkdir -p "$REPO/release"
  cp "$DMG" "$REPO/release/Zero.dmg"
  MAC_STATUS="$REPO/release/Zero.dmg (build $BUILD$( (( UPLOAD )) || echo ', not notarized'))"
fi

echo
echo "iPhone: $IOS_STATUS"
echo "Mac: $MAC_STATUS"
[[ "$IOS_STATUS" != NOT\ UPLOADED* ]]
