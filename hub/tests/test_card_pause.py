"""Card pause fix (Oct 9, 11:18 p.m.): an old sign-in card kept Zero "paused for your okay" while Colin talked to it
about something else, and the card could not go away when it stopped mattering.

- Paused comes from a live wait: a card raised in the person's latest turn or after it, a card holding other work,
  or a running turn held on a card. An older open card stays usable but marks nothing as paused.
- A Zip held on a card is not busy: the person's message makes it give way and goes straight through, unqueued.
- A Zip can close a sign-in card it no longer needs (close_sign_in_card), and hears about older open cards.
- Card reasons speak to the person ("see your last order"), never about them.

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import activity, hermes, signin, store, turns, who  # noqa: E402
import test_fluid_app as fluid  # noqa: E402   (a module, so its Endpoints tests are not collected here again)

REASON = "see Colin's last contacts order and price"


def _card(bot: str, site: str, at: float, why: str = REASON, state: str = "open") -> int:
    fid = store.add_feed("login", bot, None, f"Sign in to {site}",
                         json.dumps({"url": f"https://www.{site}/account/signin", "site": site, "why": why,
                                     "state": state, "session_id": "sid"}))
    store.touch_feed(fid, at)
    return fid


class SecondPerson(unittest.TestCase):
    def test_reasons_speak_to_the_person(self):
        with mock.patch.object(who, "first", lambda: "Colin"), mock.patch.object(who, "full", lambda: "Colin O'Brien"):
            self.assertEqual(who.to_you("see Colin's last contacts order and price"),
                             "see your last contacts order and price")
            self.assertEqual(who.to_you("check Colin O'Brien's account for Colin"), "check your account for you")
            self.assertEqual(who.to_you("read the Colinwood syllabus"), "read the Colinwood syllabus")
            self.assertEqual(who.to_you(""), "")


class CardEndpoints(unittest.TestCase):
    setUp = fluid.Endpoints.setUp
    tearDown = fluid.Endpoints.tearDown
    bots = fluid.Endpoints.bots
    a_zip = fluid.Endpoints.a_zip

    def activity_cards(self, bot: str) -> dict[int, dict]:
        r = self.client.get(f"/api/bots/{bot}/activity", headers=self.phone)
        self.assertEqual(r.status_code, 200, r.text)
        return {i["id"]: json.loads(i["body"]) for i in r.json()["items"] if i["kind"] == "login"}

    def test_an_old_card_does_not_pause_the_zip(self):
        """The 11:54 a.m. card, with Colin writing at 11:18 p.m.: no paused status, and the card stays open."""
        now = time.time()
        fid = _card("default", "1800contacts.com", now - 11 * 3600)
        store.note_said("default", now - 60)
        self.assertIsNone(self.bots()["default"]["status"])
        card = self.activity_cards("default")[fid]
        self.assertEqual(card["state"], "open")      # still pinned and usable
        self.assertFalse(card["waiting"])            # but nothing waits on it

    def test_a_card_from_the_latest_turn_pauses_until_the_person_writes_again(self):
        now = time.time()
        store.note_said("default", now - 30)
        fid = _card("default", "1800contacts.com", now - 20)
        st = self.bots()["default"]["status"]
        self.assertEqual((st["state"], st["line"]), ("paused", "Waiting for you to sign in to 1800contacts.com."))
        self.assertEqual(st["feed_id"], fid)
        self.assertAlmostEqual(st["at"], now - 20, delta=2)
        self.assertTrue(self.activity_cards("default")[fid]["waiting"])
        store.note_said("default", now + 5)          # "I think I actually bought them over the phone."
        self.assertIsNone(self.bots()["default"]["status"])
        self.assertFalse(self.activity_cards("default")[fid]["waiting"])

    def test_asking_again_makes_the_card_live_again(self):
        now = time.time()
        fid = _card("default", "1800contacts.com", now - 3600)
        store.note_said("default", now - 10)
        self.assertIsNone(self.bots()["default"]["status"])
        with mock.patch.object(signin.consent, "site_of", lambda url: "1800contacts.com"):
            again, _ = signin.ask("default", "https://www.1800contacts.com/account/signin", "see your order", "sid")
        self.assertEqual(again, fid)   # moved down, notified again (more than REPUSH_SEC since the first)
        self.assertEqual(self.bots()["default"]["status"]["state"], "paused")

    def test_chat_cards_from_before_the_latest_message_do_not_pause(self):
        z = self.a_zip()
        now = time.time()
        with mock.patch.object(store, "session_for", lambda bot: "chat-sid"):
            store.note_said(z.id, now)
            old = {"id": 4, "bot_id": z.id, "site": "dana@x.example", "kind": "send", "reason": "Email Dana",
                   "created": now - 600, "session_id": "chat-sid"}
            self.pending = [old]
            self.assertIsNone(self.bots()[z.id]["status"])
            # The same card holding a board task, or from other work, still pauses it.
            self.pending = [dict(old, task_id="t_9")]
            self.assertEqual(self.bots()[z.id]["status"]["state"], "paused")
            self.pending = [dict(old, session_id="some-lane")]
            self.assertEqual(self.bots()[z.id]["status"]["state"], "paused")
            # Raised in the latest turn: paused, with when.
            self.pending = [dict(old, created=now + 2)]
            st = self.bots()[z.id]["status"]
            self.assertEqual(st["state"], "paused")
            self.assertAlmostEqual(st["at"], now + 2, delta=1)

    def test_reasons_on_cards_speak_to_the_person(self):
        fid = _card("default", "1800contacts.com", time.time())
        with mock.patch.object(who, "first", lambda: "Colin"), mock.patch.object(who, "full", lambda: "Colin O'Brien"):
            card = self.activity_cards("default")[fid]
        self.assertEqual(card["why"], "see your last contacts order and price")
        self.assertNotIn("session_id", card)

    def test_a_new_card_is_written_to_the_person(self):
        with mock.patch.object(who, "first", lambda: "Colin"), mock.patch.object(who, "full", lambda: "Colin O'Brien"):
            fid, told = signin.ask("default", "https://www.1800contacts.com/account/signin", REASON, "sid")
        body = json.loads(store.db().execute("SELECT body FROM feed WHERE id=?", (fid,)).fetchone()["body"])
        self.assertEqual(body["why"], "see your last contacts order and price")
        self.assertIn("close_sign_in_card", told)

    def test_the_zip_closes_a_card_it_no_longer_needs(self):
        """Colin bought his contacts over the phone: Zero closes the card, the app drops it, nothing follows."""
        if not hasattr(self.app, "internal_close_sign_in"):
            self.skipTest("no close endpoint")
        now = time.time()
        fid = _card("default", "1800contacts.com", now - 3600)
        other = _card("default", "netlify.com", now - 1800)
        with mock.patch.object(signin, "_continue") as cont:
            out = asyncio.run(self.app.internal_close_sign_in(
                self.app.MacBody(bot_id="default", session_id="", args={"site": "https://www.1800contacts.com/x"}),
                None))
            cont.assert_not_called()                   # the Zip closed it itself: no follow-up turn
        self.assertTrue(out["ok"], out)
        self.assertIn("1800contacts.com", out["result"])
        cards = self.activity_cards("default")
        self.assertEqual(cards[fid]["state"], "retired")
        self.assertEqual(cards[other]["state"], "open")   # only the card it named
        again = asyncio.run(self.app.internal_close_sign_in(
            self.app.MacBody(bot_id="default", session_id="", args={"site": "1800contacts.com"}), None))
        self.assertFalse(again["ok"])
        self.assertIn("no open sign-in card", again["error"])
        # Without a site, the newest open card.
        self.assertEqual(signin.retire("default"), ["netlify.com"])
        self.assertRaises(signin.AskError, signin.retire, "default")

    def test_the_zip_hears_about_older_open_cards_in_its_chat_only(self):
        from taffy_hub import team
        team._ready = False                        # this test's own database gets the team tables
        self.addCleanup(setattr, team, "_ready", False)   # and the next test's gets them too
        now = time.time()
        _card("default", "1800contacts.com", now - 3600)
        store.note_said("default", now)
        with mock.patch.object(store, "session_for", lambda bot: "chat-sid"), \
                mock.patch.object(team, "in_team_session", lambda sid: False):
            text = team.context_for("default", "chat-sid", first=False)
            self.assertIn("1800contacts.com", text)
            self.assertIn("close_sign_in_card", text)
            self.assertNotIn("1800contacts.com", team.context_for("default", "other-sid", first=False))
            self.assertNotIn("1800contacts.com", team.context_for("default", "chat-sid", first=False, work=True))
        self.assertIsNone(signin.old_cards_note("money"))


class HeldOnACard(fluid._Turns):
    """A Zip held on a card is not busy (the queued look at 11:18 p.m.): the person's message goes through now."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(signin, "done_in_chat", lambda *a: None)   # no sign-in card is read or answered
        p.start()
        self.addCleanup(p.stop)

    def held_stream(self):
        self.release = asyncio.Event()
        self.turn_texts = []

        async def stream(bot_id, text, team=False, lane=None):
            self.turn_texts.append(text)
            activity.begin(bot_id)
            try:
                if len(self.turn_texts) == 1:
                    for ev in ({"type": "run", "id": "run_held"},
                               {"type": "status", "text": "Waiting for your okay", "waiting": True}):
                        activity.note(bot_id, ev)
                        yield ev
                    await asyncio.wait_for(self.release.wait(), 3)
                    if "run_held" in hermes.yielded_runs:   # what hermes._stream_turn does on run.cancelled
                        hermes.yielded_runs.discard("run_held")
                        yield {"type": "yielded"}
                    else:
                        yield {"type": "error", "text": "That reply stopped partway."}
                else:
                    yield {"type": "run", "id": "run_next"}
                    yield {"type": "reply", "text": "That explains why there are no emails."}
                yield {"type": "done"}
            finally:
                activity.end(bot_id)
        return stream

    async def _done(self, turn):
        while not turn.done:
            await asyncio.sleep(0.02)

    def test_a_message_to_a_zip_held_on_a_card_goes_straight_through(self):
        async def go():
            with mock.patch.object(hermes, "stream_turn", self.held_stream()), \
                    mock.patch.object(store, "note_said", lambda *a, **k: None):
                first = turns.send("default", "Delete the old build folder")
                await asyncio.sleep(0.05)
                self.assertTrue(activity.waiting("default"))
                self.assertTrue(activity.live("default")["waiting"])
                second = turns.send("default", "I think I actually bought them over the phone.")
                await asyncio.wait_for(self._done(second), 5)
                await asyncio.wait_for(self._done(first), 5)
                self.assertEqual(self.stopped_runs, [("default", "run_held")])
                types = [e["type"] for e in second.events]
                self.assertNotIn("queued", types)                    # never shown as queued
                self.assertIn("reply", types)
                first_types = [e["type"] for e in first.events]
                self.assertNotIn("error", first_types)               # making way is not a failure
                self.assertIsNone(activity.outcome("default"))
                self.assertEqual(turns.queued_counts(), {})
        asyncio.run(go())

    def test_a_working_zip_still_queues(self):
        async def go():
            release = asyncio.Event()

            async def stream(bot_id, text, team=False, lane=None):
                activity.begin(bot_id)
                try:
                    yield {"type": "run", "id": "run_busy"}
                    activity.note(bot_id, {"type": "status", "text": "Checking the board"})
                    yield {"type": "status", "text": "Checking the board"}
                    await asyncio.wait_for(release.wait(), 3)
                    yield {"type": "reply", "text": "ok"}
                    yield {"type": "done"}
                finally:
                    activity.end(bot_id)
            with mock.patch.object(hermes, "stream_turn", stream), \
                    mock.patch.object(store, "note_said", lambda *a, **k: None):
                turns.send("money", "Any bills?")
                await asyncio.sleep(0.05)
                second = turns.send("money", "And the rent?")
                await asyncio.sleep(0.05)
                self.assertEqual(second.events[0]["type"], "queued")
                self.assertEqual(self.stopped_runs, [])
                release.set()
                await asyncio.wait_for(self._done(second), 5)
        asyncio.run(go())

    def test_if_the_held_run_cannot_stop_the_message_shows_as_queued(self):
        async def go():
            async def no_stop(bot_id, run_id):
                self.stopped_runs.append((bot_id, run_id))
                return False
            with mock.patch.object(hermes, "stream_turn", self.held_stream()), \
                    mock.patch.object(hermes, "stop", no_stop), \
                    mock.patch.object(store, "note_said", lambda *a, **k: None):
                first = turns.send("school", "Check eLearning")
                await asyncio.sleep(0.05)
                second = turns.send("school", "Never mind")
                await asyncio.sleep(0.1)
                self.assertEqual(second.events[0]["type"], "queued")
                self.assertNotIn("run_held", hermes.yielded_runs)
                self.release.set()
                await asyncio.wait_for(self._done(second), 5)
                await asyncio.wait_for(self._done(first), 5)
        asyncio.run(go())

    def test_the_persons_turn_notes_when_they_spoke(self):
        said = []

        async def go():
            async def stream(bot_id, text, team=False, lane=None):
                yield {"type": "reply", "text": "ok"}
                yield {"type": "done"}
            with mock.patch.object(hermes, "stream_turn", stream), \
                    mock.patch.object(store, "note_said", lambda bot, at=None: said.append(bot)):
                t = turns.send("career", "Any new jobs?")
                await asyncio.wait_for(self._done(t), 3)
                self.assertEqual(said, ["career"])
        asyncio.run(go())

    def test_a_follow_up_that_made_way_is_not_reported_as_failed(self):
        posted = []

        async def go():
            async def run_internal(bot_id, note, continues=None):
                hermes.last_yield[bot_id] = True
                return None
            with mock.patch.object(turns, "run_internal", run_internal), \
                    mock.patch.object(store, "add_feed", lambda *a, **k: posted.append(a)):
                out = await turns.follow_up("builder", "[Hub note] allowed", "sid", urgent=True)
                self.assertIsNone(out)
                self.assertEqual(posted, [])
        asyncio.run(go())


class PluginTool(unittest.TestCase):
    """close_sign_in_card in taffy-tools: it reaches the hub's close endpoint, and the reason a Zip gives on a new
    card is asked for in the person's own terms."""

    def setUp(self):
        import plugin_loader
        self.tt = plugin_loader.load()

    def test_close_goes_to_the_hub(self):
        calls = []
        with mock.patch.object(self.tt.asksignin.mac, "_call",
                               lambda op, args, timeout, session_id="", area="mac":
                               calls.append((op, args, area)) or {"ok": True, "result": "closed"}):
            out = json.loads(self.tt.asksignin.close_sign_in_card({"site": "1800contacts.com"}, session_id="s1"))
        self.assertEqual(out, {"result": "closed"})
        self.assertEqual(calls, [("close_sign_in", {"site": "1800contacts.com"}, "chat")])
        with mock.patch.object(self.tt.asksignin.mac, "_call", lambda *a, **k: {"ok": False, "error": "none open"}):
            self.assertEqual(json.loads(self.tt.asksignin.close_sign_in_card({}))["error"], "none open")

    def test_schemas(self):
        self.assertEqual(self.tt.asksignin.CLOSE_SCHEMA["name"], "close_sign_in_card")
        why = self.tt.asksignin.SCHEMA["parameters"]["properties"]["why"]["description"]
        self.assertIn("your", why)
        self.assertIn("close_sign_in_card", self.tt.asksignin.SCHEMA["description"])
        self.assertIn("close_sign_in_card", self.tt.gate.TEST_OFF)


if __name__ == "__main__":
    unittest.main()
