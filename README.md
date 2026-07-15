# claude-farmer — Farm Water Analysis

Turns weekly **EcoWitt GW1200** exports into a self-contained HTML dashboard for a
backyard garden in Santa Clara (95050): two soil-moisture probes (a tomato raised
bed and pepper fabric bags) plus a WFC01 water-flow meter, with live weather
correlation fetched in the browser.

## How it works

```
inputs/*.xlsx  ──▶  lib/parse_ecowitt.py  ──▶  lib/analyze.py  ──▶  lib/render.py  ──▶  outputs/farm-water-dashboard-<date>.html
   (raw daily        (locate columns by         (series, daily,       (fill the
    EcoWitt logs)      header name, clean)        weekly, trend,        HTML template)
                                                  distribution,
                                                  water usage)
```

Drop the week's EcoWitt daily-log exports into `inputs/`, run the build, and a new
dated dashboard appears in `outputs/`. The dashboard is a single HTML file with all
soil/trend/distribution/water charts baked in; air-temperature, ET₀ and the
moisture↔weather correlation are fetched client-side from Open-Meteo when the file
is opened with an internet connection.

## Run it

```bash
python3 lib/build_dashboard.py
```

Requires Python 3 with `openpyxl` (`pip install openpyxl`). Options:
`--inputs DIR`, `--outputs DIR`, `--config FILE` (all default to the repo root).

There is also a one-click task in `tasks/` — see **Weekly update** below.

## Weekly update

After downloading the new daily logs from the EcoWitt cloud:

1. Save the `.xlsx` files into `inputs/`.
2. Run the task in `tasks/update_dashboard.md` (or just `python3 lib/build_dashboard.py`).
3. Open the newest file in `outputs/`.

It's built to be run manually, roughly weekly, whenever fresh logs are downloaded.

## Repository layout

| Path | What it is |
| --- | --- |
| `config.json` | All the knobs: location/lat-lon, probe setpoints & gauge bands, irrigation plan, sample interval, column names. Edit this, not the code, when the garden changes. |
| `lib/parse_ecowitt.py` | Reads every `.xlsx` in `inputs/`, locates the tomato/pepper/water columns by their two-row header names, and returns clean time-sorted readings. |
| `lib/analyze.py` | Computes everything the dashboard shows: resampled series, daily/weekly aggregates, dry-down trend (OLS), moisture distribution, water usage, and the data-driven gauge verdicts + "do next" advice. |
| `lib/render.py` | Fills `dashboard_template.html` with the computed data. |
| `lib/dashboard_template.html` | The dashboard shell (styles + Chart.js render logic) with `__DATA__` / `__CFG__` placeholders. |
| `lib/build_dashboard.py` | Entry point that runs parse → analyze → render and writes the dated output. |
| `tasks/update_dashboard.md` | Manually-triggered "refresh the dashboard" task (run after downloading new logs). |
| `inputs/` | Raw EcoWitt exports. **Git-ignored** (data, not source). |
| `outputs/` | Generated dashboards. **Git-ignored** (build artifacts). |

## Input format (EcoWitt GW1200 export)

Each `all_GW1200B-...(YYYYMMDD0000-YYYYMMDD2359).xlsx` covers one day at 5-minute
resolution, with a two-row header. Columns are matched by name, so re-ordered
exports keep working as long as the probe **group** names in `config.json` match
what's set on the EcoWitt console:

- `Tomato Probe` → `Soil Moisture(%)`
- `Pepper Probe` → `Soil Moisture(%)`
- `WFC01-…` → `Water Total(L)` (a cumulative odometer; daily draw = day-over-day diff)

Missing sensor samples are exported as `-` and treated as gaps.

## Notes on the analysis

- **"Current" moisture** on the gauges is a **trailing 24-hour average**, not the
  single last sample. With 5-minute data a lone final reading often lands on a
  post-irrigation spike; the trailing-day average is a truer picture of where the
  bed actually sits.
- **Setpoints** (tomato 62%, pepper 35%) are the EcoWitt alert thresholds, not
  universal ideals — read each probe against itself.
- **Water** comes from a cumulative meter, so daily draws can look lumpy (batched or
  back-filled counts appear as single-reading jumps). Treat totals as a rough tally
  until several clean weeks accumulate.
- **Weather correlation** needs an internet connection at view time; all
  moisture/trend/water panels work fully offline.

## Configuration example

```jsonc
// config.json (excerpt)
"probes": {
  "tomato": { "group": "Tomato Probe", "setpoint": 62,
              "gauge": { "name": "Tomatoes", "loc": "raised bed · in-ground",
                         "lo": 55, "hi": 90, "idealLo": 60, "idealHi": 68 } }
}
```
