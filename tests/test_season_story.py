"""
The lead panel's figures (#32): per-schedule kept share and applied-vs-estimate,
and the season summary, from synthetic rows. Run from the repo root:

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "lib"))

import events  # noqa: E402

KC = [1.05, 1.15]
# Five days whose ET0 sums to one inch: the ETc top band over them is 1.15 in.
MM = events.MM_PER_INCH / 5


def _days(start, n):
    return [f"2026-09-{d:02d}" for d in range(start, start + n)]


REGIMES = [{"label": "long", "start": "2026-09-01", "end": "2026-09-05",
            "runs": [{"time": "05:05", "seconds": 840}]},
           {"label": "pulsed", "start": "2026-09-06", "end": None,
            "runs": [{"time": "05:05", "seconds": 90}, {"time": "17:05", "seconds": 90}]}]
# long: 100 L/day = 5 in over its 5 days; pulsed: 20 L/day = 1 in.
WATER = ([{"date": d, "draw": 100.0, "span_days": 1} for d in _days(1, 5)]
         + [{"date": d, "draw": 20.0, "span_days": 1} for d in _days(6, 5)])
ET0 = {d: MM for d in _days(1, 10)}
# long runs keep 60% of their rise, pulses 90%; one manual pour is left out.
EVENTS = ([{"date": d, "retained": 6.0, "shed": 4.0} for d in _days(1, 5)]
          + [{"date": d, "retained": 9.0, "shed": 1.0} for d in _days(6, 5)] * 2
          + [{"date": "2026-09-07", "retained": 1.0, "shed": 9.0, "manual": True}])


class RegimeEfficiency(unittest.TestCase):
    def setUp(self):
        self.eff = events.regime_efficiency(REGIMES, EVENTS, WATER, ET0, KC, 100.0)

    def test_kept_share_per_schedule_skips_hand_applications(self):
        self.assertEqual((self.eff[("long", "2026-09-01")]["kept_pct"],
                          self.eff[("long", "2026-09-01")]["n_runs"]), (60, 5))
        self.assertEqual((self.eff[("pulsed", "2026-09-06")]["kept_pct"],
                          self.eff[("pulsed", "2026-09-06")]["n_runs"]), (90, 10))

    def test_applied_vs_estimate_is_on_the_schedules_own_days(self):
        # 5 in against 1.15 in of ETc, and 1 in against 1.15 in.
        self.assertEqual(self.eff[("long", "2026-09-01")]["x_etc"], 4.3)
        self.assertEqual(self.eff[("pulsed", "2026-09-06")]["x_etc"], 0.9)

    def test_a_zero_litre_day_counts_its_demand(self):
        water = [dict(r, draw=0.0) if r["date"] == "2026-09-02" else r for r in WATER]
        eff = events.regime_efficiency(REGIMES, EVENTS, water, ET0, KC, 100.0)
        self.assertEqual(eff[("long", "2026-09-01")]["x_etc"], 3.5)      # 4 in / 1.15


class SeasonSummary(unittest.TestCase):
    def test_cut_floors_and_kept_contrast(self):
        rows = [{"label": "long", "start": "2026-09-01", "days": 5, "liters_day": 100.0,
                 "pre_irrigation": 60.0, "kept_pct": 60, "n_runs": 5},
                {"label": "pulsed", "start": "2026-09-06", "days": 5, "liters_day": 20.0,
                 "pre_irrigation": 58.0, "kept_pct": 90, "n_runs": 10},
                # a second pulsed schedule, half the runs: the contrast is
                # run-weighted, (90 x 10 + 60 x 5) / 15 = 80, not (90 + 60) / 2.
                {"label": "pulsed2", "start": "2026-09-11", "days": 5, "liters_day": 20.0,
                 "pre_irrigation": 58.0, "kept_pct": 60, "n_runs": 5}]
        budget = [{"inches": 5.0, "etc_lo": 1.05, "etc_hi": 1.15},
                  {"inches": 1.0, "etc_lo": 1.05, "etc_hi": 1.15}]
        regimes = REGIMES + [{"label": "pulsed2", "start": "2026-09-11", "end": None,
                              "runs": [{"time": "05:05", "seconds": 90}]}]
        s = events.season_summary(budget, rows, regimes)
        self.assertEqual((s["weeks"], s["applied_in"], s["etc_hi_in"], s["x_etc"]), (2, 6.0, 2.3, 2.6))
        self.assertEqual((s["most_label"], s["current_label"], s["cut"]), ("long", "pulsed2", 5.0))
        self.assertEqual((s["floor_lo"], s["floor_hi"]), (58.0, 60.0))
        self.assertEqual((s["kept_long"], s["kept_pulsed"]), (60, 80))

    def test_a_week_without_a_band_is_not_scored(self):
        s = events.season_summary([{"inches": 5.0, "etc_lo": None, "etc_hi": None}], [], REGIMES)
        self.assertEqual((s["weeks"], s["x_etc"], s["cut"], s["kept_long"]), (0, None, None, None))


if __name__ == "__main__":
    unittest.main()
