#!/usr/bin/env python3
# Exit 0 only when the Zips are idle: busy.json calls 0 and queued 0, no running board task,
# and no POST or agent lines in gateway.log during the last 3 minutes. Read-only.
import json, sqlite3, sys, datetime as dt
b = json.load(open("/home/jdoe/taffy/hub-state/busy.json"))
c = sqlite3.connect("file:/home/jdoe/.taffy/kanban.db?mode=ro", uri=True)
running = c.execute("select count(*) from tasks where status='running'").fetchone()[0]
cut = dt.datetime.now() - dt.timedelta(minutes=3)
recent = []
for line in open("/home/jdoe/.taffy/logs/gateway.log", errors="replace").readlines()[-3000:]:
    try:
        t = dt.datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        continue
    if t >= cut and ("POST" in line or "agent" in line.lower()):
        recent.append(line[:160].rstrip())
ok = b.get("calls") == 0 and b.get("queued") == 0 and running == 0 and not recent
print(f"{dt.datetime.now():%H:%M:%S} calls={b.get('calls')} queued={b.get('queued')} running_tasks={running} "
      f"recent_log_lines={len(recent)} idle={ok}")
for r in recent[-5:]:
    print("  ", r)
sys.exit(0 if ok else 1)
