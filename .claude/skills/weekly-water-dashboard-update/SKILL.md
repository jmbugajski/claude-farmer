---
name: weekly-water-dashboard-update
description: Weekly water dashboard update — rebuild the farm water dashboard from new EcoWitt logs, then publish it to GitHub Pages. Manual, run on demand; use only when the user asks to update or publish the dashboard, never as part of issue work.
---

Update the farm water dashboard and publish it. This is a manual weekly task, run after new EcoWitt daily-log exports have been added. Build detail and troubleshooting live in `tasks/update_dashboard.md` — read it first; this skill adds the publish step.

Steps:
1. Confirm new EcoWitt daily `.xlsx` exports are in `inputs/`. Filenames look like `all_GW1200B-WIFIECD0(202607150000-202607152359).xlsx`. Existing files are fine to keep — the pipeline de-duplicates by timestamp and uses the full history. If no `.xlsx` files are found in `inputs/`, stop and tell the user to add them first.

2. Confirm the checkout is on `main` and up to date with `origin/main`. GitHub Pages serves `docs/index.html` from `main`, so a build committed anywhere else does not publish. If the repo is on another branch or has uncommitted code changes, stop and tell the user rather than switching branches or stashing for them.

3. Save the currently published dashboard for the step-6 comparison, then refresh weather and build from the repo root:
   ```bash
   git show HEAD:docs/index.html > "${TMPDIR:-/tmp}/prev-dashboard.html"
   ./pull_weather_data.sh
   .venv/bin/python lib/build_dashboard.py
   ```
   (First time only: `python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt`.)

   - **"weather deps are missing from: python3"** usually means the venv broke, not that deps were never installed: a Homebrew Python upgrade leaves `.venv/bin/python` pointing at a deleted interpreter, the script's `-x` test fails, and it falls back to bare `python3`. Check with `.venv/bin/python --version`. Plain `python3 -m venv .venv` does not repair it — recreate it: `rm -rf .venv && python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt`.
   - **Weather fetch fails on the network** (e.g. a proxy 403 to `archive-api.open-meteo.com`): build anyway — the pipeline tolerates a stale `inputs/weather.csv` — and tell the user which dates have no ET₀, since the dry-down normalization and the panel-4 ETc band are degraded for them.
   This writes two identical files: a dated archive at `outputs/farm-water-dashboard-<date>.html` (a local build artifact, intentionally git-ignored) and the stable published copy at `docs/index.html` (tracked; its git history preserves every past week).

4. Publish it by committing and pushing only `docs/`:
   ```bash
   git add docs/
   git commit -m "Update water dashboard <date>"
   git push
   ```
   Replace `<date>` with the dashboard's end date (the same date in the output filename). If `git push` fails on auth, tell the user — don't attempt to store or work around credentials.

5. Give the user the shareable link: https://jmbugajski.github.io/claude-farmer/ (GitHub Pages must be enabled once in repo Settings → Pages → Deploy from branch `main`, folder `/docs`; and the repo must be public for a free plan). Also present the newest local `outputs/farm-water-dashboard-<date>.html` so they can view it immediately.

6. Flag anything notable versus the prior week (soil-moisture dry-down trend, moisture distribution, metered water usage). Compare the new `docs/index.html` against the copy saved in step 3: each embeds its data as one JSON line beginning `const DATA =`. Useful keys: `stats.tom`/`stats.pep` (mean, min, last, plus the `floor`/`working_lo`/`ceiling` setpoints to judge against), `daily` (per-day probe means/mins — roll up by week), `water.daily` (`draw` per day), `split` (moisture distribution; `withheld` means the bands are unverified, not a bug), and `health` (probe voltage, calibration breaks). Lead with whichever probe is nearest or below its floor. Note that the WFC01 is a cumulative meter, so batched/back-filled counts can show up as single-reading jumps — lumpy water totals are expected and sharpen over several clean weeks.

Config lives in `config.json` (setpoints, gauge bands, irrigation plan, location/lat-lon, resample interval) — edit that, not the code, if behavior needs adjusting, then re-run step 3. Schedule-log entries follow the length rule in `tasks/update_dashboard.md`. Note: the published dashboard is public and shows the garden's ZIP-level location (Santa Clara 95050); no API keys are embedded (weather comes from the keyless Open-Meteo API).
