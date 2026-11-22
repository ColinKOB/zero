""








import json
import re

from . import home, mac, team

SCHEMAS = {
    "team_propose": {
        "name": "team_propose",

        "description": (
            "Do not use: new teams are no longer made. A project that needs several hands gets one Zip with modes "
            "(for example Marketing, Dev and Ops), made with team_add_bot once Colin says yes. Write its job with one "
            "short section per mode. That Zip switches modes in one chat and starts board tasks assigned to itself "
            "when two things need doing at once."
        ),
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["dropshipping", "print_on_demand", "3d_printing", "digital_products",
                                                 "services", "local", "general"]},
            "goal": {"type": "string", "description": "One sentence in Colin's words, under 200 characters."},
            "name": {"type": "string", "description": "A short team name, under 40 characters, e.g. 'Dropship Team'."},
            "members": {"type": "array", "minItems": 1, "description": "Every member of the team, each with a brief.",
                        "items": {"type": "object", "properties": {
                            "template": {"type": "string", "description": "store, marketing, sales, outreach, pr, ops or coder."},
                            "name": {"type": "string"},
                            "brief": {"type": "string", "minLength": 250, "maxLength": 2000, "description": (
                                "What this member needs to start without asking: the exact paths and repos it will "
                                "use, the accounts, current numbers with their dates and sources, the known gaps, its "
                                "teammates and their jobs, and what needs Colin's yes. Facts you checked, not a job "
                                "description.")},
                            "hello": {"type": "string", "description": (
                                "Its first words in the team's group chat: one or two short sentences in the first "
                                "person on what I'll do first, never its own name. For example: \"I'm reading the "
                                "creator list on the Desktop and will draft the first ten messages today.\"")},
                        }, "required": ["template", "brief"]}},
            "milestones": {"type": "array", "description": (
                "The plan, in order. Leave it out to use the playbook's. Each member owns at least one."), "items": {
                "type": "object", "properties": {
                    "title": {"type": "string"},
                    "owner": {"type": "string", "description": "The member's name or template id."},
                    "done_when": {"type": "string"}}, "required": ["title"]}},
        }, "required": ["kind", "goal", "members"]},
    },
    "team_start": {
        "name": "team_start",
        "description": (
            "Create the members of a proposed team. Call it only when Colin's message says Start the team (proposal "
            "p_...), with that proposal id; it makes each member Zip and tells the hub, which starts the plan. Then "
            "reply with one line saying the team is setting up."
        ),
        "parameters": {"type": "object", "properties": {
            "proposal_id": {"type": "string"},
        }, "required": ["proposal_id"]},
    },
    "team_milestone": {
        "name": "team_milestone",
        "description": (
            "Update one of your team milestones: its state (doing, blocked, ready or done), a one-line result and a "
            "file path or link. Call it at the end of every work turn. Ready means finished with Colin's yes the one "
            "step left, after a team_need with item set to the file. Done needs a check from the taste kit: "
            "{type, answers: [{q, yes, note}]} with the kit's questions for that deliverable type; a no needs a note. "
            "With a title and no id it adds a new milestone you own, for work Colin asked for that no milestone covers."
        ),
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "The milestone to update. Leave it out to add a new one."},
            "title": {"type": "string", "description": "For a new milestone: under 80 characters."},
            "state": {"type": "string", "enum": ["doing", "blocked", "ready", "done"]},
            "result": {"type": "string", "description": "One plain line: what exists now, or where you are."},
            "link": {"type": "string", "description": "The file path in the team folder, or a public link."},
            "check": {"type": "object", "description": "For done: {type, answers: [{q, yes, note}]}."},
        }, "required": []},
    },
    "team_need": {
        "name": "team_need",
        "description": (
            "Put something only Colin can settle in the team's Needs You list: a decision between real options "
            "(kind decide, with two to five options in your words) or something he must do himself, such as buying "
            "samples or connecting a payment account (kind do). Approvals for sends, publishes and sign-ins are not "
            "raised here: try the action, and the card the gate shows becomes the Need. Keep working on everything "
            "that does not need the answer. When you ask him to approve or review something you made (posts, a page, "
            "a plan), pass item with the file's path in the team folder: the card shows a short preview and Open. "
            "To withdraw one of your own requests that no longer applies, pass close with its id and a short reason "
            "instead. For X or Instagram account access, use ask_to_sign_in with "
            "https://x.com/i/flow/login or https://www.instagram.com/accounts/login/. He signs in himself, without "
            "developer keys. A sign-in never approves a post. To ask him to approve finished posts, pass posts, one "
            "entry per post: each gets its own card, so he can say yes to one and no to another. When the request "
            "holds up something with a date (a launch, a post day), pass due, and it is raised again as that day "
            "comes close."
        ),
        "parameters": {"type": "object", "properties": {
            "close": {"type": "integer", "description": "The id of your own open request to withdraw."},
            "reason": {"type": "string", "description": "With close: why it no longer applies, one short sentence."},
            "kind": {"type": "string", "enum": ["decide", "do"]},
            "title": {"type": "string", "description": (
                "The ask itself, to Colin, under 70 characters, in plain words he would use: 'Print one grommet and one "
                "organizer', 'Pick a name', 'Buy samples ($38)'. No file names, ids, milestone numbers or tool words.")},
            "detail": {"type": "string", "description": (
                "One or two short sentences: why it matters and what you do once he answers. Plain words, no file "
                "names or internal terms. He reads this on his phone.")},
            "options": {"type": "array", "items": {"type": "string"}, "description": "decide: two to five options."},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "label": {"type": "string"}, "detail": {"type": "string"}}}},
            "item": {"type": "string", "description": (
                "The file in the team folder this asks him to approve or review, when you made one, or the pull "
                "request link github draft_pr returned: Open takes him to it on GitHub, where he merges it.")},
            "due": {"type": "string", "description": (
                "When what this holds up is due, YYYY-MM-DD or YYYY-MM-DD HH:MM in his time zone.")},
            "posts": {"type": "array", "description": (
                "Instead of kind: finished posts to approve, one card each. After his yes on one, you are woken at "
                "its time to post it, and his yes covers that exact post on that account."),
                "items": {"type": "object", "properties": {
                    "text": {"type": "string", "description": "The post exactly as it will appear."},
                    "where": {"type": "string", "description": "X or Instagram."},
                    "at": {"type": "string", "description": "When it goes out: YYYY-MM-DD or YYYY-MM-DD HH:MM."},
                    "media": {"type": "string", "description": "Optional: the image or video it carries, one line."},
                }, "required": ["text", "where", "at"]}},
        }},
    },
    "team_post": {
        "name": "team_post",
        "description": (
            "Say one more thing in the team's group chat while you are answering a check-in or a message from "
            "Colin there. Not for work turns: your work speaks through team_milestone."
        ),
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"},
        }, "required": ["text"]},
    },
    "team_site_publish": {
        "name": "team_site_publish",
        "description": (
            "Put the team's static site online at its public link. `folder` is the site folder inside the team's "
            "folder (its index.html at the top). The first publish waits for Colin's okay in Needs You. A form with "
            "data-netlify=\"true\" and a name records signups the plan card counts."
        ),
        "parameters": {"type": "object", "properties": {
            "folder": {"type": "string"},
            "check": {"type": "object", "description": "The taste kit's site check: {type: 'site', answers: [...]}."},
        }, "required": ["folder"]},
    },
}

OPS = {"team_propose": "propose", "team_milestone": "milestone", "team_need": "need", "team_post": "post",
       "team_site_publish": "publish"}
TIMEOUTS = {"publish": 180}


def _call(op: str, args: dict, session_id: str, timeout: float = 20) -> dict:
    return mac._call(op, args, timeout, session_id=session_id, area="team_space")


def handler(name: str):
    op = OPS[name]

    def run(args: dict, **kwargs):
        if name == "team_need":
            signin = _social_signins(args or {}, kwargs.get("session_id") or "")
            if signin is not None:
                return signin
        reply = _call(op, dict(args or {}), kwargs.get("session_id") or "", TIMEOUTS.get(op, 20))
        if not reply.get("ok"):
            return json.dumps({"error": reply.get("error") or "That did not work."})
        return json.dumps({"result": reply.get("result")}, ensure_ascii=False)
    return run


def _social_signins(args: dict, session_id: str) -> str | None:
    ""
    if args.get("close") is not None or args.get("kind") != "do":
        return None
    text = " ".join(str(args.get(k) or "") for k in ("title", "detail"))
    if not re.search(r"\b(connect\w*|sign[ -]?in|log[ -]?in|accounts?|handles?|access|api keys?)\b", text, re.I) \
            or re.search(r"\b(approv\w*|sign.off)\b", text, re.I):
        return None
    sites = [(r"\b(?:x(?:\.com)?|twitter(?:\.com)?)\b", "https://x.com/i/flow/login"),
             (r"\binstagram(?:\.com)?\b", "https://www.instagram.com/accounts/login/")]
    urls = [url for pattern, url in sites if re.search(pattern, text, re.I)]
    if not urls:
        return None
    from . import asksignin
    replies = [json.loads(asksignin.ask_to_sign_in(
        {"url": url, "why": "I can prepare your posts. Each post still needs your approval."}, looked=True,
        session_id=session_id))
               for url in urls]
    return json.dumps({"result": replies}, ensure_ascii=False)


def team_start(args: dict, **kwargs) -> str:
    ""

    session_id = kwargs.get("session_id") or ""
    pid = str((args or {}).get("proposal_id") or "").strip()
    plan = _call("start_plan", {"proposal_id": pid}, session_id)
    if not plan.get("ok"):
        return json.dumps({"error": plan.get("error") or "The hub has no team being started for that proposal."})
    plan = plan["result"]
    missing = [m for m in plan["members"] if not m.get("exists")]
    room = team.zips_room()
    if room is not None and len(missing) > room:


        note = home.say("This home has room for {room} more {zips} and the team needs {n}. Change It to a smaller team.",
                        room=room, zips="Zip" if room == 1 else "Zips", n=len(missing))
        _call("start_failed", {"proposal_id": pid, "note": note}, session_id)
        return json.dumps({"error": note + home.personalize(" Tell Colin in one line and offer the smaller team that fits.")})
    made, failed = [], None
    for m in plan["members"]:
        if m.get("exists"):
            made.append(m["name"])
            continue
        out = json.loads(team.add_bot({
            "id": m["bot_id"], "name": m["name"], "role": m["role"], "description": m["description"], "job": m["job"],
            "color": m["color"], "shape": m["shape"], "hello": m.get("hello") or "", "team": plan["team_id"],
            "team_name": plan["name"]}, "default", session_id))
        if not out.get("ok"):
            failed = f"{m['name']}: {out.get('error') or 'could not be created'}"
            break
        made.append(m["name"])
        _call("member_created", {"proposal_id": pid, "bot_id": m["bot_id"]}, session_id)
    if failed:
        _call("start_failed", {"proposal_id": pid, "note": failed}, session_id)
        return json.dumps({"error": home.say("The team could not be set up. {failed}. Tell Colin in one line; Start Team "
                                             "can be tapped again.", failed=failed)})
    done = _call("started", {"proposal_id": pid}, session_id)
    if not done.get("ok"):
        return json.dumps({"error": done.get("error") or "The hub could not start the team."})
    return json.dumps({"result": home.say("{made} are being set up. Tell Colin in one line that the team is setting up "
                                          "and will say hello in the group when ready; nothing else.", made=", ".join(made))})
