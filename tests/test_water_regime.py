"""
Litres are scoped to the regime the readings cover, and the advice names the
schedule that delivered them (#12). Run from the repo root:

    .venv/bin/python -m unittest discover tests
"""

import copy
import os
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "lib"))

import analyze  # noqa: E402
import farm_config  # noqa: E402


def _readings(per_day):
    """Hourly readings from 2026-09-01, one full day per entry of litres drawn."""
    out, cum = [], 0.0
    for i, litres in enumerate(per_day):
        cum += litres
        out += [{"dt": datetime(2026, 9, 1) + timedelta(days=i, hours=h), "tom": 60.0,
                 "pep": 40.0, "water": cum} for h in range(24)]
    return out


# Sep 1-20 at 100 L/day (the first day only sets the meter's baseline), Sep 21-25 at 40.
STEP_DOWN = _readings([100] * 20 + [40] * 5)
STEP_UP = _readings([40] * 20 + [100] * 5)

RUNS = [{"time": "05:05", "seconds": 90}, {"time": "17:05", "seconds": 90}]
REGIMES = [{"label": "A", "start": "2026-09-01", "end": "2026-09-20", "runs": RUNS},
           {"label": "B", "start": "2026-09-21", "end": "2026-09-25", "runs": RUNS},
           {"label": "C", "start": "2026-09-26", "end": None, "runs": RUNS}]


def _config(regimes):
    """The tracked config with only the regimes replaced -- build() needs the rest.
    runs_effective is the last row's start: there is no second date to set (#15)."""
    cfg = farm_config.load(os.path.join(ROOT, "config.json"))
    for key in farm_config.DERIVED_PLAN_KEYS:
        del cfg["plan"][key]
    cfg["plan"]["regimes"] = copy.deepcopy(regimes)
    return farm_config.resolve(cfg)


class RecentAverageIsBoundedByTheWindow(unittest.TestCase):
    def test_since_excludes_the_earlier_regime(self):
        w = analyze._water(STEP_DOWN, "2026-09-21", None, "B")
        self.assertEqual((w["recent_avg_L"], w["recent_n"], w["recent_label"]), (40.0, 5, "B"))

    def test_until_is_exclusive_and_excludes_the_later_regime(self):
        w = analyze._water(STEP_DOWN, "2026-09-02", "2026-09-21", "A")
        self.assertEqual((w["recent_avg_L"], w["recent_n"]), (100.0, 19))


class PlanLoggedButNotYetRun(unittest.TestCase):
    """The open regime starts the day after the newest reading."""

    def setUp(self):
        data, cfg = analyze.build(STEP_DOWN, _config(REGIMES))
        self.water, self.text = data["water"], cfg["advice"]["water"]

    def test_litres_come_from_the_regime_that_ran(self):
        self.assertEqual((self.water["recent_avg_L"], self.water["recent_label"]), (40.0, "B"))

    def test_advice_names_the_previous_schedule_not_the_new_plan(self):
        self.assertIn("~40.0 L", self.text)
        self.assertIn("previous schedule (B)", self.text)
        self.assertIn("logged for 2026-09-26 has not run yet", self.text)
        self.assertNotIn("bed's 2 ×", self.text)


class ShiftClauseFollowsTheSign(unittest.TestCase):
    def _text(self, readings):
        return analyze.build(readings, _config(REGIMES[:2]))[1]["advice"]["water"]

    def test_lower_recent_mean_reads_down(self):
        self.assertIn("down from a 87.5 L lifetime average", self._text(STEP_DOWN))

    def test_higher_recent_mean_reads_up(self):
        text = self._text(STEP_UP)
        self.assertIn("up from a 52.5 L lifetime average", text)
        self.assertNotIn("flood dosing", text)

    def test_started_plan_is_not_called_pending(self):
        self.assertNotIn("has not run yet", self._text(STEP_DOWN))


if __name__ == "__main__":
    unittest.main()
