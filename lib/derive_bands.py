#!/usr/bin/env python3
"""
derive_bands.py
===============
Derive each probe's drainage ceiling (field capacity) and stress floor from the
data itself, with NO soil calibration required.

Why this exists
---------------
Both probes run FACTORY calibration, where 0%AD is the reading in air and 100%AD
is submerged in water. The "%" is therefore an index on that calibration, not
volumetric water content, and it cannot be compared against published
ideal-soil-moisture figures. That is what killed the old `setpoint`: it was a
hand-set app alert threshold on an uncalibrated index, so scoring irrigation
against it measured the operator's guess rather than the soil.

The way out is to stop asking "what number is correct" and instead ask "at what
level does the soil's BEHAVIOUR change". Two behaviours are separable with only
a probe and an ET0 series:

  NIGHT (ET0 = 0).  Nothing is transpiring, so any moisture leaving the soil in
  the dark is draining. Binned by level, night loss is flat and near zero across
  the plant-fed range, then jumps by an order of magnitude at one level. That
  discontinuity is FIELD CAPACITY -- read straight off the probe's own index.
  Above it, applied water leaves whether or not a plant wants it.

  DAY (ET0 > 0).  Moisture lost per mm of ET0 is flat wherever uptake is
  demand-limited (the plant takes what the atmosphere asks for). At the dry end
  the plant starts closing stomata and the curve FALLS OFF. That falloff is the
  STRESS FLOOR.

Both are properties of this soil, this probe and this calibration. Re-run if a
probe is reseated, moved to a new depth, switched to WH51L Custom calibration,
or the media is amended -- and paste the results into config.json probes.*.bands.

    python3 lib/derive_bands.py            # both probes
    python3 lib/derive_bands.py --step 5   # coarser bins

Caveat that matters for reading the output: the floor is only as good as the
time the plant has actually spent dry. A bed that has been over-watered all
season gives a confident ceiling and a weak floor, because there are no
observations down there. n is printed for exactly this reason -- read it.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import os
import statistics
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import events  # noqa: E402
import parse_ecowitt  # noqa: E402
import weather  # noqa: E402

# A bin needs at least this many intervals before its mean is worth printing.
MIN_N = 40
# The FLOOR needs a stricter bar than the ceiling. Dry-end bins are thin by
# construction (a well-watered bed rarely visits them) and the probe reports
# integers, so a 40-sample bin can read a clean 0.00 purely because nothing
# happened to tick over. That artefact looked like total stomatal closure and
# put the pepper floor 3 points too low on the first run.
MIN_N_FLOOR = 100
# Hours per interval must be under this to count as contiguous (native is 5 min,
# but exports have gaps; anything longer spans a hole and its slope is fiction).
MAX_GAP_HR = 0.35
# ET0 below this is "night" for our purposes; above the second, unambiguous day.
NIGHT_ET0 = 0.01
DAY_ET0 = 0.02
# Minutes after an irrigation event's peak during which a falling reading is the
# wetting front moving past a 3-inch probe, not drainage or uptake AT that level.
# No width is "correct" -- report() prints the ceiling at each of SETTLE_SWEEP so
# a threshold that only exists at one width shows itself as one.
POST_EVENT_MIN = 60
SETTLE_SWEEP = (None, 30, 60, 120)


def load_et0(path: str) -> dict:
    """Hourly ET0 keyed by the hour it covers -- see weather.et0_by_hour."""
    return weather.et0_by_hour(weather.load(*os.path.split(path)))


def off_nominal_days(readings, vkey, health):
    """Dates whose mean supply voltage is outside health.nominal_volts +/-
    volt_tolerance -- the same rule analyze._sensor_health warns on.

    The %-scale is only stationary at one excitation voltage, and everything
    below bins by level, so a day on a shifted scale lands in the wrong bin.
    On 2026-09-19 this was pepper 07-13 -> 08-06: a sagging alkaline (1.28-1.34
    V), then the lithium cell (1.70 V) whose flatline put 985 zero-drop
    intervals into the 1433-interval 36-38 day bin -- a dead sensor read as a
    plant that had stopped drinking (#4). The AD-range floor is NOT used: per
    day it also fires on live low-swing days (pepper 09-11 -> 09-18, range 7-9).
    """
    nominal = health.get("nominal_volts", 1.5)
    tol = health.get("volt_tolerance", 0.08)
    by_day = collections.defaultdict(list)
    for r in readings:
        if r.get(vkey) is not None:
            by_day[r["dt"].date()].append(r[vkey])
    return {d for d, vs in by_day.items() if abs(statistics.mean(vs) - nominal) > tol}


def _day_rate(intervals):
    """Day loss for one bin, in %AD per mm ET0: total drop over total demand.

    NOT the mean of per-interval drop/ET0. The probe reports integers, so a
    5-minute drop is 0 or 1, while hourly ET0 runs from 0.02 at dawn to ~0.7 at
    midday: one tick at ET0 = 0.03 scores 400, the same tick at noon scores 20.
    For ticks that track demand the two estimators agree in expectation; for
    ticks that do NOT -- the +/-1 dither -- a mean of ratios weights each by
    1/ET0, so a bin's figure is set by how much of its time fell in low-demand
    hours. That read the flat 2.7-2.9 tomato plateau (50-69%) as 3.5-9.4 and
    put a "falloff" at 55-59 that the totals do not show. Totals still carry
    the dither (as ticks / total ET0); they just stop amplifying it.
    """
    demand = sum(e for _, e in intervals)
    if demand <= 0:
        return None
    return sum(d for d, _ in intervals) / demand


def settle_windows(readings, key, minutes):
    """Merged (start, end) spans from each detected event's onset to its peak +
    `minutes`, sorted. Levels at the wet end are only ever visited right after a
    run, so binning by level alone makes the top bins a measure of time since
    irrigation (#5); curves() drops intervals that start inside a span."""
    spans = []
    for ev in events.detect_events(readings, key):
        onset = datetime.strptime(f"{ev['date']} {ev['onset']}", "%Y-%m-%d %H:%M")
        spans.append((onset, onset + timedelta(minutes=ev["min_to_peak"] + minutes)))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def curves(readings, et, key, step, exclude_days=frozenset(), exclude_spans=()):
    """Bin night loss (%AD/hr) and day (drop, mm ET0) pairs by moisture level.

    Returns (night, day, skipped). `skipped` counts loss intervals whose hour
    has NO ET0 row. They are dropped, not defaulted: ET0 is the only thing
    separating night from day here, so a missing hour read as 0.0 is filed as
    night drainage -- the inversion fixed in analyze._partition on 2026-09-19.
    On 2026-09-19 weather.csv began 07-08 against readings from 06-29, which
    filed the whole flood-dosing period, day and night, as night and moved the
    pepper ceiling from 48 to 45 (#2).

    Intervals starting on a date in `exclude_days` (see off_nominal_days) are
    dropped before anything else and are not counted in `skipped`; so are
    intervals starting inside one of `exclude_spans` (see settle_windows).
    """
    starts = [sp[0] for sp in exclude_spans]
    night = collections.defaultdict(list)
    day = collections.defaultdict(list)
    skipped = 0
    for a, b in zip(readings, readings[1:]):
        hrs = (b["dt"] - a["dt"]).total_seconds() / 3600.0
        if not 0 < hrs <= MAX_GAP_HR:
            continue
        if a["dt"].date() in exclude_days:
            continue
        i = bisect.bisect_right(starts, a["dt"]) - 1
        if i >= 0 and a["dt"] <= exclude_spans[i][1]:
            continue
        if a[key] is None or b[key] is None:
            continue
        if b[key] > a[key]:
            continue  # wetting event, not a loss interval
        e = et.get(a["dt"].strftime("%Y-%m-%d %H"))
        if e is None:
            skipped += 1
            continue
        level = int(a[key] // step) * step
        drop = a[key] - b[key]
        if e <= NIGHT_ET0:
            night[level].append(drop / hrs)
        elif e > DAY_ET0:
            day[level].append((drop, e * hrs))
    return night, day, skipped


def find_ceiling(night, step, jump=3.0, absolute=1.2):
    """Lowest bin whose night loss exceeds `jump`x the median of the bins below.

    Deliberately reports drainage ONSET, not the point where loss goes vertical.
    An operational ceiling has to sit where waste STARTS -- for the tomato bed
    that is the 75-79 bin at 1.66 %AD/hr against a ~0.44 baseline, not the 80-84
    bin at 12.07. A stricter test picks 80, which reads as "you may fill to 80"
    and is exactly the over-application this whole exercise exists to stop.
    """
    levels = sorted(night)
    usable = [lv for lv in levels if len(night[lv]) >= MIN_N // 2]
    if len(usable) < 3:
        return None, None
    for i in range(2, len(usable)):
        lv = usable[i]
        below = [statistics.mean(night[x]) for x in usable[:i]]
        base = statistics.median(below)
        cur = statistics.mean(night[lv])
        if base >= 0 and cur > max(jump * max(base, 0.05), absolute):
            return lv, (cur / max(base, 0.05))
    return None, None


def find_floor(day, step, drop_to=0.65):
    """Highest low-end bin whose day rate falls below `drop_to` x the plateau."""
    levels = sorted(day)
    usable = [lv for lv in levels if len(day[lv]) >= MIN_N_FLOOR]
    if len(usable) < 3:
        return None, None
    rates = {lv: _day_rate(day[lv]) for lv in usable}
    plateau = statistics.median(list(rates.values()))
    for lv in usable:
        if rates[lv] < drop_to * plateau:
            return lv + step, plateau  # top of the falloff bin
    return None, plateau


def report(label, night, day, step, skipped=0, excluded=(), full=None, sweep=(), n_spans=0):
    """`full` is the (night, day) pair binned WITHOUT exclusions; when given, each
    n prints as kept/all, so a bin that was mostly untrusted data stays visible
    as one. `sweep` is [(minutes | None, night)] for the width-sensitivity line."""
    print(f"\n=== {label} ===")
    if excluded:
        print(f"  {len(excluded)} days excluded -- supply voltage off nominal, %-scale shifted "
              f"({min(excluded)} -> {max(excluded)})")
    if n_spans:
        print(f"  {n_spans} irrigation spans excluded -- onset -> peak + {POST_EVENT_MIN} min")
    if skipped:
        used = sum(len(v) for v in night.values()) + sum(len(v) for v in day.values())
        print(f"  {skipped} loss intervals skipped -- no ET0 row for their hour "
              f"({skipped / (skipped + used):.0%} of {skipped + used})")
    nw = 11 if full else 6
    print(f"{'level':>11} {'n':>{nw}} {'night %AD/hr':>13} {'n':>{nw}} {'day %AD/mm ET0':>15}")
    for lv in sorted(set(night) | set(day)):
        nv, dv = night.get(lv, []), day.get(lv, [])
        if len(nv) < MIN_N and len(dv) < MIN_N:
            continue
        ns = f"{statistics.mean(nv):+.3f}" if len(nv) >= MIN_N else "--"
        ds = f"{_day_rate(dv):.2f}" if len(dv) >= MIN_N else "--"
        nn, dn = str(len(nv)), str(len(dv))
        if full:
            nn += f"/{len(full[0].get(lv, []))}"
            dn += f"/{len(full[1].get(lv, []))}"
        print(f"{lv:>7}-{lv + step - 1:<3} {nn:>{nw}} {ns:>13} {dn:>{nw}} {ds:>15}")

    ceiling, ratio = find_ceiling(night, step)
    floor, plateau = find_floor(day, step)
    print()
    if ceiling is not None:
        n_above = sum(len(night[lv]) for lv in night if lv >= ceiling)
        print(f"  drainage_ceiling : {ceiling}%  ({ratio:.0f}x step, n={n_above} night intervals above)")
    else:
        print("  drainage_ceiling : NOT FOUND — no clear night-loss discontinuity")
    if sweep:
        cells = []
        for minutes, nt in sweep:
            c, r = find_ceiling(nt, step)
            cells.append(f"{'none' if minutes is None else minutes}: "
                         + (f"{c} ({r:.0f}x)" if c is not None else "--"))
        print(f"    by post-event exclusion, min : {'   '.join(cells)}"
              f"   <- trust a ceiling only if it holds across widths")
    if floor is not None:
        n_at = sum(len(day[lv]) for lv in day if lv < floor)
        conf = "LOW — too little time spent this dry" if n_at < 200 else "usable"
        print(f"  stress_floor     : {floor}%  (plateau {plateau:.2f} %AD/mm ET0, n={n_at} below; {conf})")
    else:
        print("  stress_floor     : NOT FOUND — no uptake falloff in the observed range")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inputs", default=os.path.join(ROOT, "inputs"))
    ap.add_argument("--weather", default=os.path.join(ROOT, "inputs", "weather.csv"))
    ap.add_argument("--step", type=int, default=0, help="bin width; default per-probe hist_bin x2")
    args = ap.parse_args()

    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    readings = parse_ecowitt.load_readings(args.inputs, cfg)
    if not readings:
        print("No readings found — add EcoWitt .xlsx exports to inputs/.", file=sys.stderr)
        return 1
    et = load_et0(args.weather)
    if not et:
        print("No weather.csv — run ./pull_weather_data.sh first.", file=sys.stderr)
        return 1

    print(f"{len(readings)} readings  {readings[0]['dt']:%Y-%m-%d} → {readings[-1]['dt']:%Y-%m-%d}")
    print(f"{len(et)} hourly ET0 rows  {min(et)[:10]} → {max(et)[:10]}")
    any_skipped = False
    for key, vkey, pname, default_step in (("tom", "v_tom", "tomato", 5),
                                           ("pep", "v_pep", "pepper", 3)):
        step = args.step or default_step
        gauge = cfg["probes"][pname]["gauge"]
        excluded = off_nominal_days(readings, vkey, cfg.get("health", {}))
        full = curves(readings, et, key, step)[:2]
        sweep = [(m, curves(readings, et, key, step, excluded,
                            settle_windows(readings, key, m) if m is not None else ())[0])
                 for m in SETTLE_SWEEP]
        spans = settle_windows(readings, key, POST_EVENT_MIN)
        night, day, skipped = curves(readings, et, key, step, excluded, spans)
        report(f"{gauge['name']} — {gauge['loc']}", night, day, step, skipped, excluded, full, sweep,
               len(spans))
        any_skipped = any_skipped or bool(skipped)
    if any_skipped:
        print(f"\nWARNING: weather.csv covers {min(et)[:10]} → {max(et)[:10]} but readings cover "
              f"{readings[0]['dt']:%Y-%m-%d} → {readings[-1]['dt']:%Y-%m-%d}. Intervals without an "
              "ET0 row were skipped, so the bands above describe the covered span only.",
              file=sys.stderr)
    print("\nPaste results into config.json probes.*.bands, and note n before trusting the floor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
