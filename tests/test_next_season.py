"""
Panel 9 scales next season's tomato schedule from the least water per mm of
ET0 that held this season's bed. Run from the repo root:

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest
from datetime import date, timedelta

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "lib"))

import analyze  # noqa: E402
import events  # noqa: E402

START = date(2026, 8, 1)


def _days(n, first, step, offset=0):
    """n consecutive complete days of extremes whose before-first-run level
    moves `step` points a day from `first`."""
    return [{"date": (START + timedelta(days=offset + i)).isoformat(),
             "pre_irrigation": first + step * i} for i in range(n)]


def _regime(label, offset, n, seconds, runs=2):
    return {"label": label,
            "start": (START + timedelta(days=offset)).isoformat(),
            "end": (START + timedelta(days=offset + n - 1)).isoformat(),
            "runs": [{"time": f"0{5 + i}:05", "seconds": seconds} for i in range(runs)]}


class HoldRate(unittest.TestCase):
    def setUp(self):
        # Three regimes, ET0 4 mm every day:
        #   held  -- 48 L/day = 12 L/mm, level rising
        #   slid  -- 40 L/day = 10 L/mm, level falling 1 pt/day
        #   long  -- one 14-min run, 120 L/day, level falling: must be ignored
        self.cfg = [_regime("held", 0, 10, 90), _regime("slid", 10, 10, 90),
                    _regime("long", 20, 10, 840, runs=1)]
        self.ext = _days(10, 60, 0.5) + _days(10, 65, -1.0, 10) + _days(10, 55, -1.0, 20)
        self.summary = [{"label": "held", "start": self.cfg[0]["start"], "liters_day": 48.0},
                        {"label": "slid", "start": self.cfg[1]["start"], "liters_day": 40.0},
                        {"label": "long", "start": self.cfg[2]["start"], "liters_day": 120.0}]
        self.et0 = {r["date"]: 4.0 for r in self.ext}

    def test_rate_is_the_least_water_that_held_and_long_runs_are_ignored(self):
        h = events.hold_rate(self.cfg, self.summary, self.ext, self.et0)
        self.assertEqual([r["label"] for r in h["regimes"]], ["held", "slid"])
        self.assertEqual(h["rate"], 12.0)
        self.assertEqual(h["slid_max"], 10.0)

    def test_a_schedule_shorter_than_min_days_is_not_scored(self):
        h = events.hold_rate(self.cfg, self.summary, self.ext, self.et0, min_days=11)
        self.assertEqual(h["regimes"], [])
        self.assertIsNone(h["rate"])

    def test_days_without_et0_do_not_count(self):
        et0 = {d: v for d, v in self.et0.items() if d >= self.cfg[1]["start"]}
        h = events.hold_rate(self.cfg, self.summary, self.ext, et0)
        self.assertEqual([r["label"] for r in h["regimes"]], ["slid"])
        self.assertIsNone(h["rate"])


class MonthTable(unittest.TestCase):
    NS = {"pulse_seconds": 90, "young_plant_factor": 0.5,
          "et0_reference": {"mm_per_day": {"07": 5.69, "10": 2.66}},
          "start_times": {"_comment": "x", "2": ["05:05", "17:05"],
                          "3": ["05:05", "11:05", "17:05"],
                          "4": ["05:05", "09:05", "13:05", "17:05"]},
          "rules": ["r"], "_private": "never on the page"}

    def test_pulses_round_up_so_the_plan_never_falls_below_the_rate(self):
        # July: 5.69 x 12.1 = 68.8 L / 19.7 L per pulse = 3.49 -> 4, not 3.
        out = analyze._next_season(self.NS, {"rate": 12.1, "regimes": []}, 0.2188)
        jul, octo = out["months"]
        self.assertEqual(jul["pulses"], 4)
        self.assertEqual(jul["times"], ["05:05", "09:05", "13:05", "17:05"])
        self.assertEqual(jul["young"], 2)
        self.assertEqual(octo["pulses"], 2)
        self.assertNotIn("_private", out)
        self.assertNotIn("et0_reference", out)

    def test_no_panel_without_a_rate(self):
        self.assertIsNone(analyze._next_season(self.NS, {"rate": None, "regimes": []}, 0.2188))
        self.assertIsNone(analyze._next_season(None, {"rate": 12.1, "regimes": []}, 0.2188))


if __name__ == "__main__":
    unittest.main()
