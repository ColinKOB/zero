"""The facts store (facts.py): saving, replacing, privacy, relevance in the user turn, recall, briefings, the app's
list, the old team notes, and the person's own words from any Zip's chat (docs/qa/2026-10-10-memory-facts.md).

Everything here is fictional and runs in temporary folders; nothing reads a live home.
"""

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import config, extract, facts, store, team  # noqa: E402
import home_fixture  # noqa: E402

_tmp = Path(tempfile.mkdtemp())
_saved = {}


def _bots():
    for b in [config.Bot("default", "Nova", "Lead", "#F2711C", "sparkles"),
              config.Bot("inbox", "Inbox", "Mail", "#06B6D4", "tray"),
              config.Bot("todo", "To-Do", "Lists", "#84CC16", "checklist"),
              config.Bot("money", "Money", "Money", "#EAB308", "dollarsign.circle")]:
        config.BOTS_BY_ID[b.id] = b
    config.BOTS[:] = list(config.BOTS_BY_ID.values())


def fresh(test: unittest.TestCase):
    for name in ("HUB_DB", "HERMES_HOME"):
        _saved.setdefault(name, getattr(config, name))
    _saved.setdefault("reload", config.reload_bots)
    _saved.setdefault("notes_file", team.NOTES_FILE)
    folder = Path(tempfile.mkdtemp(dir=_tmp))
    config.HUB_DB = folder / "hub.db"
    config.HERMES_HOME = folder / "hermes"
    config.HERMES_HOME.mkdir()
    team.NOTES_FILE = folder / "team-notes.md"
    config.reload_bots = lambda: False
    bots_before, by_id_before = list(config.BOTS), dict(config.BOTS_BY_ID)
    _bots()
    store._local.conn = None
    store._schema_ready = False
    team._ready = False
    facts.reset_for_tests()
    home_fixture.use_in(test, {"person": {"name": "Sam Rivera", "first_name": "Sam", "timezone": "America/New_York"}})

    def undo():
        config.HUB_DB, config.HERMES_HOME = _saved["HUB_DB"], _saved["HERMES_HOME"]
        config.reload_bots = _saved["reload"]
        team.NOTES_FILE = _saved["notes_file"]
        config.BOTS[:] = bots_before
        config.BOTS_BY_ID.clear()
        config.BOTS_BY_ID.update(by_id_before)
        store._local.conn = None
        store._schema_ready = False
        team._ready = False
        facts.reset_for_tests()
    test.addCleanup(undo)


def state_db(bot: str, rows: list[tuple]) -> None:
    """A Hermes state.db for one Zip: (session_id, source, role, content, timestamp) rows, with its FTS5 index."""
    home = config.HERMES_HOME if bot == "default" else config.HERMES_HOME / "profiles" / bot
    home.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(home / "state.db")
    c.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT,
                               timestamp REAL);
        CREATE VIRTUAL TABLE messages_fts USING fts5(content, tool_name, tool_calls);
    """)
    for sid, source, role, content, ts in rows:
        c.execute("INSERT OR IGNORE INTO sessions (id, source) VALUES (?, ?)", (sid, source))
        mid = c.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                        (sid, role, content, ts)).lastrowid
        c.execute("INSERT INTO messages_fts (rowid, content, tool_name, tool_calls) VALUES (?, ?, '', '')",
                  (mid, content))
    c.commit()
    c.close()


COFFEE = "Sam's usual coffee is the Cafe Bustelo espresso brick, 10 oz, from the Stop & Shop on Hope Street."


class Saving(unittest.TestCase):
    def setUp(self):
        fresh(self)

    def test_save_and_find_in_a_fresh_conversation(self):
        fid, msg = facts.save("default", COFFEE, subject="coffee", kind="preference", session_id="s1")
        self.assertIn(f"fact {fid}", msg)
        found = facts.search("Can you order new coffee for me?", "todo")
        self.assertEqual([f["id"] for f in found], [fid])
        rel = facts.relevant("Can you order new coffee for me?", "todo", "s2")
        self.assertIn("Bustelo", rel)
        self.assertIn("Stop & Shop", rel)
        self.assertLessEqual(len(rel), facts.RELEVANT_MAX_CHARS)
        self.assertIn("Nova noted it", rel)    # the source, in plain words

    def test_relevant_is_not_repeated_in_one_conversation(self):
        facts.save("default", COFFEE, subject="coffee", kind="preference")
        self.assertIn("Bustelo", facts.relevant("order coffee please", "default", "s1"))
        self.assertEqual(facts.relevant("more coffee talk", "default", "s1"), "")
        self.assertIn("Bustelo", facts.relevant("coffee again", "default", "s2"))   # a new conversation hears it

    def test_unrelated_message_gets_nothing(self):
        facts.save("default", COFFEE, subject="coffee", kind="preference")
        for i in range(8):
            facts.save("default", f"Sam likes thing number {i} a great deal at the bakery.", kind="preference")
        self.assertEqual(facts.relevant("What time is it in Lisbon right now?", "default", "s1"), "")
        # Words in most facts (the person's name, "likes") are not a match on their own.
        self.assertEqual(facts.search("Sam likes", "default"), [])

    def test_same_subject_nearly_same_text_updates_instead_of_duplicating(self):
        a, _ = facts.save("default", COFFEE, subject="coffee", kind="preference")
        b, msg = facts.save("todo", "Sam's usual coffee is the Cafe Bustelo espresso brick, 10 ounce, from Stop & Shop "
                                    "on Hope Street.", subject="Coffee", kind="preference")
        self.assertIn(f"replacing fact {a}", msg)
        self.assertEqual([f["id"] for f in facts.current(None, kinds=("preference",))], [b])
        same, msg = facts.save("inbox", COFFEE.upper().lower().replace("sam's", "Sam's"), subject="coffee",
                               kind="preference")
        self.assertTrue(msg.startswith("Saved") or msg.startswith("That is already"))

    def test_correction_replaces_and_the_old_fact_is_kept_as_history(self):
        a, _ = facts.save("default", COFFEE, subject="coffee", kind="preference")
        b, msg = facts.save("default", "Sam switched to Lavazza Super Crema whole bean, bought at Whole Foods.",
                            subject="coffee", kind="preference", replaces=a)
        self.assertIn(f"replacing fact {a}", msg)
        self.assertEqual(facts.get(a)["replaced_by"], b)
        rel = facts.relevant("order my coffee", "todo", "s9")
        self.assertIn("Lavazza", rel)
        self.assertNotIn("Bustelo", rel)
        # Replacing an already replaced fact replaces its newest version.
        c, _ = facts.save("default", "Sam now buys Lavazza at Wegmans.", subject="coffee", kind="place", replaces=a)
        self.assertEqual(facts.get(b)["replaced_by"], c)

    def test_other_facts_on_the_subject_are_offered_for_replacing(self):
        facts.save("default", COFFEE, subject="coffee", kind="preference")
        _, msg = facts.save("default", "Sam buys coffee every two weeks.", subject="coffee", kind="supply")
        self.assertIn("Also saved about coffee", msg)
        self.assertIn("replaces", msg)

    def test_secrets_never_saved_and_private_kept_to_owner(self):
        for bad in ["Sam's card is 4111 2222 3333 4444.", "The Square password is hunter2.",
                    "Sam's verification code is 551204."]:
            with self.subTest(bad=bad), self.assertRaises(facts.FactError):
                facts.save("default", bad)
        fid, msg = facts.save("money", "Sam's business checking balance was $1,234.56 on Oct 9.", kind="other")
        self.assertIn("Only you", msg)
        self.assertEqual(facts.get(fid)["visibility"], "owner")
        self.assertEqual(facts.search("checking balance", "default"), [])
        self.assertEqual(len(facts.search("checking balance", "money")), 1)
        hid, _ = facts.save("default", "Sam takes a daily allergy medication in the morning.", kind="other")
        self.assertEqual(facts.get(hid)["visibility"], "owner")
        # A phone number and a 1-800 number are not account numbers.
        facts.save("inbox", "Sam orders contact lenses from Lens Shop by phone at 1-800-266-8228.",
                   subject="contact lenses", kind="place")
        # A job's pay saved by the lead stays a team fact.
        pid, _ = facts.save("default", "Sam applied Oct 7 for Production Supervisor at Narragansett Bay Foods, "
                                       "$58k to $64k.", subject="job", kind="plan")
        self.assertEqual(facts.get(pid)["visibility"], "team")

    def test_limits_for_zips(self):
        for i in range(facts.FACTS_PER_SESSION_HOUR):
            facts.save("default", f"Sam decided on item number {i} for the shop.", session_id="s1")
        with self.assertRaises(facts.FactError):
            facts.save("default", "Sam decided one more thing for the shop.", session_id="s1")
        facts.save("hub", "Sam said one more thing in chat about the shop.", session_id="s1", source="chat")


class TeamNotesStream(unittest.TestCase):
    def setUp(self):
        fresh(self)

    def test_old_team_notes_become_facts_with_the_same_numbers(self):
        c = store.db()
        team._db()
        c.execute("INSERT INTO team_notes (id, bot_id, text, created_at) VALUES (7, 'default', "
                  "'Sam applied for a new job on Oct 7.', 1)")
        c.execute("INSERT INTO team_notes (id, bot_id, text, created_at, replaced_by) VALUES (5, 'default', "
                  "'Sam was thinking about a job.', 1, 7)")
        c.commit()
        facts.reset_for_tests()
        self.assertEqual(facts.get(7)["text"], "Sam applied for a new job on Oct 7.")
        self.assertEqual(facts.get(5)["replaced_by"], 7)
        nid, _ = facts.save("default", "Sam starts the new job in November.", kind="plan")
        self.assertGreater(nid, 7)
        facts.reset_for_tests()   # runs once: a restart does not copy again
        self.assertEqual(len(facts.current(None)), 2)

    def test_preferences_are_not_broadcast_but_plans_are(self):
        facts.save("default", COFFEE, subject="coffee", kind="preference")
        facts.save("default", "Sam applied for the Gregg's job on Oct 6.", subject="job", kind="plan")
        first = team.context_for("todo", "t1", first=True)
        self.assertIn("Gregg's", first)
        self.assertNotIn("Bustelo", first)
        # The broadcast fact is not given again as "relevant" in the same conversation; the preference is.
        rel = team.context_for("todo", "t1", first=False, message="Gregg's job and coffee")
        self.assertIn("Bustelo", rel)
        self.assertNotIn("Gregg's job on Oct 6", rel)

    def test_save_fact_tool_and_feed(self):
        out = team.save_fact("default", {"fact": COFFEE, "subject": "coffee", "kind": "preference"}, "s1")
        self.assertIn("Saved as fact", out)
        feed = store.db().execute("SELECT * FROM feed WHERE kind='team'").fetchall()
        self.assertEqual(len(feed), 1)
        with self.assertRaises(team.TeamError):
            team.save_fact("default", {"fact": "The PIN is 4471.", "subject": "pin", "kind": "other"}, "s1")

    def test_team_note_keeps_its_rules(self):
        self.assertIn("team note", team.add_note("default", "Sam applied for the Gregg's job on Oct 6."))
        with self.assertRaises(team.TeamError):
            team.add_note("default", "Sam's rent is 2,000 dollars a month.")


class OwnWords(unittest.TestCase):
    def setUp(self):
        fresh(self)
        t = time.time() - 14 * 86400
        state_db("todo", [("api_1", "api_server", "user", "Start a Walmart list for me: paper towels, dog food for "
                                                         "Pepper, and AA batteries.", t),
                          ("api_1", "api_server", "assistant", "I started your Walmart list.", t + 5),
                          ("cron_1", "cron", "user", "Walmart list check for the week ahead.", t)])
        state_db("money", [("api_m", "api_server", "user", "I paid $412.50 at Walmart for the bakery supplies "
                                                          "yesterday afternoon.", t)])
        state_db("default", [("api_d", "api_server", "user", "[Voice call ended] Walmart transcript here.", t),
                             ("api_d", "api_server", "user", "[From a voice call] Sam is on a call. Request from the "
                                                             "call: Put lightbulbs on the Walmart list too please.", t)])

    def test_own_words_from_any_zip(self):
        found = facts.message_search("What's on my Walmart list?", "default")
        texts = " ".join(f["text"] for f in found)
        self.assertIn("paper towels", texts)
        self.assertIn("lightbulbs", texts)            # a call's request, without the guide before it
        self.assertNotIn("transcript here", texts)    # the hub's own notes are not his words
        self.assertNotIn("for the week ahead", texts)  # a scheduled job's prompt is not his words
        self.assertNotIn("412", texts)                 # Money's amounts stay with Money
        self.assertIn("412", " ".join(f["text"] for f in facts.message_search("Walmart bakery supplies", "money")))

    def test_relevant_includes_own_words_when_no_fact(self):
        rel = facts.relevant("What's on my Walmart list?", "default", "api_d")
        self.assertIn("Sam to To-Do", rel)
        self.assertIn("paper towels", rel)
        self.assertLessEqual(len(rel), facts.RELEVANT_MAX_CHARS)

    def test_recall(self):
        facts.save("todo", "Sam keeps a Walmart list in To-Do's work folder.", subject="walmart list", kind="other")
        out = facts.recall("walmart list", "default")
        self.assertIn("Saved facts", out)
        self.assertIn("own words", out)
        self.assertIn("paper towels", out)
        self.assertIn("Nothing saved", facts.recall("sailboat regatta", "default"))


class Reading(unittest.TestCase):
    def setUp(self):
        fresh(self)

    def test_briefing_has_every_fact_by_kind(self):
        facts.save("default", COFFEE, subject="coffee", kind="preference")
        facts.save("default", "Sam applied for the Gregg's job on Oct 6.", subject="job", kind="plan")
        facts.save("money", "Sam's business checking balance was $1,234.56 on Oct 9.")
        text = facts.briefing_text("default")
        self.assertIn("Preferences:", text)
        self.assertIn("Plans:", text)
        self.assertNotIn("6,067", text)   # Money's own fact
        self.assertIn("6,067", facts.briefing_text("money"))

    def test_app_list_edit_and_delete(self):
        a, _ = facts.save("default", COFFEE, subject="coffee", kind="preference")
        b, _ = facts.save("default", "Sam switched to Lavazza at Whole Foods.", subject="coffee", kind="preference",
                          replaces=a)
        items = facts.for_app()
        self.assertEqual([i["id"] for i in items], [b])
        self.assertEqual(items[0]["heading"], "Preferences")
        self.assertTrue(items[0]["source"].startswith("Nova noted it"))
        facts.edit(b, "Sam drinks Lavazza Super Crema from Whole Foods.")
        self.assertEqual(facts.get(b)["text"], "Sam drinks Lavazza Super Crema from Whole Foods.")
        self.assertTrue(facts.delete(b))
        self.assertIsNone(facts.get(a))          # the older version goes too
        self.assertEqual(facts.search("coffee Lavazza Bustelo", "default"), [])
        self.assertFalse(facts.delete(b))
        c = facts.add_by_person("Sam prefers oat milk.", kind="preference", subject="milk")
        self.assertEqual(c["source"], "app")


class Extractor(unittest.TestCase):
    def setUp(self):
        fresh(self)
        self.prompts = []
        from unittest import mock
        p = mock.patch.object(extract, "ENABLED", True)
        p.start()
        self.addCleanup(p.stop)

        async def haiku(prompt):
            self.prompts.append(prompt)
            return self.answer
        saved = extract.ask_haiku
        extract.ask_haiku = haiku
        self.addCleanup(setattr, extract, "ask_haiku", saved)
        self.answer = '{"facts": []}'

    def test_signal_is_code_only_and_selective(self):
        for yes in ["Grabbed coffee: the Cafe Bustelo brick from the Stop & Shop on Hope Street. That's my usual.",
                    "Actually, not that brand anymore, I switched to Lavazza.",
                    "I applied for the Gregg's job on October 6.",
                    "My sister Lucia's birthday is November 3."]:
            self.assertTrue(extract.signal(yes), yes)
        for no in ["Can you order new coffee for me?", "What's the weather tomorrow?", "ok thanks",
                   "[Voice call ended] I like coffee from Stop & Shop", "Email Jordan that the check goes out."]:
            self.assertFalse(extract.signal(no), no)

    def test_extract_saves_replaces_and_refuses(self):
        import asyncio
        a, _ = facts.save("default", COFFEE, subject="coffee", kind="preference")
        self.answer = json.dumps({"facts": [
            {"subject": "coffee", "kind": "preference", "text": "Sam switched to Lavazza Super Crema whole bean from "
             "Whole Foods.", "replaces": a, "confidence": "said", "private": False},
            {"subject": "square", "kind": "other", "text": "Sam's Square password is hunter2.", "replaces": None},
            {"subject": "x", "kind": "other", "text": "Sam likes X.", "replaces": 999}]})
        ids = asyncio.run(extract._extract("default", "Actually I switched to Lavazza from Whole Foods.",
                                           "Got it, Lavazza from Whole Foods from now on.", "s1", "", "chat"))
        self.assertEqual(len(ids), 2)                     # the password was refused
        self.assertEqual(facts.get(a)["replaced_by"], ids[0])
        self.assertIsNone(facts.get(ids[1])["replaced_by"])
        self.assertIn(f"[{a}]", self.prompts[0])          # the same-subject fact was shown, so it could replace it
        self.assertEqual(facts.get(ids[0])["source"], "chat")
        self.assertIn("said it to Nova", facts.source_words(facts.get(ids[0])))

    def test_budget(self):
        import asyncio
        extract.HAIKU_DAILY, saved = 1, extract.HAIKU_DAILY
        self.addCleanup(setattr, extract, "HAIKU_DAILY", saved)
        asyncio.run(extract._extract("default", "I always buy oat milk at Whole Foods.", "", None, "", "chat"))
        asyncio.run(extract._extract("default", "I always buy rye flour at Whole Foods.", "", None, "", "chat"))
        self.assertEqual(len(self.prompts), 1)

    def test_orders(self):
        self.assertTrue(extract.is_order("Your Whole Foods Market order"))
        self.assertTrue(extract.is_order("Order confirmation: your Chewy order #4410992"))
        self.assertFalse(extract.is_order("Your order has shipped"))
        self.assertFalse(extract.is_order("Weekly deals for you"))
        rows = extract.purchase_facts({"store": "Chewy", "ordered_on": "2026-09-26", "items": [
            {"name": "Blue Buffalo Life Protection Lamb Dog Food", "size": "30 lb", "quantity": 1, "price": "$64.98"}]},
            "Sam")
        subject, text, data = rows[0]
        self.assertEqual(text, "Sam bought 1 x Blue Buffalo Life Protection Lamb Dog Food (30 lb) from Chewy on "
                               "Sep 26, 2026 for $64.98.")
        self.assertEqual(subject, "dog food")
        self.assertEqual(data["quantity"], 1)
        self.assertIsNotNone(data["ordered_at"])


class Noise(unittest.TestCase):
    """What the Oct 10 eval showed coming back as "relevant" when it was not."""

    def setUp(self):
        fresh(self)
        facts.save("default", COFFEE, subject="coffee", kind="preference")
        facts.save("todo", "Pepper eats Purina Pro Plan salmon, the 35 pound bag, from Petco.", subject="dog food",
                   kind="supply")
        facts.save("todo", "Tess is Sam's part-time baker and works Thursday to Saturday.", subject="tess",
                   kind="person")
        t = time.time() - 3600
        state_db("default", [("api_1", "api_server", "user", "Order my coffee please.", t),
                             ("api_1", "api_server", "user", "Who's my accountant again, the one for the bakery?", t),
                             ("api_1", "api_server", "user", "Actually I switched coffee: Lavazza from Whole Foods "
                                                             "now, since the Bustelo brick ran out.", t)])

    def test_one_weak_shared_word_is_not_relevant(self):
        rel = facts.relevant("Grabbed coffee: Death Wish ground, the 16 ounce bag, from Whole Foods.", "default",
                             "s1")
        self.assertIn("Bustelo", rel)
        self.assertNotIn("Purina", rel)    # "bag" alone

    def test_questions_and_short_requests_are_not_snippets(self):
        rel = facts.relevant("Can you order new coffee for me?", "default", "s2")
        self.assertNotIn("Order my coffee please", rel)
        self.assertIn("Lavazza", facts.relevant("Did I switch coffee brands?", "default", "s3"))   # a statement is

    def test_board_task_prompts_and_hub_notes_get_nothing(self):
        self.assertEqual(facts.relevant("work kanban task t_c088a01b", "todo", "k1"), "")
        self.assertEqual(facts.relevant("[Board update from the hub, not a message from Sam] To-Do works on it",
                                        "default", "k2"), "")
        self.assertIn("Purina", facts.relevant("[From Nova] Which dog food does Pepper eat?\n\n(Nova, a teammate, "
                                               "asked you this.)", "todo", "k3"))


class CallContext(unittest.TestCase):
    def setUp(self):
        fresh(self)
        from unittest import mock
        from taffy_hub import voice
        self.voice = voice
        self.sent, self.logged = [], []

        async def call(method, params, timeout=40):
            self.sent.append((method, params))
            return {}
        for p in (mock.patch.object(voice.server, "call", call), mock.patch.object(voice, "CONTEXT_ON", True),
                  mock.patch.object(voice.calllog, "write", lambda cid, kind, **kw: self.logged.append((kind, kw)))):
            p.start()
            self.addCleanup(p.stop)
        facts.save("default", "Sam applied on October 6, 2026 for the Kitchen Operations Manager job at Gregg's "
                              "Restaurants in East Providence.", subject="job search", kind="plan")
        facts.save("default", "Sam wants to write a bread cookbook by next fall.", subject="goals", kind="plan")
        facts.save("default", "Sam's usual coffee is Peet's Big Bang whole bean, bought at Wegmans.",
                   subject="coffee", kind="preference")

    def test_note_and_background_plans(self):
        note, keys = facts.call_context("thinking about taking that job with Gregg's, my other passions", "default")
        self.assertIn("Gregg's", note)
        self.assertIn("cookbook", note)      # a plan it may clash with, though no word matches
        self.assertNotIn("Peet's", note)
        self.assertEqual(facts.call_context("job with Gregg's", "default", set(keys)), ("", []))

    def test_pushed_as_quiet_developer_context_while_talking_once(self):
        import asyncio
        v = self.voice
        call = v.Call(id="c1", bot_id="default", voice="cove")

        async def go():
            for word in "Can you order new coffee for me".split():
                await v.on_event(call, "thread/realtime/transcript/delta", {"role": "user", "delta": " " + word})
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            await v.push_context(call, "order new coffee for me please now", final=True)
        asyncio.run(go())
        pushes = [p for m, p in self.sent if m == "thread/realtime/appendText"]
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0]["role"], "developer")
        self.assertIn("Peet's", pushes[0]["text"])
        logged = [kw for kind, kw in self.logged if kind == "context"]
        self.assertEqual(logged[0]["via"], "search")
        self.assertLess(logged[0]["search_ms"], 500)

    def test_off_unless_the_hub_runs(self):
        from unittest import mock
        v = self.voice
        with mock.patch.object(v, "CONTEXT_ON", False):
            call = v.Call(id="c3", bot_id="default", voice="cove")
            v.line_context(call, "Can you order new coffee for me today please")
            v.context_soon(call)
        self.assertEqual(self.sent, [])
        with mock.patch.object(extract, "ENABLED", False):
            self.assertIsNone(extract.after_turn("default", "I always buy my coffee at Wegmans."))

    def test_refine_only_for_thinking_out_loud(self):
        import asyncio
        v = self.voice
        started = []

        async def refine(call, text):
            started.append(text)
        from unittest import mock
        with mock.patch.object(v, "_refine", refine):
            async def go():
                call = v.Call(id="c2", bot_id="default", voice="cove")
                v.line_context(call, "Can you order new coffee for me today please, the usual one from the store")
                v.line_context(call, "I'm thinking about taking that job with Gregg's but I'm not sure it fits")
                await asyncio.sleep(0.05)
            asyncio.run(go())
        self.assertEqual(len(started), 1)
        self.assertIn("Gregg's", started[0])

    def test_voice_prompt_has_thinking_partner_rules(self):
        text = self.voice.voice_prompt("default", "You are Nova.")
        self.assertIn("thinking partner", text)
        self.assertIn("[Context for this call]", text)
        self.assertNotIn("\u2014", text)


class AppApi(unittest.TestCase):
    def setUp(self):
        fresh(self)
        from test_onboarding import _load_app
        self.app = _load_app()
        if self.app is None:
            self.skipTest("the hub's web dependencies are not installed here")
        from fastapi.testclient import TestClient
        self.app.app.dependency_overrides.clear()
        self.client = TestClient(self.app.app)
        self.auth = {"Authorization": f"Bearer {store.redeem_pairing_code(store.new_pairing_code(), 'Test iPhone')}"}

    def test_list_edit_add_delete(self):
        fid, _ = facts.save("default", COFFEE, subject="coffee", kind="preference")
        self.assertEqual(self.client.get("/api/facts").status_code, 401)
        r = self.client.get("/api/facts", headers=self.auth).json()
        self.assertEqual(r["facts"][0]["heading"], "Preferences")
        self.assertIn("What", " ".join(r["headings"]) + "What")
        self.assertEqual(self.client.put(f"/api/facts/{fid}", json={"text": "Sam drinks Lavazza from Whole Foods."},
                                         headers=self.auth).status_code, 200)
        self.assertIn("Lavazza", facts.get(fid)["text"])
        self.assertEqual(self.client.put(f"/api/facts/{fid}", json={"text": "My PIN is 1234."},
                                         headers=self.auth).status_code, 400)
        r = self.client.post("/api/facts", json={"text": "Sam prefers oat milk.", "kind": "preference"},
                             headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.delete(f"/api/facts/{fid}", headers=self.auth).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/facts/{fid}", headers=self.auth).status_code, 404)
        self.assertEqual([f["text"] for f in self.client.get("/api/facts", headers=self.auth).json()["facts"]],
                         ["Sam prefers oat milk."])


class HomeIsolation(unittest.TestCase):
    """Phase 5: every home has its own hub database and Zips' folder; a Riley-shaped home (shared plan, no
    connectors) starts empty and never sees the first home's facts or the first person's words."""

    def test_second_home_sees_nothing(self):
        fresh(self)
        first_db, first_hermes = config.HUB_DB, config.HERMES_HOME
        facts.save("default", COFFEE, subject="coffee", kind="preference")
        facts.save("money", "Sam's business checking balance was $1,234.56 on Oct 9.")
        state_db("default", [("api_1", "api_server", "user", "My coffee is the Cafe Bustelo brick from Stop & Shop, "
                                                            "please remember it.", time.time())])
        self.assertIn("Bustelo", facts.relevant("order coffee", "default", "s1"))
        # The second home: its own files, as its hub (another Linux user) would have them.
        folder = Path(tempfile.mkdtemp(dir=_tmp))
        config.HUB_DB, config.HERMES_HOME = folder / "hub.db", folder / "hermes"
        config.HERMES_HOME.mkdir()
        store._local.conn = None
        store._schema_ready = False
        facts.reset_for_tests()
        home_fixture.use_in(self, "riley")
        self.assertEqual(facts.current(None), [])
        self.assertEqual(facts.for_app(), [])
        self.assertEqual(facts.relevant("order coffee", "default", "b1"), "")
        self.assertEqual(facts.call_context("can you order coffee for me", "default"), ("", []))
        self.assertEqual(facts.message_search("coffee Bustelo Stop", "default"), [])
        self.assertEqual(facts.briefing_text("default"), "")
        self.assertIn("Nothing saved", facts.recall("coffee", "default"))
        facts.save("default", "Riley's tea is Yorkshire Gold from Target.", subject="tea", kind="preference")
        # Back in the first home, nothing of the second's arrived.
        config.HUB_DB, config.HERMES_HOME = first_db, first_hermes
        store._local.conn = None
        store._schema_ready = False
        facts.reset_for_tests()
        self.assertEqual(facts.search("Yorkshire tea Target", None), [])
        self.assertEqual(len(facts.current(None)), 2)

    def test_no_fixed_paths(self):
        import inspect
        for mod in (facts, extract):
            src = inspect.getsource(mod)
            self.assertNotIn("/home/", src)
            self.assertNotIn("jdoe", src)


class Plugin(unittest.TestCase):
    def test_pre_llm_call_sends_the_message_and_tools_are_registered(self):
        import plugin_loader
        pkg = plugin_loader.load()
        tm = pkg.teammates
        if "save_fact" not in tm.SCHEMAS:
            self.skipTest("the installed plugin predates save_fact")
        sent = {}

        def fake_call(op, args, timeout=60, session_id="", area="mac"):
            sent.update(op=op, args=args, area=area)
            return {"ok": True, "result": "[Relevant to this message] coffee"}
        saved = tm.mac._call
        tm.mac._call = fake_call
        try:
            out = tm.pre_llm_call(session_id="s1", is_first_turn=False, platform="api_server",
                                  user_message=[{"type": "text", "text": "order coffee"}, {"type": "image_url"}])
        finally:
            tm.mac._call = saved
        self.assertEqual(sent["args"]["message"], "order coffee")
        self.assertIn("coffee", out["context"])
        self.assertEqual(tm.OPS["save_fact"], "fact")
        self.assertEqual(tm.OPS["recall"], "recall")
        self.assertIn("save_fact", pkg.gate.TEST_OFF)


if __name__ == "__main__":
    unittest.main()
