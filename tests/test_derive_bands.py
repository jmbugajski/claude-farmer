"""
Invariants of lib/derive_bands.py. Run from the repo root:

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import derive_bands as db  # noqa: E402


def _flat_with_ticks(start, level, hourly_et0, tick_at):
    """5-minute readings holding `level`, dropping 1 point at each index in
    `tick_at`. hourly_et0 is one ET0 value per consecutive hour."""
    rows, et, v = [], {}, float(level)
    for h, e in enumerate(hourly_et0):
        et[(start + timedelta(hours=h)).strftime("%Y-%m-%d %H")] = e
    for i in range(len(hourly_et0) * 12):
        if i in tick_at:
            v -= 1
        rows.append({"dt": start + timedelta(minutes=5 * i), "tom": v})
    return rows, et


class DayRate(unittest.TestCase):
    def test_rate_is_total_drop_over_total_demand(self):
        # One tick in a dawn hour (ET0 0.03) and one at midday (0.60), in the
        # same bin. Scored per interval the dawn tick is worth 400 and the noon
        # tick 20, so a mean of ratios reports ~8.8; the bin actually lost 2
        # points against 1.18 mm of demand, i.e. 1.69.
        rows, et = _flat_with_ticks(datetime(2026, 8, 1, 6), 73, [0.03, 0.60, 0.60], {6, 30})
        _, day, _ = db.curves(rows, et, "tom", 5)
        self.assertEqual(sorted(day), [70])
        demand = (12 * 0.03 + 12 * 0.60 + 11 * 0.60) / 12   # 36 rows = 35 intervals
        self.assertAlmostEqual(sum(e for _, e in day[70]), demand, places=6)
        self.assertAlmostEqual(db._day_rate(day[70]), 2 / demand, places=6)

    def test_bin_without_demand_has_no_rate(self):
        self.assertIsNone(db._day_rate([]))
        self.assertIsNone(db._day_rate([(1.0, 0.0)]))


class MissingEt0(unittest.TestCase):
    def test_hour_without_et0_row_is_skipped_not_night(self):
        # Three hours, one tick in each. Hour 0 has ET0 = 0 (a real night),
        # hour 1 has NO row, hour 2 is day. Defaulting the missing hour to 0.0
        # files its 12 intervals as night drainage.
        start = datetime(2026, 8, 1, 4)
        rows, et = _flat_with_ticks(start, 73, [0.0, 0.5, 0.5], {6, 18, 30})
        del et[(start + timedelta(hours=1)).strftime("%Y-%m-%d %H")]
        night, day, skipped = db.curves(rows, et, "tom", 5)
        self.assertEqual(skipped, 12)
        self.assertEqual(len(night[70]), 12)              # the ET0 = 0 hour only
        self.assertAlmostEqual(sum(night[70]), 12.0)      # one tick / (5 min)
        self.assertEqual(len(day[70]), 11)
        self.assertAlmostEqual(sum(d for d, _ in day[70]), 1.0)


class OffNominalDays(unittest.TestCase):
    HEALTH = {"nominal_volts": 1.5, "volt_tolerance": 0.15}

    def _two_days(self):
        # Day 1 on a lithium cell (1.70 V) and flat -- the 2026-07 pepper
        # failure; day 2 on alkaline (1.60 V) with one tick per hour.
        d1, d2 = datetime(2026, 8, 1, 10), datetime(2026, 8, 2, 10)
        dead, et1 = _flat_with_ticks(d1, 37, [0.5, 0.5], set())
        live, et2 = _flat_with_ticks(d2, 37, [0.5, 0.5], {6, 18})
        for r in dead:
            r["v_tom"] = 1.70
        for r in live:
            r["v_tom"] = 1.60
        return dead + live, {**et1, **et2}, d1.date(), d2.date()

    def test_day_outside_volt_tolerance_is_returned(self):
        rows, _, d1, _ = self._two_days()
        self.assertEqual(db.off_nominal_days(rows, "v_tom", self.HEALTH), {d1})

    def test_excluded_day_never_reaches_a_bin(self):
        # Unfiltered, the flat day halves the bin's rate: 2 points over 46
        # intervals instead of over 23.
        rows, et, d1, _ = self._two_days()
        _, mixed, _ = db.curves(rows, et, "tom", 5)
        _, day, skipped = db.curves(rows, et, "tom", 5, exclude_days={d1})
        self.assertEqual(len(mixed[35]), 46)
        self.assertEqual(len(day[35]), 23)
        self.assertEqual(skipped, 0)
        self.assertAlmostEqual(db._day_rate(day[35]), 2 * db._day_rate(mixed[35]), places=6)


if __name__ == "__main__":
    unittest.main()
