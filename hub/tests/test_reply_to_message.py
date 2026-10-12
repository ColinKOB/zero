"""Reply to a specific message (Colin, Oct 10: "i should also be able to reply to specific messages like you can on
iphone with the messages app"). The app sends the quoted message with the person's words; the Zip gets it as a quiet
first line of the same user turn, and the history shows the words with the quote as `reply_to`.

Run: python3 -m unittest discover -s hub/tests
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import consent, hermes, history, store, turns, who  # noqa: E402
from test_fixes_r1 import AppCase  # noqa: E402
from test_fluid_app import _Turns  # noqa: E402


def msgs(*pairs):
    return [{"id": str(i), "role": r, "text": t, "ts": 1000 + i} for i, (r, t) in enumerate(pairs)]


class Prefix(unittest.TestCase):
    def test_lines_round_trip(self):
        for who_, words in (("bot", "your message"), ("person", "Sam's earlier message"),
                            ("other", "an earlier update in this chat")):
            line = history.reply_line("The cart is ready.", who_, "Sam")
            self.assertEqual(line, f'[Replying to {words}: "The cart is ready."]')
            self.assertEqual(history.parse_reply_line(line), {"text": "The cart is ready.", "who": who_})

    def test_excerpt_is_one_line_and_short(self):
        long = "Here is the plan.\n\n" + " ".join(["word"] * 200)
        line = history.reply_line(long, "bot", "Colin")
        self.assertNotIn("\n", line)
        quoted = history.parse_reply_line(line)["text"]
        self.assertTrue(quoted.startswith("Here is the plan. word word"))
        self.assertTrue(quoted.endswith("..."))
        self.assertLessEqual(len(quoted), history.REPLY_EXCERPT_MAX + 3)
        self.assertEqual(history.reply_excerpt("  a\n b\tc  "), "a b c")

    def test_quotes_and_brackets_inside_the_excerpt(self):
        line = history.reply_line('She said "fine"] and left', "bot", "Colin")
        self.assertEqual(history.parse_reply_line(line)["text"], 'She said "fine"] and left')

    def test_names_with_an_apostrophe(self):
        line = history.reply_line("ok", "person", "D'Arcy")
        self.assertEqual(history.parse_reply_line(line), {"text": "ok", "who": "person"})

    def test_not_a_reply_line(self):
        for line in ("[Replying to your message: no quotes]", '[Replying to your message: ""]', "Replying to you",
                     '[Replying to the whole team and everyone else who might be reading this long line: "x"]'):
            self.assertIsNone(history.parse_reply_line(line), line)


class History(unittest.TestCase):
    def test_reply_shows_the_words_and_the_quote(self):
        line = history.reply_line("The cart is ready now.", "bot", "Colin")
        out = history.classify(msgs(("assistant", "The cart is ready now."),
                                    ("user", f"{line}\nIs pickup free?"),
                                    ("assistant", "Yes, after two hours.")))
        self.assertEqual([m["class"] for m in out], ["bot", "colin", "bot"])
        self.assertEqual(out[1]["display"], "Is pickup free?")
        self.assertEqual(out[1]["reply_to"], {"text": "The cart is ready now.", "who": "bot"})
        self.assertNotIn("reply_to", out[0])
        self.assertNotIn("reply_to", out[2])
        self.assertTrue(out[1]["text"].startswith("[Replying to your message: "))   # the Zip's copy stays whole

    def test_with_the_reactions_and_stopped_notes(self):
        line = history.reply_line("Plan my week", "person", "Colin")
        text = ('[Reactions from Colin since your last message: heart on "ok". Context only; no need to mention them.]\n'
                '[Stopped by Colin: "Plan my week". Colin pressed Stop on that request, so it is cancelled. '
                'Context only; no need to mention it.]\n'
                f"{line}\nOnly Monday, please.\nAnd Tuesday.")
        out = history.classify(msgs(("user", text)))
        self.assertEqual(out[0]["class"], "colin")
        self.assertEqual(out[0]["display"], "Only Monday, please.\nAnd Tuesday.")
        self.assertEqual(out[0]["reply_to"], {"text": "Plan my week", "who": "person"})
        self.assertEqual(history.strip(text), "Only Monday, please.\nAnd Tuesday.")
        self.assertEqual([m["display"] for m in history.colin_words(msgs(("user", text)))],
                         ["Only Monday, please.\nAnd Tuesday."])

    def test_typed_words_that_look_like_a_reply_stay(self):
        typed = "[Replying to your message: they wrote this]\nhello"
        out = history.classify(msgs(("user", typed)))
        self.assertEqual(out[0]["display"], typed)
        self.assertNotIn("reply_to", out[0])

    def test_one_quote_only(self):
        a, b = history.reply_line("first", "bot", "Colin"), history.reply_line("second", "bot", "Colin")
        out = history.classify(msgs(("user", f"{a}\n{b}\nhi")))
        self.assertEqual(out[0]["reply_to"]["text"], "first")
        self.assertEqual(out[0]["display"], f"{b}\nhi")   # only the hub's own first line is taken off

    def test_a_steered_reply_unwraps(self):
        line = history.reply_line("Draft ready", "bot", "Colin")
        text = (f"[OUT-OF-BAND USER MESSAGE — a direct message from the user]\n{line}\nSend it"
                "\n[/OUT-OF-BAND USER MESSAGE]")
        out = history.classify(msgs(("user", text)))
        self.assertEqual(out[0]["display"], "Send it")
        self.assertEqual(out[0]["reply_to"]["text"], "Draft ready")

    def test_reaction_excerpts_stay_on_one_line(self):
        with mock.patch.object(store, "take_untold_reactions",
                               lambda b: [{"kind": "heart", "excerpt": "Two lines\nof text"}]), \
                mock.patch.object(turns, "_stopped", {}):
            text = turns._with_reactions("default", turns._with_reply("ok", {"text": "Two lines\nof text"}))
        self.assertEqual(text.count("\n"), 2)
        bare, quoted = history.peel(text)
        self.assertEqual(bare, "ok")
        self.assertEqual(quoted, {"text": "Two lines of text", "who": "bot"})


class Turns(_Turns):
    def stream(self, release=None):
        self.seen = []
        self.release = release if release is not None else asyncio.Event()   # _Turns._stop sets it

        async def stream(bot_id, text, team=False, lane=None):
            self.seen.append(text)
            yield {"type": "run", "id": f"run_{len(self.seen)}"}
            if release is not None:
                try:
                    await asyncio.wait_for(release.wait(), 2)
                except TimeoutError:
                    pass
            yield {"type": "reply", "text": "Got it."}
            yield {"type": "done"}
        return stream

    async def _done(self, turn):
        while not turn.done:
            await asyncio.sleep(0.02)

    def test_the_zip_gets_the_quote_and_only_the_words_count(self):
        passes = []
        with mock.patch.object(consent, "open_pass", lambda kind, words, **k: passes.append(words)), \
                mock.patch.object(hermes, "stream_turn", self.stream()), \
                mock.patch.object(turns.activity, "expect", lambda b, w: passes.append(("expect", w))):
            async def go():
                t = turns.send("default", "Is pickup free?", reply_to={"text": "The cart\nis ready.", "who": "bot"})
                await asyncio.wait_for(self._done(t), 3)
                return t
            t = asyncio.run(go())
        name = who.first()
        self.assertEqual(self.seen, [f'[Replying to your message: "The cart is ready."]\nIs pickup free?'])
        self.assertEqual(t.own_words, "Is pickup free?")
        self.assertIn("Is pickup free?", passes)                       # sign-in consent: his words only
        self.assertIn(("expect", "Is pickup free?"), passes)           # Retry offers his words
        self.assertTrue(all("Replying" not in str(p) for p in passes))
        self.assertTrue(name)

    def test_a_queued_reply_keeps_its_quote_and_stop_returns_the_words(self):
        release = asyncio.Event()
        with mock.patch.object(hermes, "stream_turn", self.stream(release)):
            async def go():
                first = turns.send("money", "Any bills?")
                await asyncio.sleep(0.05)
                second = turns.send("money", "This one?", reply_to={"text": "Rent is due Friday.", "who": "bot"})
                third = turns.send("money", "And mine?", reply_to={"text": "Any bills?", "who": "person"})
                await asyncio.sleep(0.05)
                self.assertEqual(second.events[0]["type"], "queued")
                self.assertEqual(second.own_words, "This one?")
                release.set()
                await asyncio.wait_for(self._done(third), 5)
                return first, second, third
            asyncio.run(go())
        name = who.first()
        self.assertEqual(self.seen, ["Any bills?",
                                     '[Replying to your message: "Rent is due Friday."]\nThis one?',
                                     f'[Replying to {name}\'s earlier message: "Any bills?"]\nAnd mine?'])

    def test_stop_hands_back_a_queued_reply_without_the_quote(self):
        release = asyncio.Event()
        with mock.patch.object(hermes, "stream_turn", self.stream(release)):
            async def go():
                turns.send("school", "What is due?")
                await asyncio.sleep(0.05)
                turns.send("school", "Skip that one", reply_to={"text": "Essay due Monday.", "who": "bot"})
                out = await turns.stop("school")
                release.set()
                await asyncio.sleep(0.1)
                return out
            out = asyncio.run(go())
        self.assertEqual(out["returned"], ["Skip that one"])
        self.assertEqual(len(self.seen), 1)

    def test_no_quote_sends_the_words_alone(self):
        self.assertEqual(turns._with_reply("hi", None), "hi")
        self.assertEqual(turns._with_reply("hi", {"text": "  \n ", "who": "bot"}), "hi")


class Api(AppCase):
    def test_send_passes_the_quote(self):
        sent = []

        def fake_send(bot_id, text, reply_to=None, device=None):
            sent.append((bot_id, text, reply_to))
            t = turns.Turn(bot_id, "app")
            t.events, t.done = [{"type": "done"}], True
            return t
        with mock.patch.object(turns, "send", fake_send):
            r = self.client.post("/api/bots/default/messages",
                                 json={"text": " Is pickup free? ", "reply_to": {"text": "The cart is ready.", "who": "bot"}})
            self.assertEqual(r.status_code, 200, r.text)
            r = self.client.post("/api/bots/default/messages", json={"text": "plain"})
            self.assertEqual(r.status_code, 200, r.text)
            r = self.client.post("/api/bots/default/messages", json={"text": "x", "reply_to": {"text": "y", "who": "boss"}})
            self.assertEqual(r.status_code, 400)
        self.assertEqual(sent, [("default", "Is pickup free?", {"text": "The cart is ready.", "who": "bot"}),
                                ("default", "plain", None)])

    def test_history_returns_the_quote(self):
        line = history.reply_line("The cart is ready.", "bot", who.first())

        async def fake_messages(bot_id, limit=200):
            return msgs(("assistant", "The cart is ready."), ("user", f"{line}\nIs pickup free?"))
        with mock.patch.object(hermes, "messages", fake_messages):
            r = self.client.get("/api/bots/default/messages")
        self.assertEqual(r.status_code, 200, r.text)
        mine = r.json()["messages"][1]
        self.assertEqual(mine["class"], "colin")
        self.assertEqual(mine["display"], "Is pickup free?")
        self.assertEqual(mine["reply_to"], {"text": "The cart is ready.", "who": "bot"})


if __name__ == "__main__":
    unittest.main()
