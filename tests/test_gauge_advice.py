"""
The gauge verdict and the advice line read one classifier (#14). Run from the
repo root:

    .venv/bin/python -m unittest discover tests
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import analyze  # noqa: E402

BANDS = {"drainage_ceiling": 75, "stress_floor": 50, "working_lo": 58,
         "refill_target": 70, "verified": True}
PCFG = {"gauge": {"name": "Tomatoes", "loc": "bed", "lo": 30, "hi": 90}, "bands": BANDS}
CONFIG = {"probes": {"tomato": PCFG, "pepper": PCFG}, "plan": {}}

# state -> the instruction the advice must carry for it
ACTION = {"DRAINING": "Shorten the run", "STRESS RISK": "add water now",
          "DRYING": "heading for the 50% floor", "WORKING": "Hold the schedule",
          None: "No complete day"}


def _cycle(peak, trough):
    return {"peak": peak, "trough": trough, "n_days": 7}


def _render(cycle, last=66.0):
    """Gauge verdict and advice line for one probe, as build() wires them."""
    stats = {"last": last, "min": 40.0, "max": 85.0}
    gauge = analyze._gauge_for("tom", PCFG, stats, cycle)
    data = {"stats": {"tom": stats, "pep": stats}, "water": None}
    advice = analyze._advice(data, CONFIG, {"tom": cycle, "pep": cycle})
    return gauge, advice["tom"]


class ClassifierOrderAndStrictness(unittest.TestCase):
    def test_peak_on_the_ceiling_is_not_draining(self):
        self.assertEqual(analyze._classify(_cycle(75, 60), BANDS), "WORKING")
        self.assertEqual(analyze._classify(_cycle(76, 60), BANDS), "DRAINING")

    def test_drainage_wins_over_a_trough_below_the_floor(self):
        self.assertEqual(analyze._classify(_cycle(80, 45), BANDS), "DRAINING")

    def test_trough_states(self):
        self.assertEqual(analyze._classify(_cycle(70, 49), BANDS), "STRESS RISK")
        self.assertEqual(analyze._classify(_cycle(70, 50), BANDS), "DRYING")
        self.assertEqual(analyze._classify(_cycle(70, 58), BANDS), "WORKING")

    def test_no_complete_day_is_no_state(self):
        self.assertIsNone(analyze._classify(_cycle(None, None), BANDS))


class GaugeAndAdviceNameOneState(unittest.TestCase):
    def test_pulsed_bed_with_a_mean_inside_the_band_reads_draining(self):
        # The case the mean could not reach: peak past the ceiling, 24 h mean 66.
        gauge, advice = _render(_cycle(82, 60), last=66.0)
        self.assertTrue(gauge["verdict"].startswith("DRAINING"), gauge["verdict"])
        self.assertIn("Shorten the run", advice)

    def test_every_state_carries_its_own_action(self):
        # `last` is held inside the working band throughout, so a verdict that
        # still read the mean would say WORKING on every row.
        for cycle in (_cycle(82, 60), _cycle(70, 45), _cycle(70, 54),
                      _cycle(70, 62), _cycle(None, None)):
            state = analyze._classify(cycle, BANDS)
            gauge, advice = _render(cycle)
            with self.subTest(state=state):
                self.assertTrue(gauge["verdict"].startswith(state or "NO CYCLE YET"),
                                gauge["verdict"])
                self.assertIn(ACTION[state], advice)
                for other, phrase in ACTION.items():
                    if other != state:
                        self.assertNotIn(phrase, advice)

    def test_gauge_tile_carries_the_classified_peak_and_trough(self):
        gauge, _ = _render(_cycle(82, 60))
        self.assertEqual((gauge["peak"], gauge["trough"]), (82, 60))


class EmptyChannelStillBuilds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        with open(os.path.join(root, "config.json"), encoding="utf-8") as f:
            cls.config = json.load(f)

    def test_probe_offline_for_the_whole_window_reads_no_reading(self):
        t0 = datetime(2026, 9, 10)
        readings = [{"dt": t0 + timedelta(minutes=5 * i), "tom": 60.0, "pep": None,
                     "water": None} for i in range(288 * 3)]
        data, cfg = analyze.build(readings, self.config)
        self.assertEqual(cfg["gauge"]["pep"]["verdict"], "NO READING")
        self.assertIsNone(data["stats"]["pep"]["min"])
        self.assertIn("check the sensor", cfg["advice"]["pep"])

    def test_no_reading_tile_does_not_draw_unverified_bands(self):
        pcfg = dict(PCFG, bands=dict(BANDS, verified=False))
        gauge = analyze._gauge_for("pep", pcfg, {"last": None}, _cycle(None, None))
        self.assertEqual(gauge["verdict"], "NO READING")
        self.assertIs(gauge["verified"], False)

    def test_no_readings_is_a_named_error(self):
        with self.assertRaisesRegex(ValueError, "no readings"):
            analyze.build([], self.config)


if __name__ == "__main__":
    unittest.main()
