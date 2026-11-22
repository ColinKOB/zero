""























import json
import subprocess
import sys
import time

from . import config, home, store


def _busy() -> dict | None:
    ""
    try:
        data = json.loads((config.HUB_DIR / "busy.json").read_text())
    except (OSError, ValueError):
        return None
    return data if time.time() - data.get("at", 0) < 15 else None


def restart(wait: float, force: bool, unit: str = "taffy-hub") -> int:
    if unit not in ("taffy-hub", "taffy-gateway"):
        print(f"unknown service: {unit}")
        return 1
    name = "gateway" if unit == "taffy-gateway" else "hub"


    keys = ("calls", "queued", "mac", "jobs", "turns") if unit == "taffy-gateway" else ("calls", "queued", "mac")
    deadline = time.time() + wait
    while True:
        b = _busy()
        if b is None or not any(b.get(k) for k in keys):
            break
        if time.time() >= deadline:
            what = ", ".join(f"{k}: {b[k]}" for k in keys if b.get(k))
            if not force:
                print(f"{name} still busy after {wait:g}s ({what}); not restarting. Use --force to restart anyway.")
                return 1
            print(f"{name} still busy ({what}); restarting anyway")
            break
        time.sleep(2)
    subprocess.run(["systemctl", "--user", "restart", unit], check=True)
    print(f"{name} restarted")
    return 0


def pair_link(server: str, code: str, fp: str | None = None) -> str:
    ""


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
            from . import pairlookup
            if pairlookup.announce(code):
                print("The code alone also finds this home for 15 minutes.")
    elif cmd == "buzz-summary":
        from . import buzz
        print(json.dumps(buzz.summary(float(sys.argv[2]) if len(sys.argv) > 2 else 7), indent=2))
    elif cmd == "share-usage":
        from . import telemetry
        arg = sys.argv[2].lower() if len(sys.argv) > 2 else ""
        if arg in ("on", "off"):
            telemetry.set_sharing(arg == "on")
        on = telemetry.sharing()
        print("This home shares anonymous usage counts and error reports." if on
              else "This home does not share usage counts or error reports.")
        if arg in ("on", "off"):
            print("Restart the hub for this to take effect: taffy-hub restart")
    elif cmd in ("restart", "restart-gateway"):
        args = sys.argv[2:]
        wait = float(args[args.index("--wait") + 1]) if "--wait" in args else 600
        unit = "taffy-gateway" if cmd == "restart-gateway" else "taffy-hub"
        sys.exit(restart(wait, "--force" in args, unit))
    elif cmd == "deployed":
        from . import deployed
        print(deployed.summarize(deployed.read() or None, "", mention_main=False))
    elif cmd == "refresh-chats":
        from . import freshness
        config.reload_bots()
        done = freshness.check(recheck=True, everything="--all" in sys.argv[2:])
        if done:
            print(f"Refreshed {len(done)} chat(s): {', '.join(done)}. Each one picks up its instructions on its "
                  "next message.")
        else:
            print("Every chat already has the current instructions.")
    elif cmd == "work-mail-rejudge":
        import asyncio
        from . import workmail
        dry = "--dry-run" in sys.argv[2:]
        config.reload_bots()
        rows = asyncio.run(workmail.rejudge_all(apply=not dry))
        for r in rows:
            sender = f"{r['sender_name']} <{r['sender']}>" if r["sender_name"] else r["sender"]
            shown = f"{r['short_title']} / {r['ask']}" + (f" (+{r['folded']} folded in)" if r["folded"] else "")
            print(f"{r['old']} -> {r['new']}\t{r['subject']}\t{sender}\t{r['why']}\t{shown}")
        stays = sum(r["new"] == "needs_you" for r in rows)
        opened = sum(r["old"] == "needs_you" for r in rows)
        print(f"{'Dry run: ' if dry else ''}{opened} mail asks open before, {stays} after. "
              "Cards and other Needs You items are not counted.")
    elif cmd == "cloudflare-adopt":
        from . import apps, cloudflare, keys
        args = sys.argv[2:]
        if len(args) != 4 or args[3] != "--complete-live-project":
            print("Use cloudflare-adopt ACCOUNT ZIP FOLDER --complete-live-project only with the complete "
                  "project that is currently online.")
            sys.exit(1)
        account, bot_id, folder, _ = args
        try:
            apps._need("cloudflare")
            out = cloudflare.adopt_project(apps._load("cloudflare"),
                                           {"account": account, "folder": folder}, bot_id)
        except (apps.AppError, keys.KeyAppError) as e:
            print(str(e))
            sys.exit(1)
        print(out["message"])
    elif cmd == "undo-night":

        if len(sys.argv) != 4:
            print("Use undo-night ZIP NIGHT, for example: taffy-hub undo-night default 2026-10-22")
            sys.exit(1)
        from . import consolidate
        n = consolidate.undo_night(sys.argv[2], sys.argv[3])
        print(f"Undid {n} change(s). That night won't be read again." if n else
              "No changes from that night to undo.")
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
              "taffy-hub restart-gateway [--wait SECONDS] [--force] | taffy-hub deployed | "
              "taffy-hub refresh-chats [--all] | taffy-hub migrate-registry [--dry-run] | "
              "taffy-hub buzz-summary [days] | "
              "taffy-hub cloudflare-adopt ACCOUNT ZIP FOLDER --complete-live-project")


if __name__ == "__main__":
    main()
