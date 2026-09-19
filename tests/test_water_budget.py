"""
One rule scores a weekly depth against the ETc band, for measured weeks and for
the plan projection alike (#13). Run from the repo root:

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "lib"))

import events  # noqa: E402

BAND = [1.75, 2.45]


class BandVerdict(unittest.TestCase):
    def test_below_the_band_is_under_not_in_band(self):
        self.assertEqual(events.band_verdict(1.0, BAND), "under")

    def test_edges_are_in_band(self):
        self.assertEqual(events.band_verdict(1.75, BAND), "in band")
        self.assertEqual(events.band_verdict(2.45, BAND), "in band")
        self.assertEqual(events.band_verdict(2.46, BAND), "over")

    def test_no_band_or_no_depth_gives_no_verdict(self):
        self.assertIsNone(events.band_verdict(2.0, None))
        self.assertIsNone(events.band_verdict(None, BAND))

    def test_weeks_are_scored_by_the_same_rule(self):
        # 7 full days of 10 L at 100 L/inch = 0.7 in: under.
        daily = [{"date": f"2026-09-{d:02d}", "draw": 10.0} for d in range(1, 8)]
        wk = events.water_budget(daily, 100.0, BAND)
        self.assertEqual([w["verdict"] for w in wk], ["under"])


if __name__ == "__main__":
    unittest.main()
