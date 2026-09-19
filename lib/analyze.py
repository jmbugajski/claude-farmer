"""
analyze.py
==========
Turn the clean reading list from parse_ecowitt into the two objects the
dashboard template needs:

  * DATA -- all the numeric series/aggregates the in-page JS charts read
  * CFG  -- config-derived constants + short data-driven narrative (setpoints,
            lat/lon, window label, irrigation plan, gauge verdicts/notes)

Everything the soil charts show is derived from a single canonical resample of
the readings (default: one point per hour) so the reported sample count "n" is
consistent with what's plotted. Water usage is computed from the full-resolution
cumulative meter reading.
"""

from __future__ import annotations

import math
import statistics

import events as events_mod
import parse_ecowitt
from datetime import datetime, timedelta

import weather as weather_mod


# ----------------------------------------------------------------------------- helpers
def _mean(xs):
    return round(statistics.fmean(xs), 1) if xs else None


def _std(xs):
    return round(statistics.pstdev(xs), 1) if len(xs) > 1 else 0.0


def _fmt_md(d: datetime) -> str:
    return d.strftime("%b %-d")


def _linreg(xs, ys):
    """Ordinary least squares. Returns (slope, intercept, r2)."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return 0.0, my, 0.0
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return slope, intercept, r2


# ----------------------------------------------------------------------------- resample
def resample(readings, interval_min):
    """
    Keep one reading per interval bucket (default hourly = readings on the hour).
    Falls back to the reading closest to the bucket start if none lands exactly.
    Returns list of {"dt", "tom", "pep"}.
    """
    buckets: dict[datetime, dict] = {}
    for r in readings:
        dt = r["dt"]
        secs = (dt.hour * 3600 + dt.minute * 60)
        bucket_secs = (secs // (interval_min * 60)) * interval_min * 60
        b = dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(seconds=bucket_secs)
        prev = buckets.get(b)
        # prefer the sample nearest the bucket boundary
        if prev is None or abs((dt - b).total_seconds()) < abs((prev["dt"] - b).total_seconds()):
            buckets[b] = {"dt": dt, "bucket": b, "tom": r["tom"], "pep": r["pep"]}
    out = []
    for b in sorted(buckets):
        s = buckets[b]
        out.append({"dt": b, "tom": s["tom"], "pep": s["pep"]})
    return out


# ----------------------------------------------------------------------------- soil stats
def _regime_split(series, key, bands, since=None, until=None):
    """Share of readings in each irrigation regime: draining / working / dry.

    This replaces pct_above/pct_below a setpoint. The old figure answered "how
    often were we above a number Justin typed into the EcoWitt app", which is
    unanswerable in agronomic terms on a factory-calibrated index. These three
    answer "how often was applied water being wasted, used, or short" -- which
    is the only question the dashboard exists to serve.

    Scoped by _regime_window()'s `since`/`until` (until exclusive). The gauge
    shows this as a present-tense figure, and over the whole history it
    averaged nine schedules and two battery eras into it (#7).
    """
    vals = [p[key] for p in series if p[key] is not None
            and (since is None or p["dt"] >= since)
            and (until is None or p["dt"] < until)]
    if not vals:
        return {"pct_draining": None, "pct_working": None, "pct_dry": None, "n": 0}
    # Every share below is a share RELATIVE TO the ceiling and floor. If those
    # thresholds are not currently defensible the percentages inherit that, so
    # they are withheld rather than published with a caveat -- see
    # bands.verified in config.json.
    if not bands.get("verified", True):
        return {"pct_draining": None, "pct_working": None, "pct_dry": None,
                "n": len(vals), "withheld": "bands_unverified"}
    ceiling, floor = bands["drainage_ceiling"], bands["stress_floor"]
    n = len(vals)
    drain = sum(1 for v in vals if v >= ceiling)
    dry = sum(1 for v in vals if v < floor)
    return {
        "pct_draining": round(drain / n * 100, 1),
        "pct_working": round((n - drain - dry) / n * 100, 1),
        "pct_dry": round(dry / n * 100, 1),
        "n": n,
    }


def _regime_window(config, readings):
    """The irrigation regime the DATA actually covers, as (start, end, label).

    Everything that judges the present schedule must be scoped by this. A fixed
    trailing window silently averages across plan changes: on 2026-08-22 a 14-day
    partition put 11 days of the retired 4x90s flood plan alongside 3 days of the
    2x90s plan and reported 52% drainage, which described a schedule that no
    longer existed. config._regimes_comment already warns that comparisons
    straddling a regime boundary are meaningless; this is the guard that enforces
    it instead of trusting the reader to remember.

    Picks the latest regime that has ACTUALLY STARTED as of the newest reading,
    which is not always the open one. A plan change is logged the day it is
    entered but takes effect the next morning, so between those two moments the
    open regime is future-dated. The previous version took the open regime,
    found it in the future, returned None, and let the caller fall back to a
    blind 14 days -- reintroducing exactly the cross-regime averaging this
    function exists to prevent, and doing it silently. Observed 2026-08-27 when
    the 3x80s plan was logged: the partition window jumped from Aug 20 back to
    Aug 13 and mixed the retired 4x90s plan back in. Falling back to the
    PREVIOUS regime is right because that is the schedule the readings were
    produced under.

    `end` is returned so the caller can also bound the window from above: a
    closed regime whose successor has not started yet must not absorb the days
    after it ended. Returns (None, None, None) when nothing is usable.
    """
    regimes = (config.get("plan", {}) or {}).get("regimes") or []
    last = readings[-1]["dt"] if readings else None

    def _d(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except (TypeError, ValueError):
            return None

    started = []
    for r in regimes:
        dt = _d(r.get("start"))
        if dt is None:
            continue
        if last is not None and dt > last:
            continue        # not in effect yet -- cannot describe these readings
        started.append((dt, r))

    if started:
        dt, r = max(started, key=lambda p: p[0])
        end = _d(r.get("end"))
        if end is not None:
            # `end` is the last full day ON the regime, so the window runs to the
            # close of that day rather than to its 00:00.
            end += timedelta(days=1)
            if last is not None and end > last:
                end = None      # regime is still the current one in practice
        return dt, end, r.get("label")

    # No regimes recorded at all -- fall back to the plan's effective date, with
    # the same future-date guard (see _scope()).
    start = (config.get("plan", {}) or {}).get("runs_effective")
    dt = _d(start)
    if dt is None or (last is not None and dt > last):
        return None, None, None
    return dt, None, None


def _partition(readings, wx_hourly, key, bands, since=None, until=None, label=None):
    """Split observed moisture loss into drainage vs plant uptake.

    The method that makes the bands derivable in the first place, run forward as
    a live metric. Night intervals (ET0 = 0) carry no transpiration, so whatever
    leaves the soil then is drainage; day intervals scale with atmospheric
    demand.

    Scoped to the regime the data actually covers (see _regime_window), falling
    back to 14 days ONLY when no regime is recorded at all. `days` in the result
    is the actual window length so the dashboard can state it rather than
    implying a fixed fortnight, and `scoped_to_regime` / `regime` say which
    schedule the figure describes -- an unscoped 14-day number straddles plan
    changes and must be labelled as such rather than shown bare.

    Returns None when there is no weather series -- the whole calculation is
    keyed on ET0, and a partition computed without it would be a guess wearing a
    number's clothes.
    """
    if not wx_hourly or not readings:
        return None

    # WITHHELD 2026-09-19. This split is not currently publishable, for a reason
    # the method cannot correct for: it sums only the DROPS between consecutive
    # 5-minute readings and discards the rises. The probe reports integers and
    # dithers +/-1 around a boundary, so symmetric sensor noise accumulates into
    # one-directional "drainage". Measured over 2026-09-15..19 the nights hold
    # 27 points of drops against 23 points of rises -- a net of -4 -- and the
    # panel reported "32.9% drainage" off the 27. Real overnight loss is ~0.2
    # pts/day. The inflation is not a constant factor either: it scales with how
    # much the signal happens to flicker, which is why the same bed read 17.1%
    # in the week before at a steadier 68-70 and 32.9% at 65-66. That makes the
    # figure unusable for the week-over-week comparison it exists to support.
    #
    # Fixing it needs an estimator that is neither drops-only (dither-inflated)
    # nor signed-net (the 05:05 run lands in ET0=0 hours, so irrigation cancels
    # the loss and everything reads 0% drainage). Excluding post-irrigation
    # intervals then taking net works for 2-run schedules but starves under
    # 5-run ones. Unresolved; do not re-enable without one.
    return None
    # weather.load() yields {"dt": datetime, "et0": mm, ...}. et0 is optional --
    # an older weather.csv predating the et0 column loads fine but cannot support
    # this calculation, hence the explicit None below rather than a silent zero.
    et = weather_mod.et0_by_hour(wx_hourly)
    if not et:
        return None

    t_end = readings[-1]["dt"]
    cutoff = since or (t_end - timedelta(days=14))
    # A closed regime must not absorb the days after it ended (see
    # _regime_window): bound the window above as well as below.
    if until is not None and until < t_end:
        t_end = until

    # The ET0 series is the ONLY thing separating day from night here, and
    # et.get(..., 0.0) below treats a missing hour as ET0 = 0, i.e. as night.
    # So a weather.csv that stops before the window does not degrade the split,
    # it inverts it: every interval lands in night_loss and the panel reports a
    # confident "100% drainage / 0% uptake". That is exactly what happened on
    # 2026-09-19, when the 2 x 90 s regime started 2026-09-15 and the cached
    # weather ended 2026-09-14. Clamp the window to the weather we actually
    # have, and return None rather than publish a split computed off the end of
    # the series -- a silently wrong figure is worse than a missing one, the
    # same rule manual_events applies to retention().
    et_end = max(
        datetime.strptime(k, "%Y-%m-%d %H") for k in et
    ) + timedelta(hours=1)
    if et_end < t_end:
        t_end = et_end
    if (t_end - cutoff) < timedelta(days=2):
        return None

    ceiling = bands["drainage_ceiling"]
    night_loss = day_loss = 0.0
    night_hi = 0.0   # night loss occurring at or above the ceiling = clear waste
    n_night = n_day = 0
    for a, b in zip(readings, readings[1:]):
        if a["dt"] < cutoff or b["dt"] > t_end:
            continue
        hrs = (b["dt"] - a["dt"]).total_seconds() / 3600.0
        if not 0 < hrs <= 0.35:
            continue
        if a[key] is None or b[key] is None or b[key] > a[key]:
            continue
        drop = a[key] - b[key]
        e = et.get(a["dt"].strftime("%Y-%m-%d %H"), 0.0)
        if e <= 0.01:
            night_loss += drop
            n_night += 1
            if a[key] >= ceiling:
                night_hi += drop
        else:
            day_loss += drop
            n_day += 1
    total = night_loss + day_loss
    if total <= 0:
        return None
    span_days = max(1, round((t_end - cutoff).total_seconds() / 86400))
    return {
        "days": span_days,
        "since": cutoff.strftime("%Y-%m-%d"),
        "until": t_end.strftime("%Y-%m-%d"),
        "scoped_to_regime": since is not None,
        "regime": label,
        "night_pts": round(night_loss, 1),
        "day_pts": round(day_loss, 1),
        "pct_drainage": round(night_loss / total * 100, 1),
        "pct_uptake": round(day_loss / total * 100, 1),
        "above_ceiling_pts": round(night_hi, 1),
        "n_night": n_night,
        "n_day": n_day,
    }


def _probe_stats(series, key, bands):
    vals = [p[key] for p in series if p[key] is not None]
    # "current" reading = trailing 24-hour mean, not the single last sample.
    # With sub-hourly data a lone last reading often lands on a post-irrigation
    # spike; a trailing-day average is a truer picture of where the bed sits.
    last = None
    if series:
        t_end = series[-1]["dt"]
        recent = [p[key] for p in series
                  if p[key] is not None and (t_end - p["dt"]).total_seconds() <= 24 * 3600]
        if recent:
            last = round(statistics.fmean(recent), 1)
        else:
            last = next((p[key] for p in reversed(series) if p[key] is not None), None)
    # A channel with no readings at all -- a renamed group header, a probe
    # offline for the whole window -- reports None and the gauge renders
    # NO READING. min()/median() of nothing used to take the build down (#14).
    return {
        "mean": _mean(vals),
        "min": round(min(vals), 1) if vals else None,
        "max": round(max(vals), 1) if vals else None,
        "std": _std(vals),
        "median": round(statistics.median(vals), 1) if vals else None,
        "ceiling": bands["drainage_ceiling"],
        "floor": bands["stress_floor"],
        "working_lo": bands["working_lo"],
        "refill_target": bands["refill_target"],
        "last": last,
    }


def _daily(series, extremes):
    """Per-day mean, trough and peak for each probe.

    min/max come from events.daily_extremes() at native resolution, NOT from
    `series`. A 90 s pulse at 05:05 peaks at ~05:15 and has shed most of its
    excursion by 06:00, so the hourly point samples understated the tomato peak
    by 5.2 points on average (up to 16) and missed 8 of 44 ceiling days; pepper
    25 of 40 (#6, 82 exports to 2026-09-19). Every ceiling test downstream --
    _cycle(), _weather_analysis() draw -- reads these rows. The mean stays on
    the hourly series, which samples the day evenly.
    """
    days: dict[str, dict] = {}
    for p in series:
        d = p["dt"].strftime("%Y-%m-%d")
        days.setdefault(d, {"tom": [], "pep": []})
        if p["tom"] is not None:
            days[d]["tom"].append(p["tom"])
        if p["pep"] is not None:
            days[d]["pep"].append(p["pep"])
    ext = {k: {e["date"]: e for e in extremes.get(k) or []} for k in ("tom", "pep")}
    out = []
    for d in sorted(days):
        if not days[d]["tom"] and not days[d]["pep"]:
            continue
        row = {"date": d}
        for k in ("tom", "pep"):
            e = ext[k].get(d)
            row[f"{k}_mean"] = _mean(days[d][k])
            row[f"{k}_min"] = round(e["day_min"], 1) if e else None
            row[f"{k}_max"] = round(e["day_max"], 1) if e else None
            row[f"{k}_partial"] = bool(e and e.get("partial"))
        out.append(row)
    return out


# _weekly / _diurnal / _trend / _distribution REMOVED 2026-08-22.
#
# _trend fitted ONE ordinary least squares line across five different irrigation
# regimes (R^2 = 0.29) and projected a setpoint crossing from that slope. A slope
# fitted across regime changes is not a trend, it is an artefact of when the plan
# happened to change; regime_summary() in events.py compares the regimes side by
# side instead, which is the only fair reading of a plan change.
#
# _distribution scored time-in-band against the hand-set setpoint -- see
# probes._setpoint_removed in config.json. _regime_split() above replaces it and
# bins against the derived drainage ceiling and stress floor instead.
#
# _weekly and _diurnal both averaged the post-irrigation spike together with the
# dry-down, which hides the only two numbers that matter (the peak and the
# floor). Neither was rendered by the dashboard.


def _water(readings, since=None, until=None, label=None):
    """Metered litres. `since`/`until`/`label` are _regime_window()'s, passed by
    build(): `recent_avg_L` is the mean under the regime the readings cover
    (`until` exclusive), and `recent_label` names it.

    It used to take plan.runs_effective raw. Plan changes are entered the day
    before they first run, so that date routinely sits ahead of the newest
    reading; the window was then empty and _advice() quoted the LIFETIME mean
    as the new plan's delivery (#12: 96.0 L/day against a 2 x 90 s plan that
    delivers ~39).
    """
    wr = [(r["dt"], r["water"]) for r in readings if r["water"] is not None]
    if not wr:
        return None
    # last cumulative reading per day, and first datetime the meter was present
    last_cum: dict[str, float] = {}
    order: list[str] = []
    for dt, cum in wr:
        d = dt.strftime("%Y-%m-%d")
        if d not in last_cum:
            order.append(d)
        last_cum[d] = cum
    meter_online = order[0]

    # first datetime with positive flow (cumulative increases)
    first_flow_dt = None
    prev = None
    for dt, cum in wr:
        if prev is not None and cum > prev + 1e-9:
            first_flow_dt = dt
            break
        prev = cum

    # daily draw = end-of-day cumulative minus the previous PRESENT day's end.
    # When an export is missing that difference is several days of water, and
    # it used to be booked to the one day after the gap: 2026-09-03 was absent
    # and 09-04 showed 108.4 L against ~55 either side (#8). The sum and the
    # number of days it covers are both measured; only the split is not, so
    # spread it evenly over the span and mark every row of it `span_days`. That
    # keeps totals, weekly bins and means right; anything that needs ONE day's
    # actual litres (events.per_day_draw) skips the marked rows.
    # A negative difference is a meter reset. The day's water is unknowable, so
    # it stays 0 -- but flagged, not swallowed.
    draws: dict[str, float] = {}
    span: dict[str, int] = {}
    gaps, resets = [], []
    prev_cum = prev_day = None
    for d in list(order):
        c = last_cum[d]
        diff = c - prev_cum if prev_cum is not None else 0.0
        if diff < 0:
            resets.append({"date": d, "from_L": round(prev_cum, 1), "to_L": round(c, 1)})
            diff = 0.0
        day = datetime.strptime(d, "%Y-%m-%d")
        n = (day - prev_day).days if prev_day is not None else 1
        covered = [(day - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(n - 1, -1, -1)]
        for cd in covered:
            draws[cd] = diff / n
            span[cd] = n
        if n > 1:
            gaps.append({"missing": covered[:-1], "booked_to": d, "liters": round(diff, 1)})
        prev_cum, prev_day = c, day
    order = sorted(draws)
    reset_days = {r["date"] for r in resets}

    if first_flow_dt is None:
        # meter present but no flow yet
        return {
            "meter_online": meter_online, "first_flow": None,
            "end": wr[-1][0].strftime("%Y-%m-%d %H:%M"),
            "total_L": 0.0, "active_days": 0, "avg_active_L": 0.0, "daily": [],
            "gaps": [], "resets": resets,
        }

    first_flow_date = first_flow_dt.strftime("%Y-%m-%d")
    display_days = [d for d in order if d >= first_flow_date]
    cum = 0.0
    daily_out = []
    total = 0.0
    for d in display_days:
        cum += draws[d]
        total += draws[d]
        row = {"date": d, "cum": round(cum, 1), "draw": round(draws[d], 1),
               "span_days": span[d]}
        if d in reset_days:
            row["reset"] = True
        daily_out.append(row)
    active = sum(1 for d in display_days if draws[d] > 0)

    # Average since the current schedule took effect, separate from the all-time
    # one. The whole-window mean is dominated by the OLD regime -- it reported
    # ~140 L/day for days after the bed had actually dropped to ~78 -- so advice
    # must quote the current-regime figure. Anchoring on the regime window
    # rather than a rolling 7 days matters: a fixed window straddles the
    # change and silently mixes both regimes.
    # Drop a trailing PARTIAL day before averaging. An export pulled midday has
    # logged that morning's runs but not the evening's, so its draw is a
    # fraction of a real day and pulls the mean down as if delivery had fallen.
    # On 2026-09-19 (export at 12:45, 05:05 run logged, 17:05 not yet) this
    # reported 35.8 L/day against an actual 39.7 -- a 10% understatement, on the
    # exact figure used to judge whether a plan change landed. Same 20-hour rule
    # events.py uses to flag a partial day, so the two agree by construction.
    span_by_day: dict[str, float] = {}
    for dt_, _cum in wr:
        d = dt_.strftime("%Y-%m-%d")
        lo, hi = span_by_day.get(d, (None, None)) if d in span_by_day else (None, None)
        span_by_day[d] = (min(lo, dt_) if lo else dt_, max(hi, dt_) if hi else dt_)
    def _complete(d):
        se = span_by_day.get(d)
        if not se or not se[0]:
            return True
        return (se[1] - se[0]).total_seconds() / 60 >= 20 * 60

    partial_tail = display_days[-1] if display_days and not _complete(display_days[-1]) else None
    # Said on the row, not only used here: every other per-day litres figure
    # reads the rows through events.per_day_draw(), and until #9 the regime
    # table quoted 35.8 L/day beside this function's 39.7 for the same schedule.
    if partial_tail:
        daily_out[-1]["partial"] = True
    usable = [d for d in display_days if d != partial_tail]
    if since:
        recent_days = [d for d in usable if d >= since and draws[d] > 0
                       and (until is None or d < until)]
    else:
        recent_days = [d for d in usable[-7:] if draws[d] > 0]
    recent_avg = (round(sum(draws[d] for d in recent_days) / len(recent_days), 1)
                  if recent_days else None)

    return {
        "meter_online": meter_online,
        "first_flow": first_flow_dt.strftime("%Y-%m-%d %H:%M"),
        "end": wr[-1][0].strftime("%Y-%m-%d %H:%M"),
        "total_L": round(total, 1),
        "active_days": active,
        "avg_active_L": round(total / active, 1) if active else 0.0,
        "recent_avg_L": recent_avg,
        "recent_n": len(recent_days),
        "recent_since": since,
        "recent_label": label,
        "daily": daily_out,
        "gaps": [g for g in gaps if g["booked_to"] >= first_flow_date],
        "resets": resets,
    }


# ----------------------------------------------------------------------------- gauges narrative
def _cycle(daily, key, since=None, until=None):
    """Median daily peak and trough over the last 7 days of the current regime.

    The ONE place this is computed: _advice() words it and the gauge tiles show
    it. It used to be computed twice, here with statistics.median and in the
    template with an upper-middle-element median, and the tile and the sentence
    beside it disagreed by a point whenever n was even (#6).

    Scoped to the current regime for the same reason the partition is: a flat
    "last 7 days" window straddles a plan change and describes a schedule that
    is no longer running.

    `since`/`until` are _regime_window()'s bounds, passed by build(). They used
    to arrive as partition["since"], so withholding the partition on 2026-09-19
    silently reverted this to a flat 7 days straddling the 2026-09-15 plan
    change, and n_days, always 7, could no longer trip the "only N days on this
    schedule" warning (#7). An empty window reports None; it does not fall back
    to the unscoped one.

    A `partial` day is dropped before the window is taken, so n_days counts
    complete days. Its trough is the overnight value and its peak is only the
    morning's; 2026-09-19 (export to 12:45) sat in the median until #9.
    """
    rows = [r for r in daily if not r.get(f"{key}_partial")]
    if since is not None:
        rows = [r for r in rows if r["date"] >= f"{since:%Y-%m-%d}"]
    if until is not None:       # exclusive: _regime_window returns end + 1 day
        rows = [r for r in rows if r["date"] < f"{until:%Y-%m-%d}"]
    rows = rows[-7:]
    peaks = [r[f"{key}_max"] for r in rows if r.get(f"{key}_max") is not None]
    troughs = [r[f"{key}_min"] for r in rows if r.get(f"{key}_min") is not None]
    return {"peak": round(statistics.median(peaks)) if peaks else None,
            "trough": round(statistics.median(troughs)) if troughs else None,
            "n_days": len(rows)}


def _classify(cycle, bands):
    """The probe's irrigation state, from _cycle()'s native peak and trough.

    The ONE classifier: _gauge_for() words the verdict from it and _advice()
    words the action. They used to test separately -- the gauge put the 24 h
    mean against the ceiling and floor, the advice put the peak and the trough
    against the same two numbers. A mean sits between peak and trough, so on 82
    exports to 2026-09-19 the two disagreed on 44 of 80 tomato days and 42 of
    77 pepper days, 37 and 36 of them WORKING on the gauge over "shorten the
    run" beneath it; the mean reached the ceiling on 5 tomato days and no
    pepper day against 44 and 40 for the peak (#14).

    The order is deliberate: DRAINAGE IS CHECKED FIRST. A bed can be above the
    ceiling at its peak and below the floor at its trough, and waste is the
    more actionable finding. Strictly greater than: landing exactly on the
    ceiling is the target, not a fault, and a `>=` here produced the nonsense
    "0 points past the ceiling".

    None when the current regime has no complete day yet -- there is no cycle
    to classify, and the mean is not a stand-in for one.
    """
    peak, trough = cycle["peak"], cycle["trough"]
    if peak is None or trough is None:
        return None
    if peak > bands["drainage_ceiling"]:
        return "DRAINING"
    if trough < bands["stress_floor"]:
        return "STRESS RISK"
    if trough < bands["working_lo"]:
        return "DRYING"
    return "WORKING"


def _advice(data, config, cycle):
    """Short, current-state-aware 'what to do next' lines for the findings box."""
    tcfg = config["probes"]["tomato"]
    pcfg = config["probes"]["pepper"]
    S = data["stats"]
    t_last, p_last = S["tom"]["last"], S["pep"]["last"]

    def _line(key, cfg, label, last, unit_desc):
        """Advice against the derived ceiling/floor rather than a setpoint.

        One sentence per _classify() state; the comparisons live there, so this
        and the gauge verdict cannot name different states (#14).
        """
        b = cfg["bands"]
        ceiling, floor, work_lo = b["drainage_ceiling"], b["stress_floor"], b["working_lo"]
        if last is None:
            return "no recent probe reading — check the sensor before reading anything else."
        peak, trough, n_days = (cycle[key][k] for k in ("peak", "trough", "n_days"))

        # Each branch below is an instruction derived from the ceiling/floor
        # ("shorten the run", "add water now"). Unverified thresholds would make
        # this advise an action on a number we cannot defend, so state what was
        # observed and stop there.
        if not b.get("verified", True):
            obs = ""
            if peak is not None and trough is not None:
                obs = f" Over the last {n_days} days it cycled {trough}–{peak}%."
            return (f"~{last}% (24 h avg).{obs} No recommendation this build: the drainage "
                    f"ceiling and stress floor are being re-derived, and the advice here is a "
                    f"function of both. Watch the daily trough in the trend chart — that is a "
                    f"direct reading and does not depend on the bands.")

        part = (data.get("partition") or {}).get(key)
        thin = (f" Only {n_days} day{'s' if n_days != 1 else ''} on this schedule so far, so treat "
                f"this as provisional." if n_days < 5 else "")

        state = _classify(cycle[key], b)
        if state is None:
            return (f"~{last}% (24 h avg). No complete day on the current schedule yet, so "
                    f"there is no peak or trough to read against the {ceiling}% ceiling and "
                    f"{floor}% floor — re-check next export.")

        # 1. The peaks are pushing PAST field capacity.
        if state == "DRAINING":
            over = peak - ceiling
            waste = ""
            if part and part.get("above_ceiling_pts"):
                waste = (f" Since {part['since']}, {part['above_ceiling_pts']:.0f} points of moisture "
                         f"drained away overnight from above the ceiling — water that never reached "
                         f"a root.")
            return (f"peaks are hitting ~{peak}%, {over} point{'s' if over != 1 else ''} past the "
                    f"{ceiling}% drainage ceiling — everything above that line leaves the {unit_desc} "
                    f"whether or not the plant wants it.{waste} Shorten the run rather than the "
                    f"frequency; the goal is to land the peak just under {ceiling}.{thin}")

        # 2. The troughs are approaching the point where uptake falls off.
        if state == "STRESS RISK":
            return (f"troughs are down to ~{trough}%, below the {floor}% stress floor where uptake "
                    f"measurably falls off — add water now, and add it as an extra run rather than "
                    f"a longer one so the peak stays under {ceiling}%.{thin}")
        if state == "DRYING":
            return (f"troughs at ~{trough}% are inside the {work_lo}–{ceiling}% working band but "
                    f"heading for the {floor}% floor. Every point of loss here is going through a "
                    f"plant, so this is demand, not waste — add ~10–15 s to one run if the trough "
                    f"keeps sliding, and re-check next export.{thin}")

        # 3. Peak under the ceiling, trough above the working floor: this is the target.
        extra = ""
        if part and part.get("pct_uptake") is not None:
            extra = (f" Since {part['since']}, {part['pct_uptake']}% of moisture loss has happened "
                     f"under daytime demand, i.e. through the plants.")
        return (f"cycling {trough}–{peak}% against a {ceiling}% ceiling and a {floor}% floor — "
                f"the whole band sits in plant-fed territory, which is the target.{extra} "
                f"Hold the schedule and watch the trough.{thin}")

    tom = _line("tom", tcfg, "Tomatoes", t_last, "root zone")
    pep = _line("pep", pcfg, "Peppers", p_last, "bags")

    # Water. Describe the schedule from config rather than hardcoding it -- this
    # line claimed a "fixed 14-min daily timer" for days after the bed moved to
    # a 4x90s pulsed plan, and quoted a lifetime average that no longer applied.
    plan = config.get("plan", {})
    runs = plan.get("runs") or []
    if runs:
        plan_desc = (f"{len(runs)} × {plan.get('run_seconds', 90)} s pulsed plan "
                     f"({plan.get('run_min', 6)} min/day total)")
    else:
        plan_desc = f"{plan.get('run_min', '?')}-min daily timer"

    w = data["water"]
    # The litres below were metered under the regime the readings cover, which
    # is not `plan.runs` while a logged change has yet to run (#12). Name the
    # schedule that produced them, and say the new one is pending.
    effective = plan.get("runs_effective")
    pending = ""
    if w and effective and effective > w["end"][:10]:
        pending = f" The {plan_desc} logged for {effective} has not run yet."
        plan_desc = f"previous schedule ({w.get('recent_label') or 'label not recorded'})"
    if not w or not w["first_flow"]:
        water = "no metered flow yet — confirm the WFC01 meter is paired and reporting."
    elif w["active_days"] < 14:
        water = (f"let the WFC01 log a few more clean weeks before leaning on the L/day "
                 f"numbers; the tomato bed runs on a {plan_desc} with no volume target, "
                 f"and peppers are watered separately and not metered.")
    elif w.get("recent_avg_L"):
        older = w["avg_active_L"]
        shift = ""
        if older and abs(older - w["recent_avg_L"]) / older > 0.15:
            # Direction from the sign, and no cause: this said "down from ...
            # flood dosing" whatever the numbers or the previous regime were.
            way = "down" if w["recent_avg_L"] < older else "up"
            shift = (f" — {way} from a {older} L lifetime average, which spans "
                     f"earlier schedules")
        water = (f"metered ~{w['recent_avg_L']} L per watering day over the last "
                 f"{w['recent_n']} active days on the tomato bed's {plan_desc}{shift}. "
                 f"Read as delivered volume, not against a target.")
    else:
        water = (f"metered ~{w['avg_active_L']} L per watering day on the tomato bed's "
                 f"{plan_desc} — read as delivered volume, not against a target.")
    water += pending

    return {"tom": tom, "pep": pep, "water": water}


def _gauge_for(pkey, pcfg, stats, cycle, split=None, partition=None):
    """Gauge verdict stated as an irrigation regime, not a distance from a number.

    The old verdict read "+8 vs setpoint", which is a true statement about an
    arbitrary index and tells the reader nothing about what to do. These four
    states map one-to-one onto an action: DRAINING means shorten the run, DRY
    means add one, WORKING means hold. They do so because the state is
    _classify()'s, the same one _advice() words the action from; the 24 h mean
    is the reading on the tile and takes no part in the verdict (#14).
    """
    g = dict(pcfg["gauge"])
    peak, trough = cycle["peak"], cycle["trough"]
    g.update(peak=peak, trough=trough)
    b = pcfg["bands"]
    ceiling, floor, work_lo = b["drainage_ceiling"], b["stress_floor"], b["working_lo"]
    last = stats["last"]
    # Set before any return: the tile draws its floor/ceiling zones unless told
    # not to, and the NO READING tile drew the unverified ones (#14).
    if not b.get("verified", True):
        g["verified"] = False

    if last is None:
        g["verdict"] = "NO READING"
        g["vclass"] = "v-trend"
        g["note"] = "No recent probe reading — nothing below this is trustworthy."
        return g

    # Verdict states are distances from the ceiling and floor. Unverified
    # thresholds make the STATE wrong, not just imprecise -- at a 50% floor the
    # current tomato trough reads WORKING, at the 60% the same data now derives
    # it reads STRESS RISK. Report the observation and say the thresholds are
    # under review rather than pick one.
    if not b.get("verified", True):
        g["verdict"] = f"{last}% · BANDS UNDER REVIEW"
        g["vclass"] = "v-trend"
        g["note"] = (
            f"Latest {last}% (24 h avg), range {stats['min']}–{stats['max']}%. "
            f"The drainage ceiling and stress floor this gauge scores against are being "
            f"re-derived, so no band percentages, verdict or distance-from-threshold is "
            f"shown. The readings themselves are unaffected — the trend, the daily "
            f"peak/trough and the metered water below are all measured, not inferred."
        )
        g["bands"] = None
        return g

    state = _classify(cycle, b)
    if state is None:
        state, vclass = "NO CYCLE YET", "v-trend"
        detail = "no complete day on this schedule"
    elif state == "DRAINING":
        vclass = "v-wet"
        detail = f"peaks {peak - ceiling} pt past the {ceiling}% ceiling"
    elif state == "STRESS RISK":
        vclass = "v-trend"
        detail = f"troughs {floor - trough} pt below the {floor}% floor"
    elif state == "DRYING":
        vclass = "v-ok"
        detail = f"troughs {trough - floor} pt above the {floor}% floor"
    else:
        vclass = "v-ok"
        detail = f"cycling {trough}–{peak}% in the {work_lo}–{ceiling}% plant-fed band"
    g["verdict"] = f"{state} · {detail}"
    g["vclass"] = vclass

    bits = [f"Latest {last}% (24 h avg), range {stats['min']}–{stats['max']}%."]
    if split and split.get("pct_working") is not None:
        bits.append(f"{split['pct_working']}% of readings sit in the plant-fed band, "
                    f"{split['pct_draining']}% at or above the drainage ceiling.")
    if partition and partition.get("pct_uptake") is not None:
        bits.append(f"Last {partition['days']} d: {partition['pct_uptake']}% of moisture loss "
                    f"under daytime demand, {partition['pct_drainage']}% overnight.")
    g["note"] = " ".join(bits)
    g["bands"] = {"ceiling": ceiling, "floor": floor, "working_lo": work_lo,
                  "refill_target": b["refill_target"]}
    return g


def _scope(since, readings):
    """
    Guard a `since` date against being in the future.

    Plan changes are entered the day BEFORE they first run, so an effective date
    routinely sits ahead of the newest reading. Any panel filtered on it then
    silently shows nothing -- which is how the onset table went blank when the
    pepper duration cut was logged. Returns None (no filter) in that case, so the
    panel keeps showing the schedule that has actually been running.
    """
    if not since or not readings:
        return since
    last = readings[-1]["dt"].strftime("%Y-%m-%d")
    return since if since <= last else None


def _sensor_health(readings, config, event_days):
    """
    Per-channel instrument trust check. Two failure modes matter for remote
    diagnosis, and neither shows up in the moisture % series itself:

      1. Supply-voltage change. These capacitance probes derive moisture from a
         raw AD count that scales with excitation voltage, and the factory
         calibration assumes ~1.5 V alkaline. Swapping to lithium (~1.7 V)
         shifts the derived % with no physical change in the soil.
      2. Collapsing daily AD range. A healthy probe resolves a clear diurnal
         wet/dry cycle. If the daily range falls to a small fraction of its
         trailing norm, the probe is degrading or has lost contact with the
         medium (air gap) — the reading may still look plausible but is no
         longer responsive.

    `event_days` is {channel: set of ISO dates with a detected irrigation
    event}. It exists for `trust_from`, the date from which the channel's
    readings can be scored: the later of the last voltage step and the day
    after the last DEAD RUN. A dead run is 3+ consecutive days under
    `ad_range_floor` with no detected event. The floor alone cannot say this
    (#11, #19): pepper sat at 4-9 counts on 2026-07-22 -> 08-05 with zero
    events, and at 7-9 counts on 09-11 -> 09-18 with an event every scored
    day. Only the first is a probe that could not register an onset.
    """
    hc = config.get("health", {})
    v_tol = hc.get("volt_tolerance", 0.08)
    range_frac = hc.get("range_collapse_frac", 0.25)
    range_floor = hc.get("ad_range_floor", 12)
    nominal = hc.get("nominal_volts", 1.5)
    step_days = hc.get("step_recent_days", 14)

    by_day = {}
    for r in readings:
        d = r["dt"].date()
        by_day.setdefault(d, []).append(r)

    out = {}
    for key, label, vkey, adkey in (("tom", "Tomato", "v_tom", "tom_ad"),
                                    ("pep", "Pepper", "v_pep", "pep_ad")):
        days = []
        for d in sorted(by_day):
            vs = [x[vkey] for x in by_day[d] if x.get(vkey) is not None]
            ads = [x[adkey] for x in by_day[d] if x.get(adkey) is not None]
            if not vs and not ads:
                continue
            days.append({
                "date": d.isoformat(),
                "v": round(statistics.mean(vs), 3) if vs else None,
                "ad_range": round(max(ads) - min(ads), 1) if len(ads) > 1 else None,
            })
        if not days:
            continue

        flags = []
        # --- voltage: off nominal, and step changes vs the prior day
        last_v = days[-1]["v"]
        if last_v is not None and abs(last_v - nominal) > v_tol:
            flags.append({
                "level": "warn",
                "msg": (f"sensor voltage {last_v:.2f} V is off the {nominal:.1f} V "
                        f"nominal — derived % is on a shifted calibration, so "
                        f"absolute values aren't comparable to earlier history."),
            })
        # A voltage step is a property of the TIMELINE, not of the sensor's
        # current state: it marks a date across which absolute % values are not
        # comparable. It is not evidence that the probe is unhealthy today, and
        # left in `flags` forever it makes `ok` permanently False. That is the
        # Aug 4 lesson inverted — a panel crying wolf about a channel that
        # recovered six weeks ago trains the reader to ignore the flag, which is
        # exactly how the NEXT real failure gets missed. So steps older than
        # step_recent_days are demoted to `breaks` (chart annotations) and only
        # a RECENT step, where the reader may still be comparing across it,
        # stays in `flags`.
        breaks = []
        step_dates = []
        last_day = days[-1]["date"]
        for a, b in zip(days, days[1:]):
            if a["v"] is not None and b["v"] is not None and abs(b["v"] - a["v"]) > v_tol:
                step_dates.append(b["date"])
                age = (datetime.fromisoformat(last_day)
                       - datetime.fromisoformat(b["date"])).days
                msg = (f"voltage stepped {a['v']:.2f} → {b['v']:.2f} V on "
                       f"{b['date']} (battery change?) — treat that date as a "
                       f"calibration break, not a moisture event.")
                if age <= step_days:
                    flags.append({"level": "warn", "msg": msg})
                else:
                    breaks.append({"level": "info", "date": b["date"],
                                   "age_days": age, "msg": msg})

        # --- AD dynamic range. Two tests, deliberately at different severities.
        #
        # The `bad` test is an ABSOLUTE floor, because it is the only version of
        # this check that does not move when the irrigation schedule moves. A
        # dead channel reads a few counts no matter what the plan is; the July
        # pepper failure sat at 4-6 while the healthy tomato channel ran 97-145.
        #
        # The relative test is kept, but only as a `warn`. Its baseline is a
        # trailing median, so every schedule change that flattens the moisture
        # curve — which is what a pulsed plan is FOR — shrinks the numerator
        # while the denominator still holds the old regime, and the check
        # reports a collapse that is really a working plan. It did exactly that
        # on the tomato channel Aug 10-15 (days after the 4x90s change) and on
        # the peppers Sep 2-3 (days after 3x80s). At `bad` that produced a red
        # CHECK PROBE badge on a healthy probe, which is the Aug 4 lesson again:
        # a panel that cries wolf trains the reader to ignore it, and that is
        # how the next real failure gets missed. As a `warn` it still surfaces a
        # channel worth a second look — the net for a failure mode that does not
        # look like the one incident these numbers are fit to — without
        # asserting the instrument is broken.
        rngs = [d["ad_range"] for d in days if d["ad_range"] is not None]
        if len(rngs) >= 8:
            base = statistics.median(rngs[:-3][-14:]) if len(rngs) > 6 else None
            recent = statistics.median(rngs[-3:])
            if recent < range_floor:
                flags.append({
                    "level": "bad",
                    "msg": (f"daily AD range is ~{recent:.0f} counts, below the "
                            f"{range_floor}-count floor — the probe is not resolving "
                            f"a diurnal cycle at all. This floor is absolute, so it "
                            f"is not an artefact of the irrigation schedule. Suspect "
                            f"a failing sensor or lost soil contact; verify with an "
                            f"air-vs-water span test."),
                })
            elif base and base > 0 and recent < base * range_frac:
                flags.append({
                    "level": "warn",
                    "msg": (f"daily AD range narrowed to ~{recent:.0f} counts against a "
                            f"~{base:.0f}-count trailing norm, but is still above the "
                            f"{range_floor}-count floor, so the probe is resolving. A "
                            f"recent irrigation change that flattens the moisture curve "
                            f"produces this too — check whether the plan changed before "
                            f"suspecting the instrument."),
                })

        # Trust boundary (see docstring). ISO dates, so max() is chronological.
        # A run needs 3 days, the same window the `bad` grade's median uses:
        # pepper 2026-09-11 was one under-floor day with no detected event,
        # yet the raw trace still moved +2 on the 06:45 run.
        seen = event_days.get(key) or set()
        trust = step_dates[-1:]
        run = 0
        dead_end = None
        for d in days:
            is_dead = (d["ad_range"] is not None and d["ad_range"] < range_floor
                       and d["date"] not in seen)
            run = run + 1 if is_dead else 0
            if run >= 3:
                dead_end = d["date"]
        if dead_end:
            trust.append((datetime.fromisoformat(dead_end)
                          + timedelta(days=1)).date().isoformat())

        out[key] = {
            "label": label,
            "days": days[-14:],
            "v_last": last_v,
            "ad_range_last": days[-1]["ad_range"],
            "trust_from": max(trust) if trust else None,
            "flags": flags,
            "breaks": breaks,
            "ok": not flags,
            # Severity matters for presentation. A "warn" (voltage a little off
            # nominal) means absolute % may be shifted; a "bad" (AD range
            # collapsed) means the channel is not measuring at all. Collapsing
            # both into ok=False once left the published page telling the reader
            # to distrust a probe that had already recovered.
            "has_bad": any(f["level"] == "bad" for f in flags),
        }
    return out


# ----------------------------------------------------------------------------- weather
def _pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    num = sum((a - mx) * (b - my) for a, b in pairs)
    dx = sum((a - mx) ** 2 for a, _ in pairs)
    dy = sum((b - my) ** 2 for _, b in pairs)
    if dx <= 0 or dy <= 0:
        return None
    return round(num / math.sqrt(dx * dy), 3)


# Under this |r| the daily swing is read as not following ET0. 0.35 is the old
# narrative's weak/moderate boundary; MIN_PAIRED is where a Pearson r on noisy
# daily data stops swinging past that boundary on its own.
SWING_R_MAX = 0.35
MIN_PAIRED = 10


def _within_regime_r(rows, regimes, xkey, ykey):
    """Pearson r of xkey vs ykey with each plan.regimes window demeaned first.

    The pooled r answers a different question. Swing is set mostly by litres
    per run, and the long single runs happened in July when ET0 was highest, so
    schedule and season move together: on 82 days to 2026-09-19 the pooled
    ET0-vs-swing r was 0.55 while this figure was 0.036 (#13). Days outside
    every regime, and regimes with under 3 paired days, carry no within-group
    information and are left out. Returns (r, n).
    """
    xs, ys = [], []
    for g in regimes or []:
        lo, hi = g["start"], g.get("end") or "9999-12-31"
        blk = [(r[xkey], r[ykey]) for r in rows
               if lo <= r["date"] <= hi and r[xkey] is not None and r[ykey] is not None]
        if len(blk) < 3:
            continue
        mx = statistics.fmean(b[0] for b in blk)
        my = statistics.fmean(b[1] for b in blk)
        xs += [b[0] - mx for b in blk]
        ys += [b[1] - my for b in blk]
    return _pearson(xs, ys), len(xs)


def _weather_analysis(daily_soil, wx_daily, regimes=None):
    """
    Join daily soil stats to daily weather and say whether the tomato bed's
    daily swing (max - min) follows evaporative demand.

    `swing_driver` is what panel 2 prints a conclusion from: "pulse" when the
    within-regime r is under SWING_R_MAX, "weather" when it is not, None when
    there are too few paired days to say. The sentence used to be fixed text in
    the template, and a narrative built here asserted "a flat correlation is a
    real result" beside whatever r it interpolated -- r=0.604 at the time (#13).
    Only keys the template reads, plus `corr`, are returned.
    """
    if not wx_daily:
        return None
    wx_by = {w["date"]: w for w in wx_daily}
    rows = []
    for r in daily_soil:
        w = wx_by.get(r["date"])
        if not w:
            continue
        rows.append({
            "date": r["date"], "et0_mm": w["et0_mm"], "tmax_f": w["tmax_f"],
            "tom_mean": r["tom_mean"], "pep_mean": r["pep_mean"],
            "tom_draw": (round(r["tom_max"] - r["tom_min"], 1)
                         if r["tom_max"] is not None and r["tom_min"] is not None else None),
            "pep_draw": (round(r["pep_max"] - r["pep_min"], 1)
                         if r["pep_max"] is not None and r["pep_min"] is not None else None),
        })
    if len(rows) < 3:
        return None

    et0s = [r["et0_mm"] for r in rows]
    r_within, n_within = _within_regime_r(rows, regimes, "et0_mm", "tom_draw")
    corr = {
        "tom_draw_vs_et0": _pearson(et0s, [r["tom_draw"] for r in rows]),
        "tom_draw_vs_et0_within": r_within,
        "n_within": n_within,
        "pep_draw_vs_et0": _pearson(et0s, [r["pep_draw"] for r in rows]),
        "tom_mean_vs_tmax": _pearson([r["tmax_f"] for r in rows],
                                     [r["tom_mean"] for r in rows]),
        "pep_mean_vs_tmax": _pearson([r["tmax_f"] for r in rows],
                                     [r["pep_mean"] for r in rows]),
    }
    driver = None
    if r_within is not None and n_within >= MIN_PAIRED:
        driver = "pulse" if abs(r_within) < SWING_R_MAX else "weather"

    sources = {w["et0_source"] for w in wx_daily}
    return {
        "swing_driver": driver,
        "corr": corr,
        "et0_source": "fao56" if sources == {"fao56"} else sorted(sources)[0],
        "span": [rows[0]["date"], rows[-1]["date"]],
    }


# ----------------------------------------------------------------------------- top-level
def build(readings, config, wx_hourly=None):
    if not readings:
        raise ValueError("no readings: nothing parsed from the EcoWitt exports")
    interval = config["sample_interval_minutes"]
    series = resample(readings, interval)

    tom_cfg = config["probes"]["tomato"]
    pep_cfg = config["probes"]["pepper"]

    tom_stats = _probe_stats(series, "tom", tom_cfg["bands"])
    pep_stats = _probe_stats(series, "pep", pep_cfg["bands"])

    # Regime split and drainage/uptake partition -- the two figures that replace
    # the setpoint. Partition is computed on RAW readings, not `series`: it needs
    # the native 5-minute resolution to catch the post-irrigation shed, which an
    # hourly resample averages straight out of existence.
    regime_since, regime_until, regime_label = _regime_window(config, readings)
    _day = lambda dt: dt.strftime("%Y-%m-%d") if dt else None
    water = _water(readings, _day(regime_since), _day(regime_until), regime_label)
    split = {k: _regime_split(series, k, c["bands"], regime_since, regime_until)
             for k, c in (("tom", tom_cfg), ("pep", pep_cfg))}
    partition = {"tom": _partition(readings, wx_hourly, "tom", tom_cfg["bands"],
                                   regime_since, regime_until, regime_label),
                 "pep": _partition(readings, wx_hourly, "pep", pep_cfg["bands"],
                                   regime_since, regime_until, regime_label)}

    # ---- native-resolution analytics (see lib/events.py for why these do NOT
    # use `series`: hourly resampling erases the pulse structure that every
    # irrigation decision in plan.schedule_log was actually made from).
    plan_cfg = config.get("plan", {})
    bed_cfg = config.get("bed", {})
    manual = plan_cfg.get("manual_events") or []
    ev_tom = events_mod.tag_manual(events_mod.detect_events(readings, "tom"), manual, channel="tom")
    ev_pep = events_mod.tag_manual(events_mod.detect_events(readings, "pep"), manual, channel="pep")
    ext_tom = events_mod.daily_extremes(readings, "tom", ev_tom)
    ext_pep = events_mod.daily_extremes(readings, "pep", ev_pep)
    daily = _daily(series, {"tom": ext_tom, "pep": ext_pep})
    sched_tom = [r["time"] for r in plan_cfg.get("runs", [])]
    sched_pep = [plan_cfg["pepper_time"]] if plan_cfg.get("pepper_time") else []
    # Same absolute floor _sensor_health grades `bad` on: under it a flat trace
    # says nothing about whether a scheduled run fired.
    ad_floor = config.get("health", {}).get("ad_range_floor", 12)
    health = _sensor_health(readings, config,
                            {"tom": {e["date"] for e in ev_tom},
                             "pep": {e["date"] for e in ev_pep}})
    etc_band = (bed_cfg.get("target") or {}).get("august_etc_in_per_week")
    native = {
        "events": {"tom": ev_tom, "pep": ev_pep},
        "extremes": {"tom": ext_tom, "pep": ext_pep},
        "retention": events_mod.retention(ev_tom, water.get("daily") if water else None),
        # Scope each onset check to the date its CURRENT schedule took effect,
        # and for peppers also to the date the probe became trustworthy again --
        # a flatlined probe cannot register an onset, so scoring that window
        # would manufacture "late" runs out of a dead sensor.
        # Onset checking compares START TIMES, so it must be scoped by the date
        # the times last moved -- NOT by runs_effective/pepper_effective, which
        # also advance on a DURATION change. Using the latter broke this panel
        # the moment the 2026-08-20 pepper duration cut was entered: the "since"
        # date was in the future, every event was filtered out, and the table
        # silently rendered empty. A duration change does not reset onset
        # history; only a time change does.
        "onset": {
            "tom": events_mod.onset_check(
                ev_tom, sched_tom, readings, "tom", ad_floor=ad_floor,
                since=_scope(plan_cfg.get("runs_time_effective")
                             or plan_cfg.get("runs_effective"), readings)),
            "pep": events_mod.onset_check(
                ev_pep, sched_pep, readings, "pep", ad_floor=ad_floor,
                since=_scope(max([d for d in (plan_cfg.get("pepper_time_effective")
                                              or plan_cfg.get("pepper_effective"),
                                              (health.get("pep") or {}).get("trust_from"))
                                   if d] or [None]),
                             readings)),
        },
        "regimes": events_mod.regime_summary(
            plan_cfg.get("regimes"), ext_tom, water.get("daily") if water else None,
            skip_days=events_mod.metered_manual_days(manual)),
        "budget": events_mod.water_budget(
            water.get("daily") if water else None,
            bed_cfg.get("liters_per_inch_of_water"), etc_band),
        # The live plan's weekly depth, scored by the same rule as the weeks.
        "projection": {
            "inches": plan_cfg.get("expected_in_per_week"),
            "verdict": events_mod.band_verdict(
                plan_cfg.get("expected_in_per_week"), etc_band)},
        "regime_bands": plan_cfg.get("regimes") or [],
    }
    cycle = {k: _cycle(daily, k, regime_since, regime_until) for k in ("tom", "pep")}
    wx_daily = weather_mod.daily(wx_hourly, config["location"]["lat"]) if wx_hourly else []
    wx = _weather_analysis(daily, wx_daily, plan_cfg.get("regimes"))

    t0, t1 = series[0]["dt"], series[-1]["dt"]
    interval_hr = round(interval / 60, 2)
    interval_hr_disp = int(interval_hr) if interval_hr == int(interval_hr) else interval_hr

    DATA = {
        "series": [{"t": p["dt"].strftime("%Y-%m-%dT%H:%M"),
                    "tom": p["tom"], "pep": p["pep"]} for p in series],
        # Native-resolution tail for the pulse-detail panel. Deliberately only
        # the last 7 days: full history at 5 min is ~14.6k points, which bloats
        # the published page for no analytical gain -- pulse shape is a
        # short-horizon question, and the long view is the hourly `series`.
        "series5": [{"t": r["dt"].strftime("%Y-%m-%dT%H:%M"),
                     "tom": r["tom"], "pep": r["pep"]}
                    for r in readings
                    if (readings[-1]["dt"] - r["dt"]).days < 7],
        "daily": daily,
        "split": split,
        "partition": partition,
        "water": water,
        "native": native,
        "health": health,
        # None when inputs/weather.csv is absent.
        "weather": wx,
        "stats": {
            "range_start": t0.strftime("%Y-%m-%d %H:%M"),
            "range_end": t1.strftime("%Y-%m-%d %H:%M"),
            # Days with no export at all. Named on the page and in the build
            # log; _water() spreads the draw that spans them (#8).
            "missing_days": parse_ecowitt.missing_days(readings),
            "n": len(series),
            "interval_hr": interval_hr_disp,
            "tom": tom_stats,
            "pep": pep_stats,
        },
    }

    loc = config["location"]
    window_label = f"{_fmt_md(t0)} – {_fmt_md(t1)}"
    footer_meta = (f"{interval_hr_disp}-hour interval, {window_label} {t1.year} "
                   f"({len(series)} readings)")

    CFG = {
        "lat": loc["lat"], "lon": loc["lon"],
        "window_label": window_label,
        "interval_label": f" @ {interval_hr_disp} h",
        # Derived thresholds, replacing the retired `setpoints` key. See
        # probes._bands_provenance in config.json and lib/derive_bands.py.
        "bands": {"tom": tom_cfg["bands"], "pep": pep_cfg["bands"]},
        "plan": config["plan"],
        "gauge": {
            "tom": _gauge_for("tom", tom_cfg, tom_stats, cycle["tom"], split["tom"], partition["tom"]),
            "pep": _gauge_for("pep", pep_cfg, pep_stats, cycle["pep"], split["pep"], partition["pep"]),
        },
        "footer_meta": footer_meta,
        "location_desc": loc["name"],
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M %Z").strip(),
    }
    CFG["advice"] = _advice(DATA, config, cycle)
    return DATA, CFG
