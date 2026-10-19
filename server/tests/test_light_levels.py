"""A brightness % and a light's own level: 1% is the light's lowest level, and a light that's on never reads 0%."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from newman.integrations._common import level_from_pct, pct_from_level  # noqa: E402


class LevelTests(unittest.TestCase):
    def test_percent_to_level(self):
        # Seen live: "1%" sent level 3 to IKEA drivers, brighter than the IKEA remote's lowest (level 1).
        self.assertEqual([level_from_pct(p, 254) for p in (1, 2, 50, 60, 100)], [1, 4, 126, 152, 254])
        self.assertEqual((level_from_pct(0, 254), level_from_pct(150, 254)), (1, 254))
        self.assertEqual(level_from_pct(1, 255), 1)

    def test_level_to_percent(self):
        # Seen live: drivers on at their lowest levels read "On · 0%".
        self.assertEqual([pct_from_level(v, 254) for v in (0, 1, 2, 3, 126, 152, 254)], [0, 1, 1, 2, 50, 60, 100])

    def test_round_trip(self):
        for p in range(1, 101):
            self.assertEqual(pct_from_level(level_from_pct(p, 254), 254), p)


if __name__ == "__main__":
    unittest.main()
