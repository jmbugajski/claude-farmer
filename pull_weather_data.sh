#!/usr/bin/env bash
#
# pull_weather_data.sh
# ====================
# Refresh inputs/weather.csv so it covers every EcoWitt export currently in
# inputs/. Run this after dropping in new daily .xlsx logs and before
# build_dashboard.py, so the analysis has real ambient weather to work with.
#
#   ./pull_weather_data.sh           # fetch only if weather.csv is stale
#   ./pull_weather_data.sh --force   # always re-fetch
#   ./pull_weather_data.sh --check   # report coverage, fetch nothing
#
# Strategy: re-pull the FULL date range every time rather than appending only
# new days. Reasons, in order of importance:
#   1. Idempotent and self-healing -- skip a week and the gap backfills itself.
#      No merge logic, no dedup, no chance of a silent hole in the series.
#   2. Open-Meteo REVISES recent days as forecast is replaced by reanalysis.
#      An append-only file would freeze the least accurate version of every day.
#   3. It is free. 42 days is ~1,000 rows / 54 KB; a full year is ~8,800 rows.
# So: one file, no datestamp. The staleness check below is what stops it from
# hitting the API pointlessly, not incremental fetching.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUTS="$ROOT/inputs"
WEATHER="$INPUTS/weather.csv"

FORCE=0
CHECK=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --check) CHECK=1 ;;
    -h|--help) sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

# --- pick an interpreter -----------------------------------------------------
# Homebrew Python is PEP 668 managed, so the venv is the expected home for the
# pandas/openmeteo deps. Fall back sensibly if it hasn't been created yet.
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif command -v python3.11 >/dev/null 2>&1; then
  PY="python3.11"
else
  PY="python3"
fi

if ! "$PY" -c "import openmeteo_requests" >/dev/null 2>&1; then
  echo "The weather deps are missing from: $PY" >&2
  echo >&2
  echo "  cd $ROOT" >&2
  echo "  python3 -m venv .venv" >&2
  echo "  .venv/bin/python -m pip install -r requirements.txt" >&2
  exit 1
fi

# --- what range do we need, and what do we already have? ---------------------
read -r NEED_START NEED_END HAVE_START HAVE_END STALE < <("$PY" - "$INPUTS" "$WEATHER" <<'PYEOF'
import csv, os, re, sys

inputs, weather = sys.argv[1], sys.argv[2]

dates = []
for fn in os.listdir(inputs):
    if fn.lower().endswith(".xlsx"):
        for stamp in re.findall(r"(\d{8})\d{4}", fn):
            dates.append(f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}")
need_start, need_end = (min(dates), max(dates)) if dates else ("-", "-")

have_start = have_end = "-"
if os.path.exists(weather):
    days = sorted({r["datetime"][:10] for r in csv.DictReader(open(weather))
                   if r.get("datetime")})
    if days:
        have_start, have_end = days[0], days[-1]

# Stale if we have no file, or it fails to span the EcoWitt window.
stale = 1 if (have_start == "-" or need_start == "-"
              or have_start > need_start or have_end < need_end) else 0
print(need_start, need_end, have_start, have_end, stale)
PYEOF
)

if [[ "$NEED_START" == "-" ]]; then
  echo "No EcoWitt .xlsx exports found in $INPUTS -- add them first." >&2
  exit 1
fi

echo "EcoWitt exports span : $NEED_START -> $NEED_END"
echo "weather.csv covers   : $HAVE_START -> $HAVE_END"

if [[ "$CHECK" == "1" ]]; then
  [[ "$STALE" == "1" ]] && echo "Status: STALE (run without --check to refresh)" \
                        || echo "Status: current"
  exit 0
fi

if [[ "$STALE" == "0" && "$FORCE" == "0" ]]; then
  echo "Already covers the full range -- nothing to do. Use --force to re-fetch."
  exit 0
fi

# --- fetch -------------------------------------------------------------------
echo "Fetching $NEED_START -> $NEED_END via $PY ..."
"$PY" "$ROOT/lib/fetch_weather.py" --start "$NEED_START" --end "$NEED_END"

echo
echo "Done. Next: $PY lib/build_dashboard.py"
