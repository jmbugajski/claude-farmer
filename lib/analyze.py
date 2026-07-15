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

import statistics
from datetime import datetime, timedelta


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


def _water(readings):
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
    return {
        "meter_online": meter_online,
        "first_flow": first_flow_dt.strftime("%Y-%m-%d %H:%M"),
        "end": wr[-1][0].strftime("%Y-%m-%d %H:%M"),
        "total_L": round(total, 1),
        "active_days": active,
        "avg_active_L": round(total / active, 1) if active else 0.0,
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

    # Tomatoes
    if t_last is not None and t_last < tsp - 2:
        drift = f" and still drying ({pw} %/wk)" if pw < -0.3 else " and roughly flat"
        tom = (f"the bed is now ~{t_last}% (24 h avg), below the {tsp}% setpoint{drift} — "
               f"lengthen the dose or add a cycle, then re-check next export.")
    elif t_last is not None and t_last > t_hi:
        tom = f"sitting ~{t_last}%, above the healthy band — ease back on watering and let it dry down."
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

    # Water
    w = data["water"]
    if not w or not w["first_flow"]:
        water = "no metered flow yet — confirm the WFC01 meter is paired and reporting."
    elif w["active_days"] < 14:
        water = ("let the WFC01 log a few more clean weeks before leaning on the L/day numbers, "
                 "but the every-other-day draw pattern is already visible.")
    else:
        water = (f"metered ~{w['avg_active_L']} L per watering day; "
                 f"compare against the {config['plan']['weekly_target']} L/wk target.")

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


# ----------------------------------------------------------------------------- top-level
def build(readings, config):
    interval = config["sample_interval_minutes"]
    series = resample(readings, interval)

    tom_cfg = config["probes"]["tomato"]
    pep_cfg = config["probes"]["pepper"]

    tom_stats = _probe_stats(series, "tom", tom_cfg["setpoint"])
    pep_stats = _probe_stats(series, "pep", pep_cfg["setpoint"])
    daily = _daily(series)
    weekly = _weekly(series)
    trend = _trend(daily, tom_cfg["setpoint"])
    water = _water(readings)

    t0, t1 = series[0]["dt"], series[-1]["dt"]
    interval_hr = round(interval / 60, 2)
    interval_hr_disp = int(interval_hr) if interval_hr == int(interval_hr) else interval_hr

    DATA = {
        "series": [{"t": p["dt"].strftime("%Y-%m-%dT%H:%M"),
                    "tom": p["tom"], "pep": p["pep"]} for p in series],
        "daily": daily,
        "weekly": weekly,
        "diurnal": _diurnal(series),
        "trend": trend,
        "water": water,
        "distribution": {
            "tom": _distribution(series, "tom", tom_cfg["setpoint"], tom_cfg["hist_bin"]),
            "pep": _distribution(series, "pep", pep_cfg["setpoint"], pep_cfg["hist_bin"]),
        },
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
