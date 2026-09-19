---
name: weekly-water-dashboard-update
description: Weekly water dashboard update — rebuild the farm water dashboard from new EcoWitt logs, then publish it to GitHub Pages. Manual, run on demand; use only when the user asks to update or publish the dashboard, never as part of issue work.
---

Update the farm water dashboard and publish it. This is a manual weekly task, run after new EcoWitt daily-log exports have been added. Build detail and troubleshooting live in `tasks/update_dashboard.md` — read it first; this skill adds the publish step.

Steps:
1. Confirm new EcoWitt daily `.xlsx` exports are in `inputs/`. Filenames look like `all_GW1200B-WIFIECD0(202607150000-202607152359).xlsx`. Existing files are fine to keep — the pipeline de-duplicates by timestamp and uses the full history. If no `.xlsx` files are found in `inputs/`, stop and tell the user to add them first.

2. Confirm the checkout is on `main` and up to date with `origin/main`. GitHub Pages serves `docs/index.html` from `main`, so a build committed anywhere else does not publish. If the repo is on another branch or has uncommitted code changes, stop and tell the user rather than switching branches or stashing for them.

3. From the repo root, refresh weather and build:
   ```bash
   ./pull_weather_data.sh
   .venv/bin/python lib/build_dashboard.py
   ```
   (First time only: `python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt`.)
   This writes two identical files: a dated archive at `outputs/farm-water-dashboard-<date>.html` (a local build artifact, intentionally git-ignored) and the stable published copy at `docs/index.html` (tracked; its git history preserves every past week).

4. Publish it by committing and pushing only `docs/`:
   ```bash
   git add docs/
   git commit -m "Update water dashboard <date>"
   git push
   ```
   Replace `<date>` with the dashboard's end date (the same date in the output filename). If `git push` fails on auth, tell the user — don't attempt to store or work around credentials.

5. Give the user the shareable link: https://jmbugajski.github.io/claude-farmer/ (GitHub Pages must be enabled once in repo Settings → Pages → Deploy from branch `main`, folder `/docs`; and the repo must be public for a free plan). Also present the newest local `outputs/farm-water-dashboard-<date>.html` so they can view it immediately.

6. Flag anything notable versus the prior week (soil-moisture dry-down trend, moisture distribution, metered water usage). Note that the WFC01 is a cumulative meter, so batched/back-filled counts can show up as single-reading jumps — lumpy water totals are expected and sharpen over several clean weeks.

Config lives in `config.json` (setpoints, gauge bands, irrigation plan, location/lat-lon, resample interval) — edit that, not the code, if behavior needs adjusting, then re-run step 3. Schedule-log entries follow the length rule in `tasks/update_dashboard.md`. Note: the published dashboard is public and shows the garden's ZIP-level location (Santa Clara 95050); no API keys are embedded (weather comes from the keyless Open-Meteo API).
