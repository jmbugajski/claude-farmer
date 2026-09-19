#!/usr/bin/env python3
"""
fetch_weather.py
================
Pull real ambient weather for the garden from the Open-Meteo API and cache it
to inputs/weather.csv, so the ANALYSIS layer has access to it -- not just the
client-side chart in the dashboard.

Why this exists
---------------
The dashboard fetches Open-Meteo from the browser when the page is opened, so
weather never reaches analyze.py. Without this, anyone analysing the data is
tempted to reach for the "[WFC01] Water Flow Temperature" column in the EcoWitt
exports, which is the METER'S OWN BODY TEMPERATURE sitting in direct sun -- it
runs 110-123 F while real Santa Clara ambient is 30-40 F lower. That column is
not ambient and must never be used as a weather proxy.

Run this yourself (it needs network access that the agent sandbox does not have).
Homebrew Python is PEP 668 "externally managed", so install into a venv:

    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    .venv/bin/python lib/fetch_weather.py

Date range defaults to the span of the EcoWitt exports already in inputs/, so
it always lines up with the soil data. Override with --start / --end.

Two endpoints, one series (#3, 2026-09-19)
------------------------------------------
The forecast endpoint reaches back ~92 days and returns NaN, not an error, for
hours before that. This script used to skip NaN rows as "future hours", so by
2026-09-19 it had silently dropped 06-29 -> 07-08 15:00 (231 hours) and still
exited 0. It now pulls the archive endpoint for the whole span and the forecast
endpoint for the recent tail, prefers archive wherever it has a value, and
REFUSES to write a series that does not cover the requested window.

Archive is preferred, rather than used only as back-fill, so that a past day's
value comes from one model and stops moving. The two disagree (archive ET0 ran
3.5% above forecast over 73 shared days); forecast-preferred would flip a
week of days from one to the other on every pull as the 92-day window advances.

Output: inputs/weather.csv -- plain CSV, local time, one row per hour. Written
as CSV on purpose so analyze.py can read it with the stdlib csv module and the
build pipeline keeps its openpyxl-only dependency footprint.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Order matters -- Open-Meteo returns variables positionally.
HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    # FAO-56 reference evapotranspiration. This is the important one: it is the
    # physically correct measure of how hard the air was pulling water out of
    # the soil, and analyze.py normalises dry-down against it. Without this
    # column weather.py falls back to a cruder Hargreaves estimate.
    "et0_fao_evapotranspiration",
    "shortwave_radiation",
    "precipitation",
    "cloud_cover",
    "wind_speed_10m",
    "wind_gusts_10m",
]

# A row the analysis cannot use is a hole. ET0 is what derive_bands.py and the
# partition are keyed on, so an hour with temperature but no ET0 counts as one.
REQUIRED = ("temperature_2m", "et0_fao_evapotranspiration")

# How far back the forecast request reaches. It only has to bridge the archive's
# publication lag (~5 days); 30 is margin, and well inside the ~92-day limit
# past which the endpoint stops answering.
FORECAST_TAIL_DAYS = 30


def _usable(row) -> bool:
    return row is not None and all(row[k] == row[k] for k in REQUIRED)  # NaN != NaN


def merge_hourly(archive: dict, forecast: dict) -> dict:
    """
    {epoch_seconds: {var: value}} from each endpoint -> one series, usable rows
    only. Archive wins wherever its row is usable; forecast fills the rest.
    Keyed on epoch seconds, not local strings, so a DST change is neither a
    duplicate nor a gap.
    """
    out = {}
    for ts in set(archive) | set(forecast):
        a = archive.get(ts)
        row = a if _usable(a) else forecast.get(ts)
        if _usable(row):
            out[ts] = row
    return out


def coverage_error(rows: dict, first: int, last_min: int, step: int = 3600):
    """
    None if `rows` starts at `first`, has no missing hour, and reaches at least
    `last_min`; otherwise a message naming the first thing wrong. Hours after
    the last usable row are not holes -- those are the future.
    """
    if not rows:
        return "no usable rows at all"
    lo, hi = min(rows), max(rows)
    if lo != first:
        return f"series starts {(lo - first) // step} hours after the requested start"
    for ts in range(lo, hi + step, step):
        if ts not in rows:
            return f"hour {(ts - first) // step} after the requested start is missing"
    if hi < last_min:
        return f"series ends {(last_min - hi) // step} hours before the requested end date"
    return None


def _window_from_inputs(inputs_dir: str):
    """Infer (start, end) ISO dates from the EcoWitt export filenames."""
    dates = []
    for fn in os.listdir(inputs_dir):
        if not fn.lower().endswith(".xlsx"):
            continue
        for stamp in re.findall(r"(\d{8})\d{4}", fn):
            dates.append(f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}")
    if not dates:
        return None, None
    return min(dates), max(dates)


def main() -> int:
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    loc = cfg["location"]

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inputs", default=os.path.join(ROOT, "inputs"))
    ap.add_argument("--out", default=None, help="default: <inputs>/weather.csv")
    ap.add_argument("--start", default=None, help="YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD")
    args = ap.parse_args()

    out_path = args.out or os.path.join(args.inputs, "weather.csv")

    start, end = args.start, args.end
    if not (start and end):
        s, e = _window_from_inputs(args.inputs)
        start = start or s
        end = end or e
    if not (start and end):
        print("No .xlsx exports found in inputs/ and no --start/--end given.",
              file=sys.stderr)
        return 1

    try:
        import openmeteo_requests
        import pandas as pd
        import requests_cache
        from retry_requests import retry
    except ImportError as exc:
        # Nearly always an interpreter/pip mismatch on macOS: `pip` belongs to a
        # different Python than the `python3` on PATH, so "Requirement already
        # satisfied" and "Missing dependency" are both true, of different Pythons.
        v = sys.version_info
        print(
            f"Missing dependency: {exc.name}\n\n"
            f"  This script is running:  {sys.executable}\n"
            f"  Python version:          {v.major}.{v.minor}.{v.micro}\n\n"
            "If `pip install` just told you the package was already satisfied, it\n"
            "installed into a DIFFERENT Python than the one above. Install into\n"
            "this exact interpreter instead -- `-m pip` guarantees they match:\n\n"
            f"  {sys.executable} -m pip install openmeteo-requests requests-cache "
            "retry-requests numpy pandas\n",
            file=sys.stderr)
        return 1

    # Cache lives in the system temp dir, not the repo: keeps a stray sqlite
    # file out of git, and avoids "disk I/O error" when the repo sits on a
    # cloud-synced volume (Drive/Dropbox) that sqlite cannot lock properly.
    import tempfile
    session = requests_cache.CachedSession(
        os.path.join(tempfile.gettempdir(), "claude-farmer-openmeteo"),
        expire_after=3600)
    client = openmeteo_requests.Client(
        session=retry(session, retries=5, backoff_factor=0.2))

    import datetime as _dt

    def fetch(base, s, e):
        """-> ({epoch: {var: value}}, first_epoch, end_epoch, step, tz name)"""
        r = client.weather_api(base, params={
            "latitude": loc["lat"], "longitude": loc["lon"],
            "timezone": loc["timezone"], "hourly": HOURLY_VARS,
            "start_date": s, "end_date": e})[0]
        h = r.Hourly()
        cols = [h.Variables(i).ValuesAsNumpy() for i in range(len(HOURLY_VARS))]
        step = h.Interval()
        rows = {h.Time() + i * step: {n: float(c[i]) for n, c in zip(HOURLY_VARS, cols)}
                for i in range(len(cols[0]))}
        return rows, h.Time(), h.TimeEnd(), step, r.Timezone().decode()

    print(f"Open-Meteo: {loc['name']} ({loc['lat']}, {loc['lon']})  {start} -> {end}")
    try:
        archive, first, t_end, step, tzname = fetch(
            "https://archive-api.open-meteo.com/v1/archive", start, end)
    except Exception as exc:
        print(f"\nArchive request failed: {exc}\n"
              "If this is a network/allowlist error, run it from a machine with\n"
              "direct internet access -- the agent sandbox cannot reach this API.",
              file=sys.stderr)
        return 1

    # The forecast tail is a fill, not a requirement: if it fails, the coverage
    # check below decides whether what archive returned is enough on its own.
    forecast = {}
    fc_start = max(start, (_dt.date.today()
                           - _dt.timedelta(days=FORECAST_TAIL_DAYS)).isoformat())
    if fc_start <= end:
        try:
            forecast = fetch("https://api.open-meteo.com/v1/forecast", fc_start, end)[0]
        except Exception as exc:
            print(f"  forecast tail {fc_start} -> {end} failed ({exc}); "
                  "continuing on archive alone", file=sys.stderr)

    rows = merge_hourly(archive, forecast)
    # t_end is midnight AFTER the end date, so t_end - 24h is that day's first hour.
    err = coverage_error(rows, first, t_end - 24 * step, step)
    if err:
        print(f"\nREFUSING to write {out_path}: {err}.\n"
              f"  Requested {start} -> {end}; the existing file is untouched.",
              file=sys.stderr)
        return 1
    n_fc = sum(1 for ts in rows if not _usable(archive.get(ts)))

    index = pd.to_datetime(sorted(rows), unit="s", utc=True).tz_convert(tzname)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # Written beside the target and renamed, so a crash mid-write cannot leave a
    # short file that the next --check reads as the truth.
    tmp = out_path + ".tmp"
    with open(tmp, "w", newline="") as fh:
        w = csv.writer(fh)
        # temp_f is derived here so downstream code never has to guess units.
        w.writerow(["datetime", "temp_c", "temp_f"] + HOURLY_VARS[1:])
        for ts, when in zip(sorted(rows), index):
            r = rows[ts]
            tc = r["temperature_2m"]
            row = [when.strftime("%Y-%m-%d %H:%M"), round(tc, 2),
                   round(tc * 9 / 5 + 32, 2)]
            for name in HOURLY_VARS[1:]:
                v = r[name]
                row.append("" if v != v else round(v, 2))
            w.writerow(row)
    os.replace(tmp, out_path)

    tf = [r["temperature_2m"] * 9 / 5 + 32 for r in rows.values()]
    print(f"  {len(rows)} hourly rows -> {out_path}  "
          f"({len(rows) - n_fc} archive, {n_fc} forecast fill)")
    print(f"  ambient range {min(tf):.1f} - {max(tf):.1f} F  "
          f"(compare: WFC01 body temp in sun reads 110-123 F -- not ambient)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
