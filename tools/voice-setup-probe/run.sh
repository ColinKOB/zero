#!/bin/zsh
# Opens the test browser at a URL and runs the guide harness over it.
# Usage: run.sh <shots-dir> <url> [harness flags...]   (needs build.sh first, and the practice server for local pages:
#   cd practice && python3 -m http.server 8790 --bind 127.0.0.1)
shots=$1; url=$2; shift 2
rm -rf $shots
/tmp/zero-guide-bin/testbrowser "$url" 40 60 1240 820 > /dev/null 2>&1 &
tb=$!
sleep 3
/tmp/zero-guide-bin/harness --pid $tb --start "$url" --shots $shots "$@"
code=$?
kill $tb 2>/dev/null
exit $code
