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
def _probe_stats(series, key, setpoint):
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
    return {
        "mean": _mean(vals),
        "min": round(min(vals), 1),
        "max": round(max(vals), 1),
        "std": _std(vals),
        "median": round(statistics.median(vals), 1),
        "setpoint": setpoint,
        "last": last,
    }


def _daily(series):
    days: dict[str, dict] = {}
    for p in series:
        d = p["dt"].strftime("%Y-%m-%d")
        days.setdefault(d, {"tom": [], "pep": []})
        if p["tom"] is not None:
            days[d]["tom"].append(p["tom"])
        if p["pep"] is not None:
            days[d]["pep"].append(p["pep"])
    out = []
    for d in sorted(days):
        t, pe = days[d]["tom"], days[d]["pep"]
        if not t and not pe:
            continue
        out.append({
            "date": d,
            "tom_mean": _mean(t), "tom_min": round(min(t), 1) if t else None, "tom_max": round(max(t), 1) if t else None,
            "pep_mean": _mean(pe), "pep_min": round(min(pe), 1) if pe else None, "pep_max": round(max(pe), 1) if pe else None,
        })
    return out


def _weekly(series):
    if not series:
        return []
    d0 = series[0]["dt"].replace(hour=0, minute=0, second=0, microsecond=0)
    weeks: dict[int, dict] = {}
    for p in series:
        wi = (p["dt"] - d0).days // 7
        w = weeks.setdefault(wi, {"tom": [], "pep": [], "dts": []})
        w["dts"].append(p["dt"])
        if p["tom"] is not None:
            w["tom"].append(p["tom"])
        if p["pep"] is not None:
            w["pep"].append(p["pep"])
    out = []
    for i, wi in enumerate(sorted(weeks), start=1):
        w = weeks[wi]
        lo, hi = min(w["dts"]), max(w["dts"])
        out.append({
            "idx": i,
            "label": f"{_fmt_md(lo)}–{hi.strftime('%-d') if lo.month == hi.month else _fmt_md(hi)}",
            "tom_mean": _mean(w["tom"]), "tom_min": round(min(w["tom"]), 1), "tom_max": round(max(w["tom"]), 1), "tom_std": _std(w["tom"]),
            "pep_mean": _mean(w["pep"]), "pep_min": round(min(w["pep"]), 1), "pep_max": round(max(w["pep"]), 1), "pep_std": _std(w["pep"]),
            "n": len(w["dts"]),
        })
    return out


def _diurnal(series):
    by_h: dict[int, dict] = {}
    for p in series:
        h = p["dt"].hour
        b = by_h.setdefault(h, {"tom": [], "pep": []})
        if p["tom"] is not None:
            b["tom"].append(p["tom"])
        if p["pep"] is not None:
            b["pep"].append(p["pep"])
    return [{"h": h, "tom": _mean(by_h[h]["tom"]), "pep": _mean(by_h[h]["pep"])}
            for h in sorted(by_h)]


def _trend(daily, setpoint):
    pts = [(i, r["tom_mean"]) for i, r in enumerate(daily) if r["tom_mean"] is not None]
    if len(pts) < 2:
        return {"slope": 0, "intercept": daily[0]["tom_mean"] if daily else 0, "r2": 0,
                "per_week": 0, "start_fit": None, "end_fit": None,
                "set_reach_date": None, "days_from_start": 0, "last_daily": None}
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    slope, intercept, r2 = _linreg(xs, ys)
    d0 = datetime.strptime(daily[0]["date"], "%Y-%m-%d")
    last_x = xs[-1]
    set_reach = None
    if slope < -1e-6:
        x_reach = (setpoint - intercept) / slope
        if x_reach > last_x:
            set_reach = (d0 + timedelta(days=x_reach)).strftime("%Y-%m-%d")
    return {
        "slope": round(slope, 3), "intercept": round(intercept, 1), "r2": round(r2, 3),
        "per_week": round(slope * 7, 1),
        "start_fit": round(intercept, 1),
        "end_fit": round(intercept + slope * last_x, 1),
        "set_reach_date": set_reach,
        "days_from_start": round(last_x, 1),
        "last_daily": ys[-1],
    }


def _distribution(series, key, setpoint, bin_w):
    vals = [p[key] for p in series if p[key] is not None]
    lo = int(min(vals) // bin_w * bin_w)
    hi = int(-(-max(vals) // bin_w) * bin_w)  # ceil to bin
    hist = []
    b = lo
    while b < hi:
        cnt = sum(1 for v in vals if b <= v < b + bin_w)
        hist.append({"bin": f"{b}-{b + bin_w}", "lo": b, "hi": b + bin_w, "count": cnt})
        b += bin_w
    n = len(vals)
    below = sum(1 for v in vals if v < setpoint)
    return {
        "pct_below": round(below / n * 100, 1),
        "pct_above": round((n - below) / n * 100, 1),
        "hist": hist,
    }


def _water(readings, since=None):
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

    # daily draw = end-of-day cumulative minus previous day's end (clamp >= 0)
    draws: dict[str, float] = {}
    prev_cum = None
    for d in order:
        c = last_cum[d]
        draws[d] = max(0.0, c - prev_cum) if prev_cum is not None else 0.0
        prev_cum = c

    if first_flow_dt is None:
        # meter present but no flow yet
        return {
            "meter_online": meter_online, "first_flow": None,
            "end": wr[-1][0].strftime("%Y-%m-%d %H:%M"),
            "total_L": 0.0, "active_days": 0, "avg_active_L": 0.0, "daily": [],
        }

    first_flow_date = first_flow_dt.strftime("%Y-%m-%d")
    display_days = [d for d in order if d >= first_flow_date]
    cum = 0.0
    daily_out = []
    total = 0.0
    for d in display_days:
        cum += draws[d]
        total += draws[d]
        daily_out.append({"date": d, "cum": round(cum, 1), "draw": round(draws[d], 1)})
    active = sum(1 for d in display_days if draws[d] > 0)

    # Average since the current schedule took effect, separate from the all-time
    # one. The whole-window mean is dominated by the OLD regime -- it reported
    # ~140 L/day for days after the bed had actually dropped to ~78 -- so advice
    # must quote the current-regime figure. Anchoring on the plan's effective
    # date rather than a rolling 7 days matters: a fixed window straddles the
    # change and silently mixes both regimes.
    if since:
        recent_days = [d for d in display_days if d >= since and draws[d] > 0]
    else:
        recent_days = [d for d in display_days[-7:] if draws[d] > 0]
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
        "daily": daily_out,
    }


# ----------------------------------------------------------------------------- gauges narrative
def _advice(data, config):
    """Short, current-state-aware 'what to do next' lines for the findings box."""
    tcfg = config["probes"]["tomato"]
    pcfg = config["probes"]["pepper"]
    S = data["stats"]
    tr = data["trend"]
    tsp, psp = tcfg["setpoint"], pcfg["setpoint"]
    t_last, p_last = S["tom"]["last"], S["pep"]["last"]
    t_hi = tcfg["gauge"]["idealHi"]
    p_hi = pcfg["gauge"]["idealHi"]
    pw = tr["per_week"]

    # Tomatoes. The overnight minimum is the honest retention read: it is the
    # pre-irrigation trough, so unlike the 24 h mean it is not inflated by
    # whatever was applied that morning.
    # Median, not min: the last few days typically contain both a transitional
    # day just after a schedule change and the odd meter-batching spike, and a
    # bare min lets either one dictate the advice.
    daily_rows = data["daily"][-5:]
    ovn = [r["tom_min"] for r in daily_rows if r["tom_min"] is not None]
    ovn_lo = round(statistics.median(ovn)) if ovn else None
    headroom = round(ovn_lo - tsp) if ovn_lo is not None else None

    if t_last is not None and t_last < tsp - 2:
        drift = f" and still drying ({pw} %/wk)" if pw < -0.3 else " and roughly flat"
        tom = (f"the bed is now ~{t_last}% (24 h avg), below the {tsp}% setpoint{drift} — "
               f"lengthen the dose or add a cycle, then re-check next export.")
    elif t_last is not None and t_last > t_hi:
        if headroom is not None and headroom >= 3:
            tom = (f"sitting ~{t_last}% (24 h avg), above the {t_hi}% top of band, with a "
                   f"median daily trough of {ovn_lo}% over the last {len(ovn)} days — "
                   f"{headroom} points above the {tsp}% setpoint. There is room to trim, "
                   f"but only {len(ovn)} days of data since the schedule changed and at "
                   f"below-average evaporative demand. Hold one more week, then trim a "
                   f"single pulse if the trough still clears setpoint on a hot week.")
        else:
            tom = (f"sitting ~{t_last}%, above the healthy band — ease back on watering "
                   f"and let it dry down.")
    elif tr["set_reach_date"]:
        d = datetime.strptime(tr["set_reach_date"], "%Y-%m-%d").strftime("%b %-d")
        tom = f"trend projects the {tsp}% setpoint around {d} — hold the current timer and watch the daily min."
    else:
        tom = f"sitting ~{t_last}%, near the {tsp}% setpoint — hold the timer and watch the daily min."

    # Peppers
    if p_last is None:
        pep = "no recent probe reading — check the sensor."
    elif p_last < psp:
        pep = f"~{p_last}%, below the {psp}% setpoint — nudge water up."
    elif p_last > p_hi:
        pep = f"~{p_last}%, above the healthy band — ease back slightly."
    else:
        pep = "in band and stable — no change."

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
            shift = (f" — down from a {older} L lifetime average, which still reflects "
                     f"the pre-{plan.get('runs_effective', 'change')} flood dosing")
        water = (f"metered ~{w['recent_avg_L']} L per watering day over the last "
                 f"{w['recent_n']} active days on the tomato bed's {plan_desc}{shift}. "
                 f"Read as delivered volume, not against a target.")
    else:
        water = (f"metered ~{w['avg_active_L']} L per watering day on the tomato bed's "
                 f"{plan_desc} — read as delivered volume, not against a target.")

    return {"tom": tom, "pep": pep, "water": water}


def _gauge_for(pkey, pcfg, stats, trend=None):
    g = dict(pcfg["gauge"])
    sp = pcfg["setpoint"]
    last = stats["last"]
    over = round(last - sp)
    over_txt = f"{'+' if over >= 0 else ''}{over} vs setpoint"

    if trend is not None and trend["per_week"] <= -0.6:
        state = "DRYING DOWN"
        vclass = "v-trend"
    elif trend is not None and trend["per_week"] >= 0.6:
        state = "WETTING"
        vclass = "v-wet"
    elif g["idealLo"] <= last <= g["idealHi"]:
        state = "IN BAND"
        vclass = "v-ok"
    elif last > g["idealHi"]:
        state = "MOIST"
        vclass = "v-wet"
    else:
        state = "DRY"
        vclass = "v-ok"

    g["verdict"] = f"{state} · {over_txt}"
    g["vclass"] = vclass

    if trend is not None:
        note = (f"Mean {stats['mean']}%, latest {last}% (range {stats['min']}–{stats['max']}%). "
                f"Trending {trend['per_week']} %/wk vs the {sp}% setpoint (R²={trend['r2']}).")
    else:
        note = (f"Mean {stats['mean']}% in a ±{stats['std']} band (range {stats['min']}–{stats['max']}%), "
                f"latest {last}%, holding {'above' if last >= sp else 'below'} the {sp}% setpoint.")
    g["note"] = note
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


def _trust_from(config, key, _cache={}):
    """
    Date after which a channel's readings are trustworthy: the day AFTER its
    most recent calibration break. Set by _sensor_health as a side effect, so
    this just reads what that computed; returns None if there was no break.
    """
    return _cache.get(key)


def _sensor_health(readings, config):
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
    """
    hc = config.get("health", {})
    v_tol = hc.get("volt_tolerance", 0.08)
    range_frac = hc.get("range_collapse_frac", 0.35)
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
        last_day = days[-1]["date"]
        for a, b in zip(days, days[1:]):
            if a["v"] is not None and b["v"] is not None and abs(b["v"] - a["v"]) > v_tol:
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

        # --- AD dynamic range: compare latest vs trailing median of prior days
        rngs = [d["ad_range"] for d in days if d["ad_range"] is not None]
        if len(rngs) >= 8:
            base = statistics.median(rngs[:-3][-14:]) if len(rngs) > 6 else None
            recent = statistics.median(rngs[-3:])
            if base and base > 0 and recent < base * range_frac:
                flags.append({
                    "level": "bad",
                    "msg": (f"daily AD range collapsed to ~{recent:.0f} counts vs a "
                            f"~{base:.0f}-count norm — probe is barely resolving the "
                            f"diurnal cycle. Suspect a failing sensor or lost soil "
                            f"contact; verify with an air-vs-water span test."),
                })

        # Publish the trust boundary for other panels (see _trust_from): the
        # date of the most recent calibration break, recent or historical.
        all_breaks = [b["date"] for b in breaks] + \
                     [d["date"] for d in days for f in flags
                      if d["date"] in f.get("msg", "")]
        if all_breaks:
            _trust_from.__defaults__[0][key] = max(all_breaks)

        out[key] = {
            "label": label,
            "days": days[-14:],
            "v_last": last_v,
            "ad_range_last": days[-1]["ad_range"],
            "trust_from": max(all_breaks) if all_breaks else None,
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


def _weather_analysis(daily_soil, wx_daily):
    """
    Join daily soil stats to daily weather and answer the question the raw trend
    cannot: was the bed drying because it was HOT, or because it was UNDERWATERED?

    The headline number is normalised dry-down -- points of moisture lost per mm
    of ET0. Raw dry-down conflates weather with irrigation, so a cool week and a
    heavily-watered week look identical in the trend line. Dividing by
    evaporative demand removes the weather term, and what is left moves only when
    irrigation or soil condition changes.
    """
    if not wx_daily:
        return None
    wx_by = {w["date"]: w for w in wx_daily}
    rows = []
    for r in daily_soil:
        w = wx_by.get(r["date"])
        if not w:
            continue
        draw = (round(r["tom_max"] - r["tom_min"], 1)
                if r["tom_max"] is not None and r["tom_min"] is not None else None)
        pdraw = (round(r["pep_max"] - r["pep_min"], 1)
                 if r["pep_max"] is not None and r["pep_min"] is not None else None)
        et0 = w["et0_mm"]
        rows.append({
            "date": r["date"],
            "tmax_f": w["tmax_f"], "vpd_mean": w["vpd_mean"],
            "et0_mm": et0, "precip_mm": w["precip_mm"],
            "tom_mean": r["tom_mean"], "pep_mean": r["pep_mean"],
            "tom_draw": draw, "pep_draw": pdraw,
            # points of moisture per mm of evaporative demand
            "tom_draw_per_et0": (round(draw / et0, 2)
                                 if draw is not None and et0 else None),
            "pep_draw_per_et0": (round(pdraw / et0, 2)
                                 if pdraw is not None and et0 else None),
        })
    if len(rows) < 3:
        return None

    et0s = [r["et0_mm"] for r in rows]
    corr = {
        "tom_draw_vs_et0": _pearson(et0s, [r["tom_draw"] for r in rows]),
        "pep_draw_vs_et0": _pearson(et0s, [r["pep_draw"] for r in rows]),
        "tom_mean_vs_tmax": _pearson([r["tmax_f"] for r in rows],
                                     [r["tom_mean"] for r in rows]),
        "pep_mean_vs_tmax": _pearson([r["tmax_f"] for r in rows],
                                     [r["pep_mean"] for r in rows]),
    }

    # Weekly normalised dry-down, most recent last. This is the series to watch:
    # a rise means the bed is losing more per unit of demand, i.e. genuinely
    # drying out rather than merely enduring a hot spell.
    weeks = []
    chunk = [r for r in rows if r["tom_draw_per_et0"] is not None]
    for i in range(0, len(chunk), 7):
        blk = chunk[i:i + 7]
        if len(blk) < 3:
            continue
        weeks.append({
            "start": blk[0]["date"], "end": blk[-1]["date"], "n": len(blk),
            "et0_mean": round(statistics.fmean([b["et0_mm"] for b in blk]), 2),
            "tmax_mean": round(statistics.fmean([b["tmax_f"] for b in blk]), 1),
            "draw_mean": round(statistics.fmean([b["tom_draw"] for b in blk]), 1),
            "norm_draw": round(statistics.fmean(
                [b["tom_draw_per_et0"] for b in blk]), 2),
        })

    sources = {w["et0_source"] for w in wx_daily}

    # Narrative, generated from the numbers rather than asserted. The template
    # previously hardcoded "ET0 and peak temperature track the pepper dry-down
    # in the expected direction" -- which happens not to be true for this data.
    et0_vals = [r["et0_mm"] for r in rows if r["et0_mm"] is not None]
    et0_spread = (max(et0_vals) - min(et0_vals)) if et0_vals else 0
    et0_mean = statistics.fmean(et0_vals) if et0_vals else 0
    r_tom = corr["tom_draw_vs_et0"]

    def _mag(r):
        if r is None:
            return "unmeasurable"
        a = abs(r)
        return ("strong" if a >= .6 else "moderate" if a >= .35
                else "weak" if a >= .15 else "negligible")

    # Two different spreads, two different conclusions -- keep them apart.
    # DAILY ET0 varies plenty, so a near-zero daily correlation is a real
    # finding (irrigation dominates the swing), not a range-restriction artefact.
    # WEEKLY mean ET0 is nearly flat, which is what licenses week-over-week
    # comparison without worrying that a hot spell explains the difference.
    wk_et0 = [w["et0_mean"] for w in weeks] if weeks else []
    bits = [
        f"Over {len(rows)} days, daily evaporative demand varied substantially "
        f"(ET₀ {min(et0_vals):.1f}–{max(et0_vals):.1f} mm/day, mean {et0_mean:.1f}), "
        f"yet ET₀ vs tomato daily dry-down is {_mag(r_tom)} (r={r_tom}). With that "
        f"much variation in the predictor, a flat correlation is a real result: "
        f"the daily swing is set by the irrigation pulse, not by the weather."
    ]
    if wk_et0:
        bits.append(
            f"Weekly mean demand, by contrast, barely moved "
            f"({min(wk_et0):.1f}–{max(wk_et0):.1f} mm/day), so week-over-week "
            f"changes below are not a hot-spell artefact."
        )
    if len(weeks) >= 3:
        peak = max(weeks, key=lambda w: w["norm_draw"])
        first, last = weeks[0], weeks[-1]
        prev = weeks[-2]
        arrow = "falling" if last["norm_draw"] < prev["norm_draw"] else "rising"
        bits.append(
            f"Normalised dry-down — points of moisture lost per mm of ET₀, which "
            f"strips the weather term out — ran {first['norm_draw']} at the start, "
            f"peaked at {peak['norm_draw']} ({peak['start']}), and is now "
            f"{last['norm_draw']} and {arrow}."
        )
        bits.append(
            "<em>Caveat:</em> daily dry-down is max−min, so it also shrinks when "
            "less water is applied per event — a smaller pulse makes a smaller "
            "peak. Treat it as directional and lean on the overnight minimum for "
            "a clean read on retention."
        )
    if sources == {"hargreaves"}:
        bits.append(
            "ET₀ here is the cruder Hargreaves estimate — inputs/weather.csv "
            "predates the FAO-56 column. Re-run ./pull_weather_data.sh --force "
            "for the better figure."
        )

    return {
        "narrative": " ".join(bits),
        "et0_spread": round(et0_spread, 2),
        "daily": rows,
        "corr": corr,
        "weekly_norm": weeks,
        "et0_source": "fao56" if sources == {"fao56"} else sorted(sources)[0],
        "n_days": len(rows),
        "span": [rows[0]["date"], rows[-1]["date"]],
    }


# ----------------------------------------------------------------------------- top-level
def build(readings, config, wx_hourly=None):
    interval = config["sample_interval_minutes"]
    series = resample(readings, interval)

    tom_cfg = config["probes"]["tomato"]
    pep_cfg = config["probes"]["pepper"]

    tom_stats = _probe_stats(series, "tom", tom_cfg["setpoint"])
    pep_stats = _probe_stats(series, "pep", pep_cfg["setpoint"])
    daily = _daily(series)
    weekly = _weekly(series)
    trend = _trend(daily, tom_cfg["setpoint"])
    water = _water(readings, since=config.get("plan", {}).get("runs_effective"))

    # ---- native-resolution analytics (see lib/events.py for why these do NOT
    # use `series`: hourly resampling erases the pulse structure that every
    # irrigation decision in plan.schedule_log was actually made from).
    plan_cfg = config.get("plan", {})
    bed_cfg = config.get("bed", {})
    manual = plan_cfg.get("manual_events") or []
    ev_tom = events_mod.tag_manual(events_mod.detect_events(readings, "tom"), manual)
    ev_pep = events_mod.tag_manual(events_mod.detect_events(readings, "pep"), manual)
    ext_tom = events_mod.daily_extremes(readings, "tom", ev_tom)
    ext_pep = events_mod.daily_extremes(readings, "pep", ev_pep)
    sched_tom = [r["time"] for r in plan_cfg.get("runs", [])]
    sched_pep = [plan_cfg["pepper_time"]] if plan_cfg.get("pepper_time") else []
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
                ev_tom, sched_tom,
                since=_scope(plan_cfg.get("runs_time_effective")
                             or plan_cfg.get("runs_effective"), readings)),
            "pep": events_mod.onset_check(
                ev_pep, sched_pep,
                since=_scope(max([d for d in (plan_cfg.get("pepper_time_effective")
                                              or plan_cfg.get("pepper_effective"),
                                              _trust_from(config, "pep")) if d] or [None]),
                             readings)),
        },
        "regimes": events_mod.regime_summary(
            plan_cfg.get("regimes"), ext_tom, water.get("daily") if water else None),
        "budget": events_mod.water_budget(
            water.get("daily") if water else None,
            bed_cfg.get("liters_per_inch_of_water"),
            (bed_cfg.get("target") or {}).get("august_etc_in_per_week")),
        "regime_bands": plan_cfg.get("regimes") or [],
    }
    wx_daily = weather_mod.daily(wx_hourly, config["location"]["lat"]) if wx_hourly else []
    wx = _weather_analysis(daily, wx_daily)

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
        "weekly": weekly,
        "diurnal": _diurnal(series),
        "trend": trend,
        "water": water,
        "native": native,
        "health": _sensor_health(readings, config),
        # None when inputs/weather.csv is absent -- the template falls back to
        # its client-side Open-Meteo fetch in that case.
        "weather": wx,
        "stats": {
            "range_start": t0.strftime("%Y-%m-%d %H:%M"),
            "range_end": t1.strftime("%Y-%m-%d %H:%M"),
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
        "wx_start": t0.strftime("%Y-%m-%d"),
        "wx_end": t1.strftime("%Y-%m-%d"),
        "setpoints": {"tom": tom_cfg["setpoint"], "pep": pep_cfg["setpoint"]},
        "plan": config["plan"],
        "gauge": {
            "tom": _gauge_for("tom", tom_cfg, tom_stats, trend),
            "pep": _gauge_for("pep", pep_cfg, pep_stats, None),
        },
        "footer_meta": footer_meta,
        "location_desc": loc["name"],
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M %Z").strip(),
    }
    CFG["advice"] = _advice(DATA, config)
    return DATA, CFG
