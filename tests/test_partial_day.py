"""
A trailing partial day -- an export pulled before the day's last run -- stays out
of every per-day litres and daily-minimum aggregate (#9). Run from the repo root:

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import analyze  # noqa: E402
import events  # noqa: E402


def _day(dom, cum_end, hours=24, tom=60.0):
    """Hourly readings for 2026-09-<dom>; `hours` < 20 makes the day partial."""
    return [{"dt": datetime(2026, 9, dom) + timedelta(hours=h), "tom": tom, "pep": 40.0,
             "water": float(cum_end)} for h in range(hours)]


# 40 L/day for three days, then an export pulled at 12:00 with one 20 L run logged.
TAIL = _day(1, 100) + _day(2, 140) + _day(3, 180) + _day(4, 200, hours=13, tom=70.0)


class PartialTailIsMarked(unittest.TestCase):
    def setUp(self):
        self.w = analyze._water(TAIL)

    def test_only_the_partial_row_is_flagged(self):
        self.assertEqual([r["date"] for r in self.w["daily"] if r.get("partial")],
                         ["2026-09-04"])

    def test_per_day_draw_skips_it(self):
        self.assertEqual(events.per_day_draw(self.w["daily"]),
                         {"2026-09-02": 40.0, "2026-09-03": 40.0})

    def test_total_still_counts_it(self):
        self.assertEqual(self.w["total_L"], 100.0)


class RegimeMeansUseRepresentativeDays(unittest.TestCase):
    REGIME = [{"label": "r", "start": "2026-09-01", "end": None}]

    def _summary(self, skip_days=()):
        w = analyze._water(TAIL)
        ext = events.daily_extremes(TAIL, "tom")
        return events.regime_summary(self.REGIME, ext, w["daily"], skip_days)[0]

    def test_day_min_skips_the_partial_day(self):
        # three complete days at 60, a partial morning at 70
        self.assertEqual(self._summary()["day_min"], 60.0)

    def test_liters_day_skips_metered_manual_days(self):
        manual = [{"date": "2026-09-02", "metered": True},
                  {"date": "2026-09-03", "metered": False}]
        rows = [{"date": f"2026-09-0{d}", "draw": L, "span_days": 1}
                for d, L in ((1, 40.0), (2, 110.0), (3, 42.0))]
        skip = events.metered_manual_days(manual)
        self.assertEqual(skip, {"2026-09-02"})
        ext = events.daily_extremes(TAIL, "tom")
        got = events.regime_summary(self.REGIME, ext, rows, skip)[0]
        self.assertEqual(got["liters_day"], 41.0)


class BudgetWeeksAreComplete(unittest.TestCase):
    @staticmethod
    def _rows(n, **last):
        d0 = datetime(2026, 9, 1)
        rows = [{"date": f"{d0 + timedelta(days=i):%Y-%m-%d}", "draw": 40.0, "span_days": 1}
                for i in range(n)]
        rows[-1].update(last)
        return rows

    def _weeks(self, rows):
        return [(w["start"], w["days"], w["liters"])
                for w in events.water_budget(rows, 100.0, [1.75, 2.45])]

    def test_a_six_day_block_is_not_a_week(self):
        self.assertEqual(self._weeks(self._rows(13)), [("2026-09-01", 7, 280.0)])

    def test_a_partial_seventh_day_drops_the_week(self):
        self.assertEqual(self._weeks(self._rows(14, partial=True)), [("2026-09-01", 7, 280.0)])

    def test_a_reset_day_drops_the_week(self):
        self.assertEqual(self._weeks(self._rows(14, reset=True)), [("2026-09-01", 7, 280.0)])

    def test_two_full_weeks_are_both_kept(self):
        self.assertEqual(len(self._weeks(self._rows(14))), 2)


class CycleSkipsThePartialDay(unittest.TestCase):
    def test_partial_day_is_out_of_the_median_and_the_count(self):
        ext = {"tom": events.daily_extremes(TAIL, "tom"), "pep": []}
        daily = analyze._daily(TAIL, ext)
        self.assertEqual([r["date"] for r in daily if r["tom_partial"]], ["2026-09-04"])
        c = analyze._cycle(daily[1:], "tom")     # 60, 60, partial 70
        self.assertEqual((c["trough"], c["n_days"]), (60, 2))


if __name__ == "__main__":
    unittest.main()
