#!/usr/bin/env python3
"""zip-move.py <bot> <first_port>: moves one Zip onto the docker terminal backend (Zip sandbox plan, stage 2).
Adds the plan's terminal block with its own network zip-<bot> (create it first with zip-networks.sh), sets
bot_desktop.placement: gateway and adds the one SOUL line. Backs up config.yaml and SOUL.md with a timestamp
first. Text edits only, so the rest of each file is unchanged. Restart taffy-gateway afterwards, only while
zip-idle.py says the Zips are idle. Used for Money (18240) and School (18220) on Oct 8.

Kept for the record of the owner's Oct 8 move only. New homes and new Zips get their Docker block from
zero-home-render (alien/bin), which takes the paths, ports, network and time zone from the home's home.json."""
import shutil, sys, time
bot, first = sys.argv[1], int(sys.argv[2])
last = first + 9
base = f"/home/jdoe/.taffy/profiles/{bot}"
ts = time.strftime("%Y%m%d-%H%M%S")
cfg, soul = f"{base}/config.yaml", f"{base}/SOUL.md"
for f in (cfg, soul):
    shutil.copy2(f, f"{f}.bak-{ts}")
print("backups:", f"{cfg}.bak-{ts}", f"{soul}.bak-{ts}")

s = open(cfg).read()
anchor = "terminal:\n  home_mode: real\n"
assert s.count(anchor) == 1 and "backend: docker" not in s, "unexpected terminal block"
block = f"""  backend: docker
  docker_image: taffy/zip-sandbox:2026-10-08-v1
  container_persistent: true
  docker_persist_across_processes: true
  container_cpu: 2
  container_memory: 3072
  docker_network: true
  docker_run_as_host_user: false
  docker_mount_cwd_to_workspace: false
  docker_volumes:
  - /home/jdoe/taffy/work:/home/jdoe/taffy/work:ro
  - /home/jdoe/taffy/work/{bot}:/home/jdoe/taffy/work/{bot}:rw
  - /home/jdoe/taffy/shared:/home/jdoe/taffy/shared:ro
  docker_env:
    HOME: /home/jdoe
    TZ: America/New_York
  docker_forward_env: []
  docker_extra_args:
  - -p
  - 127.0.0.1:{first}-{last}:{first}-{last}
  - --network
  - zip-{bot}
"""
s = s.replace(anchor, anchor + block)
bd = "bot_desktop:\n"
assert s.count(bd) == 1 and "placement:" not in s.split(bd, 1)[1][:200]
s = s.replace(bd, bd + "  placement: gateway\n")
open(cfg, "w").write(s)

t = open(soul).read()
save = f"- Save files you produce in `/home/jdoe/taffy/work/{bot}/`"
lines = t.split("\n")
idx = [i for i, l in enumerate(lines) if l.startswith(save)]
assert len(idx) == 1 and "run in your own container" not in t
lines.insert(idx[0] + 1, f"- Your terminal, code runner and file tools run in your own container. Save deliverables under "
             f"`/home/jdoe/taffy/work/{bot}/`, which is the only folder you can write that Colin sees; the other "
             f"work folders and the shared folder are read-only there. Serve previews on ports {first} to {last}.")
open(soul, "w").write("\n".join(lines))
print("edited", cfg, soul)
