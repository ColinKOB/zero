#!/usr/bin/env bash
# Rootless Docker for the Zip sandbox on alien (stage 1, run 2026-10-08).
# Run over a real SSH login as jdoe. Uses sudo only for the apt
# prerequisites and to mask the rootful units. Changes no firewall, network
# config, other users or protected service. Safe to re-run.
set -euo pipefail
REPO_ALIEN="$(cd "$(dirname "$0")/.." && pwd)"   # this repo's alien/ folder, copied to alien

# 1. A rootful daemon must never start or touch the network.
sudo systemctl mask docker.service docker.socket containerd.service
sudo install -m 0755 -d /etc/apt/keyrings /etc/docker
echo '{"iptables": false, "ip6tables": false, "ip-forward": false, "bridge": "none"}' \
  | sudo tee /etc/docker/daemon.json >/dev/null

# 2. Docker's apt repo for noble (key fingerprint 9DC8 5822 9FC7 DD38 854A E2D8 8D81 803C 0EBF CD88).
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
  docker-ce docker-ce-cli containerd.io docker-ce-rootless-extras docker-buildx-plugin \
  uidmap slirp4netns acl

# 3. Rootless daemon as jdoe (linger is already on).
mkdir -p ~/.config/docker ~/.config/systemd/user/docker.service.d ~/.config/systemd/user/taffy-gateway.service.d ~/taffy/bin
echo '{"dns": ["1.1.1.1", "8.8.8.8"], "log-driver": "local"}' > ~/.config/docker/daemon.json
dockerd-rootless-setuptool.sh install

# 4. Egress rules inside the rootless namespace, and the gateway's DOCKER_HOST.
install -m 0755 "$REPO_ALIEN/bin/zip-egress-rules" ~/taffy/bin/zip-egress-rules
install -m 0644 "$REPO_ALIEN/systemd/docker.service.d/zip-sandbox.conf" ~/.config/systemd/user/docker.service.d/
install -m 0644 "$REPO_ALIEN/systemd/taffy-gateway.service.d/docker.conf" ~/.config/systemd/user/taffy-gateway.service.d/
systemctl --user daemon-reload
systemctl --user enable docker
systemctl --user restart docker

# 5. Check: rootless, limits work, no rootful daemon, no docker0 on the host.
export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock
docker info --format '{{.SecurityOptions}}'
docker run --rm --cpus 0.5 --memory 64m --pids-limit 32 alpine:3 true && docker rmi alpine:3 >/dev/null
systemctl is-active docker.service docker.socket containerd.service || true
ip -br link | grep -i docker && echo "WARNING: docker interface on host" || echo "no docker interface on host"

# 6. The image (new versions get new tags; never overwrite a tag).
# cd ~/taffy/sandbox && docker build -f zip-sandbox.Dockerfile -t taffy/zip-sandbox:2026-10-08-v1 .
# The gateway picks up the drop-in on its next restart, which must happen while the Zips are idle.
