"""
_sensor_health()'s trust_from is the date a channel can be scored from (#11): the
later of the last voltage step and the day after the last dead run. Run from the
repo root:

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import analyze  # noqa: E402

D0 = datetime(2026, 9, 1)
CFG = {"health": {"volt_tolerance": 0.15, "ad_range_floor": 12}}


def _readings(swings, volts=None):
    """Hourly pepper readings from 2026-09-01; day i has AD range swings[i]."""
    out = []
    for i, swing in enumerate(swings):
        for h in range(24):
            out.append({"dt": D0 + timedelta(days=i, hours=h),
                        "v_pep": volts[i] if volts else 1.6,
                        "pep_ad": 200 + (swing if h == 12 else 0)})
    return out


def _day(dom):
    return f"2026-09-{dom:02d}"


def _trust(readings, pep_event_days=()):
    return analyze._sensor_health(readings, CFG, {"pep": set(pep_event_days)})["pep"]["trust_from"]


class DeadRunSetsTheDayAfter(unittest.TestCase):
    def test_flat_days_without_events_end_the_untrusted_window(self):
        # days 3-7 flat, recovery on day 8
        r = _readings([40, 40, 5, 5, 5, 5, 5, 40, 40])
        self.assertEqual(_trust(r, [_day(1), _day(2), _day(8), _day(9)]), _day(8))

    def test_low_range_days_with_events_are_not_dead(self):
        # 2026-09-12 -> 09-18 on the real pepper channel: 7-9 counts, an event a day
        r = _readings([40, 40, 5, 5, 5, 5, 5, 40, 40])
        self.assertIsNone(_trust(r, [_day(d) for d in range(1, 10)]))

    def test_a_run_shorter_than_three_days_is_not_dead(self):
        r = _readings([40, 40, 5, 5, 40, 40])
        self.assertIsNone(_trust(r))

    def test_the_last_dead_run_wins(self):
        r = _readings([5, 5, 5, 40, 40, 5, 5, 5, 5, 40])
        self.assertEqual(_trust(r), _day(10))


class VoltageStepStillCounts(unittest.TestCase):
    def test_step_date_is_the_boundary(self):
        r = _readings([40] * 6, volts=[1.3, 1.3, 1.3, 1.7, 1.7, 1.7])
        self.assertEqual(_trust(r), _day(4))

    def test_the_last_step_wins(self):
        r = _readings([40] * 6, volts=[1.3, 1.7, 1.7, 1.7, 1.3, 1.3])
        self.assertEqual(_trust(r), _day(5))

    def test_later_of_step_and_dead_run(self):
        # the real pepper shape: step to lithium, then the probe dies, then recovers
        r = _readings([40, 40, 5, 5, 5, 5, 40], volts=[1.3, 1.7, 1.7, 1.7, 1.7, 1.7, 1.7])
        self.assertEqual(_trust(r), _day(7))


class NothingCarriesBetweenCalls(unittest.TestCase):
    def test_a_clean_record_after_a_broken_one_has_no_boundary(self):
        self.assertEqual(_trust(_readings([40, 5, 5, 5, 40])), _day(5))
        self.assertIsNone(_trust(_readings([40] * 5)))


if __name__ == "__main__":
    unittest.main()
