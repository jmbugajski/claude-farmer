"""
farm_config.py
==============
config.json, checked and completed before anything reads it.

    load(path)  ->  json  ->  validate  ->  resolve  ->  the dict every consumer gets

Until 2026-09-19 (#15) a plan change had to be typed consistently into
plan.set, plan.runs, plan.runs_effective, plan.runs_time_effective,
plan.run_min_effective, a new plan.regimes[] row and the previous row's `end`,
and different consumers read different copies: _water() the regimes, _advice()
runs_effective, onset_check() runs_time_effective. plan.mode already said
"three-start" over a two-run list. metered_daily / metered_weekly /
expected_in_per_week were products of the run list and the metered rate, typed
in by hand, and the gauge bar drew probes.*.gauge.floor/ceiling while every
verdict read probes.*.bands.

plan.regimes[] is now the one record of the tomato schedule: each row carries
the `runs` the timer held from `start` to `end`. resolve() derives the keys the
consumers already read by name, so none of them changed, and validate() refuses
a config that stores one of those keys again.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

# Computed by resolve(). A stored copy is a second source of truth, so
# validate() rejects it rather than checking that it agrees.
DERIVED_PLAN_KEYS = ("set", "runs", "run_seconds", "run_min", "runs_effective",
                     "runs_time_effective", "metered_daily", "metered_weekly",
                     "expected_in_per_week")
# The bar's zones are bands.stress_floor / drainage_ceiling (_gauge_for).
DERIVED_GAUGE_KEYS = ("floor", "ceiling")

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(ValueError):
    """config.json failed validate(); str() is one problem per line."""


def _date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _positive(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0


def _check_runs(where, runs, errors):
    if not isinstance(runs, list):
        errors.append(f"{where}: `runs` must be a list of {{time, seconds}} (empty for no timer)")
        return
    times = []
    for run in runs:
        t, sec = (run or {}).get("time"), (run or {}).get("seconds")
        if not isinstance(t, str) or not _HHMM.match(t):
            errors.append(f"{where}: run time {t!r} is not HH:MM")
        if not _positive(sec):
            errors.append(f"{where}: run seconds {sec!r} is not a positive number")
        times.append(t)
    if len(set(times)) != len(times):
        errors.append(f"{where}: two runs share a start time")


def _check_regimes(plan, errors):
    regimes = plan.get("regimes")
    if not isinstance(regimes, list) or not regimes:
        errors.append("plan.regimes: at least one row is required")
        return
    prev_end = None
    for i, row in enumerate(regimes):
        where = f"plan.regimes[{i}] ({row.get('label')!r})"
        last = i == len(regimes) - 1
        start, end = _date(row.get("start")), _date(row.get("end"))
        _check_runs(where, row.get("runs"), errors)
        if start is None:
            errors.append(f"{where}: start {row.get('start')!r} is not YYYY-MM-DD")
            prev_end = end
            continue
        if row.get("end") is None:
            if not last:
                errors.append(f"{where}: only the last row may be open (end: null)")
        elif end is None:
            errors.append(f"{where}: end {row.get('end')!r} is not YYYY-MM-DD")
        elif end < start:
            errors.append(f"{where}: start {row['start']} is after end {row['end']}")
        # `end` is the last full day ON a regime (_regime_window), so the next
        # row starts the day after: earlier is an overlap, later is a gap that
        # _regime_window and regime_summary would silently score as nothing.
        if i and prev_end is not None and start != prev_end + timedelta(days=1):
            kind = "overlaps" if start <= prev_end else "leaves a gap after"
            errors.append(f"{where}: start {row['start']} {kind} the previous row's end "
                          f"{prev_end:%Y-%m-%d}")
        prev_end = end
    if isinstance(regimes[-1].get("runs"), list) and not regimes[-1]["runs"]:
        errors.append("plan.regimes: the last row is the current plan and needs its runs")


_CHANNEL = re.compile(r"^CH[1-9][0-9]*$")


def _check_probe(name, probe, errors):
    b, g = probe.get("bands") or {}, probe.get("gauge") or {}
    # parse_ecowitt finds the probe's battery-voltage column by this tag; a
    # missing or malformed one silently costs the sensor-health check (#18).
    if not _CHANNEL.match(str(probe.get("voltage_channel"))):
        errors.append(f"probes.{name}.voltage_channel: the console channel carrying this "
                      f"probe's voltage column, as CH<n> (e.g. 'CH1')")
    for k in DERIVED_GAUGE_KEYS:
        if k in g:
            errors.append(f"probes.{name}.gauge.{k}: derived from bands -- delete the stored copy")
    floor, work, ceiling = b.get("stress_floor"), b.get("working_lo"), b.get("drainage_ceiling")
    lo, hi = g.get("lo"), g.get("hi")
    if not all(_positive(v) for v in (floor, work, ceiling, lo, hi)):
        errors.append(f"probes.{name}: stress_floor, working_lo, drainage_ceiling, gauge.lo "
                      f"and gauge.hi must all be positive numbers")
        return
    if not floor < work <= ceiling:
        errors.append(f"probes.{name}.bands: need stress_floor < working_lo <= drainage_ceiling, "
                      f"got {floor} / {work} / {ceiling}")
    # The bar spans lo..hi; a threshold outside it draws a zone of negative
    # width or none at all, under a note that still quotes the number.
    if not lo < floor or not ceiling < hi:
        errors.append(f"probes.{name}.gauge: lo..hi ({lo}..{hi}) must contain the bands "
                      f"({floor}..{ceiling})")


def validate(config) -> list[str]:
    """Every problem found, as `path: what is wrong`. Empty means usable."""
    errors: list[str] = []
    plan = config.get("plan") or {}
    for k in DERIVED_PLAN_KEYS:
        if k in plan:
            errors.append(f"plan.{k}: derived from plan.regimes -- delete the stored copy")
    _check_regimes(plan, errors)
    if not _positive(plan.get("measured_rate_lps")):
        errors.append("plan.measured_rate_lps: the metered L/s the projection scales from "
                      "must be a positive number")
    if not _positive((config.get("bed") or {}).get("liters_per_inch_of_water")):
        errors.append("bed.liters_per_inch_of_water: must be a positive number")
    for name, probe in (config.get("probes") or {}).items():
        if isinstance(probe, dict) and "bands" in probe:
            _check_probe(name, probe, errors)
    return errors


def _times(row):
    return {r["time"] for r in row["runs"]}


def resolve(config):
    """Validate, then fill in DERIVED_PLAN_KEYS on config["plan"]. Returns config.

    The current plan is the LAST regimes row whether or not it has started: a
    change is logged the evening before it first runs, and the plan panel
    describes what the timer is now set to. What the readings were produced
    under is a different question, and _regime_window() answers it.
    """
    errors = validate(config)
    if errors:
        raise ConfigError("\n".join(errors))
    plan = config["plan"]
    regimes = plan["regimes"]
    current = regimes[-1]
    runs = current["runs"]

    # Start times last moved at the oldest row of the trailing streak that
    # holds the current SET of times. A duration-only change keeps the streak
    # going, which is the distinction onset_check() needs: on 2026-08-19 a
    # duration cut advanced the one date there was, put it in the future, and
    # the onset panel rendered empty.
    since = current
    for row in reversed(regimes[:-1]):
        if _times(row) != _times(current):
            break
        since = row

    seconds = sum(r["seconds"] for r in runs)
    daily = seconds * plan["measured_rate_lps"]
    uniform = len({r["seconds"] for r in runs}) == 1
    plan.update({
        "set": current["start"],
        "runs": runs,
        "run_seconds": runs[0]["seconds"] if uniform else None,
        "run_min": round(seconds / 60, 1),
        "runs_effective": current["start"],
        "runs_time_effective": since["start"],
        "metered_daily": round(daily, 1),
        "metered_weekly": round(daily * 7, 1),
        "expected_in_per_week": round(daily * 7 / config["bed"]["liters_per_inch_of_water"], 2),
    })
    return config


def load(path):
    with open(path, encoding="utf-8") as f:
        return resolve(json.load(f))
