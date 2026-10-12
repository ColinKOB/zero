"""No Colin outside Colin's data (plan 2026-10-09, section 7): every prompt, note and tool text the hub writes, rendered
for a stranger's home, names the stranger and holds none of Colin's facts; the hub's code holds his facts only in the
places that are his data; the protocol parsers read the old and the new forms; the registry migration keeps his team
exactly as it was.

Run: python3 -m unittest discover -s hub/tests (from the repo)
"""

import ast
import io
import json
import re
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import (accounts, notify, approvals, calcache, config, health, hermes, history, home, notices,  # noqa: E402
                       onboarding, store, team, voice, who)
import home_fixture  # noqa: E402
from test_onboarding import _real_reload  # noqa: E402   (test_team swaps config.reload_bots out)

HUB = Path(__file__).resolve().parents[1] / "taffy_hub"
# Colin's facts and the words that would mean the text was written for him.
COLIN = re.compile(r"Colin|O'Brien|\bhis\b|\bhim\b|\bhimself\b|\balien\b|America/New_York|/home/jdoe|"
                   r"tail0000|Lakeview|lakeview\.edu|goodworks|D\.R\. Acme Homes|AbCd1234|jdoe1")


def _fresh_store(tmp: Path) -> None:
    config.HUB_DIR = tmp
    config.HUB_DB = tmp / "hub.db"
    store._local.conn = None
    store._schema_ready = False


class StrangerHome(unittest.TestCase):
    """Sam Rivera's home: Pacific time, metric units, a shared plan, one IMAP mailbox, no board, no Tally."""

    def setUp(self):
        saved = (config.HUB_DIR, config.HUB_DB, config.HERMES_HOME)
        self.addCleanup(lambda: (setattr(config, "HUB_DIR", saved[0]), setattr(config, "HUB_DB", saved[1]),
                                 setattr(config, "HERMES_HOME", saved[2]),
                                 setattr(store._local, "conn", None), setattr(store, "_schema_ready", False)))
        _fresh_store(Path(tempfile.mkdtemp()))
        config.HERMES_HOME = config.HUB_DIR / "hermes"   # the Zips' own descriptions are the home's data
        bots, by_id = list(config.BOTS), dict(config.BOTS_BY_ID)

        def restore_team():
            config.BOTS[:] = bots
            config.BOTS_BY_ID.clear()
            config.BOTS_BY_ID.update(by_id)
        self.addCleanup(restore_team)
        config.BOTS[:] = [config.LEAD]   # a new home's team: the lead alone
        config.BOTS_BY_ID.clear()
        config.BOTS_BY_ID["default"] = config.LEAD
        home_fixture.use_in(self, "stranger")
        onboarding.save({"agent_name": "", "person_name": "Sam Rivera", "about": {}, "sources": {}, "voice": "",
                         "no_computer": False, "look": {"body": "", "outfit": ""}})

    def rendered(self) -> dict[str, str]:
        from taffy_hub import prepared, quietlook
        out = {
            "voice_prompt": voice.voice_prompt("default", "You are Zero."),
            "relay": voice.relay_instructions(),
            "job_guide": voice.job_guide(),
            "ask_bot": json.dumps(voice.ask_bot_tool()),
            "update_job": json.dumps(voice.update_job_tool()),
            "greeting": voice.greeting(),
            "greeting_late": voice.greeting(late=True),
            "work_status_tool": json.dumps(voice.work_status_tool()),
            "summary_prompt": voice.SUMMARY_PROMPT.format(name="Zero", person=who.first(), transcript=""),
            "team_block": team._soul_block(),
            "answer_guide": team.ANSWER_GUIDE.format(asker="Money", person=who.first()),
            "cal_prompt": calcache.CAL_PROMPT.format(person=who.full(), first=who.first(),
                                                     tz=home.timezone_name(), start="a", end="b"),
            "trello_prompt": calcache.TRELLO_PROMPT.format(person=who.full(), first=who.first(),
                                                           tz=home.timezone_name(), start="a", end="b", board="x"),
            "gate_prompt": notices.GATE_PROMPT.format(person=who.full(), first=who.first(), evidence="", question="",
                                                      options="", facts="", excerpt="", material="", prefs="",
                                                      now=""),
            "judge_prompt": notices.PROMPT.format(person=who.full(), first=who.first(), facts="", evidence="",
                                                  question="", excerpt="", owner="", material="", prefs="", now="",
                                                  options="", limit=90, draft=""),
            "notify_line": notify.LINE_PROMPT.format(name="Zero", person=who.full(), first=who.first(), target=90,
                                                     context="", text=""),
            "notify_buzz": notify.BUZZ_PROMPT.format(name="Zero", person=who.full(), first=who.first(), job="x",
                                                     buzzed="", now="", target=90, text=""),
            "prepared": prepared.NOTE.format(first=who.first(), possessive=who.possessive(), lead="Zero",
                                             evidence="", account="test", to="", subject="", body=""),
            "resume": prepared.RESUME.format(first=who.first(), label="Send", lead="Zero", to="x"),
            "quiet_look": quietlook.NOTE.format(first=who.first(), possessive=who.possessive(), owners="default",
                                                accounts="the email and calendar tools", now="", prefs="",
                                                open="", dismissed="", off="", calendar="", cards=""),
            "late_allow": approvals.late_note({"reason": "Run the tests"}, True),
            "late_deny": approvals.late_note({"reason": "Run the tests"}, False),
            "no_such": accounts.no_such("trello", {"services": []}),
            "no_connectors": accounts.no_connectors_message(),
            "hub_note": who.hub_note(),
            "signoff": notices.signoff("test"),
            "prefs_heading": notices.prefs_heading(),
            "health": " ".join(health.message(k, ok) for k, ok in health.MESSAGES),
            "now": __import__("taffy_hub.briefing", fromlist=["_now"])._now(),
        }
        return out

    def test_every_text_names_sam_and_holds_no_colin_fact(self):
        for name, text in self.rendered().items():
            with self.subTest(text=name):
                m = COLIN.search(text)
                self.assertIsNone(m, f"{name}: {m and text[max(0, m.start() - 60):m.end() + 60]!r}")
                self.assertNotIn("Eastern", text, name)
        texts = self.rendered()
        self.assertIn("Sam", texts["voice_prompt"])
        self.assertIn("metric", texts["voice_prompt"])
        self.assertIn("Pacific", texts["voice_prompt"])
        self.assertIn("British spelling", texts["voice_prompt"])
        self.assertIn("Sam's email", texts["voice_prompt"])
        self.assertNotIn("Trello", texts["voice_prompt"])        # no board in this home
        self.assertNotIn("money tools", texts["voice_prompt"])   # no Tally
        self.assertEqual(texts["greeting"], "Hey Sam, what's up?")
        self.assertIn("Request from the call: ", texts["job_guide"])
        self.assertEqual(texts["hub_note"], "[Hub note, not a message from Sam]")
        self.assertEqual(texts["signoff"], "Sam")

    def test_time_zone_and_quiet_hours_follow_the_home(self):
        self.assertEqual(notices.settings()["quiet_start"], 22)
        self.assertEqual(notices.settings()["quiet_end"], 6)
        self.assertEqual(notices._et(0).utcoffset().total_seconds(), -8 * 3600)   # Jan 1 1970 in Los Angeles
        self.assertIn("Pacific", __import__("taffy_hub.briefing", fromlist=["_now"])._now())

    def test_a_shared_plan_is_spent_frugally(self):
        s = notices.settings()
        self.assertEqual((s["haiku_daily"], s["prep_daily"], s["quiet_wakes"]), (30, 3, 2))
        notices.set_enabled(True)
        self.assertNotIn("haiku_daily", store.get_setting(notices.SETTING))
        with home_fixture.use(None):
            self.assertEqual(notices.settings()["haiku_daily"], 60)

    def test_shared_plan_wording(self):
        self.assertEqual(home.plan_kind(), "shared")
        self.assertIn("shared Claude plan", health.message("claude", False))
        self.assertEqual(voice.start_error(FileNotFoundError()), voice.NO_CODEX_SHARED)


class ColinHome(unittest.TestCase):
    """With no settings file the text is Colin's, as before."""

    def test_voice_prompt_for_colin(self):
        with home_fixture.use(None):
            saved = onboarding.person_name
            try:
                onboarding.person_name = lambda full=True: "Colin O'Brien" if full else "Colin"
                text = voice.voice_prompt("default", "")
                self.assertIn("Colin's accounts, email, calendar, Trello, money tools, the web, your own computer, "
                              "and Colin's Mac when Colin asks", text)
                self.assertIn("Use American spelling and Colin's units: Fahrenheit, miles", text)
                self.assertIn("Times are Eastern (New York).", text)
                self.assertIn("Fahrenheit and US units, Eastern time", voice.job_guide())
                self.assertEqual(notices.signoff("lakeview"), "Best,\nColin O'Brien")
                self.assertEqual(notices.signoff("personal"), "Colin")
            finally:
                onboarding.person_name = saved


# The places in the hub's code that hold Colin's data on purpose, and the protocol strings kept for one release.
ALLOWED = {
    "home.py": "the defaults are Colin's home",
    "config.py": "OWNER_ZIPS, Colin's five original Zips for migrate-registry",
    "history.py": "the old call request marker, still read",
    "apns.py": "the app's bundle id, a default",
    "health.py": "log-only fix notes naming repo paths",
    "consent.py": "the repo's alien/ folder", "safe_files.py": "the repo's alien/ folder",
    "noticers.py": "the repo's alien/ folder",
    "store.py": "the reactions table's who column says 'colin' for the person, an identifier",
}
# Internal identifiers that look like a name: message classes, table values, tool argument and log event names.
IDENTIFIERS = {'"colin"', "'colin'", '"colin_said"', '"colin_started"', '"colin_stopped"',
               '"who=\'colin\' AND told=0 ORDER BY created_at"', '"UPDATE reactions SET told=1 WHERE bot_id=? AND '
                                                                 'who=\'colin\'"'}
CODE_FACTS = re.compile(r"Colin(?!_said|_started|_stopped|_lines)|jdoe|tail0000|America/New_York|Lakeview|AbCd1234|D\.R\. Acme Homes|\bhis\b|"
                        r"\bhim\b|\bhimself\b", re.I)


class SourceHoldsNoColin(unittest.TestCase):
    def test_string_literals_outside_colins_data(self):
        """Every string in the hub's code, docstrings and comments aside, is free of Colin's facts and of "his" or
        "him", except where the hub keeps his data or an identifier on purpose."""
        found = []
        for f in sorted(HUB.glob("*.py")):
            if f.name in ALLOWED and f.name in ("home.py",):
                continue
            src = f.read_text()
            docs = set()
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
                    b = node.body[0]
                    if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str):
                        docs.update(range(b.lineno, b.end_lineno + 1))
            for tok in tokenize.generate_tokens(io.StringIO(src).readline):
                if tok.type not in (tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)):
                    continue
                if tok.start[0] in docs or tok.string in IDENTIFIERS:
                    continue
                if CODE_FACTS.search(tok.string):
                    found.append(f"{f.name}:{tok.start[0]}: {tok.string[:100]}")
        allowed = [x for x in found if x.split(":")[0] in ALLOWED]
        other = [x for x in found if x.split(":")[0] not in ALLOWED]
        self.assertEqual(other, [], "\n".join(other))
        self.assertLess(len(allowed), 20, "\n".join(allowed))


class Protocol(unittest.TestCase):
    def test_card_request_old_and_new(self):
        for text in ("A card asking Colin to allow it is now in his app (request 12).",
                     "A card showing it is now in Colin's app (request 12).",
                     "A card asking Sam to allow it is now in the app (request 12)."):
            m = hermes._CARD_REQUEST.search(text)
            self.assertEqual(m.group(1), "12", text)

    def test_call_request_markers(self):
        for marker in ("Request from the call: ", "Colin's request: "):
            [m] = history.classify([{"role": "user", "text": f"[From a voice call] guide text\n\n{marker}Add milk"}])
            self.assertEqual((m["class"], m["display"]), ("call_turn", "Add milk"))

    def test_reactions_from_any_name(self):
        for name in ("Colin", "Sam"):
            text = f"[Reactions from {name} since your last message: love on \"hi\". Context only.]\nWhat's up?"
            self.assertEqual(history.strip(text), "What's up?")

    def test_hub_notes_keep_their_prefix(self):
        with home_fixture.use("stranger"):
            [m] = history.classify([{"role": "user", "text": who.hub_note() + " Carry on."}])
            self.assertEqual(m["class"], "hub_note")

    def test_tool_label_names_no_host(self):
        self.assertEqual(hermes.TOOL_LABELS["terminal"], "Working on its computer")


class Registry(unittest.TestCase):
    def setUp(self):
        saved = (config.HERMES_HOME, config.REGISTRY, config.NAMES, list(config.BOTS), dict(config.BOTS_BY_ID),
                 config._registry_mtime, config.reload_bots)

        def restore():
            config.HERMES_HOME, config.REGISTRY, config.NAMES = saved[:3]
            config.reload_bots = saved[6]
            config.BOTS[:] = saved[3]
            config.BOTS_BY_ID.clear()
            config.BOTS_BY_ID.update(saved[4])
            config._registry_mtime = saved[5]
        self.addCleanup(restore)
        tmp = Path(tempfile.mkdtemp())
        config.HERMES_HOME, config.REGISTRY, config.NAMES = tmp / "hermes", tmp / "bots.json", tmp / "names.json"
        for b in ["default", "school", "builder", "clients", "money", "career", "family"]:
            (config.HERMES_HOME / "profiles" / b).mkdir(parents=True)
        config.REGISTRY.write_text(json.dumps([{"id": "family", "name": "Family", "role": "Family plans",
                                                "color": "#123456", "shape": "cloud", "symbol": "sparkles"}]))
        config._registry_mtime = None
        config.reload_bots = _real_reload

    def team(self):
        config._registry_mtime = None
        config.reload_bots()
        return [(b.id, b.name, b.color, b.symbol, b.shape) for b in config.BOTS]

    def test_migration_keeps_colins_team_exactly(self):
        with home_fixture.use(None):
            before = self.team()
            self.assertEqual([b[0] for b in before], ["default", "school", "builder", "clients", "money", "career",
                                                      "family"])
            new = config.migrate_registry()
            self.assertEqual([e["id"] for e in new], ["school", "builder", "clients", "money", "career", "family"])
            self.assertEqual(self.team(), before)
            self.assertEqual(config.migrate_registry(), json.loads(config.REGISTRY.read_text()))   # idempotent
            self.assertEqual(len(json.loads(config.REGISTRY.read_text())), 6)
            self.assertFalse(config.is_custom("school"))
            self.assertTrue(config.is_custom("family"))

    def test_another_home_builds_its_team_from_the_registry_alone(self):
        with home_fixture.use("riley"):
            self.assertEqual([b[0] for b in self.team()], ["default", "family"])
            self.assertTrue(config.is_custom("school"))
            config.migrate_registry()   # what a home created from the owner's would carry
            self.assertEqual([b[0] for b in self.team()], ["default", "school", "builder", "clients", "money",
                                                           "career", "family"])

    def test_pair_link(self):
        from taffy_hub import cli
        self.assertEqual(cli.pair_link("https://alien.example.ts.net:6444", "ABCD2345"),
                         "zero://pair?server=https%3A%2F%2Falien.example.ts.net%3A6444&code=ABCD2345")
        fp = "3a9f" + "0" * 60
        self.assertEqual(cli.pair_link("https://10.0.0.5:8650", "ABCD1234", fp),
                         f"zero://pair?server=https%3A%2F%2F10.0.0.5%3A8650&code=ABCD1234&fp={fp}")


if __name__ == "__main__":
    unittest.main()
