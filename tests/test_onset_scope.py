"""
The onset panel is scored against the regime the readings were produced under
(#25). A time change is logged the evening before it first runs; until then the
open row's times have never run and must not become the slot grid. Run from
the repo root:

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


def _readings(days):
    """Hourly readings from 2026-09-01, flat probes, 40 L/day on the meter."""
    return [{"dt": datetime(2026, 9, 1) + timedelta(days=i, hours=h), "tom": 60.0,
             "pep": 40.0, "water": 40.0 * i} for i in range(days) for h in range(24)]


RUNS_A = [{"time": "05:05", "seconds": 90}, {"time": "17:05", "seconds": 90}]
RUNS_B = [{"time": "06:05", "seconds": 90}, {"time": "18:05", "seconds": 90}]
ONE = [{"label": "A", "start": "2026-09-01", "end": None, "runs": RUNS_A}]
# B is logged on the evening of 09-10 and first runs on 09-11.
TWO = [{"label": "A", "start": "2026-09-01", "end": "2026-09-10", "runs": RUNS_A},
       {"label": "B", "start": "2026-09-11", "end": None, "runs": RUNS_B}]


def _config(regimes, **plan):
    cfg = farm_config.load(os.path.join(ROOT, "config.json"))
    for key in farm_config.DERIVED_PLAN_KEYS:
        del cfg["plan"][key]
    cfg["plan"]["regimes"] = copy.deepcopy(regimes)
    cfg["plan"].update(plan)
    return farm_config.resolve(cfg)


def _onset(readings, cfg, key):
    return analyze.build(readings, cfg)[0]["native"]["onset"][key]


class SlotsFollowTheRunningRegime(unittest.TestCase):
    def test_a_time_change_that_has_not_run_leaves_the_grid_alone(self):
        rd = _readings(10)                      # newest reading 2026-09-10 23:00
        rows = _onset(rd, _config(TWO), "tom")
        self.assertTrue(rows)
        self.assertEqual(rows, _onset(rd, _config(ONE), "tom"))
        self.assertEqual({r["scheduled"] for r in rows}, {"05:05", "17:05"})

    def test_a_time_change_that_has_run_moves_the_grid(self):
        rows = _onset(_readings(12), _config(TWO), "tom")
        self.assertEqual({r["scheduled"] for r in rows}, {"06:05", "18:05"})
        self.assertEqual(rows[0]["date"], "2026-09-11")


class PepperTimeNotYetRun(unittest.TestCase):
    def test_future_pepper_time_withholds_the_panel(self):
        rd = _readings(10)
        self.assertEqual(_onset(rd, _config(ONE, pepper_time_effective="2026-09-11"), "pep"), [])
        rows = _onset(rd, _config(ONE, pepper_time_effective="2026-09-05"), "pep")
        self.assertTrue(rows)
        self.assertEqual(rows[0]["date"], "2026-09-05")


if __name__ == "__main__":
    unittest.main()
