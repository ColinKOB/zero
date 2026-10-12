#!/bin/zsh
# Puts the GitHub token on the clipboard into Builder's own secrets on alien (~/.taffy/profiles/builder/.env as
# GH_TOKEN) and forwards it into Builder's container only (docker_forward_env), then restarts the Zips when idle.
# The token is never printed. Run: tools/install-builder-github-token.sh
set -euo pipefail
tok=$(pbpaste | tr -d '[:space:]')
[[ "$tok" == ghp_* || "$tok" == github_pat_* ]] || { echo "The clipboard does not hold a GitHub token."; exit 1; }
login=$(curl -sS -H "Authorization: Bearer $tok" https://api.github.com/user | python3 -c "import sys,json;print(json.load(sys.stdin).get('login',''))")
[[ -n "$login" ]] || { echo "GitHub did not accept that token."; exit 1; }
printf '%s' "$tok" | ssh alien 'set -e; f=~/.taffy/profiles/builder/.env; cp -a "$f" "$f.bak-$(date +%Y%m%d-%H%M%S)"; t=$(cat);
  grep -v "^GH_TOKEN=" "$f" > "$f.new" || true; printf "GH_TOKEN=%s\n" "$t" >> "$f.new"; chmod 600 "$f.new"; mv "$f.new" "$f"
  c=~/.taffy/profiles/builder/config.yaml; cp -a "$c" "$c.bak-$(date +%Y%m%d-%H%M%S)"
  sed -i "s/^  docker_forward_env: \[\]$/  docker_forward_env:\n  - GH_TOKEN/" "$c"; grep -A1 "docker_forward_env" "$c"'
pbcopy < /dev/null
echo "Saved for Builder (GitHub account $login). Clipboard cleared."
