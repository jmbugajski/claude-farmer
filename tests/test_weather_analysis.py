"""
The panel-2 "set by the pulse, not the weather" sentence is chosen by the
within-regime ET0-vs-swing correlation, not by the pooled one (#13). Run from
the repo root:

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "lib"))

import analyze  # noqa: E402

# A fixed +-1 pattern with zero mean over each 12-day block. `WOBBLE` moves ET0;
# `OTHER` is orthogonal to it over 12 days, so a swing built on OTHER has a
# within-regime r of exactly 0 against an ET0 built on WOBBLE.
WOBBLE = [1, -1, 1, -1, 1, -1, 1, -1, 1, -1, 1, -1]
OTHER = [1, 1, -1, -1, 1, 1, -1, -1, 1, 1, -1, -1]
REGIMES = [{"label": "long run", "start": "2026-07-01", "end": "2026-07-12"},
           {"label": "pulsed", "start": "2026-07-13", "end": None}]


def _inputs(swing_follows_et0):
    """24 days, two regimes. Regime 1: hot (ET0 ~6) with a 30-point swing;
    regime 2: cool (ET0 ~4) with a 6-point swing -- so the pooled r is high
    either way. Inside a regime the swing wobbles with ET0, or with OTHER."""
    soil, wx = [], []
    for i in range(24):
        first = i < 12
        k = i % 12
        et0 = (6.0 if first else 4.0) + 0.5 * WOBBLE[k]
        swing = (30.0 if first else 6.0) + 2.0 * (WOBBLE if swing_follows_et0 else OTHER)[k]
        d = (datetime(2026, 7, 1) + timedelta(days=i)).strftime("%Y-%m-%d")
        soil.append({"date": d, "tom_mean": 60.0, "pep_mean": 40.0,
                     "tom_min": 50.0, "tom_max": 50.0 + swing,
                     "pep_min": 38.0, "pep_max": 42.0})
        wx.append({"date": d, "et0_mm": et0, "tmax_f": 80.0, "et0_source": "fao56"})
    return soil, wx


class SwingDriver(unittest.TestCase):
    def test_pooled_r_high_within_r_zero_reads_as_pulse(self):
        w = analyze._weather_analysis(*_inputs(False), REGIMES)
        self.assertGreater(w["corr"]["tom_draw_vs_et0"], 0.5)
        self.assertEqual(w["corr"]["tom_draw_vs_et0_within"], 0.0)
        self.assertEqual(w["swing_driver"], "pulse")

    def test_swing_following_et0_inside_each_regime_reads_as_weather(self):
        w = analyze._weather_analysis(*_inputs(True), REGIMES)
        self.assertEqual(w["corr"]["tom_draw_vs_et0_within"], 1.0)
        self.assertEqual(w["swing_driver"], "weather")

    def test_no_regimes_means_no_conclusion(self):
        w = analyze._weather_analysis(*_inputs(False), None)
        self.assertIsNone(w["swing_driver"])

    def test_nothing_unread_is_published(self):
        w = analyze._weather_analysis(*_inputs(False), REGIMES)
        self.assertEqual(set(w), {"swing_driver", "corr", "et0_source", "span"})


if __name__ == "__main__":
    unittest.main()
