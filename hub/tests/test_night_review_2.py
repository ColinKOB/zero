"""Night review 2 (Oct 10): where tonight's branches meet. Card pause's "a Zip held on a card makes way for the
person" against an answered card, short replies against Stop, board relays, follow-ups after a card and pictures in
replies. docs/qa/2026-10-10-night-review-2.md has each scenario.

Haiku is always a fake here; nothing reaches a model.

Run: python3 -m unittest discover -s hub/tests   (tests/run_matrix.py runs it in every home shape)
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import activity, approvals, hermes, shortreply, signin, store, turns, watcher  # noqa: E402
import test_fluid_app as fluid  # noqa: E402   (a module, so its tests are not collected here again)
import test_short_replies as sr  # noqa: E402
from test_fixes_r1 import AppCase  # noqa: E402

# A picture in a reply, as Hermes's API streams it: the MEDIA:<path> line of the saved message becomes an inline
# data URL (gateway/platforms/api_server.py, _resolve_media_to_data_urls). The saved history keeps the MEDIA line.
PICTURE_PATH = "MEDIA:/home/sam/taffy/work/inbox/week.png"
PICTURE_INLINE = "![image](data:image/png;base64," + "iVBORw0KGgoAAAANSUhEUgAA" * 300 + ")"


async def _done(turn):
    while not turn.done:
        await asyncio.sleep(0.02)


class AnsweredCard(fluid._Turns):
    """Card pause lets a person's new message stop a run that is held on a card. Once the person has answered that
    card, the run is working again: Hermes runs the allowed command and says nothing until it ends."""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(signin, "done_in_chat", lambda *a: None)
        p.start()
        self.addCleanup(p.stop)

    def stream(self):
        self.release = asyncio.Event()
        self.texts = []

        async def stream(bot_id, text, team=False, lane=None):
            self.texts.append(text)
            activity.begin(bot_id)
            try:
                if len(self.texts) == 1:
                    for ev in ({"type": "run", "id": "run_held"},
                               {"type": "status", "text": "Waiting for your okay", "waiting": True}):
                        activity.note(bot_id, ev)
                        yield ev
                    await asyncio.wait_for(self.release.wait(), 3)   # the allowed command runs, with no events
                    if "run_held" in hermes.yielded_runs:
                        hermes.yielded_runs.discard("run_held")
                        yield {"type": "yielded"}
                    else:
                        ev = {"type": "reply", "text": "Deleted the old build folder."}
                        activity.note(bot_id, ev)
                        yield ev
                else:
                    yield {"type": "reply", "text": "You're welcome."}
                yield {"type": "done"}
            finally:
                activity.end(bot_id)
        return stream

    def test_a_message_after_allowing_the_card_never_cancels_the_allowed_command(self):
        async def answer(bot_id, run_id, request_id, choice):
            return True   # Hermes was still waiting and took the answer

        async def go():
            with mock.patch.object(hermes, "stream_turn", self.stream()), \
                    mock.patch.object(approvals, "answer", answer), \
                    mock.patch.object(store, "note_said", lambda *a, **k: None):
                first = turns.send("default", "Delete the old build folder")
                await asyncio.sleep(0.05)
                self.assertTrue(activity.waiting("default"))
                card = {"bot_id": "default", "fingerprint": approvals.fingerprint("run_held", "req-1")}
                self.assertTrue(await approvals.decide(card, True))   # Allow Once, while the run still waits
                self.assertFalse(activity.waiting("default"))          # working again, not paused
                self.assertFalse(activity.live("default")["waiting"])
                second = turns.send("default", "Thanks!")
                await asyncio.sleep(0.05)
                self.assertEqual(self.stopped_runs, [])                # the allowed command is not stopped
                self.assertEqual(second.events[0]["type"], "queued")   # it waits for the working Zip, as usual
                self.release.set()
                await asyncio.wait_for(_done(second), 5)
                await asyncio.wait_for(_done(first), 5)
                self.assertIn("Deleted the old build folder.",
                              [e.get("text") for e in first.events if e["type"] == "reply"])
        asyncio.run(go())

    def test_a_held_run_that_cannot_stop_leaves_no_made_way_mark(self):
        """If the held run cannot be stopped, it did not make way: a follow-up that then ends with nothing must still
        say so (turns.follow_up reads hermes.last_yield)."""
        async def no_stop(bot_id, run_id):
            return False

        async def go():
            activity.begin("builder")
            activity.note("builder", {"type": "run", "id": "run_x"})
            activity.note("builder", {"type": "status", "text": "Waiting for your okay", "waiting": True})
            waiting_for = turns.Turn("builder", "app")
            with mock.patch.object(hermes, "stop", no_stop):
                await turns._make_way("builder", waiting_for)
            self.assertFalse(hermes.last_yield.get("builder"))
            self.assertNotIn("run_x", hermes.yielded_runs)
            self.assertEqual(waiting_for.events[0]["type"], "queued")
        hermes.last_yield.pop("builder", None)
        asyncio.run(go())
        hermes.last_yield.pop("builder", None)


class InterruptedHubTurns(unittest.TestCase):
    """A run the hub started (the reply after a card, Zero's board relay) that is cut off ends with Hermes's
    "Operation interrupted: ..." as its final text. Card pause cuts such a run whenever it is held on a card and the
    person writes, and Stop cuts it too. That note is never the Zip's words: it must not be posted or pushed as a
    reply, and the board news it was meant to tell must not count as told."""

    INTERRUPTED = "Operation interrupted: waiting for model response (2.7s elapsed)."

    def stream(self, yielded: bool):
        async def stream(bot_id, text, team=False, lane=None):
            yield {"type": "run", "id": "run_hub"}
            if yielded:   # what hermes._stream_turn does when the run made way for the person (turns.send)
                hermes.last_yield[bot_id] = True
                yield {"type": "yielded"}
            else:
                yield {"type": "error", "text": "That reply stopped partway."}
            yield {"type": "reply", "text": self.INTERRUPTED}
            yield {"type": "done"}
        return stream

    def setUp(self):
        self.feed, self.pushed = [], []

        async def push(*a, **k):
            self.pushed.append((a, k))
            return True
        self.patches = [mock.patch.object(store, "add_feed", lambda *a, **k: self.feed.append(a) or 1),
                        mock.patch.object(watcher, "push", push),
                        mock.patch.object(turns.consent, "reopen_pass", lambda *a: None),
                        mock.patch.object(turns.consent, "close_message_passes", lambda *a: None),
                        mock.patch.object(shortreply, "enabled", lambda: False)]
        for p in self.patches:
            p.start()
        turns._held.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        hermes.last_yield.clear()
        turns._held.clear()

    def test_the_cancel_note_is_never_a_hub_turns_reply(self):
        with mock.patch.object(hermes, "stream_turn", self.stream(False)):
            self.assertIsNone(asyncio.run(hermes.run_turn("default", "[Board update from the hub]")))

    def test_a_follow_up_that_made_way_posts_and_pushes_nothing(self):
        with mock.patch.object(hermes, "stream_turn", self.stream(True)):
            out = asyncio.run(turns.follow_up("builder", "[Hub note] allowed", "sid", urgent=True))
        self.assertIsNone(out)
        self.assertEqual((self.feed, self.pushed), ([], []))

    def test_a_board_relay_cut_off_keeps_its_news_for_the_next_try(self):
        with mock.patch.object(hermes, "stream_turn", self.stream(False)):
            told = asyncio.run(watcher._team_update([("t_1", "- Builder finished the Halloween update.")]))
        self.assertFalse(told)   # the watcher tries again; the news is not lost
        self.assertEqual((self.feed, self.pushed), ([], []))


class StopWhileShortening(fluid._Turns):
    """The reply is held while Haiku shortens it (2 to 10 s), with the typing dots still showing. Stop pressed then
    comes too late: the reply already exists, so the Zip must not be told its request was cancelled."""

    def test_stop_during_the_hold_is_too_late_and_the_zip_is_not_told_it_was_cancelled(self):
        haiku = sr.FakeHaiku(sr.GOOD_SHORT, delay=0.4)

        async def stream(bot_id, text, team=False, lane=None):
            yield {"type": "run", "id": "run_1"}
            yield {"type": "reply", "text": sr.STATUS}
            yield {"type": "done"}

        async def go():
            with mock.patch.object(hermes, "stream_turn", stream), \
                    mock.patch.object(shortreply, "ask_haiku", haiku), \
                    mock.patch.object(shortreply, "enabled", lambda: True), \
                    mock.patch.object(shortreply, "remember", lambda *a: None), \
                    mock.patch("taffy_hub.onboarding.person_name", lambda full=True: "Sam"), \
                    mock.patch.object(store, "note_said", lambda *a, **k: None):
                self.release = asyncio.Event()
                t = turns.send("default", "Morning. What's on my plate today?")
                await asyncio.sleep(0.15)   # the reply came; Haiku is writing the short version
                self.assertTrue(haiku.prompts)
                result = await turns.stop("default")
                await asyncio.wait_for(_done(t), 5)
                replies = [e for e in t.events if e["type"] == "reply"]
                self.assertEqual(len(replies), 1)
                self.assertEqual(replies[0]["text"], sr.GOOD_SHORT)   # what the history will show
                self.assertNotIn("stopped", [e["type"] for e in t.events])
                self.assertFalse(result["stopped"])
                self.assertNotIn("default", turns._stopped)            # no "[Stopped by ...]" note next time
        try:
            asyncio.run(go())
        finally:
            turns._stopped.clear()


class ShortRepliesAcrossPaths(AppCase):
    # The helpers of test_short_replies.ChatTurns, borrowed so its tests are not collected here a second time.
    turn = sr.ChatTurns.turn
    history = sr.ChatTurns.history

    def setUp(self):
        super().setUp()
        self.haiku = sr.FakeHaiku()
        self.patches = [mock.patch.object(shortreply, "ask_haiku", self.haiku),
                        mock.patch.object(sr.briefing, "refresh_soon", lambda *a: None),
                        mock.patch("taffy_hub.onboarding.person_name", lambda full=True: "Sam")]
        for p in self.patches:
            p.start()
        shortreply._last_words.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        super().tearDown()

    def test_a_board_relay_does_not_replace_the_persons_request_for_the_follow_up(self):
        """Colin asks for a list; a card comes up; a board relay runs before he answers it. The reply after the card
        must still count his "list everything" request, not the relay's empty one."""
        self.haiku.answers = [sr.GOOD_SHORT, sr.GOOD_SHORT]
        pushed = []

        async def fake_push(title, body, bot_id=None, **kw):
            pushed.append(body)
            return True

        async def relay_turn(bot_id, note, continues=None):
            return sr.STATUS

        run = sr.run
        run(shortreply.for_reply("default", "List everything due this week.", "There's a sign-in card for you."))
        with mock.patch.object(turns, "run_internal", relay_turn), mock.patch.object(watcher, "push", fake_push):
            run(watcher._team_update([("t_1", "- Builder finished the Halloween update.")]))
            pushed.clear()
            run(sr.REAL_FOLLOW_UP("default", "[Hub note: allowed]", "sid-1", urgent=True))
        self.assertEqual(pushed, [sr.STATUS])   # asked for the list: shown in full
        self.assertEqual(len(self.haiku.prompts), 1)   # only the relay went to Haiku

    def test_a_short_reply_with_a_picture_is_left_alone(self):
        """The streamed text has the picture inline as a data URL. It is not prose: a two-sentence reply with a
        picture is not long, and the picture never goes to Haiku."""
        reply = "Here's your week on one page. The three deadlines are marked in red.\n\n" + PICTURE_INLINE
        out = self.turn(reply, "How does my week look?")
        self.assertEqual(next(e for e in out if e["type"] == "reply")["text"], reply)
        self.assertEqual(self.haiku.prompts, [])

    def test_a_long_reply_with_a_picture_keeps_it_and_the_history_shows_it_short(self):
        self.haiku.answers = [sr.GOOD_SHORT]
        streamed = sr.STATUS + "\n\n" + PICTURE_INLINE
        out = self.turn(streamed, "Morning. What's on my plate today?")
        reply = next(e for e in out if e["type"] == "reply")
        self.assertEqual(reply["text"], sr.GOOD_SHORT + "\n\n" + PICTURE_INLINE)   # the picture stays
        self.assertNotIn("base64", self.haiku.prompts[0])
        saved = sr.STATUS + "\n\n" + PICTURE_PATH   # what Hermes keeps in the session
        msgs = self.history([{"id": 2, "role": "assistant", "text": saved, "ts": 2}])
        self.assertEqual(msgs[0]["display"], sr.GOOD_SHORT + "\n\n" + PICTURE_PATH)
        self.assertEqual(msgs[0]["full"], saved)


if __name__ == "__main__":
    unittest.main()
