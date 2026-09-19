"""
Invariants of the daily peak/trough in lib/analyze.py. Run from the repo root:

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import analyze  # noqa: E402
import events  # noqa: E402


def _day_with_pulse(start, base, peak):
    """One day of 5-minute readings holding `base`, with a pulse that starts
    after 05:00, tops out at `peak` at 05:15 and is back to `base` by 05:45 --
    so no on-the-hour sample ever sees it."""
    shape = {61: base + 4, 62: (base + peak) / 2, 63: peak, 64: peak - 3,
             65: (base + peak) / 2, 66: base + 3, 67: base + 1}
    return [{"dt": start + timedelta(minutes=5 * i),
             "tom": float(shape.get(i, base)), "pep": float(base), "water": None}
            for i in range(288)]


class DailyExtremesAreNative(unittest.TestCase):
    def setUp(self):
        self.readings = _day_with_pulse(datetime(2026, 9, 1), 60, 78)
        series = analyze.resample(self.readings, 60)
        self.assertEqual(max(p["tom"] for p in series), 60.0)   # the premise
        ext = {k: events.daily_extremes(self.readings, k) for k in ("tom", "pep")}
        self.daily = analyze._daily(series, ext)

    def test_peak_between_the_hours_reaches_daily_max(self):
        self.assertEqual(self.daily[0]["tom_max"], 78.0)
        self.assertEqual(self.daily[0]["tom_min"], 60.0)

    def test_mean_stays_on_the_hourly_series(self):
        self.assertEqual(self.daily[0]["tom_mean"], 60.0)


class CycleIsOneMedian(unittest.TestCase):
    def _rows(self, peaks):
        return [{"date": f"2026-09-{i + 1:02d}", "tom_max": float(v), "tom_min": 50.0}
                for i, v in enumerate(peaks)]

    def test_even_window_takes_the_true_median(self):
        # upper-middle element, the template's old med(), would give 74
        c = analyze._cycle(self._rows([60, 70, 74, 80]), "tom")
        self.assertEqual((c["peak"], c["trough"], c["n_days"]), (72, 50, 4))

    def test_window_is_scoped_to_the_current_regime(self):
        rows = self._rows([90, 90, 90, 60, 62, 64])
        self.assertEqual(analyze._cycle(rows, "tom")["peak"], 77)
        c = analyze._cycle(rows, "tom", since=datetime(2026, 9, 4))
        self.assertEqual((c["peak"], c["n_days"]), (62, 3))

    def test_closed_regime_bounds_the_window_from_above(self):
        # until is exclusive: _regime_window returns the regime's end + 1 day
        rows = self._rows([60, 62, 64, 90, 90])
        c = analyze._cycle(rows, "tom", until=datetime(2026, 9, 4))
        self.assertEqual((c["peak"], c["n_days"]), (62, 3))

    def test_empty_regime_window_does_not_fall_back_to_all_days(self):
        c = analyze._cycle(self._rows([60, 62]), "tom", since=datetime(2026, 9, 10))
        self.assertEqual((c["peak"], c["trough"], c["n_days"]), (None, None, 0))


class RegimeSplitIsScoped(unittest.TestCase):
    BANDS = {"drainage_ceiling": 75, "stress_floor": 50}

    def setUp(self):
        # three days flooded at 80, then three days in band at 60, hourly
        t0 = datetime(2026, 9, 1)
        self.series = [{"dt": t0 + timedelta(hours=h), "tom": 80.0 if h < 72 else 60.0}
                       for h in range(144)]

    def test_retired_regime_does_not_count_as_draining_now(self):
        whole = analyze._regime_split(self.series, "tom", self.BANDS)
        self.assertEqual((whole["pct_draining"], whole["n"]), (50.0, 144))
        now = analyze._regime_split(self.series, "tom", self.BANDS, since=datetime(2026, 9, 4))
        self.assertEqual((now["pct_draining"], now["pct_working"], now["n"]), (0.0, 100.0, 72))

    def test_closed_regime_bounds_the_split_from_above(self):
        s = analyze._regime_split(self.series, "tom", self.BANDS, until=datetime(2026, 9, 4))
        self.assertEqual((s["pct_draining"], s["n"]), (100.0, 72))

    def test_withheld_split_still_reports_the_scoped_n(self):
        s = analyze._regime_split(self.series, "tom", dict(self.BANDS, verified=False),
                                  since=datetime(2026, 9, 4))
        self.assertEqual((s["pct_draining"], s["n"]), (None, 72))


if __name__ == "__main__":
    unittest.main()
