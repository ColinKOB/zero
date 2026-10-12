#!/bin/sh
# Compiles the app's pure logic with its checks and runs them on this Mac (no simulator, no signing):
# the chat's sent-message logic (Model/ChatSync.swift) and the Mac approval signature round trip (Shared/MacGrant.swift).
here="$(cd "$(dirname "$0")" && pwd)"
dir="$(mktemp -d)"
status=0
swiftc -O -o "$dir/chatsync-tests" "$here/../Taffy/Model/ChatSync.swift" "$here/main.swift" -module-name ChatSyncTests 2>&1 \
  && "$dir/chatsync-tests" || status=1
swiftc -O -o "$dir/macgrant-tests" "$here/../Shared/MacGrant.swift" "$here/MacGrant/main.swift" -module-name MacGrantTests 2>&1 \
  && "$dir/macgrant-tests" || status=1
# Certificate pinning (Model/HubTrust.swift), offline checks; LogicTests/HubTrust/main.swift also takes a live home.
swiftc -O -o "$dir/hubtrust-tests" "$here/../Taffy/Model/HubTrust.swift" "$here/../Taffy/Model/PairLink.swift" "$here/HubTrust/main.swift" \
  -module-name HubTrustTests 2>&1 && "$dir/hubtrust-tests" || status=1
rm -rf "$dir"
exit $status
