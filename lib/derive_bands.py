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
import collections
import csv
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import parse_ecowitt  # noqa: E402

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


def load_et0(path: str) -> dict:
    """Hourly ET0 keyed 'YYYY-MM-DD HH'."""
    et = {}
    if not os.path.exists(path):
        return et
    with open(path) as fh:
        for row in csv.DictReader(fh):
            try:
                et[row["datetime"][:13]] = float(row["et0_fao_evapotranspiration"])
            except (KeyError, TypeError, ValueError):
                continue
    return et


def curves(readings, et, key, step):
    """Bin night loss (%AD/hr) and day loss (%AD per mm ET0) by moisture level."""
    night = collections.defaultdict(list)
    day = collections.defaultdict(list)
    for a, b in zip(readings, readings[1:]):
        hrs = (b["dt"] - a["dt"]).total_seconds() / 3600.0
        if not 0 < hrs <= MAX_GAP_HR:
            continue
        if a[key] is None or b[key] is None:
            continue
        if b[key] > a[key]:
            continue  # wetting event, not a loss interval
        e = et.get(a["dt"].strftime("%Y-%m-%d %H"), 0.0)
        level = int(a[key] // step) * step
        drop = a[key] - b[key]
        if e <= NIGHT_ET0:
            night[level].append(drop / hrs)
        elif e > DAY_ET0:
            day[level].append(drop / (e * hrs))
    return night, day


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
    rates = {lv: statistics.mean(day[lv]) for lv in usable}
    plateau = statistics.median(list(rates.values()))
    for lv in usable:
        if rates[lv] < drop_to * plateau:
            return lv + step, plateau  # top of the falloff bin
    return None, plateau


def report(label, night, day, step):
    print(f"\n=== {label} ===")
    print(f"{'level':>11} {'n':>6} {'night %AD/hr':>13} {'n':>6} {'day %AD/mm ET0':>15}")
    for lv in sorted(set(night) | set(day)):
        nv, dv = night.get(lv, []), day.get(lv, [])
        if len(nv) < MIN_N and len(dv) < MIN_N:
            continue
        ns = f"{statistics.mean(nv):+.3f}" if len(nv) >= MIN_N else "--"
        ds = f"{statistics.mean(dv):.2f}" if len(dv) >= MIN_N else "--"
        print(f"{lv:>7}-{lv + step - 1:<3} {len(nv):>6} {ns:>13} {len(dv):>6} {ds:>15}")

    ceiling, ratio = find_ceiling(night, step)
    floor, plateau = find_floor(day, step)
    print()
    if ceiling is not None:
        n_above = sum(len(night[lv]) for lv in night if lv >= ceiling)
        print(f"  drainage_ceiling : {ceiling}%  ({ratio:.0f}x step, n={n_above} night intervals above)")
    else:
        print("  drainage_ceiling : NOT FOUND — no clear night-loss discontinuity")
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
    print(f"{len(et)} hourly ET0 rows")
    for key, pname, default_step in (("tom", "tomato", 5), ("pep", "pepper", 3)):
        step = args.step or default_step
        gauge = cfg["probes"][pname]["gauge"]
        night, day = curves(readings, et, key, step)
        report(f"{gauge['name']} — {gauge['loc']}", night, day, step)
    print("\nPaste results into config.json probes.*.bands, and note n before trusting the floor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
