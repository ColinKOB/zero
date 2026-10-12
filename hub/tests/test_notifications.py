"""Notifications that read like a text from a teammate (Oct 10, Colin, from his lock screen: "I feel like there's a
ton of room for improvement on these notifications"). See notify.py.

- One specific sentence that fits, never cut off; markdown and lists flattened; a rewrite never adds a number,
  a name or an ask.
- No vague lines: money and health cards say the specific thing.
- A scheduled job's report goes to the chat quietly and buzzes only for a reason, once a week per reason.
- A card's own answers are the notification's buttons, with sends and money protected as before.
- The payload names the Zip (Communication Notifications), groups by Zip and ranks by relevance.

Nothing here reaches Apple: apns.send refuses under tests, and the push functions are replaced.
"""

import asyncio
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import apns, config, health, notices, notify, notifyprefs, store, watcher  # noqa: E402
from taffy_hub import money_noticers  # noqa: E402
from test_proactivity import ProCase  # noqa: E402

TEAM = {b.id: b for b in config.BUILTIN + config.OWNER_ZIPS}

SPENDING = """Dining for this week (Oct 5 to 11) is at $126.08, which is $66.08 over the $60 cap. That includes a new \
$10.97 Chick-fil-A charge from last night that is still pending, and nothing is left to spend on dining.

Optional clothing is at $0, so the full $15 is still open. The week ends tomorrow, Sunday.

Some purchases still need you to say what they were for: Walmart charges of $189.83 (now posted), $29.56 and $0.01, \
and Amazon charges of $9.37, $21.29 and $15.93.

Tally last synced today at 7:00 p.m."""

GOOD_BUZZ = ("You're $66 over this week's dining budget after last night's Chick-fil-A. "
             "Want me to plan cheap dinners through Sunday?")


def run(coro):
    return asyncio.run(coro)


class FakeHaiku:
    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.prompts = []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        a = self.answers.pop(0) if self.answers else ""
        if isinstance(a, Exception):
            raise a
        return a if isinstance(a, str) else json.dumps(a)


class StoreCase(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(config.BOTS_BY_ID, TEAM, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (config.HUB_DB,)
        config.HUB_DB = self.tmp / "hub.db"
        store._local.conn = None
        store._schema_ready = False

    def tearDown(self):
        (config.HUB_DB,) = self.saved
        store._local.conn = None
        store._schema_ready = False

    def haiku(self, *answers):
        fake = FakeHaiku(answers)
        p = mock.patch.object(notify, "ask_haiku", fake)
        p.start()
        self.addCleanup(p.stop)
        return fake


# MARK: The line

class Line(StoreCase):
    def test_the_spending_report_fits_without_a_model(self):
        """Colin's cut-off notification, Oct 10: now one or two whole sentences inside the limit."""
        out = notify.fit(SPENDING)
        self.assertLessEqual(len(out), notify.LIMIT)
        self.assertNotIn("...", out)
        self.assertRegex(out, r"[.?!]$")
        self.assertIn("$126.08", out)
        self.assertLessEqual(len(notify.sentences(out)), 2)

    def test_markdown_and_lists_are_flattened(self):
        text = "## Today\n- **Dining**: $126 spent\n- Clothing: `$0`\n\n[Open the sheet](https://x.example)\nWant a plan?"
        out = notify.plain(text)
        self.assertNotRegex(out, r"[#*`\[\]]|^- |\n")
        self.assertIn("Dining: $126 spent; Clothing: $0.", out)
        self.assertIn("Open the sheet", out)

    def test_no_em_dashes_or_emojis(self):
        out = notify.fit("All set — the cart is ready \U0001F6D2. Want me to check out?")
        self.assertNotIn("—", out)
        self.assertNotRegex(out, "[\U0001F000-\U0001FAFF]")

    def test_a_long_first_sentence_is_cut_at_a_clause(self):
        long = ("Your Verizon bill of $85.00 is due Friday, Oct 16, and I don't see it paid in Tally yet, which "
                "usually means it is not on autopay this month even though it was last month.")
        out = notify.fit(long)
        self.assertLessEqual(len(out), notify.LIMIT)
        self.assertTrue(out.startswith("Your Verizon bill of $85.00 is due Friday"))
        self.assertTrue(out.endswith("."))
        self.assertNotIn("...", out)

    def test_the_ask_is_kept(self):
        text = ("I finished the comparison of the three phone plans you asked about this morning. Mint is cheapest "
                "at $15 a month, Visible is $25 and Verizon stays $85. Want me to start the switch to Mint?")
        out = notify.fit(text)
        self.assertTrue(out.endswith("Want me to start the switch to Mint?"), out)
        self.assertLessEqual(len(out), notify.LIMIT)

    def test_a_short_line_is_left_alone_and_no_model_is_asked(self):
        fake = self.haiku("never used")
        with mock.patch.object(notify, "_haiku_ok", lambda: True):
            self.assertEqual(run(notify.line("Calls work again.", "default")), "Calls work again.")
        self.assertEqual(fake.prompts, [])

    def test_haiku_rewrites_a_long_message_when_it_is_faithful(self):
        self.haiku(GOOD_BUZZ.replace(" Want me to plan cheap dinners through Sunday?", ""))
        with mock.patch.object(notify, "_haiku_ok", lambda: True):
            out = run(notify.line(SPENDING, "default"))
        self.assertEqual(out, "You're $66 over this week's dining budget after last night's Chick-fil-A.")

    def test_a_rewrite_with_a_new_number_or_name_or_ask_is_refused(self):
        for bad in ("You're $70 over the dining budget.", "You're over the dining budget at Wendy's.",
                    "You're over the dining budget. Want me to fix it?"):
            self.haiku(bad)
            with mock.patch.object(notify, "_haiku_ok", lambda: True):
                out = run(notify.line(SPENDING, "default"))
            self.assertEqual(out, notify.fit(SPENDING), bad)

    def test_tests_never_call_the_real_model(self):
        self.assertIs(notify.ask_haiku, notify._REAL_ASK)
        self.assertFalse(notify._haiku_ok())

    def test_the_daily_budget(self):
        with mock.patch.object(notify, "HAIKU_DAILY", 2), mock.patch.object(notify, "HAIKU_DAILY_FRUGAL", 2), \
                mock.patch.object(notify, "_REAL_ASK", None), \
                mock.patch.dict(health.status, {"claude": True}):
            self.assertTrue(notify._haiku_ok())
            notify._spend()
            notify._spend()
            self.assertEqual(notify.haiku_left(), 0)
            self.assertFalse(notify._haiku_ok())


# MARK: No vague lines

class Specific(unittest.TestCase):
    def test_no_vague_lines_anywhere_in_the_hub(self):
        hub = Path(notices.__file__).parent
        for f in hub.glob("*.py"):
            if f.name == "notify.py":
                continue   # its notes quote what Colin saw on Oct 10
            text = f.read_text()
            for vague in ("A bill is due soon.", "A money question for you.", "A health question for you.",
                          "A new charge for Money to sort.", "A regular charge went up."):
                self.assertNotIn(vague, text, f.name)

    def test_a_bill_card_names_the_bill_and_the_amount(self):
        with mock.patch.object(notices, "now", lambda: 1791900000.0):
            c = money_noticers._bill("Riley's car payment", 292.0, 1791900000.0 + 2 * 86400)
        line = notices.compose(c.evidence, c.question)
        self.assertIn("Riley's car payment", line)
        self.assertIn("$292.00", line)
        self.assertTrue(line.endswith("Is it on autopay, or will you pay it yourself?"), line)
        self.assertLessEqual(len(line), notify.LIMIT)
        self.assertTrue(c.sensitive)
        self.assertFalse(hasattr(c, "vague"))


# MARK: Scheduled reports

class Reports(StoreCase):
    def setUp(self):
        super().setUp()
        self.out = self.tmp / "cron" / "output"
        self.pushes = []

        async def push(title, body, bot_id=None, bots=None, urgent=False, kind=None, extra=None):
            self.pushes.append({"title": title, "body": body, "bot": bot_id, "kind": kind, "extra": extra})
            return True
        for obj, name, new in ((watcher, "_cron_dirs", lambda: [("money", self.out)]), (watcher, "push", push),
                               (notices, "on_cron_report", lambda *a: False), (notify, "_haiku_ok", lambda: True)):
            p = mock.patch.object(obj, name, new)
            p.start()
            self.addCleanup(p.stop)
        store.set_cursor("cron_mtime_ms", 1)
        self.n = 0

    def report(self, text, name="Daily spending update", script=False):
        self.n += 1
        f = self.out / "21bea8813be6" / f"run{self.n}.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        head = f"# Cron Job: {name}\n\n**Job ID:** 21bea8813be6\n"
        f.write_text(head + ("**Mode:** no_agent (script)\n\n---\n\n" if script else "\n## Response\n\n") + text + "\n")
        t = 1791414000 + self.n
        os.utime(f, (t, t))
        run(watcher.scan_cron())

    def chat(self):
        return [(r["title"], r["body"]) for r in store.db().execute("SELECT * FROM feed WHERE kind='checkin' ORDER BY id")]

    def test_a_routine_report_is_quiet(self):
        fake = self.haiku({"buzz": False, "reason": "routine", "key": "", "line": ""})
        self.report("Dining is at $40 of the $60 cap, so $20 is left. Tally last synced at 7:00 p.m.")
        self.assertEqual(self.pushes, [])
        self.assertEqual(self.chat()[0][0], "Daily spending update")   # in the chat all the same
        self.assertEqual(len(fake.prompts), 1)

    def test_a_threshold_buzzes_once_a_week_from_the_zip(self):
        self.haiku({"buzz": True, "reason": "threshold", "key": "dining-over-cap", "line": GOOD_BUZZ},
                   {"buzz": True, "reason": "threshold", "key": "dining-over-cap", "line": GOOD_BUZZ},
                   {"buzz": True, "reason": "time", "key": "bilt-payment-reversed",
                    "line": "Northwind reversed a $2,000 payment and charged a $30 fee. Want me to look into it?"})
        self.report(SPENDING)
        [p] = self.pushes
        self.assertEqual((p["title"], p["body"], p["bot"], p["kind"]), ("Daily spending update", GOOD_BUZZ, "money",
                                                                         "needs"))
        self.assertTrue(p["extra"]["item_id"])                      # Reply on it quotes the whole report
        self.report(SPENDING)                                        # still over the next day: quiet
        self.assertEqual(len(self.pushes), 1)
        self.report(SPENDING + "\n\nBilt reversed a $2,000 payment and charged a $30 returned-payment fee.")
        self.assertEqual(len(self.pushes), 2)
        self.assertIn("Northwind reversed a $2,000 payment", self.pushes[-1]["body"])
        self.assertEqual(len(self.chat()), 3)

    def test_an_unfaithful_buzz_line_falls_back_to_the_report(self):
        self.haiku({"buzz": True, "reason": "needs", "key": "classify", "line": "You owe $999 to Target. Pay now?"})
        self.report(SPENDING)
        self.assertEqual(self.pushes[0]["body"], notify.fit(SPENDING))

    def test_the_jobs_own_markers(self):
        fake = self.haiku()
        self.report("[QUIET]\nNothing changed today.")
        self.report("[NOTIFY] Your rent is due tomorrow and isn't paid yet.\nRent is $1,450.")
        self.assertEqual([p["body"] for p in self.pushes], ["Your rent is due tomorrow and isn't paid yet."])
        self.assertEqual([b for _, b in self.chat()], ["Nothing changed today.", "Rent is $1,450."])
        self.assertEqual(fake.prompts, [])

    def test_without_haiku_only_watches_and_plain_urgency_buzz(self):
        with mock.patch.object(notify, "_haiku_ok", lambda: False):
            self.report("Dining is at $40 of the $60 cap.")
            self.report("Theo Avery replied about the Brightpath handoff.", name="Theo Avery reply watch", script=True)
            self.report("Your Northwind payment bounced and a $30 fee was charged. Dining is fine.")
        self.assertEqual([p["body"] for p in self.pushes],
                         ["Theo Avery replied about the Brightpath handoff.",
                          "Your Northwind payment bounced and a $30 fee was charged."])

    def test_a_zip_that_may_not_notify_is_not_even_checked(self):
        fake = self.haiku({"buzz": True, "reason": "threshold", "key": "x", "line": GOOD_BUZZ})
        store.set_notify("money", False)
        self.report(SPENDING)
        self.assertEqual(self.pushes, [])
        self.assertEqual(fake.prompts, [])
        self.assertEqual(len(self.chat()), 1)


# MARK: The push itself

class Push(StoreCase):
    def setUp(self):
        super().setUp()
        self.sent = []
        for obj, name, new in ((apns, "ready", lambda: True), (watcher, "_night", lambda: False),
                               (watcher, "_push_sync",
                                lambda t, b, bot_id=None, **kw: self.sent.append((t, b, bot_id, kw)) or True)):
            p = mock.patch.object(obj, name, new)
            p.start()
            self.addCleanup(p.stop)

    def test_it_comes_from_the_zip_in_one_fitted_sentence(self):
        bot = next(iter(config.BOTS_BY_ID.values()))
        run(watcher.push("Daily spending update", SPENDING, bot.id, kind="progress"))
        [(title, body, bot_id, kw)] = self.sent
        self.assertEqual((title, bot_id), (bot.name, bot.id))
        self.assertLessEqual(len(body), notify.LIMIT)
        self.assertEqual(kw["relevance"], notify.relevance("progress"))
        self.assertEqual(kw["topic"], "Daily spending update")

    def test_team_news_comes_from_the_lead(self):
        run(watcher.push("Team update", "School finished Form A.", "default", bots=["school", "money"], kind="done"))
        self.assertEqual(self.sent[0][:3], (notify.sender("default")[1], "School finished Form A.", "default"))

    def test_relevance_ranks_what_needs_you_first(self):
        self.assertGreater(notify.relevance("needs"), notify.relevance("done"))
        self.assertGreater(notify.relevance("done"), notify.relevance("progress"))
        self.assertEqual(notify.relevance("progress", urgent=True), 1.0)


class Payload(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(config.BOTS_BY_ID, TEAM, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def test_communication_fields(self):
        bot = next(iter(config.BOTS_BY_ID.values()))
        p = apns.payload_for(bot.name, SPENDING, bot.id, relevance=0.9, extra={"item_id": 7})
        aps = p["aps"]
        self.assertEqual(aps["mutable-content"], 1)                 # the extension adds the Zip's avatar
        self.assertEqual(aps["thread-id"], bot.id)                  # grouped by Zip
        self.assertEqual(aps["relevance-score"], 0.9)
        self.assertEqual(aps["category"], f"zero.msg.{bot.id}")     # Reply on any Zip's message
        self.assertEqual(p["sender"], {"id": bot.id, "name": bot.name})
        self.assertEqual(aps["alert"]["title"], bot.name)
        self.assertLessEqual(len(aps["alert"]["body"]), notify.LIMIT)
        self.assertEqual(p["item_id"], 7)

    def test_a_card_carries_its_answers(self):
        actions = [{"id": "option:0", "title": "It's on autopay", "mode": "auth"}]
        p = apns.payload_for("Zero", "Riley's car payment of $292.00 is due Sunday.", "default",
                             category="zero.open", actions=actions, extra={"feed_id": 12})
        self.assertEqual(p["aps"]["category"], "zero.open")          # the fallback when the extension can't
        self.assertEqual(p["actions"], actions)
        self.assertEqual(p["feed_id"], 12)

    def test_a_quiet_update_is_passive(self):
        p = apns.payload_for("Career", "Allowed on your Mac.", "career", sound=False)
        self.assertNotIn("sound", p["aps"])
        self.assertEqual(p["aps"]["interruption-level"], "passive")


# MARK: A card's own answers on the lock screen

class CardAnswers(unittest.TestCase):
    def card(self, **kw):
        return {"options": [{"text": "It's on autopay", "kind": "decide"}, {"text": "I'll pay it Saturday", "kind": "done"},
                            {"text": "Already paid", "kind": "close"}], **kw}

    def test_modes(self):
        self.assertEqual([a["mode"] for a in notices.card_actions(self.card())], ["background"] * 3)
        self.assertEqual([a["mode"] for a in notices.card_actions(self.card(sensitive=True))], ["auth"] * 3)
        send = {"options": [{"text": "Send it", "kind": "send"}, {"text": "Not now", "kind": "wait"}]}
        self.assertEqual([a["mode"] for a in notices.card_actions(send)], ["open", "background"])
        review = {"options": [{"text": "Approve", "kind": "hub", "hub": "approve_task"}, {"text": "Drop it", "kind": "hub",
                                                                                          "hub": "drop_task"}]}
        self.assertEqual([a["mode"] for a in notices.card_actions(review)], ["open", "background"])
        self.assertEqual([a["id"] for a in notices.card_actions(self.card())], ["option:0", "option:1", "option:2"])


class LockOptions(ProCase):
    """option:N from the notification answers the card, except a send (opens the card) and a money or health answer
    from a phone that was not unlocked."""

    def bill(self, sensitive=True):
        c = self.cand("bill:x", "bill", deadline=self.soon(10), sensitive=sensitive,
                      options=[notices.Option("It's on autopay", "decide", say="It is on autopay."),
                               notices.Option("I'll pay it Saturday", "done", follow="Okay."),
                               notices.Option("Already paid", "close")])
        self.raise_(c)
        return self.notice("bill:x")["feed_id"]

    def test_a_plain_answer_goes_from_the_lock_screen(self):
        fid = self.bill(sensitive=False)
        out = notices.lock_answer(fid, "option:1")
        self.assertNotIn("open", out)
        self.assertEqual(notices.row("bill:x")["picked"], "I'll pay it Saturday")

    def test_money_needs_the_phone_unlocked(self):
        fid = self.bill()
        out = notices.lock_answer(fid, "option:2")
        self.assertEqual(out["open"], "Open Zero to see this question.")
        self.assertIsNone(notices.row("bill:x")["picked"])
        notices.lock_answer(fid, "option:2", unlocked=True)
        self.assertEqual(notices.row("bill:x")["picked"], "Already paid")

    def test_a_bad_index_is_refused(self):
        fid = self.bill(sensitive=False)
        with self.assertRaises(notices.AnswerError):
            notices.lock_answer(fid, "option:7")
        with self.assertRaises(notices.AnswerError):
            notices.lock_answer(fid, "option:x")

    def test_the_push_names_the_bill_and_carries_the_answers(self):
        self.bill()
        run(notices.deliver())
        p = self.pushes[0]
        self.assertEqual([a["title"] for a in p["actions"]], ["It's on autopay", "I'll pay it Saturday", "Already paid"])
        self.assertEqual({a["mode"] for a in p["actions"]}, {"auth"})
        self.assertTrue(p["body"].endswith("?"))


if __name__ == "__main__":
    unittest.main()
