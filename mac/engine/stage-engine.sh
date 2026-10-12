#!/bin/bash
# Copies what Zero's engine installer needs into one folder: Zero.app/Contents/Resources/Engine in the Mac build
# (the Xcode build phase in ios/project.yml calls this), or any folder for a test run from Terminal.
#
#   mac/engine/stage-engine.sh <dest>
#
# The result is small (the hub, the taffy-tools plugin, the home renderer and alien's Hermes patches). Hermes, Python
# and the packages are downloaded by `zero-engine install` on the person's Mac, pinned and checked by hash.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
dest="${1:?usage: stage-engine.sh <dest>}"

rm -rf "$dest"
mkdir -p "$dest/src/hub" "$dest/src/plugins" "$dest/src/lib" "$dest/src/shared" "$dest/src/patches"
cp "$here/zero-engine" "$here/zero_engine.py" "$dest/"
chmod 755 "$dest/zero-engine"
rsync -a --exclude __pycache__ --exclude tests --exclude '*.pyc' "$repo/hub/taffy_hub" "$dest/src/hub/"
rsync -a --exclude __pycache__ --exclude tests --exclude '*.pyc' "$repo/alien/plugins/taffy-tools" "$dest/src/plugins/"
cp "$repo/alien/bin/zero-home-lib.py" "$repo/alien/bin/set-toolsets.py" "$here/tls_proxy.py" "$dest/src/lib/"
cp -R "$repo/alien/bin/zero-home-templates" "$dest/src/lib/"
cp "$repo/alien/shared/zip-templates.json" "$dest/src/shared/"
cp "$repo"/alien/patches/*.patch "$dest/src/patches/"
echo "staged the engine installer in $dest"
