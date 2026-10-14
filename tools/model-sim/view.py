"""The blind page for the back-and-forth test: each scenario's two runs side by side as A and B, each drawn the way
the phone's chat shows it (person on the right, Zip on the left, short versions with Show Full Reply, the step line
while the Zip works, choice cards), with the end product, time and usage, and no model names. Colin picks a winner per
scenario; the page reveals which model was which only after every scenario has a pick.

Under the two sides, Sonnet's comparison of them (Oct 12): it reads the same blind page (brief, both chats with short
and full replies, end products, time and price, no model names) and says how the two differ. Written once per
scenario in the background and kept in analysis.json. Colin can also select words in a Zip's reply and mark them
liked or disliked with a note; those go to comments.json and are grouped by model after the reveal.

    python3 tools/model-sim/view.py results.json [port]

The A/B assignment is drawn once and kept in sealed.json next to the results; votes go to votes.json. Neither file
is served until the reveal.
"""
import hashlib
import json
import os
import random
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scenarios import SCENARIOS  # noqa: E402

RESULTS = Path(sys.argv[1]).resolve()
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 4330
SEALED = RESULTS.with_name("sealed.json")
VOTES = RESULTS.with_name("votes.json")
ANALYSIS = RESULTS.with_name("analysis.json")
COMMENTS = RESULTS.with_name("comments.json")
LOCK = threading.Lock()

# API list prices in dollars per million tokens, looked up Oct 12, 2026 (Anthropic's model table; OpenAI's pricing page,
# short context). Both Zips ran on subscriptions, so this is what the same run would cost on the API. Cache writes:
# Anthropic's five-minute rate (1.25x input); the Codex log has none. The hub's short versions run on Haiku.
PRICES = {"opus": {"read": 0.20, "write": 5.00, "uncached": 4.00, "out": 20.00},
          "sonnet": {"read": 0.20, "write": 2.50, "uncached": 2.00, "out": 10.00},
          "sol": {"read": 0.10, "write": 2.50, "uncached": 2.00, "out": 10.00},
          "haiku": {"read": 0.01, "write": 0.125, "uncached": 0.10, "out": 0.50}}
LABELS = "ABC"


def weighted(u: dict) -> int:
    return round(0.1 * u.get("read", 0) + 2 * u.get("write", 0) + u.get("uncached", 0)
                 + 5 * (u.get("out", 0) + u.get("reasoning", 0)))


def price(u: dict, model: str) -> float:
    p = PRICES[model]
    return (p["read"] * u.get("read", 0) + p["write"] * u.get("write", 0) + p["uncached"] * u.get("uncached", 0)
            + p["out"] * (u.get("out", 0) + u.get("reasoning", 0))) / 1e6


def read_json(f: Path) -> dict:
    return json.loads(f.read_text()) if f.exists() else {}


def models(runs: list) -> list[str]:
    """The models in this results file (opus and sol; opus, sonnet and haiku)."""
    return sorted({r["model"] for r in runs})


def load() -> tuple[list, dict]:
    runs = json.loads(RESULTS.read_text())
    sealed = json.loads(SEALED.read_text()) if SEALED.exists() else {}
    changed = False
    both = set(models(runs))
    for s in SCENARIOS:
        if s["id"] not in sealed and len(both) >= 2 and {r["model"] for r in runs if r["scenario"] == s["id"]} == both:
            pair = sorted(both)
            random.SystemRandom().shuffle(pair)
            sealed[s["id"]] = dict(zip(LABELS, pair))
            changed = True
    if changed:
        SEALED.write_text(json.dumps(sealed, indent=1))
    return runs, sealed


def data() -> dict:
    runs, sealed = load()
    votes = json.loads(VOTES.read_text()) if VOTES.exists() else {}
    analyses, comments = read_json(ANALYSIS), read_json(COMMENTS)
    out = []
    for s in SCENARIOS:
        if s["id"] not in sealed:
            continue
        sides = {}
        for label, model in sealed[s["id"]].items():
            r = next(x for x in runs if x["scenario"] == s["id"] and x["model"] == model)
            u, h = r.get("usage") or {}, r.get("hub_usage") or {}
            rows = [{k: v for k, v in m.items() if k in ("role", "text", "full", "backup", "steps", "secs", "options",
                                                         "picked", "tapped", "kind", "title")} for m in r["transcript"]]
            sides[label] = {"transcript": rows, "product": r["product"], "check": r["check"],
                            "secs": r["secs"], "turns": r["turns"], "calls": u.get("calls", 0),
                            "weighted": weighted(u), "hub_weighted": weighted(h), "hub_calls": h.get("calls", 0),
                            "out": u.get("out", 0) + u.get("reasoning", 0),
                            "price": round(price(u, model) + price(h, "haiku"), 4),
                            "elsewhere": r.get("saved_elsewhere") or [], "errors": len(r.get("errors") or [])}
        a = analyses.get(s["id"]) or {}
        out.append({"id": s["id"], "title": s["title"], "brief": s["brief"].replace("{d}", "~/files"),
                    "sides": sides, "vote": votes.get(s["id"]),
                    "analysis": a.get("text") if a.get("pair") == pair_key(sealed[s["id"]]) else None,
                    "comments": comments.get(s["id"]) or []})
    return {"scenarios": out, "all_voted": bool(out) and all(x["vote"] for x in out)}


def reveal() -> dict:
    runs, sealed = load()
    votes = json.loads(VOTES.read_text()) if VOTES.exists() else {}
    names = models(runs)
    tally = {m: {} for m in names}
    for sid, v in votes.items():
        for part in ("conversation", "product", "overall"):
            pick = v.get(part)
            model = sealed[sid].get(pick) if pick in LABELS else "tie"
            for m in names:
                tally[m].setdefault(part, 0)
            if model in tally:
                tally[model][part] += 1
    totals = {}
    for m in names:
        rs = [r for r in runs if r["model"] == m]
        totals[m] = {"secs": round(sum(r["secs"] for r in rs)),
                     "price": round(sum(price(r.get("usage") or {}, m) + price(r.get("hub_usage") or {}, "haiku")
                                        for r in rs), 2),
                     "weighted": sum(weighted(r.get("usage") or {}) + weighted(r.get("hub_usage") or {}) for r in rs),
                     "wins": tally[m]}
    return {"sealed": sealed, "totals": totals, "models": names}


# MARK: Sonnet's comparison
def pair_key(pair: dict) -> str:
    """Which model is A, kept as a hash so analysis.json says nothing about it."""
    return hashlib.sha256("|".join(pair[k] for k in sorted(pair)).encode()).hexdigest()[:16]


COMPARE_PROMPT = """You are helping a person judge {n} AI assistants, {names}, in a blind test. Each had the same
conversation with the same simulated person, through a phone chat app. You do not know which models they are. Do not
guess.

What the simulated person was told (the assistants could not see this):
{brief}

{sides}

Write the person a short comparison of how they differ. Cover how each one talked (how it opened its replies, how
long they ran, whether it asked or guessed, its tone), how it handled the change of mind or new requirement if there
was one, the end product (quality, anything missing, anything invented or wrong), and time only if the gap matters. Point to specific moments, and quote a few words where that helps. Do not rate them or pick a winner; the
person decides.

Format: three short paragraphs that start with **Conversation.**, **End product.** and **The main difference.** Under
{words} words in all. Plain complete sentences. No headings, lists, emojis or em dashes."""


def side_text(label: str, x: dict) -> str:
    out = [f"=== ZIP {label} === (time {round(x['secs'])} s, {x['turns']} turns, {x['calls']} model calls)", "",
           "The chat:"]
    for m in x["transcript"]:
        if m["role"] == "user":
            out.append(("Person (tapped): " if m.get("tapped") else "Person: ") + m["text"])
        elif m["role"] == "zip":
            if m.get("full"):
                out.append(f"Zip (shown short): {m['text']}\nZip (full reply, behind Show Full Reply): {m['full']}")
            else:
                out.append("Zip: " + m["text"])
        elif m["role"] == "steps" and m.get("steps"):
            out.append("(Working: " + ", ".join(m["steps"][-8:]) + f"; {round(m.get('secs') or 0)} s)")
        elif m["role"] == "card":
            opts = " ".join(f"[{o}]" for o in m.get("options") or [])
            out.append(f"(Card: {m.get('title') or ''} {m['text']} {opts})".replace("  ", " "))
        elif m["role"] == "error":
            out.append("(App: " + m["text"] + ")")
    out += ["", "End product:"]
    for name, body in (x["product"] or {}).items():
        out.append(f"--- {name} ---\n" + ("(not saved)" if body is None else body[:12000]))
    if not x["product"]:
        out.append("(none expected)")
    if x["check"]:
        out.append("Tests: " + ("pass" if x["check"]["passed"] else "fail"))
    if x["elsewhere"]:
        out.append("Also saved elsewhere: " + ", ".join(x["elsewhere"]))
    return "\n".join(out)


def write_analysis(s: dict, key: str) -> None:
    labels = sorted(s["sides"])
    names = ", ".join(f"Zip {k}" for k in labels[:-1]) + f" and Zip {labels[-1]}"
    prompt = COMPARE_PROMPT.format(n="two" if len(labels) == 2 else "three", names=names, brief=s["brief"],
                                   sides="\n\n".join(side_text(k, s["sides"][k]) for k in labels),
                                   words=220 if len(labels) == 2 else 300)
    try:
        p = subprocess.run(["claude", "-p", "--model", "sonnet", "--tools", "", "--output-format", "json"],
                           input=prompt, capture_output=True, text=True, timeout=600, cwd=str(Path.home()))
        text = json.loads(p.stdout.strip().splitlines()[-1]).get("result", "").strip()
    except (subprocess.TimeoutExpired, ValueError, IndexError) as e:
        print(f"comparison for {s['id']} failed: {e}", flush=True)
        return
    if not text:
        print(f"comparison for {s['id']} came back empty: {p.stderr[-300:]}", flush=True)
        return
    with LOCK:
        a = read_json(ANALYSIS)
        a[s["id"]] = {"pair": key, "text": text, "at": time.time()}
        ANALYSIS.write_text(json.dumps(a, indent=1))
    print(f"comparison for {s['id']} written", flush=True)


def write_missing() -> None:
    _, sealed = load()
    todo = [(s, pair_key(sealed[s["id"]])) for s in data()["scenarios"] if not s["analysis"]]
    if todo:
        print(f"writing {len(todo)} comparisons with Sonnet", flush=True)
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(lambda t: write_analysis(*t), todo))


PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blind Model Test</title>
<style>
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1d1f;--mute:#6e6e73;--line:#e3e1dc;--a:#3b6fd8;--b:#c4602d;--c:#4f8a3c;--me:#0a7cff;--zip:#e9e9eb}
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
.cols.n3{grid-template-columns:1fr 1fr 1fr;gap:12px}
@media (max-width:900px){.cols,.cols.n3{grid-template-columns:1fr}.tabs{display:flex}.cols.showA .B,.cols.showA .C,.cols.showB .A,.cols.showB .C,.cols.showC .A,.cols.showC .B{display:none}main{padding:12px}}
.side{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;min-width:0}
html,body{overflow-x:hidden}.brief,.vote{overflow-wrap:anywhere}
.side h2{margin:0;padding:11px 16px;font-size:16px;color:#fff}.A h2{background:var(--a)}.B h2{background:var(--b)}.C h2{background:var(--c)}
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
.stats .price{margin-left:auto;color:var(--ink);font-weight:600}
details.prod>summary{cursor:pointer;font-weight:600;font-size:14px;list-style:none;display:flex;gap:8px;align-items:center}
details.prod>summary::-webkit-details-marker{display:none}
details.prod>summary svg{width:14px;height:14px;transition:transform .15s}details.prod[open]>summary svg{transform:rotate(180deg)}
details.prod>summary .hint{font-weight:400}
.analysis{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 18px;margin-top:18px}
.analysis h2{font-size:16px;margin:0 0 2px}.analysis .body{white-space:pre-wrap;margin-top:8px}
.pending{color:var(--mute);font-style:italic}
mark{background:#fff2a8;border-radius:3px;padding:0 1px}mark.dislike{background:#ffd6d1}
#pop{position:fixed;z-index:20;background:var(--ink);color:#fff;border-radius:12px;padding:8px;display:none;width:260px;box-shadow:0 6px 24px rgba(0,0,0,.2)}
#pop textarea{min-height:38px;background:#fff;color:var(--ink);font-size:13px;margin:0 0 6px}
#pop button{border:0;border-radius:8px;padding:5px 12px;font:inherit;font-size:13px;cursor:pointer;margin-right:4px}
#pop .like{background:#1e7d32;color:#fff}#pop .dislike{background:#b3261e;color:#fff}#pop .x{background:none;color:#ccc;float:right}
.hl{list-style:none;padding:0;margin:8px 0 0}.hl li{border-top:1px solid var(--line);padding:7px 0;font-size:14px;display:flex;gap:10px;align-items:flex-start}
.hl .tag{font-size:12px;font-weight:600;border-radius:6px;padding:1px 7px;flex:none}.hl .tag.like{background:#e3f3e5;color:#1e7d32}.hl .tag.dislike{background:#fbe4e1;color:#b3261e}
.hl q{font-style:italic}.hl .del{margin-left:auto;border:0;background:none;color:var(--mute);cursor:pointer;font:inherit;font-size:12.5px;text-decoration:underline;flex:none}
</style>
<header><h1>Blind Model Test</h1><nav id="nav"></nav></header>
<main id="main"></main>
<div id="pop"><button class="x" onclick="hidePop()">Close</button><div style="font-size:12px;opacity:.8;margin-bottom:6px">Mark this part</div>
<textarea id="popnote" placeholder="Why (optional)"></textarea>
<button class="like" onclick="comment('like')">Like</button><button class="dislike" onclick="comment('dislike')">Dislike</button></div>
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
      out.push(`<div class="bubble zip" id="b${key}" data-side="${label}" data-i="${i}">${marked(label, i, md(m.text))}</div>`);
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
  FULL[key] = !FULL[key]; el.innerHTML = marked(label, i, md(FULL[key] ? m.full : m.text)); btn.textContent = FULL[key] ? "Show Short Reply" : "Show Full Reply"; }
function side(label, x){
  const check = x.check ? `<h3>Tests</h3><p class="${x.check.passed?'ok':'missing'}">${x.check.passed?'Pass':'Fail'}</p>` : "";
  const else_ = (x.elsewhere||[]).length ? `<h3>Also saved somewhere else</h3><pre>${esc(x.elsewhere.join("\n"))}</pre>` : "";
  const files = Object.keys(x.product||{});
  const sum = [files.length ? `${files.length} file${files.length>1?"s":""}` : "", x.check ? (x.check.passed ? "tests pass" : "tests fail") : "",
    files.some(f=>x.product[f]===null) ? "something not saved" : ""].filter(Boolean).join(", ");
  const prod = files.length || x.check || else_ ? `<details class="prod"><summary>${ICON.down}End Product <span class="hint">${esc(sum)}</span></summary>${product(x.product)}${check}${else_}</details>` : "";
  const w = x.weighted + (x.hub_weighted||0);
  return `<div class="side ${label}"><h2>Zip ${label}</h2>
  <div class="stats"><span>Time <b>${Math.round(x.secs)} s</b></span><span>Turns <b>${x.turns}</b></span>
  <span>Model calls <b>${x.calls}</b></span><span title="The Zip's calls plus the hub's short versions for it. Cache reads x0.1, cache writes x2, new input x1, output x5">Usage <b>${(w/1000).toFixed(0)}k</b> weighted tokens</span>
  ${x.errors?`<span class="missing">${x.errors} errors</span>`:""}
  </div>
  <div class="chat">${chat(label, x.transcript)}</div>${prod}</div>`; }
function pick(name, v){ const L = Object.keys(D.scenarios[cur].sides).sort();
  return (L.length===2 ? ["A","Tie","B"] : [...L,"Tie"]).map(o=>`<input type="radio" name="${name}" id="${name}${o}" value="${o}" ${v===o?'checked':''}><label for="${name}${o}">${o}</label>`).join(""); }
function draw(){ nav(); FULL = {}; const s = D.scenarios[cur]; const v = s.vote || {}; const L = Object.keys(s.sides).sort();
  document.getElementById("main").innerHTML = `<div class="brief"><b>${cur+1}. ${esc(s.title)}</b><br>What the simulated person was told: ${esc(s.brief)}</div>
  <div class="tabs">${L.map(k=>`<button class="${tab===k?'on':''}" onclick="tab='${k}';draw()">Zip ${k}</button>`).join("")}</div>
  <div class="cols show${tab} n${L.length}">${L.map(k=>side(k, s.sides[k])).join("")}</div>
  <div class="analysis"><h2>How They Differ</h2><span class="hint">Written by Sonnet from this same page, without model names or prices.</span>
  <div class="body" id="analysis">${analysisHtml(s)}</div></div>
  <div class="analysis"><h2>Your Highlights</h2><span class="hint">Select words in either Zip's reply to mark them.</span><div id="hl">${highlights(s)}</div></div>
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
  const names = {opus:"Opus", sol:"Sol", sonnet:"Sonnet", haiku:"Haiku"}; const L = Object.keys(r.sealed[D.scenarios[0].id]).sort();
  const rows = D.scenarios.map((s,i)=>`<tr><td>${i+1}. ${esc(s.title)}</td>${L.map(k=>`<td>${names[r.sealed[s.id][k]]}</td>`).join("")}<td>${esc((s.vote||{}).overall)}</td></tr>`).join("");
  const t = m => r.totals[m]; const w = (m,k) => t(m).wins[k]||0;
  document.getElementById("main").innerHTML = `<h2>Reveal</h2><table><tr><th>Scenario</th>${L.map(k=>`<th>Zip ${k}</th>`).join("")}<th>Your overall pick</th></tr>${rows}</table>
  <h2>Totals</h2><table><tr><th></th><th>Working-with wins</th><th>Product wins</th><th>Overall wins</th><th>Total time</th><th>Weighted tokens</th><th>API price</th></tr>
  ${r.models.map(m=>`<tr><td>${names[m]}</td><td>${w(m,"conversation")}</td><td>${w(m,"product")}</td><td>${w(m,"overall")}</td><td>${Math.round(t(m).secs/60)} min</td><td>${(t(m).weighted/1e6).toFixed(2)}M</td><td>$${t(m).price.toFixed(2)}</td></tr>`).join("")}</table>
  ${r.models.map(m=>{ const cs = D.scenarios.flatMap(s=>(s.comments||[]).filter(c=>r.sealed[s.id][c.side]===m).map(c=>({...c, side:esc(s.title)})));
    return `<h2>Your Highlights for ${names[m]}</h2>` + (cs.length ? `<ul class="hl">${cs.map(c=>`<li><span class="tag ${c.kind}">${c.kind==="like"?"Liked":"Disliked"}</span><span><q>${esc(c.excerpt)}</q><br><span class="hint">${c.side}${c.note?": "+esc(c.note):""}</span></span></li>`).join("")}</ul>` : `<p class="hint">None.</p>`); }).join("")}`; }
function analysisHtml(s){ return s.analysis ? md(s.analysis) : `<span class="pending">Sonnet is still writing this one. It appears here on its own when it is done.</span>`; }
function marked(label, i, html){ (D.scenarios[cur].comments||[]).filter(c=>c.side===label && c.i===i).forEach(c=>{
  const e = esc(c.excerpt); const at = html.indexOf(e);
  if(at>=0) html = html.slice(0,at)+`<mark class="${c.kind}" title="${esc(c.note)}">${e}</mark>`+html.slice(at+e.length); });
  return html; }
function highlights(s){ const cs = s.comments||[]; if(!cs.length) return `<p class="hint">None yet.</p>`;
  return `<ul class="hl">${cs.map(c=>`<li><span class="tag ${c.kind}">${c.kind==="like"?"Liked":"Disliked"} in ${c.side}</span>
    <span><q>${esc(c.excerpt)}</q>${c.note?`<br><span class="hint">${esc(c.note)}</span>`:""}</span>
    <button class="del" onclick="uncomment('${c.cid}')">Remove</button></li>`).join("")}</ul>`; }
let SEL = null;
function hidePop(){ document.getElementById("pop").style.display="none"; SEL = null; }
document.addEventListener("selectionchange", ()=>{ clearTimeout(window._selT); window._selT = setTimeout(()=>{
  const sel = window.getSelection(); if(!sel || sel.isCollapsed) return;
  const text = sel.toString().trim(); if(text.length < 3) return;
  const a = sel.anchorNode && (sel.anchorNode.nodeType===1 ? sel.anchorNode : sel.anchorNode.parentElement);
  const bub = a && a.closest(".bubble.zip"); if(!bub || !bub.contains(sel.focusNode)) return;
  const r = sel.getRangeAt(0).getBoundingClientRect(); const pop = document.getElementById("pop");
  const key = bub.id.slice(1);
  SEL = {side: bub.dataset.side, i: Number(bub.dataset.i), excerpt: text, version: FULL[key] ? "full" : "short"};
  pop.style.display = "block";
  pop.style.left = Math.max(8, Math.min(window.innerWidth-270, r.left)) + "px";
  pop.style.top = (r.bottom + 150 < window.innerHeight ? r.bottom + 8 : Math.max(8, r.top - 150)) + "px";
  document.getElementById("popnote").value = ""; }, 250); });
document.addEventListener("mousedown", e=>{ if(!e.target.closest("#pop")) document.getElementById("pop").style.display="none"; });
async function comment(kind){ if(!SEL) return; const s = D.scenarios[cur];
  await fetch("/api/comment",{method:"POST",body:JSON.stringify({id:s.id, ...SEL, kind, note:document.getElementById("popnote").value.trim()})});
  hidePop(); window.getSelection().removeAllRanges(); await refresh(); }
async function uncomment(cid){ await fetch("/api/comment/delete",{method:"POST",body:JSON.stringify({id:D.scenarios[cur].id, cid})}); await refresh(); }
// Re-read the data without redrawing the page, so open replies, open end products and the scroll stay as they are.
async function refresh(){ const fresh = await (await fetch("/api/data")).json();
  fresh.scenarios.forEach((s,i)=>{ D.scenarios[i].analysis = s.analysis; D.scenarios[i].comments = s.comments; });
  const s = D.scenarios[cur];
  document.getElementById("analysis").innerHTML = analysisHtml(s);
  document.getElementById("hl").innerHTML = highlights(s);
  document.querySelectorAll(".bubble.zip").forEach(b=>{ const m = s.sides[b.dataset.side].transcript[Number(b.dataset.i)];
    b.innerHTML = marked(b.dataset.side, Number(b.dataset.i), md(FULL[b.id.slice(1)] ? m.full : m.text)); }); }
setInterval(()=>{ if(D && D.scenarios.some(s=>!s.analysis)) refresh(); }, 10000);
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
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with LOCK:
            if self.path == "/api/vote":
                votes = read_json(VOTES)
                votes[str(body["id"])] = body["vote"]
                VOTES.write_text(json.dumps(votes, indent=1))
            elif self.path == "/api/comment":
                c = read_json(COMMENTS)
                keep = {k: body[k] for k in ("side", "i", "excerpt", "kind", "note", "version")}
                keep["cid"] = hashlib.sha256(f"{time.time()}{keep}".encode()).hexdigest()[:10]
                c.setdefault(str(body["id"]), []).append(keep)
                COMMENTS.write_text(json.dumps(c, indent=1))
            elif self.path == "/api/comment/delete":
                c = read_json(COMMENTS)
                c[str(body["id"])] = [x for x in c.get(str(body["id"]), []) if x["cid"] != body["cid"]]
                COMMENTS.write_text(json.dumps(c, indent=1))
            else:
                return self._send(404, "{}")
        self._send(200, "{}")


if __name__ == "__main__":
    load()
    threading.Thread(target=write_missing, daemon=True).start()
    print(f"http://localhost:{PORT}", flush=True)
    ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), PORT), Handler).serve_forever()
