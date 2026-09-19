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


if __name__ == "__main__":
    unittest.main()
