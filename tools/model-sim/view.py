"""The blind page for the back-and-forth test: each scenario's two runs side by side as A and B, each drawn the way
the phone's chat shows it (person on the right, Zip on the left, short versions with Show Full Reply, the step line
while the Zip works, choice cards), with the end product, time and usage, and no model names. Colin picks a winner per
scenario; the page reveals which model was which only after every scenario has a pick.

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
            u, h = r.get("usage") or {}, r.get("hub_usage") or {}
            # Only what the phone shows, never the run's id, sessions, served model or raw events.
            rows = [{k: v for k, v in m.items() if k in ("role", "text", "full", "backup", "steps", "secs", "options",
                                                         "picked", "tapped", "kind", "title")} for m in r["transcript"]]
            sides[label] = {"transcript": rows, "product": r["product"], "check": r["check"],
                            "secs": r["secs"], "turns": r["turns"], "calls": u.get("calls", 0),
                            "weighted": weighted(u), "hub_weighted": weighted(h), "hub_calls": h.get("calls", 0),
                            "out": u.get("out", 0) + u.get("reasoning", 0),
                            "elsewhere": r.get("saved_elsewhere") or [], "errors": len(r.get("errors") or [])}
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
        totals[m] = {"secs": round(sum(r["secs"] for r in rs)),
                     "weighted": sum(weighted(r.get("usage") or {}) + weighted(r.get("hub_usage") or {}) for r in rs),
                     "wins": tally[m]}
    return {"sealed": sealed, "totals": totals}


PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blind Model Test</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1f;--mute:#6e6e73;--line:#e3e1dc;--a:#3b6fd8;--b:#c4602d;--me:#0a7cff;--zip:#e9e9eb}
*{box-sizing:border-box}body{margin:0;font:15px/1.45 -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--ink)}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 20px;display:flex;gap:12px;align-items:center;z-index:5;flex-wrap:wrap}
header h1{font-size:18px;margin:0;flex:1}nav button{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:5px 10px;margin:2px;cursor:pointer;font:inherit;font-size:13px}
nav button.on{background:var(--ink);color:#fff}nav button.done{border-color:#1e7d32}
main{max-width:1500px;margin:0 auto;padding:16px 20px 80px}
.brief{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;color:var(--mute);font-size:14px}
.brief b{color:var(--ink)}.cols{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:16px}
.tabs{display:none;margin-top:14px;background:var(--line);border-radius:10px;padding:3px}
.tabs button{flex:1;border:0;background:none;padding:7px;border-radius:8px;font:inherit;font-weight:600;cursor:pointer}
.tabs button.on{background:var(--card)}
@media (max-width:900px){.cols{grid-template-columns:1fr}.tabs{display:flex}.cols.showA .B,.cols.showB .A{display:none}main{padding:12px}}
.side{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;min-width:0}
html,body{overflow-x:hidden}.brief,.vote{overflow-wrap:anywhere}
.side h2{margin:0;padding:11px 16px;font-size:16px;color:#fff}.A h2{background:var(--a)}.B h2{background:var(--b)}
.stats{display:flex;gap:14px;padding:9px 16px;border-bottom:1px solid var(--line);font-size:13px;color:var(--mute);flex-wrap:wrap}
.stats b{color:var(--ink)}
.chat{padding:14px 12px;display:flex;flex-direction:column;gap:4px;max-height:75vh;overflow:auto;background:#fff}
.bubble{padding:8px 13px;border-radius:19px;white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere;max-width:82%}
.me{background:var(--me);color:#fff;align-self:flex-end;margin-top:8px}
.zip{background:var(--zip);align-self:flex-start;margin-top:3px}
.me .tap{display:block;font-size:11px;opacity:.8}
.full{align-self:flex-start;border:0;background:none;color:var(--mute);font:inherit;font-size:12.5px;padding:2px 13px;cursor:pointer;text-decoration:underline}
.quiet{align-self:flex-start;color:var(--mute);font-size:12px;padding:0 13px}
.time{align-self:flex-start;color:var(--mute);font-size:11px;padding:1px 13px}
.steps{align-self:flex-start;max-width:86%;margin-top:6px}
.steps button{border:0;background:none;font:inherit;font-size:12.5px;color:var(--mute);text-align:left;padding:0 4px;cursor:pointer;display:flex;gap:4px;align-items:flex-start}
.steps svg{width:12px;height:12px;flex:none;margin-top:3px}
.steps ul{list-style:none;margin:6px 0 2px 8px;padding:0;font-size:12.5px}.steps li{display:flex;gap:7px;align-items:center;padding:2px 0}
.dots{display:inline-flex;gap:4px;background:var(--zip);border-radius:19px;padding:10px 14px;margin-bottom:4px}
.dots i{width:7px;height:7px;border-radius:50%;background:#9a9aa0;display:block}
.card{align-self:flex-start;border:1px solid var(--line);border-radius:14px;padding:10px 12px;max-width:86%;margin-top:4px;font-size:14px}
.card .opts{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.card .opts span{border:1px solid var(--line);border-radius:999px;padding:4px 11px;font-size:13px}
.card .opts span.picked{background:var(--ink);color:#fff;border-color:var(--ink)}
.sys{align-self:center;color:var(--mute);font-size:12.5px;text-align:center;padding:6px 10px}
.bubble code{font:12.5px ui-monospace,Menlo,monospace;background:rgba(0,0,0,.06);padding:0 3px;border-radius:4px}
.prod{border-top:1px solid var(--line);padding:12px 16px}.prod h3{font-size:13px;margin:6px 0;color:var(--mute);font-weight:600}
.prod pre{background:#fafaf8;border:1px solid var(--line);border-radius:8px;padding:10px;max-height:360px;overflow:auto;white-space:pre-wrap;font-size:12.5px}
.prod iframe{width:100%;height:480px;border:1px solid var(--line);border-radius:8px;background:#fff}
.missing{color:#b3261e}.ok{color:#1e7d32}
.vote{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-top:18px}
.vote h2{font-size:16px;margin:0 0 10px}.row{display:flex;align-items:center;gap:8px;margin:8px 0;flex-wrap:wrap}.row span{width:200px}
.row label{border:1px solid var(--line);border-radius:8px;padding:6px 16px;cursor:pointer}.row input{display:none}
.row input:checked+label{background:var(--ink);color:#fff}
textarea{width:100%;min-height:60px;border:1px solid var(--line);border-radius:8px;padding:8px;font:inherit}
.save{margin-top:10px;background:var(--ink);color:#fff;border:0;border-radius:8px;padding:9px 18px;font:inherit;cursor:pointer}
table{border-collapse:collapse;background:var(--card)}td,th{border:1px solid var(--line);padding:8px 12px;text-align:left}
.hint{color:var(--mute);font-size:13px}
</style>
<header><h1>Blind Model Test</h1><nav id="nav"></nav></header>
<main id="main"></main>
<script>
const ICON = {
 check:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5l10 -10"/></svg>',
 down:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6l6 -6"/></svg>',
 up:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 15l6 -6l6 6"/></svg>'};
let D, cur = 0, tab = "A";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
// The app renders a little Markdown in bubbles: bold, italics and inline code.
const md = s => esc(s).replace(/`([^`\n]+)`/g,"<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g,"<b>$1</b>").replace(/(^|[^*])\*([^*\n]+)\*/g,"$1<i>$2</i>");
async function load(){ D = await (await fetch("/api/data")).json(); draw(); }
function nav(){ document.getElementById("nav").innerHTML = D.scenarios.map((s,i)=>
  `<button class="${i===cur?'on':''} ${s.vote?'done':''}" onclick="cur=${i};draw()">${i+1}${s.vote?' (voted)':''}</button>`).join("")
  + (D.all_voted ? ` <button onclick="reveal()">Reveal</button>` : ""); }
function product(p){ return Object.entries(p||{}).map(([name,body])=>{
  if(body===null) return `<h3>${esc(name)}</h3><p class="missing">Not saved.</p>`;
  if(name.endsWith(".html")) return `<h3>${esc(name)}</h3><iframe sandbox srcdoc="${esc(body)}"></iframe>`;
  return `<h3>${esc(name)}</h3><pre>${esc(body)}</pre>`; }).join(""); }
// The app's activity row: typing dots, then the newest three steps as one line; tap for every step (the last 8).
function line(steps){ return steps.slice(-3).map((s,i)=> i===0 ? s : s[0].toLowerCase()+s.slice(1)).join(", "); }
function stepsRow(m, key){
  const st = (m.steps||[]).slice(-8);
  const t = m.secs ? `<div class="time">${Math.round(m.secs)} s to reply</div>` : "";
  if(!st.length) return t;
  return `<div class="steps"><button onclick="document.getElementById('${key}').hidden^=1;this.querySelector('.chev').innerHTML=document.getElementById('${key}').hidden?ICON.down:ICON.up">
    <span>${esc(line(st))}</span>${st.length>1?`<span class="chev">${ICON.down}</span>`:""}</button>
    <ul id="${key}" hidden>${st.map(s=>`<li>${ICON.check}${esc(s)}</li>`).join("")}</ul></div>${t}`; }
function chat(label, rows){
  let pending = null, out = [];
  rows.forEach((m,i)=>{ const key = `${label}${cur}_${i}`;
    if(m.role==="user") out.push(`<div class="bubble me">${m.tapped?'<span class="tap">Tapped</span>':''}${esc(m.text)}</div>`);
    else if(m.role==="steps") pending = stepsRow(m, key);
    else if(m.role==="zip"){
      if(pending!==null){ out.push(pending); pending = null; }
      out.push(`<div class="bubble zip" id="b${key}">${md(m.text)}</div>`);
      if(m.full) out.push(`<button class="full" onclick="toggle('${key}',this)">Show Full Reply</button>`);
      if(m.backup) out.push(`<div class="quiet">Answered with the backup model.</div>`);
    }
    else if(m.role==="card" && m.kind==="event") out.push(`<div class="sys">${esc(m.text)}</div>`);
    else if(m.role==="card" && m.kind!=="choices") out.push(`<div class="card">${m.title?`<b>${esc(m.title)}</b><br>`:""}${esc(m.text)}</div>`);
    else if(m.role==="card") out.push(`<div class="card">${esc(m.text)}${(m.options||[]).length?`<div class="opts">${m.options.map(o=>`<span class="${o===m.picked?'picked':''}">${esc(o)}</span>`).join("")}</div>`:""}</div>`);
    else if(m.role==="error"){ if(pending!==null){ out.push(pending); pending=null; } out.push(`<div class="sys">${esc(m.text)}</div>`); }
  });
  if(pending!==null) out.push(pending);
  return out.join(""); }
let FULL = {};
function toggle(key, btn){ const [label, rest] = [key[0], key.slice(1)]; const [sc, i] = rest.split("_").map(Number);
  const m = D.scenarios[sc].sides[label].transcript[i]; const el = document.getElementById("b"+key);
  FULL[key] = !FULL[key]; el.innerHTML = md(FULL[key] ? m.full : m.text); btn.textContent = FULL[key] ? "Show Short Reply" : "Show Full Reply"; }
function side(label, x){
  const check = x.check ? `<h3>Tests</h3><p class="${x.check.passed?'ok':'missing'}">${x.check.passed?'Pass':'Fail'}</p>` : "";
  const else_ = (x.elsewhere||[]).length ? `<h3>Also saved somewhere else</h3><pre>${esc(x.elsewhere.join("\n"))}</pre>` : "";
  const prod = Object.keys(x.product||{}).length || x.check || else_ ? `<div class="prod"><h3 style="color:var(--ink)">End Product</h3>${product(x.product)}${check}${else_}</div>` : "";
  const w = x.weighted + (x.hub_weighted||0);
  return `<div class="side ${label}"><h2>Zip ${label}</h2>
  <div class="stats"><span>Time <b>${Math.round(x.secs)} s</b></span><span>Turns <b>${x.turns}</b></span>
  <span>Model calls <b>${x.calls}</b></span><span title="The Zip's calls plus the hub's short versions for it. Cache reads x0.1, cache writes x2, new input x1, output x5">Usage <b>${(w/1000).toFixed(0)}k</b> weighted tokens</span>
  ${x.errors?`<span class="missing">${x.errors} errors</span>`:""}</div>
  <div class="chat">${chat(label, x.transcript)}</div>${prod}</div>`; }
function pick(name, v){ return ["A","Tie","B"].map(o=>`<input type="radio" name="${name}" id="${name}${o}" value="${o}" ${v===o?'checked':''}><label for="${name}${o}">${o}</label>`).join(""); }
function draw(){ nav(); FULL = {}; const s = D.scenarios[cur]; const v = s.vote || {};
  document.getElementById("main").innerHTML = `<div class="brief"><b>${cur+1}. ${esc(s.title)}</b><br>What the simulated person was told: ${esc(s.brief)}</div>
  <div class="tabs"><button class="${tab==='A'?'on':''}" onclick="tab='A';draw()">Zip A</button><button class="${tab==='B'?'on':''}" onclick="tab='B';draw()">Zip B</button></div>
  <div class="cols show${tab}">${side("A", s.sides.A)}${side("B", s.sides.B)}</div>
  <div class="vote"><h2>Your Pick</h2>
  <div class="row"><span>Better to work with</span>${pick("conversation", v.conversation)}</div>
  <div class="row"><span>Better end product</span>${pick("product", v.product)}</div>
  <div class="row"><span>Better overall</span>${pick("overall", v.overall)}</div>
  <textarea id="note" placeholder="Notes (optional)">${esc(v.note)}</textarea>
  <button class="save" onclick="save()">Save and Next</button> <span class="hint">The reveal unlocks once every scenario has a pick.</span></div>`; }
async function save(){ const s = D.scenarios[cur]; const get = n => (document.querySelector(`input[name=${n}]:checked`)||{}).value;
  const v = {conversation:get("conversation"), product:get("product"), overall:get("overall"), note:document.getElementById("note").value};
  if(!v.overall){ alert("Pick an overall winner first."); return; }
  await fetch("/api/vote",{method:"POST",body:JSON.stringify({id:s.id, vote:v})});
  D = await (await fetch("/api/data")).json(); cur = Math.min(cur+1, D.scenarios.length-1); tab = "A"; draw(); window.scrollTo(0,0); }
async function reveal(){ const r = await (await fetch("/api/reveal")).json(); if(r.error){alert(r.error);return;}
  const names = {opus:"Opus", sol:"Sol"};
  const rows = D.scenarios.map((s,i)=>`<tr><td>${i+1}. ${esc(s.title)}</td><td>${names[r.sealed[s.id].A]}</td><td>${names[r.sealed[s.id].B]}</td><td>${esc((s.vote||{}).overall)}</td></tr>`).join("");
  const t = m => r.totals[m]; const w = (m,k) => t(m).wins[k]||0;
  document.getElementById("main").innerHTML = `<h2>Reveal</h2><table><tr><th>Scenario</th><th>Zip A</th><th>Zip B</th><th>Your overall pick</th></tr>${rows}</table>
  <h2>Totals</h2><table><tr><th></th><th>Working-with wins</th><th>Product wins</th><th>Overall wins</th><th>Total time</th><th>Weighted tokens</th></tr>
  ${["opus","sol"].map(m=>`<tr><td>${names[m]}</td><td>${w(m,"conversation")}</td><td>${w(m,"product")}</td><td>${w(m,"overall")}</td><td>${Math.round(t(m).secs/60)} min</td><td>${(t(m).weighted/1e6).toFixed(2)}M</td></tr>`).join("")}</table>`; }
load();
</script></html>
"""


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
