# claude-farmer — Farm Water Analysis

Turns weekly **EcoWitt GW1200** exports into a self-contained HTML dashboard for a
backyard garden in Santa Clara (95050): two soil-moisture probes (a 3-inch WH51 in
the tomato raised bed and one in the pepper fabric bags) plus a WFC01 water-flow
meter, read against cached Open-Meteo weather. Each build is published to GitHub
Pages from `docs/index.html`.

## How it works

```
inputs/*.xlsx ──▶ parse_ecowitt.py ──┐
                                      ├──▶ analyze.py ──▶ render.py ──▶ outputs/farm-water-dashboard-<date>.html
inputs/weather.csv ──▶ weather.py ───┘    (+ events.py)                 docs/index.html  (published copy)
       ▲
pull_weather_data.sh → fetch_weather.py (Open-Meteo)
```

1. `pull_weather_data.sh` refreshes `inputs/weather.csv` from Open-Meteo so it
   covers every day the EcoWitt exports span.
2. `build_dashboard.py` checks `config.json` (`farm_config.py`), parses the
   exports, analyses them (with `events.py` working on the raw 5-minute readings)
   and fills the HTML template.
3. Each run writes two identical files: a dated copy in `outputs/` and the
   published copy at `docs/index.html`.

The dashboard is a single HTML file with everything baked in, weather included, so
it works fully offline. Its panels are: sensor health, the control variable (what
the bed holds), a regime comparison, water budget vs crop demand, whether each run
earns its water, 5-minute pulse detail for the last 7 days, whether runs fired on
time, the irrigation schedule, and a "what the month says" summary with a
**Do next** card.

## Run it

```bash
./pull_weather_data.sh              # refresh inputs/weather.csv if stale
.venv/bin/python lib/build_dashboard.py
```

**Use the venv's interpreter, not bare `python3`.** Homebrew's Python is PEP 668
"externally managed" and refuses system-wide installs, so the dependencies live
in `.venv`. First-time setup:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The build itself needs only `openpyxl`; pandas and the Open-Meteo client are used
only by `fetch_weather.py`.

Build options: `--inputs DIR`, `--outputs DIR`, `--config FILE` (default to the repo
root), `--publish FILE` (default `docs/index.html`) and `--no-publish` (skip the
published copy, for test builds).

`pull_weather_data.sh` takes `--force` (always re-fetch) and `--check` (report
coverage, fetch nothing). It re-pulls the whole date range each time, because
Open-Meteo revises recent days.

Tests: `.venv/bin/python -m unittest discover -v tests`

## Weekly update

After downloading the new daily logs from the EcoWitt cloud:

1. Save the `.xlsx` files into `inputs/`. Old files can stay; the parser
   de-duplicates by timestamp.
2. Run `./pull_weather_data.sh && .venv/bin/python lib/build_dashboard.py`.
   `tasks/update_dashboard.md` has the full steps and troubleshooting.
3. Publish by committing and pushing `docs/` on `main`
   (`Update water dashboard <date>`). GitHub Pages serves `docs/index.html` from
   `main`.

The `weekly-water-dashboard-update` skill in `.claude/skills/` does all of this,
including the publish and a check against last week's page. Run it manually,
roughly weekly, whenever fresh logs are downloaded.

## Repository layout

| Path | What it is |
| --- | --- |
| `config.json` | All the knobs: location, sample interval, column names, each probe's derived bands and gauge range, sensor health limits, bed facts, and the irrigation plan (regimes, schedule log, manual events). Edit this, not the code, when the garden changes. Its `_comment` keys record what is believed about each value and why. |
| `lib/build_dashboard.py` | Entry point: check config → parse → analyze → render, then write the dated output and the published copy. |
| `lib/farm_config.py` | Loads `config.json`, validates it and fills in derived fields, so every module reads one consistent copy (for example, the plan is recorded once in `plan.regimes[]`). |
| `lib/parse_ecowitt.py` | Reads every `.xlsx` in `inputs/`, finds the tomato/pepper/water/voltage columns by their two-row header names, and returns clean, time-sorted readings. |
| `lib/analyze.py` | Computes what the dashboard shows: resampled series, daily and weekly aggregates, sensor health, regime comparison, water budget, gauge verdicts and "do next" advice. |
| `lib/events.py` | Irrigation analysis on the raw 5-minute readings: detects each run, measures what it retained vs its peak, checks onset time against the schedule, and builds the water budget. Hourly resampling would hide all of this. |
| `lib/weather.py` | Reads `inputs/weather.csv` and reduces it to daily ET₀ (evaporative demand) for the analysis. Standard library only. |
| `lib/fetch_weather.py` | Pulls hourly weather from Open-Meteo (archive endpoint, with forecast for the most recent days) into `inputs/weather.csv`. Fails without writing if the result does not cover the window. |
| `lib/derive_bands.py` | Derives each probe's drainage ceiling and stress floor from the data (see below). Run by hand; its results are pasted into `config.json`. |
| `lib/render.py` | Fills `dashboard_template.html` with the computed data. |
| `lib/dashboard_template.html` | The dashboard shell (styles + Chart.js render logic) with `__DATA__`, `__CFG__` and `__FOOTER_META__` placeholders. |
| `pull_weather_data.sh` | Refreshes `inputs/weather.csv` when it is stale; run before each build. |
| `tests/` | `unittest` suite for parsing, config, analysis, events, weather and rendering. |
| `tasks/update_dashboard.md` | The manual weekly build task, with troubleshooting. |
| `tasks/infiltrometer.md` | A seasonal field test to measure how fast the raised bed absorbs water, replacing an assumed figure in `config.json`. |
| `docs/index.html` | The published dashboard, served by GitHub Pages. Its git history keeps every past week. |
| `docs/*.pdf` | EcoWitt manuals for the WH51, WH51L and WFC01 sensors. |
| `.claude/` | Claude Code files: the weekly update skill, the workflow profile and its helper scripts. |
| `inputs/` | Raw EcoWitt exports and `weather.csv`. **Git-ignored** (data, not source). |
| `outputs/` | Dated dashboards. **Git-ignored** (build artifacts). |

## Input format (EcoWitt GW1200 export)

Each `all_GW1200B-...(YYYYMMDD0000-YYYYMMDD2359).xlsx` covers one day at 5-minute
resolution, with a two-row header. Columns are matched by name, so re-ordered
exports keep working as long as the probe **group** names in `config.json` match
what's set on the EcoWitt console:

- `Tomato Probe` → `Soil Moisture(%)`
- `Pepper Probe` → `Soil Moisture(%)`
- `WFC01-…` → `Water Total(L)` (a cumulative meter; daily draw = day-over-day difference)
- Each probe's battery voltage column, found by its console channel
  (`voltage_channel` in `config.json`: tomato `CH1`, pepper `CH2`), feeds the
  sensor-health panel.

Missing sensor samples are exported as `-` and treated as gaps.

## Notes on the analysis

- **"Current" moisture** on the gauges is a **trailing 24-hour average**, not the
  single last sample. With 5-minute data a lone final reading often lands on a
  post-irrigation spike; the trailing-day average is a truer picture of where the
  bed actually sits. A partial final day is dropped before daily averages.
- **There are no setpoints.** The old hand-set EcoWitt alert thresholds (tomato
  62%, pepper 35%) were removed on 2026-08-22. Both probes run factory
  calibration, so the "%" is an index, not true water content, and a single target
  number cannot be read off it. Each probe instead has a `bands` block:
  `drainage_ceiling`, `refill_target`, `working_lo` and `stress_floor`.
- **Bands are derived, not chosen.** `lib/derive_bands.py` finds field capacity
  where night-time moisture loss (no evaporative demand, so it is drainage) jumps,
  and the stress floor where daytime loss per mm of ET₀ falls off. Re-derive if a
  probe is moved, reseated or recalibrated, or the bed media changes.
- **Bands are currently unverified** (`verified: false`, since 2026-09-19): the
  method is being reworked, so the dashboard withholds figures scored against them
  rather than showing them with a caveat.
- **Weather** is ET₀ from Open-Meteo, cached at build time. Never use the WFC01's
  "Water Flow Temperature" as ambient: it is the meter's body temperature in
  direct sun and reads 30–40 °F high.
- **Water** comes from a cumulative meter, so daily draws can look lumpy (batched or
  back-filled counts appear as single-reading jumps). Treat totals as a rough tally.

## Configuration example

```jsonc
// config.json (excerpt; comment keys omitted)
"probes": {
  "tomato": {
    "group": "Tomato Probe",
    "voltage_channel": "CH1",
    "hist_bin": 2,
    "bands": { "drainage_ceiling": 75, "refill_target": 73, "working_lo": 58,
               "stress_floor": 50, "verified": false },
    "gauge": { "name": "Tomatoes", "loc": "raised bed · 3-inch probe",
               "lo": 45, "hi": 85 }
  }
}
```
