"""
Column resolution in parse_ecowitt (#18): a header the console has renamed must
fail loudly rather than blank a channel, and must not be captured by a
look-alike group. Run from the repo root:

    .venv/bin/python -m unittest discover tests

These build throwaway .xlsx exports in a temp dir. They never read inputs/ --
that data is gitignored and grows weekly.
"""

import os
import sys
import tempfile
import unittest

import openpyxl

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import parse_ecowitt  # noqa: E402

CONFIG = {
    "columns": {"time_header": "Time", "soil_moisture_sub": "Soil Moisture(%)",
                "water_total_sub": "Water Total(L)"},
    "probes": {"tomato": {"group": "Tomato Probe", "voltage_channel": "CH1"},
               "pepper": {"group": "Pepper Probe", "voltage_channel": "CH2"}},
    "water": {"group_prefix": "WFC01"},
}

# One EcoWitt export's header pair, in the current firmware's wording.
GROUPS = ["Time", "Tomato Probe", None, "Pepper Probe", None, "Battery", None,
          "[WFC01] Water Flow"]
SUBS = [None, "Soil Moisture(%)", "AD", "Soil Moisture(%)", "AD",
        "[CH1] Tomato Soil Sensor(V)", "[CH2] Pepper Soil Sensor(V)",
        "Water Total(L)"]
ROW = ["2026-09-19 00:00", 60, 500, 40, 400, 1.7, 1.7, 100.0]


def _write(dirpath, name, groups, subs, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = parse_ecowitt.DATA_SHEET
    for r in (groups, subs, *rows):
        ws.append(list(r))
    wb.save(os.path.join(dirpath, name))


class _Exports(unittest.TestCase):
    """A temp dir of synthetic exports, and the loader pointed at it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def load(self):
        return parse_ecowitt.load_readings(self.dir, CONFIG)


class ColumnResolution(_Exports):
    def test_every_channel_resolves_in_a_current_export(self):
        _write(self.dir, "a.xlsx", GROUPS, SUBS, [ROW])
        readings, rep = self.load()
        self.assertEqual(rep["unresolved"], {})
        self.assertEqual(rep["files"], 1)
        self.assertEqual(readings[0]["tom"], 60.0)
        self.assertEqual(readings[0]["water"], 100.0)
        self.assertEqual(readings[0]["v_pep"], 1.7)

    def test_rename_in_the_newest_export_raises(self):
        renamed = ["Time", "Tomatoes", None, "Pepper Probe", None, "Battery", None,
                   "[WFC01] Water Flow"]
        _write(self.dir, "a.xlsx", GROUPS, SUBS, [ROW])
        _write(self.dir, "b.xlsx", renamed, SUBS, [ROW])
        with self.assertRaises(parse_ecowitt.ParseError) as ctx:
            self.load()
        self.assertIn("tomato moisture", str(ctx.exception))
        self.assertIn("b.xlsx", str(ctx.exception))

    def test_rename_in_an_older_export_is_reported_not_raised(self):
        renamed = ["Time", "Tomatoes", None, "Pepper Probe", None, "Battery", None,
                   "[WFC01] Water Flow"]
        _write(self.dir, "a.xlsx", renamed, SUBS, [ROW])
        _write(self.dir, "b.xlsx", GROUPS, SUBS,
               [["2026-09-20 00:00", 61, 500, 40, 400, 1.7, 1.7, 101.0]])
        readings, rep = self.load()
        self.assertEqual(rep["unresolved"]["tom"], ["a.xlsx"])
        self.assertIsNone(readings[0]["tom"])
        self.assertEqual(rep["missing"]["tom"], 50.0)
        self.assertIn("tomato moisture: no column in a.xlsx",
                      parse_ecowitt.summary_lines(rep))

    def test_a_missing_diagnostic_channel_never_raises(self):
        no_volts = list(SUBS)
        no_volts[5] = no_volts[6] = None
        _write(self.dir, "a.xlsx", GROUPS, no_volts, [ROW])
        _, rep = self.load()
        self.assertEqual(sorted(rep["unresolved"]), ["v_pep", "v_tom"])
        self.assertIn("tomato voltage: no column in a.xlsx (diagnostic)",
                      parse_ecowitt.summary_lines(rep))

    def test_files_with_no_data_are_counted_not_swallowed(self):
        _write(self.dir, "a.xlsx", GROUPS, SUBS, [])
        _write(self.dir, "b.xlsx", GROUPS, SUBS, [["not a timestamp", 60, 500, 40, 400, 1.7, 1.7, 1.0]])
        _write(self.dir, "c.xlsx", GROUPS, SUBS, [ROW])
        readings, rep = self.load()
        self.assertEqual(len(readings), 1)
        self.assertEqual(rep["files"], 1)
        self.assertEqual([n for n, _ in rep["skipped"]], ["a.xlsx", "b.xlsx"])
        self.assertIn("skipped b.xlsx: 1 data row(s), none timestamped",
                      parse_ecowitt.summary_lines(rep))


class VoltageChannels(_Exports):
    """The channel tag is a whole token, so CH1 is not CH10 (#18)."""

    def test_ch1_does_not_match_ch10(self):
        subs = list(SUBS)
        subs[5] = "[CH10] Deep Soil Sensor(V)"
        _write(self.dir, "a.xlsx", GROUPS, subs, [ROW])
        readings, rep = self.load()
        self.assertIsNone(readings[0]["v_tom"])
        self.assertEqual(rep["unresolved"]["v_tom"], ["a.xlsx"])
        self.assertEqual(readings[0]["v_pep"], 1.7)

    def test_both_firmware_spellings_of_the_tag_resolve(self):
        for header in ("Soil Moisture Sensor CH1(V)", "[CH1] Tomato Soil Sensor(V)"):
            with self.subTest(header=header):
                self.setUp()
                subs = list(SUBS)
                subs[5] = header
                _write(self.dir, "a.xlsx", GROUPS, subs, [ROW])
                readings, _ = self.load()
                self.assertEqual(readings[0]["v_tom"], 1.7)

    def test_the_channel_comes_from_config(self):
        cfg = {**CONFIG, "probes": {"tomato": {"group": "Tomato Probe",
                                               "voltage_channel": "CH2"},
                                    "pepper": {"group": "Pepper Probe",
                                               "voltage_channel": "CH1"}}}
        _write(self.dir, "a.xlsx", GROUPS, SUBS,
               [["2026-09-19 00:00", 60, 500, 40, 400, 1.55, 1.75, 100.0]])
        readings, _ = parse_ecowitt.load_readings(self.dir, cfg)
        self.assertEqual(readings[0]["v_tom"], 1.75)
        self.assertEqual(readings[0]["v_pep"], 1.55)


class GroupMatching(_Exports):
    """Exact group names beat look-alikes; renames are matched but reported."""

    def test_a_look_alike_group_never_captures_the_column(self):
        for order in ("second", "first"):
            with self.subTest(look_alike=order):
                self.setUp()
                groups = ["Time", "Tomato Probe", None, "Tomato Probe 2", None,
                          "Pepper Probe", None, "[WFC01] Water Flow"]
                subs = [None, "Soil Moisture(%)", "AD", "Soil Moisture(%)", "AD",
                        "Soil Moisture(%)", "AD", "Water Total(L)"]
                row = ["2026-09-19 00:00", 60, 500, 99, 999, 40, 400, 100.0]
                if order == "first":
                    # The look-alike sorts ahead of the real group in the export.
                    groups[1], groups[3] = groups[3], groups[1]
                    row[1], row[3] = row[3], row[1]
                _write(self.dir, "a.xlsx", groups, subs, [row])
                readings, rep = self.load()
                self.assertEqual(readings[0]["tom"], 60.0)
                self.assertEqual(rep["notes"], [])

    def test_a_renamed_probe_group_is_matched_and_reported(self):
        groups = ["Time", "[CH1] Tomato Probe", None, "Pepper Probe", None,
                  "Battery", None, "[WFC01] Water Flow"]
        _write(self.dir, "a.xlsx", groups, SUBS, [ROW])
        readings, rep = self.load()
        self.assertEqual(readings[0]["tom"], 60.0)
        # The rename moves the probe's AD column with it.
        self.assertEqual(rep["notes"],
                         [("a.xlsx", ch, "no exact 'Tomato Probe'; "
                                         "matched '[CH1] Tomato Probe'")
                          for ch in ("tom", "tom_ad")])
        self.assertIn("tomato moisture: no exact 'Tomato Probe'; "
                      "matched '[CH1] Tomato Probe' -- a.xlsx",
                      parse_ecowitt.summary_lines(rep))

    def test_the_water_group_prefix_is_matched_without_a_note(self):
        # water.group_prefix is 'WFC01'; no export header ever equals it, and
        # both firmware spellings are in inputs/.
        for header in ("WFC01-00003D29", "[WFC01] Water Flow"):
            with self.subTest(header=header):
                self.setUp()
                groups = list(GROUPS)
                groups[7] = header
                _write(self.dir, "a.xlsx", groups, SUBS, [ROW])
                readings, rep = self.load()
                self.assertEqual(readings[0]["water"], 100.0)
                self.assertEqual(rep["notes"], [])

    def test_two_identical_group_names_are_reported_as_ambiguous(self):
        groups = ["Time", "Tomato Probe", None, "Tomato Probe", None,
                  "Pepper Probe", None, "[WFC01] Water Flow"]
        subs = [None, "Soil Moisture(%)", "AD", "Soil Moisture(%)", "AD",
                "Soil Moisture(%)", "AD", "Water Total(L)"]
        _write(self.dir, "a.xlsx", groups, subs,
               [["2026-09-19 00:00", 60, 500, 99, 999, 40, 400, 100.0]])
        _, rep = self.load()
        self.assertIn("2 columns are 'Tomato Probe'/'Soil Moisture(%)'",
                      rep["notes"][0][2])


if __name__ == "__main__":
    unittest.main()
