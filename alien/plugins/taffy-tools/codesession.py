""









import json

from . import home, mac


FIX_OR_BUILD = (
    "A fix for something broken is yours to make and report, and so is a bigger fix when Colin can't be reached and it "
    "can't wait. A build, rebuild or redesign is not yours to start: tell Colin in two or three plain sentences what "
    "you found, what you would build and what it gets him, and ask once with offer_choices. With his yes it runs as "
    "ONE code_session, never as a chain of board tasks between Zips. The wrong side of this line: on Nov 16 one failed "
    "PodGuard delivery became two days of board tasks rebuilding how PodGuard handles audio, and he knew nothing until "
    "a card asked him to authorize a test runner. If you aren't sure your plan is right, or whether it's a fix or a "
    "build, ask Opus first in a code_session with a short plan-check brief.")

SCHEMA = {
    "name": "code_session",
    "description": (
        "Your main way to write code. Starts a coding agent in a T3 Code session on this server, in a new git worktree "
        "of one of your repos (a folder under your work folder), on its own branch. Use it for any change bigger than a "
        "line or two: features, fixes, refactors, tests, site builds. " + FIX_OR_BUILD + " Give folder and a brief that "
        "stands alone: the goal, the files that matter, how to check the work (the test or build command), and what to "
        "report. Model and reasoning default to opus at high, the right choice for almost all work. Pick another model "
        "only for a clear reason, such as luna or haiku at low for a small mechanical fix. If opus can't start, the "
        "session runs on sol at high and the result says so. Set approved_build when Colin said yes to this build: "
        "the result gives a line to show him that opens the session. It returns at once. A note wakes "
        "you when it finishes, fails or asks something. Waiting for that report is your own work, never a reason to "
        "block a task or ask Colin: on a board task, park it with kanban_schedule and the report brings it back. "
        "Call again with session to read the report, with session and reply "
        "to answer or ask for changes, with session and stop when you're done. When it asks a question only Colin can "
        "decide, ask him with offer_choices using its options, and pass his pick as reply. It commits to its branch and never "
        "pushes, opens pull requests or deploys: check its commits with git yourself, then merge, push or publish "
        "with your own tools. Colin can open every session in T3 Code. Tell him what it did in plain words."),
    "parameters": {"type": "object", "properties": {
        "folder": {"type": "string", "description": "To start: the git repo, under your work folder."},
        "brief": {"type": "string", "description": "To start: self-contained instructions, up to 12,000 characters."},
        "title": {"type": "string", "description": "To start: a short name for the session, as Colin will see it."},
        "model": {"type": "string", "enum": ["sol", "astra", "luna", "opus", "sonnet", "haiku"],
                  "description": "To start: the coding model. Default opus."},
        "effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh", "max"],
                   "description": "To start: how hard it thinks. Default high."},
        "approved_build": {"type": "boolean", "description": "To start: true when Colin said yes to this build."},
        "session": {"type": "string", "description": "The session's id, from starting it or from the note."},
        "reply": {"type": "string", "description": "With session: your answer or next instruction."},
        "stop": {"type": "boolean", "description": "With session: end it."},
    }},
}


def schema() -> dict:
    return home.personalize_schema(SCHEMA)


def code_session(args: dict, **kwargs) -> str:
    reply = mac._call("code_session", args if isinstance(args, dict) else {}, 240,
                      session_id=kwargs.get("session_id") or "", area="chat")
    if not reply.get("ok"):
        return json.dumps({"error": reply.get("error") or "The coding session didn't start."}, ensure_ascii=False)
    return json.dumps({"result": reply.get("result")}, ensure_ascii=False)
