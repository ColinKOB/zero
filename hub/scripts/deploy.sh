#!/bin/sh
# Copies the hub to a home on alien, runs its tests there, and restarts it at a quiet moment (no live call, queued
# message or Mac task).
#
#   deploy.sh                    Colin's home, as before
#   deploy.sh colin [--force]    the same, by name
#   deploy.sh riley [--force]    another home (user zero-riley); see below
#   deploy.sh all [--force]      every home listed in /srv/zero/homes.json on alien (Colin's first)
#
# --force restarts even if the hub is still busy after the wait.
#
# Tests run once per version of the code: a hash of the copied hub and the settings shape it was tested with is kept
# in ~/taffy/hub-tested on alien after a pass, and the same code for the same shape skips straight to the restart.
# Changed code always gets the full suite. --retest runs it anyway.
#
# Paused homes (PAUSED below) are never deployed or tested, by name or by `all`.
#
# Colin's home is his own user, so the copy goes straight into ~/taffy/hub-src. Another home belongs to its own Linux
# user, which Colin's user cannot write to. Its copy is staged in /tmp on alien, the tests run there with the
# Riley-shaped sample settings, and the home's own `zero-home-deploy` (installed by the home scripts, allowed by
# /etc/sudoers.d/zero-homes) moves it into place as that user and restarts through that home's `taffy-hub restart`.
set -e
cd "$(dirname "$0")/.."

PAUSED="riley"

retest=0
for a in "$@"; do [ "$a" = --retest ] && retest=1; done
args=""
for a in "$@"; do [ "$a" = --retest ] || args="$args $a"; done
set -- $args

run_tests() {
  dir=$1; shape=$2; log=$3; env=$4
  ssh alien "cd $dir && sum=\$(find . -type f ! -path '*/__pycache__/*' -print0 | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16)-$shape;
    if [ $retest = 0 ] && grep -qx \"\$sum\" ~/taffy/hub-tested 2>/dev/null; then echo \"same code already passed its tests ($shape); skipping them\"; exit 0; fi;
    $env ~/taffy/.venv/bin/python -m unittest discover -b -s tests >$log 2>&1 || { tail -30 $log; echo 'tests failed; not deploying'; exit 1; };
    tail -1 $log; { echo \"\$sum\"; tail -n 49 ~/taffy/hub-tested 2>/dev/null || true; } > ~/taffy/hub-tested.new && mv ~/taffy/hub-tested.new ~/taffy/hub-tested"
}

home=colin
case "$1" in
  ""|--*) ;;
  *) home=$1; shift ;;
esac

deploy_colin() {
  rsync -a --delete --exclude __pycache__ ./ alien:taffy/hub-src/
  run_tests "~/taffy/hub-src" colin /tmp/hub-tests.log ""
  ssh alien "~/taffy/bin/taffy-hub restart $*"
}

deploy_other() {
  name=$1; shift
  case "$name" in *[!a-z0-9]*|"") echo "not a home name: $name"; exit 1 ;; esac
  case " $PAUSED " in *" $name "*) echo "[$name] paused; not deployed or tested (see PAUSED in deploy.sh)"; return 0 ;; esac
  stage="/tmp/zero-deploy-hub-$name"
  ssh alien "rm -rf $stage && mkdir -p $stage"
  rsync -a --delete --exclude __pycache__ ./ "alien:$stage/"
  run_tests "$stage" other "/tmp/hub-tests-$name.log" "ZERO_HOME=$stage/tests/homes/riley.json"
  if ! ssh alien "command -v zero-home-deploy >/dev/null || test -x /srv/zero/bin/zero-home-deploy"; then
    echo "zero-home-deploy is not installed on alien yet; the copy is staged in $stage but not deployed."
    exit 1
  fi
  ssh alien "sudo -n -u zero-$name \$(command -v zero-home-deploy || echo /srv/zero/bin/zero-home-deploy) hub $stage $*"
}

if [ "$home" = colin ]; then
  deploy_colin "$@"
elif [ "$home" = all ]; then
  deploy_colin "$@"
  homes=$(ssh alien "python3 -c 'import json; hs = json.load(open(\"/srv/zero/homes.json\")).get(\"homes\", {}); print(\" \".join(i for i, e in hs.items() if not e.get(\"owner\") and i != \"colin\"))'") || {
    echo "Could not read /srv/zero/homes.json on alien; deployed Colin's home only."; exit 1; }
  for h in $homes; do
    deploy_other "$h" "$@"
  done
else
  deploy_other "$home" "$@"
fi
