#!/usr/bin/env bash
# Creates one private Docker network per containerized Zip in this user's rootless daemon (Colin's decision,
# Oct 8): no Zip's container can reach another's. Each profile's config.yaml then has
#   docker_extra_args: [..., "--network", "zip-<bot>"]
# Networks persist across daemon restarts, so this runs once per Zip (it is safe to run again).
# Traffic between these networks is routed inside the rootless namespace, so it passes FORWARD, where
# DOCKER-USER (alien/bin/zip-egress-rules) rejects 172.16.0.0/12 and Docker's own isolation drops it as well.
#
# Subnets come from the home: <ports.docker_subnet_base in ~/taffy/home.json>.<slot>.0/24, with each Zip's
# slot in ~/taffy/state/zero-slots.json (written by zero-home-render). The owner's home keeps the subnets
# it has had since Oct 8 when it has no slots file (builder .0, clients .1, school .2, career .3, money .4).
#
# Usage: zip-networks.sh [bot ...]     (default: every Zip in the slots file)
set -euo pipefail
export DOCKER_HOST="${DOCKER_HOST:-unix:///run/user/$(id -u)/docker.sock}"
home_json="${ZERO_HOME:-$HOME/taffy/home.json}"
slots_json="$HOME/taffy/state/zero-slots.json"

table=$(python3 - "$home_json" "$slots_json" <<'PY'
import json, sys
try:
    base = json.load(open(sys.argv[1]))["ports"]["docker_subnet_base"]
except (OSError, ValueError, KeyError):
    base = "172.30"
try:
    slots = json.load(open(sys.argv[2]))
except (OSError, ValueError):
    slots = {"builder": 0, "clients": 1, "school": 2, "career": 3, "money": 4}
for bot, slot in sorted(slots.items(), key=lambda kv: kv[1]):
    print(f"{bot} {base}.{int(slot)}.0/24")
PY
)
declare -A SUBNET=()
while read -r bot net; do
  [ -n "$bot" ] && SUBNET[$bot]=$net
done <<<"$table"

bots=("$@")
[ ${#bots[@]} -gt 0 ] || bots=("${!SUBNET[@]}")

for bot in "${bots[@]}"; do
  subnet="${SUBNET[$bot]:-}"
  [ -n "$subnet" ] || { echo "zip-networks: no subnet for $bot (render the home first)" >&2; exit 1; }
  name="zip-$bot"
  if docker network inspect "$name" >/dev/null 2>&1; then
    echo "$name exists ($(docker network inspect -f '{{(index .IPAM.Config 0).Subnet}}' "$name"))"
    continue
  fi
  docker network create --driver bridge --subnet "$subnet" --ipv6=false \
    --opt com.docker.network.bridge.name="$name" \
    --label taffy.zip="$bot" "$name" >/dev/null
  echo "$name created ($subnet)"
done
