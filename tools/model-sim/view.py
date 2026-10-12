"""The blind page for the back-and-forth test: each scenario's two runs side by side as A and B, with the
conversation, the end product, time and usage, and no model names. Colin picks a winner per scenario; the page
reveals which model was which only after every scenario has a pick.

    python3 tools/model-sim/view.py results.json [port]

The A/B assignment is drawn once and kept in sealed.json next to the results; votes go to votes.json. Neither file
is served until the reveal.
"""
import json
import os
import random
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scenarios import SCENARIOS  # noqa: E402

RESULTS = Path(sys.argv[1]).resolve()
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 4330
SEALED = RESULTS.with_name("sealed.json")
VOTES = RESULTS.with_name("votes.json")


def weighted(u: dict) -> int:
    # Cache reads are cheap, cache writes dear, output dearest: roughly how each plan counts them.
    return round(0.1 * u.get("read", 0) + 2 * u.get("write", 0) + u.get("uncached", 0)
                 + 5 * (u.get("out", 0) + u.get("reasoning", 0)))


def load() -> tuple[list, dict]:
    runs = json.loads(RESULTS.read_text())
    sealed = json.loads(SEALED.read_text()) if SEALED.exists() else {}
    changed = False
    for s in SCENARIOS:
        if s["id"] not in sealed and {r["model"] for r in runs if r["scenario"] == s["id"]} == {"opus", "sol"}:
            pair = ["opus", "sol"]
            random.SystemRandom().shuffle(pair)
            sealed[s["id"]] = {"A": pair[0], "B": pair[1]}
            changed = True
    if changed:
        SEALED.write_text(json.dumps(sealed, indent=1))
    return runs, sealed


def data() -> dict:
    runs, sealed = load()
    votes = json.loads(VOTES.read_text()) if VOTES.exists() else {}
    out = []
    for s in SCENARIOS:
        if s["id"] not in sealed:
            continue
        sides = {}
        for label, model in sealed[s["id"]].items():
            r = next(x for x in runs if x["scenario"] == s["id"] and x["model"] == model)
            u = r.get("usage") or {}
            sides[label] = {"transcript": r["transcript"], "product": r["product"], "check": r["check"],
                            "secs": r["secs"], "turns": r["turns"], "calls": u.get("calls", 0),
                            "weighted": weighted(u), "out": u.get("out", 0) + u.get("reasoning", 0),
                            "errors": len(r.get("errors") or [])}
        out.append({"id": s["id"], "title": s["title"], "brief": s["brief"].replace("{d}", "~/files"),
                    "sides": sides, "vote": votes.get(s["id"])})
    return {"scenarios": out, "all_voted": bool(out) and all(x["vote"] for x in out)}


def reveal() -> dict:
    runs, sealed = load()
    votes = json.loads(VOTES.read_text()) if VOTES.exists() else {}
    tally = {"opus": {}, "sol": {}}
    for sid, v in votes.items():
        for part in ("conversation", "product", "overall"):
            pick = v.get(part)
            model = sealed[sid].get(pick) if pick in ("A", "B") else "tie"
            for m in ("opus", "sol"):
                tally[m].setdefault(part, 0)
            if model in tally:
                tally[model][part] += 1
    totals = {}
    for m in ("opus", "sol"):
        rs = [r for r in runs if r["model"] == m]
        totals[m] = {"secs": round(sum(r["secs"] for r in rs)), "weighted": sum(weighted(r.get("usage") or {}) for r in rs),
                     "wins": tally[m]}
    return {"sealed": sealed, "totals": totals}


PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Blind Model Test</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1f;--mute:#6e6e73;--line:#e3e1dc;--a:#3b6fd8;--b:#c4602d;--zip:#eef2fb;--user:#f1efe9}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--ink)}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:14px 28px;display:flex;gap:16px;align-items:center;z-index:5}
header h1{font-size:18px;margin:0;flex:1}nav button{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:5px 10px;margin-left:4px;cursor:pointer;font:inherit;font-size:13px}
nav button.on{background:var(--ink);color:#fff}nav button.done::after{content:" \2713"}
main{max-width:1500px;margin:0 auto;padding:20px 28px 80px}
.brief{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;color:var(--mute);font-size:14px}
.brief b{color:var(--ink)}.cols{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:18px}
.side{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.side h2{margin:0;padding:12px 18px;font-size:16px;color:#fff}.A h2{background:var(--a)}.B h2{background:var(--b)}
.stats{display:flex;gap:18px;padding:10px 18px;border-bottom:1px solid var(--line);font-size:13px;color:var(--mute);flex-wrap:wrap}
.stats b{color:var(--ink)}.chat{padding:14px 18px;display:flex;flex-direction:column;gap:10px;max-height:620px;overflow:auto}
.msg{padding:10px 13px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word;max-width:92%}
.user{background:var(--user);align-self:flex-end}.zip{background:var(--zip);align-self:flex-start}
.msg small{display:block;color:var(--mute);font-size:11px;margin-bottom:2px}
.prod{border-top:1px solid var(--line);padding:12px 18px}.prod h3{font-size:13px;margin:6px 0;color:var(--mute);font-weight:600}
.prod pre{background:#fafaf8;border:1px solid var(--line);border-radius:8px;padding:10px;max-height:360px;overflow:auto;white-space:pre-wrap;font-size:12.5px}
.prod iframe{width:100%;height:480px;border:1px solid var(--line);border-radius:8px;background:#fff}
.missing{color:#b3261e}.ok{color:#1e7d32}
.vote{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-top:20px}
.vote h2{font-size:16px;margin:0 0 10px}.row{display:flex;align-items:center;gap:10px;margin:8px 0}.row span{width:220px}
.row label{border:1px solid var(--line);border-radius:8px;padding:5px 14px;cursor:pointer}.row input{display:none}
.row input:checked+label{background:var(--ink);color:#fff}
textarea{width:100%;min-height:60px;border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit}
.save{margin-top:10px;background:var(--ink);color:#fff;border:0;border-radius:8px;padding:8px 18px;font:inherit;cursor:pointer}
#reveal{margin-top:24px}table{border-collapse:collapse;background:var(--card)}td,th{border:1px solid var(--line);padding:8px 12px;text-align:left}
.hint{color:var(--mute);font-size:13px}
</style>
<header><h1>Blind Model Test</h1><nav id="nav"></nav></header>
<main id="main"></main>
<script>
let D, cur = 0;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function load(){ D = await (await fetch("/api/data")).json(); draw(); }
function nav(){ document.getElementById("nav").innerHTML = D.scenarios.map((s,i)=>
  `<button class="${i===cur?'on':''} ${s.vote?'done':''}" onclick="cur=${i};draw()">${i+1}</button>`).join("")
  + (D.all_voted ? ` <button onclick="reveal()">Reveal</button>` : ""); }
function product(p){ return Object.entries(p||{}).map(([name,body])=>{
  if(body===null) return `<h3>${esc(name)}</h3><p class="missing">Not saved.</p>`;
  if(name.endsWith(".html")) return `<h3>${esc(name)}</h3><iframe sandbox srcdoc="${esc(body)}"></iframe>`;
  return `<h3>${esc(name)}</h3><pre>${esc(body)}</pre>`; }).join(""); }
function side(label, x){
  const check = x.check ? `<h3>Tests</h3><p class="${x.check.passed?'ok':'missing'}">${x.check.passed?'Pass':'Fail'}</p>` : "";
  const prod = Object.keys(x.product||{}).length || x.check ? `<div class="prod"><h3 style="color:var(--ink)">End Product</h3>${product(x.product)}${check}</div>` : "";
  return `<div class="side ${label}"><h2>Zip ${label}</h2>
  <div class="stats"><span>Time <b>${Math.round(x.secs)} s</b></span><span>Turns <b>${x.turns}</b></span>
  <span>Model calls <b>${x.calls}</b></span><span title="Cache reads x0.1, cache writes x2, new input x1, output x5">Usage <b>${(x.weighted/1000).toFixed(0)}k</b> weighted tokens</span>
  ${x.errors?`<span class="missing">${x.errors} errors</span>`:""}</div>
  <div class="chat">${x.transcript.map(m=>`<div class="msg ${m.role}"><small>${m.role==='user'?'Simulated user':'Zip '+label}${m.secs?' · '+Math.round(m.secs)+' s':''}</small>${esc(m.text)}</div>`).join("")}</div>${prod}</div>`; }
function pick(name, v){ return ["A","Tie","B"].map(o=>`<input type="radio" name="${name}" id="${name}${o}" value="${o}" ${v===o?'checked':''}><label for="${name}${o}">${o}</label>`).join(""); }
function draw(){ nav(); const s = D.scenarios[cur]; const v = s.vote || {};
  document.getElementById("main").innerHTML = `<div class="brief"><b>${cur+1}. ${esc(s.title)}</b><br>What the simulated user was told: ${esc(s.brief)}</div>
  <div class="cols">${side("A", s.sides.A)}${side("B", s.sides.B)}</div>
  <div class="vote"><h2>Your Pick</h2>
  <div class="row"><span>Better back-and-forth</span>${pick("conversation", v.conversation)}</div>
  <div class="row"><span>Better end product</span>${pick("product", v.product)}</div>
  <div class="row"><span>Better overall</span>${pick("overall", v.overall)}</div>
  <textarea id="note" placeholder="Notes (optional)">${esc(v.note)}</textarea>
  <button class="save" onclick="save()">Save and Next</button> <span class="hint">The reveal unlocks once every scenario has a pick.</span></div><div id="reveal"></div>`; }
async function save(){ const s = D.scenarios[cur]; const get = n => (document.querySelector(`input[name=${n}]:checked`)||{}).value;
  const v = {conversation:get("conversation"), product:get("product"), overall:get("overall"), note:document.getElementById("note").value};
  if(!v.overall){ alert("Pick an overall winner first."); return; }
  await fetch("/api/vote",{method:"POST",body:JSON.stringify({id:s.id, vote:v})});
  D = await (await fetch("/api/data")).json(); cur = Math.min(cur+1, D.scenarios.length-1); draw(); window.scrollTo(0,0); }
async function reveal(){ const r = await (await fetch("/api/reveal")).json(); if(r.error){alert(r.error);return;}
  const names = {opus:"Opus", sol:"Sol"};
  const rows = D.scenarios.map((s,i)=>`<tr><td>${i+1}. ${esc(s.title)}</td><td>${names[r.sealed[s.id].A]}</td><td>${names[r.sealed[s.id].B]}</td><td>${esc((s.vote||{}).overall)}</td></tr>`).join("");
  const t = m => r.totals[m]; const w = (m,k) => t(m).wins[k]||0;
  document.getElementById("main").innerHTML = `<h2>Reveal</h2><table><tr><th>Scenario</th><th>Zip A</th><th>Zip B</th><th>Your overall pick</th></tr>${rows}</table>
  <h2>Totals</h2><table><tr><th></th><th>Back-and-forth wins</th><th>Product wins</th><th>Overall wins</th><th>Total time</th><th>Weighted tokens</th></tr>
  ${["opus","sol"].map(m=>`<tr><td>${names[m]}</td><td>${w(m,"conversation")}</td><td>${w(m,"product")}</td><td>${w(m,"overall")}</td><td>${Math.round(t(m).secs/60)} min</td><td>${(t(m).weighted/1e6).toFixed(2)}M</td></tr>`).join("")}</table>`; }
load();
</script></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, kind="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/api/data":
            return self._send(200, json.dumps(data()))
        if self.path == "/api/reveal":
            d = data()
            if not d["all_voted"]:
                return self._send(200, json.dumps({"error": "Pick a winner in every scenario first."}))
            return self._send(200, json.dumps(reveal()))
        self._send(404, "{}")

    def do_POST(self):
        if self.path != "/api/vote":
            return self._send(404, "{}")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        votes = json.loads(VOTES.read_text()) if VOTES.exists() else {}
        votes[str(body["id"])] = body["vote"]
        VOTES.write_text(json.dumps(votes, indent=1))
        self._send(200, "{}")


if __name__ == "__main__":
    load()
    print(f"http://localhost:{PORT}", flush=True)
    ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), PORT), Handler).serve_forever()
