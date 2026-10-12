"""Each Zip's message tone (Colin, Oct 11): every Zip has its own by default, the person can pick another, the phone's
own sound, none, or a sound of their own, and every notification carries the Zip's choice (tones.py, apns.payload_for).

Run: python3 -m unittest discover -s hub/tests   (tests/run_matrix.py runs it in every home shape)
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from taffy_hub import apns, config, tones  # noqa: E402
from test_fixes_r1 import AppCase  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


class Tones(AppCase):
    def test_every_zip_has_its_own_default(self):
        self.assertEqual(tones.default_for("default"), "chime")
        others = [tones.default_for(b.id) for b in config.BOTS if b.id != "default"][:len(tones.TONES) - 1]
        self.assertEqual(len(others), len(set(others)), "two Zips share a default tone")
        self.assertNotIn("chime", others)

    def test_the_payload_carries_the_zips_tone(self):
        bot = next(b.id for b in config.BOTS if b.id != "default")
        p = apns.payload_for("x", "Done.", bot)
        self.assertEqual(p["aps"]["sound"], f"zero-{tones.default_for(bot)}.caf")

    def test_a_chosen_tone_and_the_special_choices(self):
        tones.set_tone("default", "harp")
        self.assertEqual(apns.payload_for("Zero", "Hi.", "default")["aps"]["sound"], "zero-harp.caf")
        tones.set_tone("default", "default")
        self.assertEqual(apns.payload_for("Zero", "Hi.", "default")["aps"]["sound"], "default")
        tones.set_tone("default", "custom")
        self.assertEqual(apns.payload_for("Zero", "Hi.", "default")["aps"]["sound"], "zero-custom-default.caf")
        tones.set_tone("default", "none")
        p = apns.payload_for("Zero", "Hi.", "default")
        self.assertNotIn("sound", p["aps"])
        self.assertNotIn("interruption-level", p["aps"], "None still shows the notification normally")

    def test_a_quiet_update_stays_quiet(self):
        self.assertNotIn("sound", apns.payload_for("Zero", "Answered on the Mac.", "default", sound=False)["aps"])

    def test_unknown_tones_are_refused(self):
        with self.assertRaises(ValueError):
            tones.set_tone("default", "../../etc/passwd")
        r = self.client.put("/api/bots/default/tone", json={"tone": "kazoo"})
        self.assertEqual(r.status_code, 400)
        r = self.client.put("/api/bots/default/tone", json={"tone": "bell"})
        self.assertEqual((r.status_code, r.json()["tone"]), (200, "bell"))
        bots = {b["id"]: b for b in self.client.get("/api/bots").json()["bots"]}
        self.assertEqual(bots["default"]["tone"], "bell")

    @unittest.skipUnless((REPO / "ios").is_dir(), "repo only")
    def test_the_app_bundles_every_tone(self):
        folder = REPO / "ios" / "Taffy" / "Resources" / "Tones"
        for t in tones.TONES:
            self.assertTrue((folder / f"zero-{t}.caf").is_file(), t)


if __name__ == "__main__":
    unittest.main()
