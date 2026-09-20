"""
config.json has one record of the tomato schedule and one set of thresholds;
farm_config derives the rest and refuses a config that stores a copy (#15).
Run from the repo root:

    .venv/bin/python -m unittest discover tests
"""

import copy
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "lib"))

import farm_config  # noqa: E402


def _runs(*pairs):
    return [{"time": t, "seconds": s} for t, s in pairs]


# Timer, a duration-only change, then a change of start times and back again.
GOOD = {
    "probes": {
        "_note": "string keys under probes are prose, not probes",
        "tomato": {"voltage_channel": "CH1",
                   "bands": {"stress_floor": 50, "working_lo": 58, "drainage_ceiling": 75},
                   "gauge": {"lo": 45, "hi": 85}},
    },
    "bed": {"liters_per_inch_of_water": 100.0},
    "plan": {
        "measured_rate_lps": 0.25,
        "regimes": [
            {"label": "hand", "start": "2026-07-01", "end": "2026-07-09", "runs": []},
            {"label": "2 x 60", "start": "2026-07-10", "end": "2026-07-19",
             "runs": _runs(("05:05", 60), ("17:05", 60))},
            {"label": "3 x 60", "start": "2026-07-20", "end": "2026-07-29",
             "runs": _runs(("05:05", 60), ("13:05", 60), ("17:05", 60))},
            {"label": "2 x 60 again", "start": "2026-07-30", "end": "2026-08-09",
             "runs": _runs(("05:05", 60), ("17:05", 60))},
            {"label": "2 x 120", "start": "2026-08-10", "end": None,
             "runs": _runs(("05:05", 120), ("17:05", 120))},
        ],
    },
}


def _errors(edit):
    cfg = copy.deepcopy(GOOD)
    edit(cfg)
    return "\n".join(farm_config.validate(cfg))


class Derivation(unittest.TestCase):
    def setUp(self):
        self.plan = farm_config.resolve(copy.deepcopy(GOOD))["plan"]

    def test_current_plan_is_the_last_row(self):
        self.assertEqual(self.plan["runs"], _runs(("05:05", 120), ("17:05", 120)))
        self.assertEqual((self.plan["set"], self.plan["runs_effective"]),
                         ("2026-08-10", "2026-08-10"))
        self.assertEqual((self.plan["run_seconds"], self.plan["run_min"]), (120, 4.0))

    def test_time_effective_survives_a_duration_change_but_not_an_earlier_same_set(self):
        # 08-10 changed seconds only, so times last moved 07-30 -- not 07-10,
        # which held the same set before the 3 x 60 row interrupted it.
        self.assertEqual(self.plan["runs_time_effective"], "2026-07-30")

    def test_projection_scales_from_the_metered_rate(self):
        # 240 s x 0.25 L/s = 60 L/day; x 7 = 420 L/wk; / 100 L per inch = 4.2 in.
        self.assertEqual((self.plan["metered_daily"], self.plan["metered_weekly"],
                          self.plan["expected_in_per_week"]), (60.0, 420.0, 4.2))

    def test_mixed_run_lengths_have_no_single_run_seconds(self):
        cfg = copy.deepcopy(GOOD)
        cfg["plan"]["regimes"][-1]["runs"][1]["seconds"] = 90
        self.assertIsNone(farm_config.resolve(cfg)["plan"]["run_seconds"])

    def test_invalid_config_raises_with_every_problem(self):
        cfg = copy.deepcopy(GOOD)
        cfg["plan"]["runs_effective"] = "2026-08-10"
        cfg["plan"]["measured_rate_lps"] = 0
        with self.assertRaises(farm_config.ConfigError) as ctx:
            farm_config.resolve(cfg)
        self.assertIn("plan.runs_effective", str(ctx.exception))
        self.assertIn("plan.measured_rate_lps", str(ctx.exception))


class Rejections(unittest.TestCase):
    def test_good_config_is_clean(self):
        self.assertEqual(farm_config.validate(copy.deepcopy(GOOD)), [])

    def test_stored_copy_of_a_derived_plan_key(self):
        for key in farm_config.DERIVED_PLAN_KEYS:
            self.assertIn(f"plan.{key}: derived", _errors(lambda c: c["plan"].update({key: 1})))

    def test_stored_copy_of_a_gauge_threshold(self):
        self.assertIn("gauge.floor: derived",
                      _errors(lambda c: c["probes"]["tomato"]["gauge"].update(floor=50)))

    def test_voltage_channel_must_be_a_console_channel(self):
        for bad in (None, "", "CH", "1", "Tomato"):
            self.assertIn("probes.tomato.voltage_channel:",
                          _errors(lambda c: c["probes"]["tomato"].update(voltage_channel=bad)),
                          msg=repr(bad))

    def test_no_regimes(self):
        self.assertIn("at least one row", _errors(lambda c: c["plan"].update(regimes=[])))

    def test_overlap(self):
        self.assertIn("overlaps", _errors(
            lambda c: c["plan"]["regimes"][1].update(start="2026-07-09")))

    def test_gap(self):
        self.assertIn("leaves a gap", _errors(
            lambda c: c["plan"]["regimes"][1].update(start="2026-07-11")))

    def test_out_of_order(self):
        def swap(c):
            r = c["plan"]["regimes"]
            r[1], r[2] = r[2], r[1]
        self.assertIn("overlaps", _errors(swap))

    def test_start_after_end(self):
        self.assertIn("is after end", _errors(
            lambda c: c["plan"]["regimes"][0].update(end="2026-06-30")))

    def test_open_row_before_the_last(self):
        self.assertIn("only the last row may be open", _errors(
            lambda c: c["plan"]["regimes"][2].update(end=None)))

    def test_unparseable_date(self):
        self.assertIn("not YYYY-MM-DD", _errors(
            lambda c: c["plan"]["regimes"][0].update(start="July 1")))

    def test_current_plan_without_runs(self):
        self.assertIn("needs its runs", _errors(
            lambda c: c["plan"]["regimes"][-1].update(runs=[])))

    def test_row_without_a_runs_list(self):
        self.assertIn("`runs` must be a list", _errors(
            lambda c: c["plan"]["regimes"][1].pop("runs")))

    def test_malformed_run(self):
        self.assertIn("not HH:MM", _errors(
            lambda c: c["plan"]["regimes"][-1]["runs"][0].update(time="5:05")))
        self.assertIn("not a positive number", _errors(
            lambda c: c["plan"]["regimes"][-1]["runs"][0].update(seconds=0)))
        self.assertIn("share a start time", _errors(
            lambda c: c["plan"]["regimes"][-1]["runs"][1].update(time="05:05")))

    def test_bands_out_of_order(self):
        self.assertIn("stress_floor < working_lo", _errors(
            lambda c: c["probes"]["tomato"]["bands"].update(stress_floor=60)))

    def test_bands_outside_the_gauge_bar(self):
        self.assertIn("must contain the bands", _errors(
            lambda c: c["probes"]["tomato"]["bands"].update(drainage_ceiling=85)))

    def test_missing_litres_per_inch(self):
        self.assertIn("bed.liters_per_inch_of_water", _errors(lambda c: c.pop("bed")))


class TrackedConfig(unittest.TestCase):
    """The garden's own file loads. Its values move with the garden, so only
    the identities are pinned, not the numbers."""

    def test_loads_and_the_derived_keys_agree_with_the_regimes(self):
        cfg = farm_config.load(os.path.join(ROOT, "config.json"))
        plan = cfg["plan"]
        self.assertEqual(plan["runs"], plan["regimes"][-1]["runs"])
        self.assertEqual(plan["runs_effective"], plan["regimes"][-1]["start"])
        self.assertLessEqual(plan["runs_time_effective"], plan["runs_effective"])


if __name__ == "__main__":
    unittest.main()
