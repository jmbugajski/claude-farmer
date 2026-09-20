"""
A non-result from _partition says WHICH kind it is (#20). "No weather cached"
and "cached but not splittable" were both bare None, and the weather card read
the second as the first: with a complete cache it told the reader to run
./pull_weather_data.sh, which could not fix it. Run from the repo root:

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import analyze  # noqa: E402

BANDS = {"drainage_ceiling": 75, "stress_floor": 50, "working_lo": 58,
         "refill_target": 70, "verified": True}
PCFG = {"gauge": {"name": "Tomatoes", "loc": "bed", "lo": 30, "hi": 90}, "bands": BANDS}
START = datetime(2026, 9, 1)

# Ten days of 5-minute readings drying 70 -> 60, and an hourly ET0 series
# covering them: demand by day, zero overnight. Enough for a real split, so a
# withheld result here is a decision and not a shortage of data.
READINGS = [{"dt": START + timedelta(minutes=5 * i),
             "tom": 70.0 - (i * 5 / 1440) * 1.0, "pep": 40.0}
            for i in range(12 * 24 * 10)]
WX = [{"dt": START + timedelta(hours=h),
       "et0": 0.25 if 7 <= (START + timedelta(hours=h)).hour <= 19 else 0.0}
      for h in range(24 * 10)]


class ANonResultNamesItsKind(unittest.TestCase):
    def test_no_weather_series_at_all_is_none(self):
        """The one case that means "no cache", and the only one the page may
        answer with "run ./pull_weather_data.sh"."""
        self.assertIsNone(analyze._partition(READINGS, [], "tom", BANDS))
        self.assertIsNone(analyze._partition(READINGS, None, "tom", BANDS))

    def test_a_cached_series_with_no_figures_carries_a_reason(self):
        """Survives the withhold being lifted: it asserts the two states are
        distinguishable, not which one today's code is in."""
        res = analyze._partition(READINGS, WX, "tom", BANDS)
        self.assertIsNotNone(res, "a cached series must not report as an absent one")
        if res.get("pct_uptake") is None:
            self.assertTrue(res.get("withheld"),
                            "a withheld split must name why it is withheld")

    def test_the_reason_is_a_code_not_a_sentence(self):
        """Consumers word the code themselves, as they do for
        _regime_split's bands_unverified -- so it must stay a bare token."""
        res = analyze._partition(READINGS, WX, "tom", BANDS)
        why = res.get("withheld")
        if why:
            self.assertRegex(why, r"^[a-z0-9_]+$")


class ConsumersGateOnTheFigureNotTheDict(unittest.TestCase):
    WITHHELD = {"withheld": "estimator_unreliable"}

    def test_gauge_note_omits_the_partition_sentence(self):
        stats = {"last": 66.0, "min": 40.0, "max": 85.0}
        cycle = {"peak": 70, "trough": 60, "n_days": 7}
        g = analyze._gauge_for("tom", PCFG, stats, cycle, None, self.WITHHELD)
        self.assertNotIn("moisture loss", g["note"])

    def test_advice_neither_quotes_nor_scopes_off_it(self):
        stats = {"last": 66.0, "min": 40.0, "max": 85.0}
        cycle = {"peak": 70, "trough": 60, "n_days": 7}
        data = {"stats": {"tom": stats, "pep": stats}, "water": None,
                "partition": {"tom": self.WITHHELD, "pep": self.WITHHELD}}
        adv = analyze._advice(data, {"probes": {"tomato": PCFG, "pepper": PCFG}, "plan": {}},
                              {"tom": cycle, "pep": cycle})
        self.assertNotIn("moisture loss", adv["tom"])
        self.assertNotIn("withheld", adv["tom"])


if __name__ == "__main__":
    unittest.main()
