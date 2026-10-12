# The test home gateway's own environment (its systemd unit), so a run from the command line uses the same Claude
# broker and the same tool sandbox as a chat does.
set -a
. "$HOME/.config/zero-broker/env"
set +a
export HERMES_HOME="$HOME/.taffy" ZERO_HOME="$HOME/taffy/home.json"
export PATH="$HOME/taffy/bin:$HOME/.local/bin:/srv/zero/bin:/usr/local/bin:/usr/bin:/bin"
export DOCKER_HOST="unix:///run/user/$(id -u)/docker.sock"
