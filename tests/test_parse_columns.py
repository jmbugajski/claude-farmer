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
    "probes": {"tomato": {"group": "Tomato Probe"},
               "pepper": {"group": "Pepper Probe"}},
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


class ColumnResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name

    def load(self):
        return parse_ecowitt.load_readings(self.dir, CONFIG)

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


if __name__ == "__main__":
    unittest.main()
