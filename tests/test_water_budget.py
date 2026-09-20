"""
One rule scores a weekly depth against the ETc band, for measured weeks and for
the plan projection alike (#13), and each week derives its OWN band from that
week's cached FAO-56 ET0 (#22). Run from the repo root:

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "lib"))

import events  # noqa: E402

BAND = [1.75, 2.45]
KC = [1.05, 1.15]

# ET0 chosen so a seven-day week sums to exactly one inch of reference ET:
# the week's band is then numerically equal to Kc, which keeps every band
# assertion below readable as "Kc, scaled".
ONE_INCH_DAY = events.MM_PER_INCH / 7


def _days(n, start=1):
    return [f"2026-09-{d:02d}" for d in range(start, start + n)]


def _et0(dates, mm=ONE_INCH_DAY):
    return {d: mm for d in dates}


class BandVerdict(unittest.TestCase):
    def test_below_the_band_is_under_not_in_band(self):
        self.assertEqual(events.band_verdict(1.0, BAND), "under")

    def test_edges_are_in_band(self):
        self.assertEqual(events.band_verdict(1.75, BAND), "in band")
        self.assertEqual(events.band_verdict(2.45, BAND), "in band")
        self.assertEqual(events.band_verdict(2.46, BAND), "over")

    def test_no_band_or_no_depth_gives_no_verdict(self):
        self.assertIsNone(events.band_verdict(2.0, None))
        self.assertIsNone(events.band_verdict(None, BAND))

    def test_weeks_are_scored_by_the_same_rule(self):
        # 7 full days of 10 L at 100 L/inch = 0.7 in, against a band of
        # [1.05, 1.15]: under.
        dates = _days(7)
        daily = [{"date": d, "draw": 10.0} for d in dates]
        wk = events.water_budget(daily, 100.0, _et0(dates), KC)
        self.assertEqual([w["verdict"] for w in wk], ["under"])


class WeekBand(unittest.TestCase):
    """The band is the week's own summed ET0 x Kc, or nothing at all (#22)."""

    def test_band_is_the_weeks_own_et0_times_kc(self):
        dates = _days(7)
        self.assertEqual(events.week_band(dates, _et0(dates), KC), [1.05, 1.15])
        # At ONE_INCH_DAY the depth factor is exactly 1.0, so the line above
        # cannot tell "x weekly ET0" from "ignore ET0" (#1). Scale it.
        self.assertEqual(events.week_band(dates, _et0(dates, ONE_INCH_DAY * 2), KC),
                         [2.1, 2.3])

    def test_a_drier_week_gets_a_lower_band_than_a_wetter_one(self):
        # The whole point: one constant could not tell these two apart.
        hot = _days(7, 1)
        cool = _days(7, 8)
        et0 = {**_et0(hot, ONE_INCH_DAY * 1.5), **_et0(cool, ONE_INCH_DAY * 0.5)}
        daily = [{"date": d, "draw": 23.5} for d in hot + cool]
        wk = events.water_budget(daily, 100.0, et0, KC)
        self.assertEqual([[w["etc_lo"], w["etc_hi"]] for w in wk],
                         [[1.58, 1.73], [0.53, 0.57]])
        # The same 1.65 in/wk both weeks: in band while it is hot, over once
        # demand drops. A season constant gives one verdict for both.
        self.assertEqual([w["verdict"] for w in wk], ["in band", "over"])

    def test_one_missing_day_withholds_the_band_rather_than_reading_zero(self):
        dates = _days(7)
        et0 = _et0(dates)
        del et0[dates[3]]
        self.assertIsNone(events.week_band(dates, et0, KC))

    def test_a_withheld_band_withholds_the_verdict_and_the_multiple(self):
        dates = _days(7)
        daily = [{"date": d, "draw": 130.0} for d in dates]
        wk = events.water_budget(daily, 100.0, {}, KC)
        self.assertEqual(len(wk), 1)
        self.assertEqual(wk[0]["inches"], 9.1)          # still measured
        for key in ("etc_lo", "etc_hi", "verdict", "x_etc"):
            self.assertIsNone(wk[0][key], key)

    def test_no_kc_withholds_the_band(self):
        dates = _days(7)
        self.assertIsNone(events.week_band(dates, _et0(dates), None))


if __name__ == "__main__":
    unittest.main()
