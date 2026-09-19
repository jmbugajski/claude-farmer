"""
detect_events() on synthetic traces: the cases #17 found by inspection, none of
which the 06-29 -> 09-19 record exercises except the first-pair gap. Run from
the repo root:

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import events  # noqa: E402

D0 = datetime(2026, 9, 1)


def _trace(hours, bumps, skip=(), start=D0, level=40.0):
    """
    5-minute tomato readings from `start`. `bumps` maps minutes-from-start to the
    step the trace takes there and holds; `skip` is (start, end) minute spans
    with no samples.
    """
    out = []
    for m in range(0, hours * 60, 5):
        level += bumps.get(m, 0)
        if any(a <= m < b for a, b in skip):
            continue
        out.append({"dt": start + timedelta(minutes=m), "tom": level})
    return out


class Ascents(unittest.TestCase):
    def test_ramp_longer_than_two_hours_is_detected(self):
        # +1 every 25 min for 125 min: never stalls, and used to exceed the cap.
        evs = events.detect_events(_trace(6, {60 + 25 * j: 1 for j in range(6)}), "tom")
        self.assertEqual([(e["onset"], e["pre_floor"], e["peak"]) for e in evs],
                         [("01:00", 40.0, 46.0)])

    def test_gap_before_the_climb_does_not_discard_it(self):
        # 2026-08-15 pepper: dropout, data resumes mid-climb, climb continues.
        evs = events.detect_events(
            _trace(6, {100: 9, 125: 7, 130: 1}, skip=[(60, 120)]), "tom")
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0]["onset"], evs[0]["peak"]), ("02:05", 57.0))
        # 49 is where the data resumed, not what the soil held before the run
        self.assertEqual(evs[0]["pre_floor"], 49.0)
        self.assertIsNone(evs[0]["retained"])
        self.assertEqual(evs[0]["shed"], 0.0)

    def test_flat_gap_before_the_climb_keeps_retained(self):
        evs = events.detect_events(_trace(6, {125: 7}, skip=[(60, 120)]), "tom")
        self.assertEqual([(e["onset"], e["retained"]) for e in evs], [("02:05", 7.0)])


class Neighbours(unittest.TestCase):
    RAMP = {60 + 22 * j - (60 + 22 * j) % 5: 1 for j in range(6)}   # +6, 01:00 -> 02:50

    def test_rise_just_after_a_long_ramp_joins_it(self):
        top = max(self.RAMP)
        # a dip ends the ramp's ascent, so the next rise is grouped, not walked
        evs = events.detect_events(
            _trace(8, {**self.RAMP, top + 5: -1, top + 10: 5}), "tom")
        self.assertEqual([(e["onset"], e["peak"]) for e in evs], [("01:00", 50.0)])
        self.assertEqual(evs[0]["retained"], 10.0)

    def test_next_event_is_not_read_as_this_ones_peak_or_settled(self):
        # One-step rise at 01:00: its peak window runs to 02:00 and the next
        # run lands at 01:55, 50 minutes after the top.
        evs = events.detect_events(_trace(6, {60: 8, 65: -3, 115: 9}), "tom")
        self.assertEqual([e["onset"] for e in evs], ["01:00", "01:55"])
        self.assertEqual((evs[0]["peak"], evs[0]["settled"], evs[0]["retained"], evs[0]["shed"]),
                         (48.0, 45.0, 5.0, 3.0))


if __name__ == "__main__":
    unittest.main()
