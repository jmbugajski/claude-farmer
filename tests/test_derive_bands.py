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


if __name__ == "__main__":
    unittest.main()
