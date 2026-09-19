"""
onset_check() scores the SCHEDULE, not the detections (#10): every scheduled slot
gets a row, so a run that did not fire is visible. Run from the repo root:

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import events  # noqa: E402

D0 = datetime(2026, 9, 1)


def _trace(days, bumps=(), skip=(), ad_swing=40):
    """
    5-minute pepper readings at 40 % for `days` days from 2026-09-01. `bumps` maps
    a datetime to the step the trace takes there and holds; `skip` is a list of
    (start, end) spans with no samples. `ad_swing` is the daily AD range.
    """
    out, level = [], 40.0
    bumps = dict(bumps)
    for i in range(days * 288):
        dt = D0 + timedelta(minutes=5 * i)
        level += bumps.get(dt, 0)
        if any(a <= dt < b for a, b in skip):
            continue
        out.append({"dt": dt, "pep": level, "pep_ad": 200 + (ad_swing if dt.hour == 12 else 0)})
    return out


def _ev(dt, retained=5.0, manual=False):
    return {"date": dt.strftime("%Y-%m-%d"), "onset": dt.strftime("%H:%M"),
            "onset_min": dt.hour * 60 + dt.minute, "retained": retained, "manual": manual}


def _at(day, hh, mm):
    return D0 + timedelta(days=day - 1, hours=hh, minutes=mm)


def _kinds(rows):
    return [(r["date"][-2:], r["kind"]) for r in rows]


class EverySlotGetsARow(unittest.TestCase):
    def test_flat_trace_with_no_event_is_missed(self):
        rows = events.onset_check([_ev(_at(1, 6, 45)), _ev(_at(3, 6, 45))], ["06:45"],
                                  _trace(3), "pep", ad_floor=12)
        self.assertEqual(_kinds(rows), [("01", "on_time"), ("02", "missed"), ("03", "on_time")])

    def test_export_gap_over_the_slot_is_no_data_not_missed(self):
        rd = _trace(3, skip=[(_at(2, 0, 0), _at(2, 8, 40))])
        rows = events.onset_check([_ev(_at(1, 6, 45)), _ev(_at(3, 6, 45))], ["06:45"],
                                  rd, "pep", ad_floor=12)
        self.assertEqual(_kinds(rows)[1], ("02", "no_data"))

    def test_slot_after_the_last_reading_has_no_row(self):
        rd = [r for r in _trace(2) if r["dt"] <= _at(2, 7, 0)]   # export pulled 07:00
        rows = events.onset_check([_ev(_at(1, 6, 45))], ["06:45"], rd, "pep", ad_floor=12)
        self.assertEqual(_kinds(rows), [("01", "on_time")])

    def test_raw_rise_without_an_event_is_undetected_not_missed(self):
        rd = _trace(2, bumps={_at(2, 7, 0): 1, _at(2, 7, 40): 1})
        rows = events.onset_check([_ev(_at(1, 6, 45))], ["06:45"], rd, "pep", ad_floor=12)
        self.assertEqual((rows[1]["kind"], rows[1]["rise"]), ("undetected", 2.0))

    def test_flat_trace_on_a_dead_probe_is_no_data(self):
        rows = events.onset_check([], ["06:45"], _trace(3, ad_swing=5), "pep", ad_floor=12)
        self.assertEqual({r["kind"] for r in rows}, {"no_data"})

    def test_hand_application_inside_the_window_masks_the_slot(self):
        evs = [_ev(_at(1, 6, 45)), _ev(_at(2, 7, 0), manual=True)]
        rows = events.onset_check(evs, ["06:45"], _trace(2), "pep", ad_floor=12)
        self.assertEqual(_kinds(rows), [("01", "on_time"), ("02", "no_data")])


class ShortIsJudgedOnRetainedAlone(unittest.TestCase):
    def test_on_time_run_with_a_third_of_the_gain_is_short(self):
        evs = [_ev(_at(d, 6, 45)) for d in (1, 2, 3)] + [_ev(_at(4, 6, 45), retained=1.5)]
        rows = events.onset_check(evs, ["06:45"], _trace(4), "pep")
        self.assertEqual(_kinds(rows)[-1], ("04", "short"))
        self.assertFalse(rows[-1]["late"])

    def test_late_full_size_run_is_still_displaced(self):
        evs = [_ev(_at(d, 6, 45)) for d in (1, 2)] + [_ev(_at(3, 7, 25))]
        rows = events.onset_check(evs, ["06:45"], _trace(3), "pep")
        self.assertEqual((rows[-1]["kind"], rows[-1]["delta_min"]), ("displaced", 40))


class OneEventPerSlot(unittest.TestCase):
    def test_second_event_at_a_slot_is_extra(self):
        evs = [_ev(_at(1, 6, 50)), _ev(_at(1, 7, 40))]
        rows = events.onset_check(evs, ["06:45"], _trace(1), "pep")
        self.assertEqual([(r["onset"], r["kind"]) for r in rows],
                         [("06:50", "on_time"), ("07:40", "extra")])

    def test_event_before_midnight_joins_the_next_days_slot(self):
        rows = events.onset_check([_ev(_at(1, 23, 55))], ["00:05"], _trace(2), "pep",
                                  since="2026-09-01")
        hit = [r for r in rows if r["onset"] == "23:55"][0]
        self.assertEqual((hit["scheduled"], hit["delta_min"], hit["kind"]),
                         ("00:05", -10, "on_time"))


if __name__ == "__main__":
    unittest.main()
