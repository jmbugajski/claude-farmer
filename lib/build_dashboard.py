#!/usr/bin/env python3
"""
build_dashboard.py
==================
End-to-end build for the Farm Water Analysis dashboard.

    inputs/*.xlsx  ->  parse  ->  analyze  ->  render  ->  outputs/farm-water-dashboard-<date>.html

Run it after dropping fresh EcoWitt daily-log exports into inputs/:

    python3 lib/build_dashboard.py

Options:
    --inputs DIR    folder of EcoWitt .xlsx exports (default: <repo>/inputs)
    --outputs DIR   where to write the dashboard   (default: <repo>/outputs)
    --config FILE   config file                    (default: <repo>/config.json)
    --publish FILE  stable copy for hosting         (default: <repo>/docs/index.html)
    --no-publish    skip writing the published copy

The generated file is self-contained: soil-moisture, trend, distribution and
water charts are baked in; live weather + correlation are fetched client-side
from Open-Meteo when the file is opened online.

Each run writes two identical files: a dated archive in outputs/ and a stable
docs/index.html. The latter is what GitHub Pages serves, giving one permanent
shareable link that refreshes whenever the repo is pushed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import analyze          # noqa: E402
import parse_ecowitt    # noqa: E402
import render           # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the Farm Water Analysis dashboard.")
    ap.add_argument("--inputs", default=os.path.join(REPO, "inputs"))
    ap.add_argument("--outputs", default=os.path.join(REPO, "outputs"))
    ap.add_argument("--config", default=os.path.join(REPO, "config.json"))
    ap.add_argument("--publish", default=os.path.join(REPO, "docs", "index.html"))
    ap.add_argument("--no-publish", action="store_true",
                    help="skip writing the stable published copy")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        config = json.load(f)

    print(f"Reading EcoWitt exports from {args.inputs} ...")
    readings = parse_ecowitt.load_readings(args.inputs, config)
    print(f"  {len(readings)} raw readings "
          f"({readings[0]['dt']:%Y-%m-%d %H:%M} → {readings[-1]['dt']:%Y-%m-%d %H:%M})")

    data, cfg = analyze.build(readings, config)
    s = data["stats"]
    print(f"  resampled to {s['n']} points @ {s['interval_hr']} h")
    print(f"  tomato: mean {s['tom']['mean']}%  last {s['tom']['last']}%  "
          f"trend {data['trend']['per_week']} %/wk (R²={data['trend']['r2']})")
    print(f"  pepper: mean {s['pep']['mean']}%  last {s['pep']['last']}%")
    if data["water"]:
        w = data["water"]
        print(f"  water : {w['total_L']} L over {w['active_days']} metered day(s)")

    html = render.render(data, cfg)

    os.makedirs(args.outputs, exist_ok=True)
    end_date = data["stats"]["range_end"][:10]
    out_path = os.path.join(args.outputs, f"farm-water-dashboard-{end_date}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nDashboard written: {out_path}")

    if not args.no_publish:
        os.makedirs(os.path.dirname(args.publish), exist_ok=True)
        with open(args.publish, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Published copy : {args.publish}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
