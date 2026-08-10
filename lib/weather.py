"""
weather.py
==========
Read inputs/weather.csv (written by lib/fetch_weather.py) and reduce it to the
daily evaporative-demand numbers the analysis needs.

Why this exists
---------------
The dashboard template fetches Open-Meteo from the BROWSER and computes its
ET0-vs-dry-down correlation there. That has two costs: the correlation panel
dies whenever the page is opened without internet, and nothing server-side --
not the trend, not the findings text -- can see the weather at all. So a cool
week and a heavy-irrigation week produce an identical-looking dry-down trend.

Reading the cached CSV here fixes both: the numbers get baked into the HTML, and
analyze.py can normalise dry-down against evaporative demand.

Deliberately stdlib-only (csv + math). fetch_weather.py owns the pandas/SDK
dependency; the weekly build must stay openpyxl-only.
"""

from __future__ import annotations

import csv
import math
import os
import statistics
from datetime import datetime


def _f(row, key):
    v = row.get(key, "")
    if v in ("", None):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load(inputs_dir: str, filename: str = "weather.csv"):
    """
    Return hourly weather records, or None if the cache is absent.

    None (rather than []) is meaningful: it lets every caller distinguish "no
    weather cached, fall back to the client-side fetch" from "cached but empty".
    """
    path = os.path.join(inputs_dir, filename)
    if not os.path.exists(path):
        return None
    out = []
    with open(path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ts = row.get("datetime")
            if not ts:
                continue
            try:
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            out.append({
                "dt": dt,
                "temp_c": _f(row, "temp_c"),
                "temp_f": _f(row, "temp_f"),
                "rh": _f(row, "relative_humidity_2m"),
                "precip": _f(row, "precipitation"),
                "cloud": _f(row, "cloud_cover"),
                "wind": _f(row, "wind_speed_10m"),
                # Present only if fetch_weather.py requested them. Optional on
                # purpose: an older weather.csv still works, via Hargreaves below.
                "et0": _f(row, "et0_fao_evapotranspiration"),
                "rad": _f(row, "shortwave_radiation"),
            })
    return out or None


# ----------------------------------------------------------------------------- physics
def _ra_mm(lat_deg: float, doy: int) -> float:
    """
    Extraterrestrial radiation for a latitude and day-of-year, expressed in
    mm/day of equivalent evaporation (FAO-56 eq. 21, divided by latent heat
    2.45 MJ/kg). Depends only on geometry -- no measurements involved.
    """
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    dec = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    x = max(-1.0, min(1.0, -math.tan(phi) * math.tan(dec)))
    ws = math.acos(x)
    ra = ((24 * 60) / math.pi) * 0.0820 * dr * (
        ws * math.sin(phi) * math.sin(dec)
        + math.cos(phi) * math.cos(dec) * math.sin(ws))
    return ra / 2.45


def _hargreaves(tmax_c, tmin_c, tmean_c, lat, doy):
    """
    Hargreaves-Samani ET0 (mm/day). Fallback for when the CSV has no FAO-56 ET0
    column. Needs only temperature extremes, so it works with any weather.csv,
    but it is the cruder estimate -- prefer the API's et0_fao_evapotranspiration.
    """
    if None in (tmax_c, tmin_c, tmean_c) or tmax_c < tmin_c:
        return None
    return round(0.0023 * _ra_mm(lat, doy) * (tmean_c + 17.8)
                 * math.sqrt(tmax_c - tmin_c), 3)


def _vpd_kpa(temp_c, rh):
    """Vapour-pressure deficit (kPa) -- the 'thirstiness' of the air."""
    if temp_c is None or rh is None:
        return None
    es = 0.6108 * math.exp(17.27 * temp_c / (temp_c + 237.3))
    return es * (1 - rh / 100.0)


# ----------------------------------------------------------------------------- daily roll-up
def daily(hourly, lat: float):
    """
    Collapse hourly records into one row per local calendar day.

    et0_source is reported per-day so the dashboard can be honest about which
    estimate it is showing rather than silently mixing the two.
    """
    if not hourly:
        return []
    by_day: dict[str, list] = {}
    for r in hourly:
        by_day.setdefault(r["dt"].strftime("%Y-%m-%d"), []).append(r)

    out = []
    for d in sorted(by_day):
        rows = by_day[d]
        temps = [r["temp_c"] for r in rows if r["temp_c"] is not None]
        if not temps:
            continue
        rhs = [r["rh"] for r in rows if r["rh"] is not None]
        et0s = [r["et0"] for r in rows if r["et0"] is not None]
        precs = [r["precip"] for r in rows if r["precip"] is not None]
        rads = [r["rad"] for r in rows if r["rad"] is not None]
        vpds = [v for v in (_vpd_kpa(r["temp_c"], r["rh"]) for r in rows)
                if v is not None]

        tmax, tmin = max(temps), min(temps)
        tmean = statistics.fmean(temps)
        doy = datetime.strptime(d, "%Y-%m-%d").timetuple().tm_yday

        # Prefer the API's FAO-56 hourly ET0 summed over the day; fall back to
        # Hargreaves only when that column is absent from the CSV.
        if et0s:
            et0, src = round(sum(et0s), 3), "fao56"
        else:
            et0, src = _hargreaves(tmax, tmin, tmean, lat, doy), "hargreaves"

        out.append({
            "date": d,
            "tmax_f": round(tmax * 9 / 5 + 32, 1),
            "tmin_f": round(tmin * 9 / 5 + 32, 1),
            "tmean_f": round(tmean * 9 / 5 + 32, 1),
            "rh_min": round(min(rhs), 1) if rhs else None,
            "rh_mean": round(statistics.fmean(rhs), 1) if rhs else None,
            "vpd_mean": round(statistics.fmean(vpds), 3) if vpds else None,
            "vpd_max": round(max(vpds), 3) if vpds else None,
            "et0_mm": et0,
            "et0_source": src,
            "precip_mm": round(sum(precs), 2) if precs else 0.0,
            "rad_max": round(max(rads), 1) if rads else None,
            "hours": len(rows),
        })
    return out
