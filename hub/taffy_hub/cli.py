"""Command line helpers for the hub, run on the home's server as the home's user.

`taffy-hub pair [hours]` prints a one-time pairing code, the home's address and a `zero://pair` link for a QR code.
`taffy-hub restart [--wait SECONDS] [--force]` restarts the hub at a quiet moment: it waits until no call
is live, no message is waiting for a busy bot and no Mac task is running, because a restart loses those.
`taffy-hub refresh-chats [--all]` makes every Zip's ongoing chats pick up SOUL.md changes now instead of within two
minutes. Each refreshed chat rebuilds its instructions on its next message. `--all` refreshes every chat, even one
that is already current.
`taffy-hub migrate-registry [--dry-run]` writes the owner's five original Zips into the registry (bots.json), first and
with their colors, symbols and shapes, so the team no longer depends on a built-in list. Run once in Colin's home.
"""

import json
import subprocess
import sys
import time

from . import config, home, store


def _busy() -> dict | None:
    """The hub's busy state, or None when the hub isn't writing it (stopped, or an older version)."""
    try:
        data = json.loads((config.HUB_DIR / "busy.json").read_text())
    except (OSError, ValueError):
        return None
    return data if time.time() - data.get("at", 0) < 15 else None


def restart(wait: float, force: bool) -> int:
    deadline = time.time() + wait
    while True:
        b = _busy()
        if b is None or not (b["calls"] or b["queued"] or b["mac"]):
            break
        if time.time() >= deadline:
            what = ", ".join(f"{k}: {b[k]}" for k in ("calls", "queued", "mac") if b[k])
            if not force:
                print(f"hub still busy after {wait:g}s ({what}); not restarting. Use --force to restart anyway.")
                return 1
            print(f"hub still busy ({what}); restarting anyway")
            break
        time.sleep(2)
    subprocess.run(["systemctl", "--user", "restart", "taffy-hub"], check=True)
    print("hub restarted")
    return 0


def pair_link(server: str, code: str, fp: str | None = None) -> str:
    """The link the app opens (and the QR code holds): zero://pair?server=...&code=...[&fp=...]. fp is the lowercase
    hex SHA-256 of the hub's self-signed certificate (DER), set when the hub serves HTTPS on the local network
    (network.tls_fingerprint in home.json), so the app can pin it."""
    from urllib.parse import urlencode
    q = {"server": server, "code": code}
    if fp:
        q["fp"] = fp
    return "zero://pair?" + urlencode(q)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "pair":
        hours = float(sys.argv[2]) if len(sys.argv) > 2 else 0.25
        code = store.new_pairing_code(int(hours * 3600))
        print(f"Pairing code: {code[:4]}-{code[4:]} (valid for {hours:g} hours)")
        server = str(home.get("network.public_url") or "").strip()
        if server:
            print(f"Server: {server}")
            fp = str(home.get("network.tls_fingerprint") or "").strip().lower() or None
            print(f"Link: {pair_link(server, code, fp)}")
    elif cmd == "restart":
        args = sys.argv[2:]
        wait = float(args[args.index("--wait") + 1]) if "--wait" in args else 600
        sys.exit(restart(wait, "--force" in args))
    elif cmd == "refresh-chats":
        from . import freshness
        config.reload_bots()
        done = freshness.check(recheck=True, everything="--all" in sys.argv[2:])
        if done:
            print(f"Refreshed {len(done)} chat(s): {', '.join(done)}. Each one picks up its instructions on its "
                  "next message.")
        else:
            print("Every chat already has the current instructions.")
    elif cmd == "migrate-registry":
        dry = "--dry-run" in sys.argv[2:]
        before = {e["id"] for e in config._registry_entries()}
        new = config.migrate_registry(write=not dry)
        added = [e["id"] for e in new if e["id"] not in before]
        verb = "Would add" if dry else "Added"
        print(f"{verb} {', '.join(added)} to {config.REGISTRY}." if added else
              f"{config.REGISTRY} already lists every Zip.")
        if not dry:
            config.reload_bots()
            print("Team: " + ", ".join(f"{b.id} ({b.name})" for b in config.BOTS))
    else:
        print("usage: taffy-hub pair [hours] | taffy-hub restart [--wait SECONDS] [--force] | "
              "taffy-hub refresh-chats [--all] | taffy-hub migrate-registry [--dry-run]")


if __name__ == "__main__":
    main()
