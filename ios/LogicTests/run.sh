#!/bin/sh
here="$(cd "$(dirname "$0")" && pwd)"
dir="$(mktemp -d)"
status=0
swiftc -O -o "$dir/chatsync-tests" "$here/../Taffy/Model/ChatSync.swift" "$here/main.swift" -module-name ChatSyncTests 2>&1 \
  && "$dir/chatsync-tests" || status=1
swiftc -O -o "$dir/macgrant-tests" "$here/../Shared/MacGrant.swift" "$here/MacGrant/main.swift" -module-name MacGrantTests 2>&1 \
  && "$dir/macgrant-tests" || status=1
swiftc -O -o "$dir/hubtrust-tests" "$here/../Taffy/Model/HubTrust.swift" "$here/../Taffy/Model/PairLink.swift" "$here/HubTrust/main.swift" \
  -module-name HubTrustTests 2>&1 && "$dir/hubtrust-tests" || status=1
swiftc -O -o "$dir/worklog-tests" "$here/../Taffy/Model/WorkLog.swift" "$here/WorkLog/main.swift" -module-name WorkLogTests 2>&1 \
  && "$dir/worklog-tests" || status=1
rm -rf "$dir"
exit $status
