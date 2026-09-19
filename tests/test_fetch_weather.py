"""
Invariants of the merge and coverage check in lib/fetch_weather.py (#3). Pure
functions on synthetic rows -- nothing here touches the network or inputs/.

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import fetch_weather as fw  # noqa: E402

NAN = float("nan")
H = 3600


def _rows(hours, temp, et0=0.1):
    """{epoch: row} for each hour index in `hours`."""
    return {h * H: {"temperature_2m": temp, "et0_fao_evapotranspiration": et0}
            for h in hours}


class Merge(unittest.TestCase):
    def test_archive_wins_where_both_have_a_value(self):
        out = fw.merge_hourly(_rows(range(4), 10.0), _rows(range(4), 99.0))
        self.assertEqual({r["temperature_2m"] for r in out.values()}, {10.0})

    def test_forecast_fills_only_the_hours_archive_lacks(self):
        archive = {**_rows(range(3), 10.0), **_rows(range(3, 6), NAN)}
        out = fw.merge_hourly(archive, _rows(range(2, 6), 99.0))
        self.assertEqual([out[h * H]["temperature_2m"] for h in range(6)],
                         [10.0, 10.0, 10.0, 99.0, 99.0, 99.0])

    def test_an_hour_with_temperature_but_no_et0_is_not_usable(self):
        archive = _rows([0], 10.0, et0=NAN)
        self.assertEqual(fw.merge_hourly(archive, {}), {})
        self.assertEqual(
            fw.merge_hourly(archive, _rows([0], 99.0))[0]["temperature_2m"], 99.0)

    def test_hours_nobody_has_are_dropped_not_written_as_nan(self):
        # Both endpoints return a row for every requested hour; an unknown hour
        # is a NaN row, not an absent one.
        forecast = {**_rows(range(2), 99.0), **_rows(range(2, 4), NAN)}
        out = fw.merge_hourly(_rows(range(4), NAN), forecast)
        self.assertEqual(sorted(out), [0, H])


class Coverage(unittest.TestCase):
    def test_a_full_series_passes(self):
        self.assertIsNone(fw.coverage_error(_rows(range(48), 10.0), 0, 24 * H))

    def test_trailing_future_hours_are_not_a_hole(self):
        # The end day has started (hour 24 present) but is not over.
        self.assertIsNone(fw.coverage_error(_rows(range(30), 10.0), 0, 24 * H))

    def test_leading_truncation_is_an_error(self):
        # The 2026-09-19 failure: forecast returned NaN for the first 231 hours
        # and the script wrote the remainder and exited 0.
        err = fw.coverage_error(_rows(range(231, 300), 10.0), 0, 24 * H)
        self.assertIn("231 hours after the requested start", err)

    def test_an_interior_hole_is_an_error(self):
        rows = _rows([h for h in range(48) if h != 17], 10.0)
        self.assertIn("hour 17", fw.coverage_error(rows, 0, 24 * H))

    def test_stopping_before_the_end_date_is_an_error(self):
        err = fw.coverage_error(_rows(range(20), 10.0), 0, 48 * H)
        self.assertIn("29 hours before the requested end", err)

    def test_nothing_usable_is_an_error(self):
        self.assertIsNotNone(fw.coverage_error({}, 0, 24 * H))


if __name__ == "__main__":
    unittest.main()
