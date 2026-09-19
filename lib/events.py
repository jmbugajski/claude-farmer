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

# A "rise" is a NET gain of at least this many points, accumulated from a local
# trough within RISE_WINDOW_MIN. The probes quantise to whole percent and normal
# diurnal movement is well under 1 point per 5 minutes, so 3 is comfortably
# above noise.
#
# This used to require the whole jump between two CONSECUTIVE samples, which was
# calibrated on the high-volume regimes (the comment here cited the 2026-08-13
# pepper run, +9 in one step). That definition failed silently as the regimes got
# shorter: by 2026-09-10 the 05:05 tomato run climbed 64 -> 67 over 110 minutes in
# single-point steps, so its largest 5-minute delta was +1 and the detector saw
# nothing at all. Six days of real irrigation went unlisted while the WFC01 kept
# metering ~55 L/day, which read as "water is no longer reaching the bed" and cost
# a false alarm on 2026-09-10. The failure mode is structural, not a bad constant:
# as the medium dries and pulses shorten, the wetting front reaches the probe more
# slowly and smears across more samples, so an instantaneous-delta detector goes
# blind exactly when the garden most needs watching.
#
# Lowered 3 -> 2 on 2026-09-10, together with the windowing above. Validated by
# counting events that land on days the WFC01 metered ZERO litres: 2 and 3 give
# the IDENTICAL four (2026-06-29, 06-30, 07-01, 07-03), all of which predate
# first_flow on 2026-07-02 and are real pre-meter hand-waterings. So the looser
# threshold bought recall without buying a single false positive. Recall over
# Sep 4-10 went from 8 events to 19 against 21 scheduled runs.
MIN_RISE = 2
# How long a rise may take to accumulate. Set from the slowest real ascent in the
# record -- the 2026-09-10 05:05 tomato run needed 110 minutes to gather its 3
# points. Merging risk is low: starts are >= 3 h apart, and GROUP_MIN still folds
# genuine multi-pulse blocks into one event. Overnight drift (~-0.2 pts/hr) cannot
# manufacture a false positive in either direction over this span.
RISE_WINDOW_MIN = 120
# An ascent ENDS after this long without gaining a point. Without it, a flat hour
# glues unrelated movement together: on 2026-09-10 a +1 blip at 19:05 and the
# 20:05 diagnostic run merged into one "event" dated 19:05, which put it outside
# tag_manual()'s 45-minute window and would have left the test unlabelled in
# exactly the calculations it was logged to stay out of.
STALL_MIN = 30
# How long after the ASCENT ENDS to read the settled value, for events whose ramp
# outlasts SETTLE_MIN. Fast events are unaffected: a pulse peaking 5-10 min after
# onset still settles at onset+SETTLE_MIN, exactly as before.
SETTLE_TAIL_MIN = 30
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


def tag_manual(evs, manual, window_min=45, channel=None):
    """
    Mark events that were hand-applied (fertigation, spot watering) rather than
    delivered by a timer.

    This matters more than bookkeeping tidiness. Hand-applied water DOES NOT PASS
    THE WFC01, so on those days the meter under-reports what the bed received
    while the probe still records the full response. Any litres-denominated
    metric — points per 100 L above all — is inflated on a manual day, because
    the numerator saw water the denominator did not. They are excluded from
    retention() rather than shown with an asterisk, since a silently wrong
    efficiency figure is worse than a missing one.

    Manual events are also not schedule violations, so onset_check() skips them.

    `channel` ("tom"/"pep") is matched against each entry's `channels` list. This
    was a latent bug until 2026-08-22: the list was written in config from the
    start but never read, so a manual event on ONE line would tag a coincidental
    event on the OTHER line as manual too, provided it fell inside window_min.
    It had not bitten because the only logged event (the 2026-08-16 fertigation)
    genuinely hit both channels. Adding the pepper-only bubbler test made the
    difference real -- a tomato run within 45 minutes of it would have been
    silently written off as hand-applied and dropped from retention.

    Passing channel=None preserves the old match-any behaviour, so an entry that
    omits `channels` still tags every line.
    """
    dates = {m["date"] for m in (manual or [])}
    for e in evs:
        e["manual"] = False
        if e["date"] not in dates:
            continue
        for m in manual:
            if m["date"] != e["date"]:
                continue
            chans = m.get("channels")
            if channel is not None and chans and channel not in chans:
                continue
            t = m.get("time")
            if not t:
                e["manual"] = True
                break
            hh, mm = t.split(":")
            if abs(e["onset_min"] - (int(hh) * 60 + int(mm))) <= window_min:
                e["manual"] = True
                e["manual_note"] = m.get("what", "manual application")
                break
    return evs


def manual_days(evs):
    return {e["date"] for e in evs if e.get("manual")}


def detect_events(readings, key, min_rise=MIN_RISE, group_min=GROUP_MIN,
                  settle_min=SETTLE_MIN, rise_window=RISE_WINDOW_MIN,
                  stall_min=STALL_MIN):
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
    # Each rise is (trough, onset_row, ascent_top). A rise is a NET gain of
    # min_rise points from a local trough, reached within rise_window -- so it
    # fires whether the water arrives in one 5-minute step (the old high-volume
    # regimes) or trickles in over two hours (the short-pulse regimes). Starting
    # only from a local trough is what keeps a slow ramp from registering once
    # per sample on the way up.
    rises = []
    n = len(vals)
    i = 0
    while i < n - 1:
        if vals[i + 1][key] <= vals[i][key]:
            i += 1                      # flat or falling: no ascent starts here
            continue
        # walk the CONTIGUOUS non-decreasing ascent that starts at i. Requiring
        # contiguity is what stops the window from reaching back past a long flat
        # or falling stretch and mis-dating the onset: an early version anchored
        # on any trough within rise_window and moved the 2026-06-29 tomato onset
        # from 06:20 to 04:25, inventing 24 extra events out of overnight drift.
        k = i + 1
        last_up = k                     # last sample that actually gained ground
        while (k + 1 < n and vals[k + 1][key] >= vals[k][key]
               and (vals[k + 1]["dt"] - vals[k]["dt"]).total_seconds() / 60 <= MAX_GAP_MIN
               and (vals[k + 1]["dt"] - vals[last_up]["dt"]).total_seconds() / 60 <= stall_min):
            k += 1
            if vals[k][key] > vals[k - 1][key]:
                last_up = k
        k = last_up                     # trim the flat tail off the ascent
        gain = vals[k][key] - vals[i][key]
        span = (vals[k]["dt"] - vals[i]["dt"]).total_seconds() / 60
        gap_ok = (vals[i + 1]["dt"] - vals[i]["dt"]).total_seconds() / 60 <= MAX_GAP_MIN
        if gain >= min_rise and span <= rise_window and gap_ok:
            rises.append((vals[i], vals[i + 1], vals[k]))
        i = max(k, i + 1)
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
        onset_prev, onset_row, _ = g[0]
        onset = onset_row["dt"]
        pre_floor = onset_prev[key]

        # End of the slowest ascent in this group. Everything below is floored at
        # the old onset-relative timing, so fast events are byte-identical to the
        # pre-2026-09-10 detector; only ramps that outlast settle_min move.
        top_dt = max(t["dt"] for _, _, t in g)

        # window covering the event plus its settling tail
        w_end = max(onset + timedelta(minutes=settle_min + 10),
                    top_dt + timedelta(minutes=SETTLE_TAIL_MIN + 10))
        win = [r for r in vals if onset - timedelta(minutes=5) <= r["dt"] <= w_end]
        if not win:
            continue

        # Per-pulse peaks. A contiguous ascent can swallow a whole multi-pulse
        # block (the Aug 4 regime fired 4 x 90 s at 15-minute spacing and the
        # probe never dipped between them), which would collapse n_pulses to 1
        # and silently kill floor_between -- the field-capacity tell. So pulses
        # are found by the ORIGINAL steep-step criterion, scanned across the
        # event window, and only fall back to the ascent top when the water
        # arrived too gradually to show discrete steps.
        starts = []
        for a, b in zip(win, win[1:]):
            if (b["dt"] - a["dt"]).total_seconds() / 60 > MAX_GAP_MIN:
                continue
            if b[key] - a[key] >= min_rise:
                starts.append(b)
        pulses = []
        for start in starts:
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
        if not pulses:
            top = max(g, key=lambda x: x[2]["dt"])[2]
            pulses = [{"t": top["dt"].strftime("%H:%M"), "peak": top[key]}]

        peak_row = max(win, key=lambda r: r[key])

        # settled: nearest sample at/after onset+settle_min, but never before the
        # ascent has finished plus its drainage tail -- otherwise a 110-minute ramp
        # gets "settled" read mid-climb, understating retained and making shed
        # negative. For fast events onset+settle_min always wins, so this is a
        # no-op on the historical record.
        target = max(onset + timedelta(minutes=settle_min),
                     top_dt + timedelta(minutes=SETTLE_TAIL_MIN))
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


def per_day_draw(water_daily):
    """
    {date: litres} for the days whose draw is that ONE day's measured water.

    analyze._water() spreads a draw that spans a missing export evenly over the
    days it covers (`span_days` > 1) and flags a meter reset (`reset`). Totals
    and means survive that; a per-day figure does not -- the split is assumed,
    and a reset day's litres are unknown. 2026-09-04 carried two days of water
    and its points-per-100 L read 5.5 between neighbours at 16.3 and 9.0 (#8).
    Withheld rather than estimated.
    """
    return {r["date"]: r["draw"] for r in (water_daily or [])
            if r.get("span_days", 1) == 1 and not r.get("reset")}


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
    draw = per_day_draw(water_daily)
    skip = manual_days(events)          # meter is blind to hand-applied water
    by_day: dict[str, float] = {}
    for e in events:
        if e["retained"] is None:
            continue
        by_day[e["date"]] = by_day.get(e["date"], 0) + e["retained"]

    out = []
    for d in sorted(by_day):
        litres = draw.get(d)
        if not litres or litres <= 0 or d in skip:
            continue
        out.append({
            "date": d,
            "retained": round(by_day[d], 1),
            "liters": round(litres, 1),
            "pts_per_100L": round(by_day[d] / litres * 100, 1),
        })
    return out


def onset_check(events, scheduled, tol_min=15, since=None, fault_frac=0.6):
    """
    Did each run fire when the schedule says it should?

    Cheap, and the only run-log that exists for the pepper line, which sits on
    the house multi-zone controller with no metering and no logging of its own.
    A run that fires late but FULL SIZE is a displacement (something upstream in
    the zone sequence ran long), not a valve failure.

    That distinction used to live only in this docstring and in a sentence under
    the table, leaving the reader to eyeball Delta against Retained. It is now
    computed, as `kind`:

        on_time    -- within tol_min
        displaced  -- late, but retained a normal amount: an upstream zone
                      overran. The pepper valve is fine; the zone AHEAD of it is
                      the one being over-watered.
        fault      -- late AND retained materially less than normal: the run
                      itself was short or partial, i.e. the problem is at this
                      valve.

    Justin confirmed on 2026-08-22 that the surviving pepper displacements are
    exactly this -- other house zones running serially ahead of the peppers -- and
    are not a concern. Encoding it keeps the panel honest without crying wolf: a
    warning that fires on expected behaviour is one the reader learns to skip,
    which is the same failure as the permanent-warning health panel fixed on
    2026-08-19. `displaced` stays in the table because this is the only run-log
    the house system has, but it is not styled as an alarm.

    The `retained` reference is the median of on-time runs in the same scoped
    window, so it tracks the current schedule rather than a historical constant;
    `fault_frac` is how far below that a run must land to count as a fault.
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
        if e.get("manual"):     # hand-applied: not a schedule violation
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

    # Second pass: classify. Needs the whole set first, because "normal
    # retained" is defined by the on-time runs in this same window.
    ref = [r["retained"] for r in out if not r["late"] and r["retained"] is not None]
    ref_med = statistics.median(ref) if ref else None
    for r in out:
        if not r["late"]:
            r["kind"] = "on_time"
        elif ref_med is None or r["retained"] is None or ref_med <= 0:
            # No usable baseline -- say so rather than guessing a category.
            r["kind"] = "late_unclassified"
        elif r["retained"] >= fault_frac * ref_med:
            r["kind"] = "displaced"
        else:
            r["kind"] = "fault"
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
    draw = per_day_draw(water_daily)
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
