"""
Native-resolution irrigation analytics.

WHY THIS MODULE EXISTS
----------------------
The rest of the pipeline resamples to `sample_interval_minutes` (60 by default)
before doing anything. That is fine for showing the shape of a month, but it
destroys the thing that has actually driven every irrigation decision in this
project: what the soil does in the minutes AROUND a run.

Concretely, at hourly resolution the 2026-08-04 plan -- three 90-second pulses
at 05:05 / 05:20 / 05:35 -- collapses into a single bucket, so the finding that
the inter-pulse floor was pinned at 79-81% (field capacity, i.e. pulses 2 and 3
added nothing) is arithmetically invisible. Same for the pepper bags reaching
container capacity ~5-8 minutes into a 15-minute run and then FALLING while
water was still being applied.

So everything here works on the raw 5-minute readings, never on `series`.

The three questions this module answers, which map 1:1 to the decisions in
plan.schedule_log:
  1. What is the control variable doing?   -> daily_extremes()  (pre-irrigation floor, daily min)
  2. Is a run earning its water?           -> detect_events()   (retained gain vs peak excursion)
  3. Did the run happen when it should?    -> detect_events()   (onset time vs scheduled)
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta

# A "rise" is a jump of at least this many points between consecutive native
# samples. The probes quantise to whole percent, and normal diurnal movement is
# well under 1 point per 5 minutes, so 3 is comfortably above noise while still
# catching the weakest observed real event (the 2026-08-13 pepper run, +9).
MIN_RISE = 3
# Two rises closer together than this belong to the SAME irrigation event. Set
# above the 15-minute pulse spacing of the Aug 4 plan so a multi-pulse morning
# block is treated as one event with several pulses, not several events.
GROUP_MIN = 45
# How long after onset to read the "settled" value: the level the profile
# actually holds once drainage and redistribution are done. Both channels are
# flat by ~40 min (tomato reaches its inter-pulse floor within 10 min of the
# last pulse; peppers are parked by onset+45), so 50 min is a safe read point.
SETTLE_MIN = 50
# Maximum gap between consecutive samples that still counts as continuous data.
MAX_GAP_MIN = 10


def _by_day(readings):
    days: dict[str, list] = {}
    for r in readings:
        days.setdefault(r["dt"].strftime("%Y-%m-%d"), []).append(r)
    for d in days:
        days[d].sort(key=lambda x: x["dt"])
    return days


def detect_events(readings, key, min_rise=MIN_RISE, group_min=GROUP_MIN,
                  settle_min=SETTLE_MIN):
    """
    Find irrigation events for one probe channel at native resolution.

    Returns one dict per event with the numbers that decide whether a run is
    worth its water:

      pre_floor   value immediately before the first rise -- what the soil held
      peak        highest value reached during the event
      settled     value at onset + settle_min -- what it actually KEPT
      retained    settled - pre_floor        <- the only gain that matters
      shed        peak - settled             <- water that arrived and left
      pulses      per-pulse (time, peak) within the event
      floor_between  lowest value between the first and last pulse peak. When
                  this is flat across pulses it means the profile is already at
                  field capacity and the later pulses are adding nothing.

    `retained` vs `shed` is the whole diagnostic. A run that peaks 20 points and
    settles back to +7 delivered 7 points of storage and flushed the rest past
    the root zone -- which is exactly what both the pre-Aug-3 tomato flood
    regime and the current 15-minute pepper run look like.
    """
    vals = [r for r in readings if r.get(key) is not None]
    vals.sort(key=lambda x: x["dt"])
    if len(vals) < 3:
        return []

    # --- locate rises
    rises = []
    for a, b in zip(vals, vals[1:]):
        gap = (b["dt"] - a["dt"]).total_seconds() / 60
        if gap > MAX_GAP_MIN:
            continue
        if b[key] - a[key] >= min_rise:
            rises.append((a, b))
    if not rises:
        return []

    # --- group rises that belong to one event
    groups = [[rises[0]]]
    for pair in rises[1:]:
        if (pair[0]["dt"] - groups[-1][-1][1]["dt"]).total_seconds() / 60 <= group_min:
            groups[-1].append(pair)
        else:
            groups.append([pair])

    out = []
    for g in groups:
        onset_prev, onset_row = g[0]
        onset = onset_row["dt"]
        pre_floor = onset_prev[key]

        # window covering the event plus its settling tail
        w_end = onset + timedelta(minutes=settle_min + 10)
        win = [r for r in vals if onset - timedelta(minutes=5) <= r["dt"] <= w_end]
        if not win:
            continue

        # per-pulse peaks: after each rise, follow until the value turns down
        pulses = []
        for _, start in g:
            seg = [r for r in vals if start["dt"] <= r["dt"]
                   <= start["dt"] + timedelta(minutes=group_min)]
            if not seg:
                continue
            best = seg[0]
            for r in seg:
                if r[key] >= best[key]:
                    best = r
                else:
                    break
            pulses.append({"t": best["dt"].strftime("%H:%M"), "peak": best[key]})

        peak_row = max(win, key=lambda r: r[key])

        # settled: nearest sample at/after onset+settle_min
        target = onset + timedelta(minutes=settle_min)
        after = [r for r in vals if r["dt"] >= target]
        settled = after[0][key] if after and (after[0]["dt"] - target).total_seconds() / 60 <= 15 else None

        # floor between first and last pulse peak -- the field-capacity tell
        floor_between = None
        if len(pulses) >= 2:
            t_first = datetime.strptime(
                onset.strftime("%Y-%m-%d") + " " + pulses[0]["t"], "%Y-%m-%d %H:%M")
            t_last = datetime.strptime(
                onset.strftime("%Y-%m-%d") + " " + pulses[-1]["t"], "%Y-%m-%d %H:%M")
            mid = [r[key] for r in vals if t_first < r["dt"] < t_last]
            if mid:
                floor_between = min(mid)

        out.append({
            "date": onset.strftime("%Y-%m-%d"),
            "onset": onset.strftime("%H:%M"),
            "onset_min": onset.hour * 60 + onset.minute,
            "pre_floor": pre_floor,
            "peak": peak_row[key],
            "peak_t": peak_row["dt"].strftime("%H:%M"),
            "min_to_peak": round((peak_row["dt"] - onset).total_seconds() / 60),
            "settled": settled,
            "retained": round(settled - pre_floor, 1) if settled is not None else None,
            "shed": round(peak_row[key] - settled, 1) if settled is not None else None,
            "pulses": pulses,
            "n_pulses": len(pulses),
            "floor_between": floor_between,
        })
    return out


def daily_extremes(readings, key, events=None):
    """
    Per-day control-variable readings, at native resolution.

    pre_irrigation  the value immediately before the day's FIRST event. This is
                    the real overnight minimum -- the quantity every decision in
                    plan.schedule_log actually used ("hold until the overnight
                    min returns to 58-60%"), and it is not the same as the daily
                    mean the old dashboard tracked.
    day_min         lowest point of the day and when it occurred. Under the
                    current two-run plan this lands mid-afternoon, just before
                    the PM run, and it is the number that says whether the bed
                    is reaching setpoint at all.
    """
    ev_by_day: dict[str, list] = {}
    for e in (events or []):
        ev_by_day.setdefault(e["date"], []).append(e)

    out = []
    for d, rows in sorted(_by_day(readings).items()):
        vals = [r for r in rows if r.get(key) is not None]
        if not vals:
            continue
        lo = min(vals, key=lambda r: r[key])
        hi = max(vals, key=lambda r: r[key])
        first_ev = sorted(ev_by_day.get(d, []), key=lambda e: e["onset_min"])
        # A day whose export stops early has a day_min that is simply "the
        # lowest value so far", which on a partial morning is the overnight
        # value rather than the real afternoon trough. Flag it so callers can
        # report the last COMPLETE day instead of silently comparing a half-day
        # against full ones.
        span_min = (vals[-1]["dt"] - vals[0]["dt"]).total_seconds() / 60
        partial = span_min < 20 * 60
        out.append({
            "date": d,
            "pre_irrigation": first_ev[0]["pre_floor"] if first_ev else None,
            "first_onset": first_ev[0]["onset"] if first_ev else None,
            "n_events": len(ev_by_day.get(d, [])),
            "day_min": lo[key], "day_min_t": lo["dt"].strftime("%H:%M"),
            "day_max": hi[key], "day_max_t": hi["dt"].strftime("%H:%M"),
            "swing": round(hi[key] - lo[key], 1),
            "n": len(vals),
            "partial": partial,
        })
    return out


def retention(events, water_daily):
    """
    Points of retained moisture per litre applied, per day (tomato only -- the
    WFC01 meters that line and nothing else).

    This is the metric that makes over-application obvious without any agronomy:
    when a regime is flood-dosing, litres go up and retained points do not, so
    points-per-litre collapses. The 2026-08-03 hand calculation that broke the
    whole case open ("6.5x the water bought 6 more points of peak") was this
    number computed once, by hand, on a single pair of runs.
    """
    draw = {r["date"]: r["draw"] for r in (water_daily or [])}
    by_day: dict[str, float] = {}
    for e in events:
        if e["retained"] is None:
            continue
        by_day[e["date"]] = by_day.get(e["date"], 0) + e["retained"]

    out = []
    for d in sorted(by_day):
        litres = draw.get(d)
        if not litres or litres <= 0:
            continue
        out.append({
            "date": d,
            "retained": round(by_day[d], 1),
            "liters": round(litres, 1),
            "pts_per_100L": round(by_day[d] / litres * 100, 1),
        })
    return out


def onset_check(events, scheduled, tol_min=15, since=None):
    """
    Did each run fire when the schedule says it should?

    Cheap, and the only run-log that exists for the pepper line, which sits on
    the house multi-zone controller with no metering and no logging of its own.
    A run that fires late but FULL SIZE is a displacement (something upstream in
    the zone sequence ran long), not a valve failure -- so `late` days are worth
    reading next to `retained`, which should be unchanged if it is displacement.
    """
    sched = []
    for s in (scheduled or []):
        hh, mm = s.split(":")
        sched.append(int(hh) * 60 + int(mm))
    if not sched:
        return []
    out = []
    # `since` matters more than it looks. Scoring the WHOLE history against the
    # CURRENT schedule flags every run made under a previous plan as "late" --
    # the pepper line ran every 12 h until 2026-07-24, so an unscoped check
    # reported 30 of 53 runs off-schedule and buried the 3 that are real.
    # Callers pass the date the current schedule took effect.
    for e in events:
        if since and e["date"] < since:
            continue
        nearest = min(sched, key=lambda s: abs(s - e["onset_min"]))
        delta = e["onset_min"] - nearest
        out.append({
            "date": e["date"], "onset": e["onset"],
            "scheduled": f"{nearest // 60:02d}:{nearest % 60:02d}",
            "delta_min": delta,
            "late": abs(delta) > tol_min,
            "retained": e["retained"],
        })
    return out


def regime_summary(regimes, extremes, water_daily):
    """
    Collapse each irrigation regime to the few numbers that decide whether it
    worked. Regimes come from config (plan.regimes), never from parsing prose.

    This exists because the old dashboard fitted ONE linear trend across the
    whole history -- five different irrigation regimes -- and then projected a
    setpoint crossing from it. That regression had R^2 = 0.28 and its projection
    was undefined, which is what a slope fitted across regime changes deserves.
    Comparing regimes side by side is the honest version of that question.
    """
    draw = {r["date"]: r["draw"] for r in (water_daily or [])}
    ext = {r["date"]: r for r in extremes}
    out = []
    for rg in (regimes or []):
        lo, hi = rg["start"], rg.get("end") or "9999-99-99"
        days = [d for d in ext if lo <= d <= hi]
        if not days:
            continue
        pre = [ext[d]["pre_irrigation"] for d in days if ext[d]["pre_irrigation"] is not None]
        mins = [ext[d]["day_min"] for d in days if ext[d]["day_min"] is not None]
        lit = [draw[d] for d in days if d in draw and draw[d] > 0]
        out.append({
            "label": rg["label"],
            "start": rg["start"], "end": rg.get("end"),
            "days": len(days),
            "pre_irrigation": round(statistics.mean(pre), 1) if pre else None,
            "day_min": round(statistics.mean(mins), 1) if mins else None,
            "liters_day": round(statistics.mean(lit), 1) if lit else None,
        })
    return out


def water_budget(water_daily, liters_per_inch, etc_band):
    """
    Weekly applied depth against estimated crop demand -- the over-watering
    question in one line, and the only place litres become agronomically
    meaningful. Needs bed geometry, which is why this could not be asked before
    2026-08-03 (and why the bed looked under-watered until it was measured).
    """
    if not water_daily or not liters_per_inch:
        return []
    rows = [r for r in water_daily if r.get("draw") is not None]
    if not rows:
        return []
    d0 = datetime.strptime(rows[0]["date"], "%Y-%m-%d")
    weeks: dict[int, dict] = {}
    for r in rows:
        wi = (datetime.strptime(r["date"], "%Y-%m-%d") - d0).days // 7
        w = weeks.setdefault(wi, {"L": 0.0, "dates": []})
        w["L"] += r["draw"]
        w["dates"].append(r["date"])
    lo, hi = (etc_band or [None, None])[:2]
    out = []
    for wi in sorted(weeks):
        w = weeks[wi]
        if len(w["dates"]) < 4:      # partial weeks are misleading as a rate
            continue
        inches = w["L"] / liters_per_inch
        verdict = None
        if lo and hi:
            verdict = "under" if inches < lo else ("over" if inches > hi else "in band")
        out.append({
            "start": min(w["dates"]), "end": max(w["dates"]), "days": len(w["dates"]),
            "liters": round(w["L"], 1), "inches": round(inches, 2),
            "etc_lo": lo, "etc_hi": hi, "verdict": verdict,
            "x_etc": round(inches / hi, 1) if hi else None,
        })
    return out
