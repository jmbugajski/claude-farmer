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

Run this yourself (it needs network access that the agent sandbox does not have):

    pip install openmeteo-requests requests-cache retry-requests numpy pandas
    python3 lib/fetch_weather.py

Date range defaults to the span of the EcoWitt exports already in inputs/, so
it always lines up with the soil data. Override with --start / --end.

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
    "precipitation",
    "cloud_cover",
    "wind_speed_10m",
    "wind_gusts_10m",
]


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
        print(f"Missing dependency: {exc.name}\n"
              "  pip install openmeteo-requests requests-cache retry-requests numpy pandas",
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

    params = {
        "latitude": loc["lat"],
        "longitude": loc["lon"],
        "timezone": loc["timezone"],
        "hourly": HOURLY_VARS,
        "start_date": start,
        "end_date": end,
    }

    print(f"Open-Meteo: {loc['name']} ({loc['lat']}, {loc['lon']})  {start} -> {end}")
    try:
        response = client.weather_api(
            "https://api.open-meteo.com/v1/forecast", params=params)[0]
    except Exception as exc:
        print(f"\nRequest failed: {exc}\n"
              "If this is a network/allowlist error, run it from a machine with\n"
              "direct internet access -- the agent sandbox cannot reach this API.",
              file=sys.stderr)
        return 1

    hourly = response.Hourly()
    cols = {name: hourly.Variables(i).ValuesAsNumpy()
            for i, name in enumerate(HOURLY_VARS)}

    index = pd.date_range(
        start=pd.to_datetime(hourly.Time(), unit="s", utc=True),
        end=pd.to_datetime(hourly.TimeEnd(), unit="s", utc=True),
        freq=pd.Timedelta(seconds=hourly.Interval()),
        inclusive="left",
    ).tz_convert(response.Timezone().decode())

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        # temp_f is derived here so downstream code never has to guess units.
        w.writerow(["datetime", "temp_c", "temp_f"] + HOURLY_VARS[1:])
        n = 0
        for i, ts in enumerate(index):
            tc = cols["temperature_2m"][i]
            if tc != tc:  # NaN -- future hours beyond observation
                continue
            row = [ts.strftime("%Y-%m-%d %H:%M"), round(float(tc), 2),
                   round(float(tc) * 9 / 5 + 32, 2)]
            for name in HOURLY_VARS[1:]:
                v = cols[name][i]
                row.append("" if v != v else round(float(v), 2))
            w.writerow(row)
            n += 1

    tf = [float(c) * 9 / 5 + 32 for c in cols["temperature_2m"] if c == c]
    print(f"  {n} hourly rows -> {out_path}")
    if tf:
        print(f"  ambient range {min(tf):.1f} - {max(tf):.1f} F  "
              f"(compare: WFC01 body temp in sun reads 110-123 F -- not ambient)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
