# Helpers for /agile-dev-workflow in claude-farmer. Source, don't execute:
#
#   source "$(git rev-parse --show-toplevel)/.claude/workflow-helpers.sh"
#
# Every function resolves the checkout it is CALLED from (so it works in a
# worktree) and writes logs to $WF_SCRATCH, never into the repo.

WF_SCRATCH="${WF_SCRATCH:-${TMPDIR:-/tmp}/claude-farmer-wf}"
mkdir -p "$WF_SCRATCH"

_wf_root() { git rev-parse --show-toplevel; }
_wf_py()   { echo "$(_wf_root)/.venv/bin/python"; }

# Clear bytecode. Python invalidates .pyc on (mtime, size), so a same-size edit
# inside one second keeps running the old code -- call after mutate AND revert.
wf_pyc() {
  find "$(_wf_root)" -name __pycache__ -not -path '*/.venv/*' -not -path '*/node_modules/*' \
    -prune -exec rm -rf {} + 2>/dev/null
}

# Test runner with the verdict captured, never piped. Prints the tail + EXIT.
wf_test() {
  local log="$WF_SCRATCH/test.log"
  wf_pyc
  ( cd "$(_wf_root)" && "$(_wf_py)" -m unittest discover -v tests ) > "$log" 2>&1
  local rc=$?; echo "EXIT=$rc" >> "$log"
  grep -E '^(Ran|OK|FAILED|ERROR|FAIL:|EXIT=)' "$log"; echo "log: $log"
  return $rc
}

# End-to-end build smoke test that CANNOT touch docs/index.html or outputs/.
# Needs inputs/*.xlsx (gitignored; see wf_link in a worktree).
wf_build() {
  local log="$WF_SCRATCH/build.log"
  ( cd "$(_wf_root)" && "$(_wf_py)" lib/build_dashboard.py --no-publish \
      --outputs "$WF_SCRATCH/out" ) > "$log" 2>&1
  local rc=$?; echo "EXIT=$rc" >> "$log"
  tail -8 "$log"; return $rc
}

# The template's JS has no unit tests. This runs the last wf_build output in
# jsdom and prints every generated sentence, folded, to sentences-<tag>.txt --
# diff a `before` against an `after`, or hand-edit the DATA/CFG JSON in a copy
# of the built page to force the branch today's data does not take.
#   wf_sentences before; ...edit...; wf_build; wf_sentences after
#   wf_sentences variant path/to/edited.html
wf_sentences() {
  local out="$WF_SCRATCH/sentences-${1:-now}.txt"
  local page="${2:-$(ls -t "$WF_SCRATCH"/out/*.html 2>/dev/null | head -1)}"
  [ -f "$page" ] || { echo "no built page: run wf_build first"; return 1; }
  node "$(_wf_root)/.claude/render-text.cjs" "$page" 2>&1 | fold -s -w 160 > "$out"
  tail -1 "$out"; echo "full: $out"
  grep -q '^SCRIPT_ERRORS=0' "$out"
}

# config.json is hand-edited and 700+ lines: prove it still parses, and print
# the values a change must not move by accident.
wf_config() {
  "$(_wf_py)" - "$(_wf_root)/config.json" <<'PY'
import json, sys
c = json.load(open(sys.argv[1], encoding="utf-8"))
for name, p in ((k, v) for k, v in c["probes"].items() if isinstance(v, dict) and "bands" in v):
    b = p["bands"]
    print(f"{name:7} ceiling {b['drainage_ceiling']}  floor {b['stress_floor']}  "
          f"working_lo {b['working_lo']}  verified {b.get('verified', True)}")
pl = c["plan"]
print("runs   ", [(r["time"], r["seconds"]) for r in pl.get("runs", [])],
      "effective", pl.get("runs_effective"))
rg = pl.get("regimes") or []
print("regimes", len(rg), "open:", [r["label"] for r in rg if not r.get("end")])
PY
}

# Band derivation on the real inputs -- the before/after for any change to
# lib/derive_bands.py. Output goes to a named file so two runs can be diffed.
wf_bands() {
  local out="$WF_SCRATCH/bands-${1:-now}.txt"
  ( cd "$(_wf_root)" && "$(_wf_py)" lib/derive_bands.py ) > "$out" 2>&1
  grep -E '===|drainage_ceiling|stress_floor' "$out"; echo "full: $out"
}

# Mutation check, reverting from a BYTE COPY (never `git checkout --`, which
# restores HEAD and eats any other uncommitted edit).
#   wf_mutate lib/derive_bands.py   -> edit the file to break it -> wf_test (must FAIL)
#   wf_revert lib/derive_bands.py   -> wf_test (must pass)
wf_mutate() { cp "$1" "$1.mutbak" && echo "backed up; now break $1, then wf_test"; }
wf_revert() {
  cp "$1.mutbak" "$1" && cmp "$1.mutbak" "$1" && rm "$1.mutbak" && wf_pyc && echo "reverted $1"
}

# Worktree setup: link the gitignored state the build and tests need.
#   wf_link /path/to/main/checkout     (run from inside the worktree)
wf_link() {
  local R="$1" W; W="$(_wf_root)"
  [ -d "$R/.venv" ] && [ ! -e "$W/.venv" ] && ln -s "$R/.venv" "$W/.venv"
  # inputs/ is tracked (holds .gitkeep), so link its CONTENTS, not the directory.
  for f in "$R"/inputs/*.xlsx "$R"/inputs/weather.csv; do
    [ -e "$f" ] && [ ! -e "$W/inputs/$(basename "$f")" ] && ln -s "$f" "$W/inputs/"
  done
  echo "linked: .venv + $(ls "$W/inputs" | grep -c xlsx) exports + weather.csv"
}

# Input coverage in one line each -- read at pickup; several open issues hinge on it.
wf_inputs() {
  local R; R="$(_wf_root)"
  ls "$R/inputs" | grep -oE '\(([0-9]{8})' | tr -d '(' | sort -u > "$WF_SCRATCH/days.txt"
  echo "exports: $(wc -l < "$WF_SCRATCH/days.txt" | tr -d ' ') days, $(head -1 "$WF_SCRATCH/days.txt") -> $(tail -1 "$WF_SCRATCH/days.txt")"
  "$(_wf_py)" - "$WF_SCRATCH/days.txt" <<'PY'
import sys
from datetime import date, timedelta
ds = [date(int(s[:4]), int(s[4:6]), int(s[6:8])) for s in open(sys.argv[1]).read().split()]
have, d, miss = set(ds), ds[0], []
while d <= ds[-1]:
    if d not in have: miss.append(d.isoformat())
    d += timedelta(days=1)
print("missing days:", miss or "none")
PY
  ( cd "$R" && ./pull_weather_data.sh --check 2>&1 | tail -3 )
}

# Workable backlog, ranked: impact high->low, then cost low->high. `published-figure`
# (wrong on the live page now) sorts ahead of `known-withheld` within a tier.
wf_triage() {
  gh issue list --state open --search '-is:blocked' --limit 100 --json number,title,labels \
    --jq '
      def lab(p): [.labels[].name | select(startswith(p))] ;
      map({n: .number, t: .title,
           i: (lab("impact:")[0] // "impact:?"), c: (lab("cost:")[0] // "cost:?"),
           a: (lab("area:") | map(sub("area:";"")) | join("+")),
           f: ([.labels[].name | select(. == "published-figure" or . == "known-withheld")][0] // "")})
      | sort_by([ ({"impact:high":0,"impact:med":1,"impact:low":2}[.i] // 9),
                  ({"published-figure":0,"":1,"known-withheld":2}[.f] // 1),
                  ({"cost:low":0,"cost:med":1,"cost:high":2}[.c] // 9), .n ])
      | .[] | "#\(.n)\t\(.i | sub("impact:";""))\t\(.c | sub("cost:";""))\t\(if .f == "" then "-" else .f end)\t\(.a)\t\(.t[0:70])"' \
  | column -t -s $'\t'
}
