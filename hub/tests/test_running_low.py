"""Running low (supplies.py, docs/qa/2026-10-10-memory-facts.md, phase 4): purchase facts give a run-out day, and one
card comes on the predicted day (two days ahead), none before, none for autoship, none from a single order with no
known pace. Fictional orders; a fixed clock."""

import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import facts, notices, supplies  # noqa: E402
from test_proactivity import ProCase, at, run  # noqa: E402

DAY = 86400


class RunningLow(ProCase):
    def setUp(self):
        super().setUp()
        facts.reset_for_tests()
        self.addCleanup(facts.reset_for_tests)

    def order(self, days_ago, item="Lavazza Super Crema Whole Bean Coffee", subject="coffee", qty=1, every=None,
              store="Whole Foods Market"):
        t = self.clock - days_ago * DAY
        facts.save("hub", f"Sam bought {qty} x {item} from {store}, order {days_ago}.", subject=subject,
                   kind="purchase", source="mail", source_ref=f"m{days_ago}{subject}", confidence="receipt",
                   data={"item": item, "size": "2.2 lb", "quantity": qty, "store": store, "ordered_at": t,
                         "every": every}, observed_at=t, limits=False)

    def cards_at(self, ts):
        self.clock = ts
        return run(supplies.running_low())

    def test_two_orders_give_the_day_and_one_card_on_it_none_before(self):
        self.order(28)
        self.order(14)
        runs_out = self.clock
        self.assertEqual(self.cards_at(runs_out - 3 * DAY), [])
        [c] = self.cards_at(runs_out - 2 * DAY)
        self.assertIsNone(notices.check(c))
        self.assertEqual(c.noticer, "running_low")
        self.assertIn("Whole Foods", c.facts["what"])
        self.assertIn("every 14 days", c.evidence)
        self.assertTrue(c.evidence.startswith("12 days ago you bought"))
        self.assertEqual(c.options[0].kind, "act")
        self.assertIn("Stop at checkout", c.options[0].say)
        self.assertNotRegex(c.options[0].say, r"(?i)please (buy|order|pay)")
        self.assertEqual(self.cards_at(runs_out + 4 * DAY), [])

    def test_through_the_rules_once(self):
        self.order(28)
        self.order(14)
        self.clock = self.clock - 2 * DAY
        cands = run(supplies.running_low())
        ids = run(notices.consider(cands, paced=False))
        self.assertEqual(len([i for i in ids if i]), 1)
        self.assertEqual(run(notices.consider(run(supplies.running_low()), paced=False)), [])

    def test_a_newer_order_moves_the_day(self):
        self.order(28)
        self.order(14)
        self.order(1)
        self.assertEqual(self.cards_at(self.clock), [])

    def test_autoship_and_single_orders_without_a_pace(self):
        self.order(40, item="Blue Buffalo Dog Food", subject="dog food", every="6 weeks")
        self.order(10, item="Oat Milk", subject="oat milk")
        self.assertEqual(self.cards_at(self.clock), [])

    def test_a_pace_the_person_said(self):
        facts.save("default", "Sam says one 2.2 lb bag of coffee lasts about three weeks.", subject="coffee",
                   kind="supply")
        self.order(19)
        [c] = self.cards_at(self.clock)
        self.assertIn("3 weeks' worth", c.evidence)

    def test_two_lines_of_one_order_are_one_trip(self):
        for days in (95, 5):
            for eye in ("Right", "Left"):
                self.order(days, item=f"Acuvue Oasys 90 pack {eye}", subject="contact lenses", store="Lens Shop")
        p = [x for x in supplies.predictions(self.clock) if x["subject"] == "contact lenses"][0]
        self.assertAlmostEqual((p["runs_out"] - p["last"]) / DAY, 90, delta=0.01)

    def test_listed_in_settings(self):
        self.assertIn("running_low", notices.NOTICERS)
        self.assertTrue(notices.fits("running_low"))


if __name__ == "__main__":
    unittest.main()
