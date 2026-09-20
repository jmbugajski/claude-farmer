"""
_sensor_health()'s two judgements about whether a probe is still resolving (#19),
and the trust_from boundary they feed (#11). Run from the repo root:

    .venv/bin/python -m unittest discover tests

Every day here is built from a (drift, bump) pair because the distinction under
test is exactly that decomposition: the daily AD RANGE cannot separate the one
labelled failure (a trace that decayed 250 -> 203 over fifteen days, all drift)
from a dry bag on a short pulsed schedule (7-9 counts, all excursion). A helper
that only emits a midday spike, as this one did until #19, cannot express the
failure at all -- it makes every day excursion-only, i.e. healthy by
construction, which is why the tests it carried could not have caught the
tomato false positive on 2026-09-12 -> 09-14.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import analyze  # noqa: E402

D0 = datetime(2026, 9, 1)
CFG = {"health": {"volt_tolerance": 0.15, "ad_range_floor": 12,
                  "ad_excursion_floor": 3}}

# Day shapes, named for the real days they stand in for.
DEAD = (-3, 0)       # 2026-07-2x pepper: monotone decay, no diurnal cycle
LOW_SWING = (0, 8)   # 2026-09-12 -> 09-18 pepper: under the floor, still cycling
HEALTHY = (0, 40)
ENDS_HIGH = (40, 0)  # 2026-07-12 tomato: range 42, excursion 0


def _readings(shape, volts=None):
    """Hourly pepper readings from 2026-09-01, one (drift, bump) pair per day.

    `drift` is the day's net change, applied as a linear ramp across its 24
    samples; `bump` is added at midday and comes back off again. So the day's
    AD range is roughly max(|drift|, bump) while its excursion above the higher
    endpoint is `bump` alone -- the two numbers the grade needs to see apart.
    """
    out = []
    for i, (drift, bump) in enumerate(shape):
        for h in range(24):
            ad = 200.0 + drift * (h / 23.0) + (bump if h == 12 else 0)
            out.append({"dt": D0 + timedelta(days=i, hours=h),
                        "v_pep": volts[i] if volts else 1.6,
                        "pep_ad": ad})
    return out


def _day(dom):
    return f"2026-09-{dom:02d}"


def _pep(readings):
    return analyze._sensor_health(readings, CFG)["pep"]


def _trust(readings):
    return _pep(readings)["trust_from"]


def _levels(readings):
    return [f["level"] for f in _pep(readings)["flags"]]


class TheBadGradeNeedsBothLimbs(unittest.TestCase):
    """Range under the floor is necessary, never sufficient."""

    def test_a_decaying_trace_under_the_floor_grades_bad(self):
        self.assertIn("bad", _levels(_readings([HEALTHY] * 6 + [DEAD] * 5)))

    def test_a_low_swing_trace_under_the_floor_does_not(self):
        # The live pepper channel: 8 counts of range, all of it excursion.
        self.assertNotIn("bad", _levels(_readings([HEALTHY] * 6 + [LOW_SWING] * 5)))

    def test_excursion_alone_does_not_condemn_a_day_that_ends_high(self):
        # Range 40, excursion 0. The control for the conjunction: without the
        # range limb this is indistinguishable from a dead trace.
        self.assertNotIn("bad", _levels(_readings([HEALTHY] * 6 + [ENDS_HIGH] * 5)))


class DeadRunSetsTheDayAfter(unittest.TestCase):
    def test_drifting_days_without_excursion_end_the_untrusted_window(self):
        # days 3-7 dead, recovery on day 8
        r = _readings([HEALTHY, HEALTHY] + [DEAD] * 5 + [HEALTHY, HEALTHY])
        self.assertEqual(_trust(r), _day(8))

    def test_low_range_days_that_still_swing_are_not_dead(self):
        # 2026-09-12 -> 09-18 on the real pepper channel: 7-9 counts, under the
        # floor, an excursion every day. Until #19 this case was carried by "a
        # detected irrigation event", which made the boundary depend on the
        # event detector's tuning and took a detection as proof of life on the
        # one channel whose ability to register an onset was in question.
        r = _readings([HEALTHY, HEALTHY] + [LOW_SWING] * 5 + [HEALTHY, HEALTHY])
        self.assertIsNone(_trust(r))

    def test_a_run_shorter_than_three_days_is_not_dead(self):
        self.assertIsNone(_trust(_readings([HEALTHY, HEALTHY, DEAD, DEAD, HEALTHY, HEALTHY])))

    def test_the_last_dead_run_wins(self):
        r = _readings([DEAD] * 3 + [HEALTHY] * 2 + [DEAD] * 4 + [HEALTHY])
        self.assertEqual(_trust(r), _day(10))


class VoltageStepStillCounts(unittest.TestCase):
    def test_step_date_is_the_boundary(self):
        r = _readings([HEALTHY] * 6, volts=[1.3, 1.3, 1.3, 1.7, 1.7, 1.7])
        self.assertEqual(_trust(r), _day(4))

    def test_the_last_step_wins(self):
        r = _readings([HEALTHY] * 6, volts=[1.3, 1.7, 1.7, 1.7, 1.3, 1.3])
        self.assertEqual(_trust(r), _day(5))

    def test_later_of_step_and_dead_run(self):
        # the real pepper shape: step to lithium, then the probe dies, then recovers
        r = _readings([HEALTHY, HEALTHY] + [DEAD] * 4 + [HEALTHY],
                      volts=[1.3, 1.7, 1.7, 1.7, 1.7, 1.7, 1.7])
        self.assertEqual(_trust(r), _day(7))


class NothingCarriesBetweenCalls(unittest.TestCase):
    def test_a_clean_record_after_a_broken_one_has_no_boundary(self):
        self.assertEqual(_trust(_readings([HEALTHY] + [DEAD] * 3 + [HEALTHY])), _day(5))
        self.assertIsNone(_trust(_readings([HEALTHY] * 5)))


if __name__ == "__main__":
    unittest.main()
