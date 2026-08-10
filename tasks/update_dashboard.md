# Task: Update the Water Dashboard

**Type:** Manual (run on demand — typically weekly, after downloading new logs)
**Trigger:** You've downloaded fresh daily-log exports from the EcoWitt cloud.

Run this whenever you have new EcoWitt daily `.xlsx` exports and want a refreshed
dashboard. It is intentionally manual — nothing runs on a schedule.

---

## Steps

1. **Download** the new daily logs from the EcoWitt cloud
   (Ecowitt.net → Devices → GW1200 → *Export*, one file per day).

2. **Drop** the `.xlsx` files into the repo's `inputs/` folder.
   Existing files are fine to keep — the pipeline de-duplicates by timestamp and
   uses the full history it finds. Filenames look like
   `all_GW1200B-WIFIECD0(202607150000-202607152359).xlsx`.

3. **Refresh weather and build** the dashboard from the repo root:

   ```bash
   ./pull_weather_data.sh
   .venv/bin/python lib/build_dashboard.py
   ```

   `pull_weather_data.sh` re-pulls `inputs/weather.csv` to cover whatever date
   range the `.xlsx` exports span, and no-ops if it is already current. The
   build then normalises soil dry-down against ET₀ (evaporative demand), so a
   hot week and an under-watered week stop looking alike.

   Use the venv's interpreter — Homebrew's `python3` is PEP 668 managed and will
   not have `openpyxl`. First time only:

   ```bash
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.txt
   ```

4. **Open** the newest file in `outputs/`, named
   `farm-water-dashboard-<latest-date>.html`. Open it with an internet
   connection so the live weather + correlation panels load.

---

## What it does

`inputs/*.xlsx → parse → analyze → outputs/farm-water-dashboard-<date>.html`

- Reads every export in `inputs/`, locating the tomato/pepper/water columns by
  their header names.
- Recomputes soil-moisture series, daily/weekly aggregates, the dry-down trend,
  moisture distribution, and metered water usage.
- Regenerates a single self-contained HTML dashboard (charts baked in; weather
  fetched client-side).

## Adjusting behavior

Edit `config.json` (not the code) to change setpoints, gauge bands, the
irrigation plan, location/lat-lon, or the resample interval. Re-run step 3.

## If something looks off

- **No files found:** confirm the `.xlsx` exports are in `inputs/`.
- **A probe reads blank/`-`:** that sensor dropped samples; gaps are expected.
- **Water totals look lumpy:** the WFC01 is a cumulative meter; batched/back-filled
  counts show up as single-reading jumps. Totals sharpen over several clean weeks.
- **Weather panel says unavailable:** open the HTML with an internet connection.

## Prompt to hand Claude

> Update the water dashboard: I've added the new EcoWitt logs to `inputs/`. Run
> `lib/build_dashboard.py`, then show me the newest dashboard in `outputs/` and
> flag anything notable versus last week.
