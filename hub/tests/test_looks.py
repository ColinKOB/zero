"""A Zip's look is saved only from known parts, and shows in the bot list's color and shape.

Run on alien: ~/taffy/.venv/bin/python -m unittest discover -s ~/taffy/hub-src/tests
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taffy_hub import config, looks  # noqa: E402

GOOD = {"shape": "egg", "color": "#f2711c", "texture": "felt", "eyes": "sleepy", "eyewear": "none",
        "top": "beanie", "neck": "scarf", "seam": True}


class Looks(unittest.TestCase):
    def setUp(self):
        self.path = config.LOOKS
        config.LOOKS = Path(tempfile.mkdtemp()) / "bot-looks.json"

    def tearDown(self):
        config.LOOKS = self.path

    def test_saves_a_known_look(self):
        saved = looks.save("default", GOOD)
        self.assertEqual(saved["color"], "#F2711C")
        self.assertEqual(looks.for_bot("default")["top"], "beanie")

    def test_refuses_unknown_parts(self):
        for part, bad in [("top", "jetpack"), ("shape", "heart"), ("color", "orange"), ("eyes", "")]:
            with self.assertRaises(looks.LookError):
                looks.save("default", {**GOOD, part: bad})
        self.assertIsNone(looks.for_bot("default"))

    def test_saves_a_rendered_look(self):
        saved = looks.save("default", {"body": "o", "outfit": "classic"})
        self.assertEqual(saved, {"body": "o", "outfit": "classic"})
        self.assertEqual(looks.for_bot("default"), {"body": "o", "outfit": "classic"})

    def test_every_plush_body_has_outfits(self):
        self.assertEqual(looks.save("school", {"body": "drop", "outfit": "cozy"}), {"body": "drop", "outfit": "cozy"})
        self.assertEqual(looks.save("school", {"body": "mushroom", "outfit": "plain"})["outfit"], "plain")
        # Looks saved before every body had outfits come back plain.
        self.assertEqual(looks.save("school", {"body": "drop"}), {"body": "drop", "outfit": "plain"})
        with self.assertRaises(looks.LookError):
            looks.save("school", {"body": "drop", "outfit": "tuxedo"})

    def test_flat_bodies(self):
        self.assertEqual(looks.save("money", {"body": "flat-kite"}), {"body": "flat-kite", "outfit": "none"})
        self.assertEqual(looks.save("money", {"body": "flat-o", "outfit": "none"})["body"], "flat-o")
        for bad in [{"body": "flat-o", "outfit": "classic"}, {"body": "flat-rocket"}, {"body": "flat-"}]:
            with self.assertRaises(looks.LookError, msg=str(bad)):
                looks.save("money", bad)
        self.assertEqual(len([b for b in looks.BODIES if b.startswith("flat-")]), 26)
        for shape in ("book", "calendar", "briefcase", "coin", "suitcase", "receipt"):
            self.assertEqual(looks.save("money", {"body": f"flat-{shape}"})["body"], f"flat-{shape}")

    def test_refuses_unknown_bodies_and_outfits(self):
        for bad in [{"body": "rocket"}, {"body": ""}, {"body": "o", "outfit": "tuxedo"}]:
            with self.assertRaises(looks.LookError, msg=str(bad)):
                looks.save("default", bad)
        self.assertIsNone(looks.for_bot("default"))

    def test_old_looks_stay_readable(self):
        looks.save("money", GOOD)
        looks.save("default", {"body": "o", "outfit": "cozy"})
        self.assertEqual(looks.for_bot("money")["color"], "#F2711C")
        self.assertEqual(looks.for_bot("default")["outfit"], "cozy")

    def test_forget(self):
        looks.save("school", GOOD)
        looks.forget("school")
        self.assertIsNone(looks.for_bot("school"))


if __name__ == "__main__":
    unittest.main()
