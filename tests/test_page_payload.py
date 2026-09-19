"""
config.json reaches the public page only through analyze's PAGE_* key lists
(#16). Run from the repo root:

    .venv/bin/python -m unittest discover tests
"""

import json
import os
import re
import sys
import unittest
from datetime import datetime, timedelta

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "lib"))

import analyze  # noqa: E402
import farm_config  # noqa: E402
import render  # noqa: E402

SENTINEL = "NOTEBOOK-ONLY-7f3a"
READINGS = [{"dt": datetime(2026, 9, 1) + timedelta(hours=h), "tom": 60.0, "pep": 40.0,
             "water": float(h)} for h in range(24 * 10)]


def _build():
    """The tracked config with a note planted at every level config text has
    reached the page from: a new plan key, a log entry, a band, a regime row."""
    cfg = farm_config.load(os.path.join(ROOT, "config.json"))
    cfg["plan"]["note_to_self"] = SENTINEL
    cfg["plan"]["schedule_log"][0]["private"] = SENTINEL
    cfg["plan"]["regimes"][0]["note"] = SENTINEL
    for probe in ("tomato", "pepper"):
        cfg["probes"][probe]["bands"]["_scratch_comment"] = SENTINEL
    return analyze.build(READINGS, cfg)


class OnlyListedConfigKeysReachThePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.cfg = _build()

    def test_a_note_under_a_new_key_is_not_serialised(self):
        self.assertNotIn(SENTINEL, json.dumps([self.data, self.cfg], default=str))

    def test_plan_is_exactly_the_listed_keys(self):
        self.assertEqual(set(self.cfg["plan"]), set(analyze.PAGE_PLAN_KEYS) | {"schedule_log"})
        self.assertTrue(self.cfg["plan"]["schedule_log"])
        for entry in self.cfg["plan"]["schedule_log"]:
            self.assertLessEqual(set(entry), set(analyze.PAGE_LOG_KEYS))

    def test_every_plan_key_the_template_reads_is_listed(self):
        with open(render.TEMPLATE, encoding="utf-8") as fh:
            html = fh.read()
        read = set(re.findall(r"\b(?:PLAN|CFG\.plan)\.(\w+)", html))
        self.assertEqual(read - {"schedule_log"}, set(analyze.PAGE_PLAN_KEYS))


if __name__ == "__main__":
    unittest.main()
