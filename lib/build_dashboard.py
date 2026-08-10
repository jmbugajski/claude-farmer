#!/usr/bin/env python3
"""
build_dashboard.py
==================
End-to-end build for the Farm Water Analysis dashboard.

    inputs/*.xlsx  ->  parse  ->  analyze  ->  render  ->  outputs/farm-water-dashboard-<date>.html

Run it after dropping fresh EcoWitt daily-log exports into inputs/. Use the
venv's interpreter -- Homebrew's python3 is PEP 668 managed and will not have
openpyxl installed:

    .venv/bin/python lib/build_dashboard.py

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


def _dependency_help(exc: ModuleNotFoundError) -> str:
    """
    Turn a bare ModuleNotFoundError into something actionable. On macOS this is
    almost always the wrong interpreter rather than a genuinely missing package:
    Homebrew's python3 is PEP 668 managed, so the deps live in .venv while
    `python3 lib/build_dashboard.py` runs against Homebrew and finds nothing.
    """
    venv = os.path.join(REPO, ".venv", "bin", "python")
    lines = [
        f"Missing dependency: {exc.name}",
        "",
        f"  Running under: {sys.executable}",
        f"  Python:        {sys.version_info.major}.{sys.version_info.minor}"
        f".{sys.version_info.micro}",
        "",
    ]
    # lexists, not exists: a venv binary is a symlink chain into the base
    # interpreter, and exists() reports False whenever that target is missing
    # from the current environment (e.g. inspecting a macOS repo from Linux).
    # We only need to know the venv was created here.
    if os.path.lexists(venv):
        lines += [
            "  The project venv exists and is probably where the packages are.",
            "  Re-run with it:",
            "",
            f"    {venv} lib/build_dashboard.py",
        ]
    else:
        lines += [
            "  No .venv found. Create one (Homebrew Python refuses system-wide",
            "  installs under PEP 668):",
            "",
            "    python3 -m venv .venv",
            "    .venv/bin/python -m pip install -r requirements.txt",
            "    .venv/bin/python lib/build_dashboard.py",
        ]
    return "\n".join(lines)


try:
    import analyze          # noqa: E402
    import parse_ecowitt    # noqa: E402
    import render           # noqa: E402
    import weather          # noqa: E402
except ModuleNotFoundError as exc:
    print(_dependency_help(exc), file=sys.stderr)
    raise SystemExit(1)


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

    wx_hourly = weather.load(args.inputs)
    if wx_hourly:
        print(f"  weather: {len(wx_hourly)} hourly rows from inputs/weather.csv")
    else:
        print("  weather: inputs/weather.csv not found — run ./pull_weather_data.sh "
              "(dashboard falls back to a client-side fetch)")

    data, cfg = analyze.build(readings, config, wx_hourly=wx_hourly)
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
