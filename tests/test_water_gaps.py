"""
A missing export day and a meter reset in the WFC01 odometer (#8). Run from the
repo root:

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import analyze  # noqa: E402
import events  # noqa: E402
import parse_ecowitt  # noqa: E402


def _day(date, cum_end):
    """Full day of hourly readings whose odometer ends the day at `cum_end`."""
    return [{"dt": date + timedelta(hours=h), "tom": 60.0, "pep": 40.0,
             "water": float(cum_end)} for h in range(24)]


def _readings(ends):
    """`ends`: {day-of-month: end-of-day cumulative}; absent days have no rows."""
    out = []
    for dom, cum in sorted(ends.items()):
        out += _day(datetime(2026, 9, dom), cum)
    return out


# 50 L/day, with the 4th never exported: the 5th's odometer is 100 L past the 3rd's.
GAP = _readings({1: 100, 2: 150, 3: 200, 5: 300, 6: 350})


class MissingDayIsNamed(unittest.TestCase):
    def test_missing_days_lists_the_absent_date(self):
        self.assertEqual(parse_ecowitt.missing_days(GAP), ["2026-09-04"])

    def test_contiguous_record_has_none(self):
        self.assertEqual(parse_ecowitt.missing_days(_readings({1: 1, 2: 2, 3: 3})), [])


class GapDrawIsSpread(unittest.TestCase):
    def setUp(self):
        self.w = analyze._water(GAP)
        self.rows = {r["date"]: r for r in self.w["daily"]}

    def test_draw_is_split_over_the_days_it_covers(self):
        for d in ("2026-09-04", "2026-09-05"):
            self.assertEqual(self.rows[d]["draw"], 50.0)
            self.assertEqual(self.rows[d]["span_days"], 2)
        self.assertEqual(self.rows["2026-09-03"]["span_days"], 1)

    def test_total_is_what_the_odometer_moved(self):
        self.assertEqual(self.w["total_L"], 250.0)
        self.assertEqual(self.w["daily"][-1]["cum"], 250.0)

    def test_gap_is_reported(self):
        self.assertEqual(self.w["gaps"], [{"missing": ["2026-09-04"],
                                           "booked_to": "2026-09-05", "liters": 100.0}])


class PerDayFiguresSkipTheSpread(unittest.TestCase):
    def setUp(self):
        self.daily = analyze._water(GAP)["daily"]

    def test_per_day_draw_drops_spread_rows(self):
        self.assertEqual(sorted(events.per_day_draw(self.daily)),
                         ["2026-09-02", "2026-09-03", "2026-09-06"])

    def test_retention_has_no_row_for_the_day_after_the_gap(self):
        evs = [{"date": d, "retained": 5.0, "manual": False}
               for d in ("2026-09-03", "2026-09-05")]
        self.assertEqual([r["date"] for r in events.retention(evs, self.daily)],
                         ["2026-09-03"])

    def test_regime_litres_exclude_the_day_after_the_gap(self):
        ext = [{"date": f"2026-09-0{d}", "pre_irrigation": 60.0, "day_min": 58.0}
               for d in (2, 3, 5, 6)]
        # Make the spread share distinguishable from a real day: 09-05 covers 160 L.
        daily = analyze._water(_readings({1: 100, 2: 150, 3: 200, 5: 360, 6: 410}))["daily"]
        rg = events.regime_summary([{"label": "r", "start": "2026-09-02"}], ext, daily)
        self.assertEqual(rg[0]["liters_day"], 50.0)


class ResetIsReported(unittest.TestCase):
    def test_negative_diff_is_flagged_not_swallowed(self):
        w = analyze._water(_readings({1: 100, 2: 150, 3: 20, 4: 70}))
        rows = {r["date"]: r for r in w["daily"]}
        self.assertEqual(rows["2026-09-03"]["draw"], 0.0)
        self.assertTrue(rows["2026-09-03"]["reset"])
        self.assertEqual(w["resets"], [{"date": "2026-09-03", "from_L": 150.0, "to_L": 20.0}])
        self.assertNotIn("2026-09-03", events.per_day_draw(w["daily"]))
        self.assertEqual(rows["2026-09-04"]["draw"], 50.0)


if __name__ == "__main__":
    unittest.main()
