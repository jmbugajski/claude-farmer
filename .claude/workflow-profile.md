# Workflow profile — claude-farmer

What `/agile-dev-workflow` needs to know about this repo. The process lives in
`~/.claude/skills/agile-dev-workflow/SKILL.md`; this file wins where the two disagree.

## Repo
Default branch `main`. Lands by: direct fast-forward push.
Sessions: one at a time, in the main checkout. If `git worktree list` ever shows more than one
entry, §5a applies.

**`main` is the deploy.** GitHub Pages serves `docs/index.html` from `main` on a public repo, so a
push that includes a rebuilt `docs/index.html` publishes it. Code work does NOT rebuild it: use
`wf_build`, which passes `--no-publish`. Regenerating the page is the weekly task
(`tasks/update_dashboard.md`, subject `Update water dashboard <date>`), not part of an issue.

Helpers — source once per shell; every block below assumes it:

```bash
source "$(git rev-parse --show-toplevel)/.claude/workflow-helpers.sh"
```

| helper | does |
|---|---|
| `wf_test` | unittest runner, verdict captured to a log, prints `EXIT=` |
| `wf_build` | full parse → analyze → render on real inputs, `--no-publish`, output to scratch |
| `wf_sentences [tag] [page]` | runs the built page's JS in jsdom, writes every generated sentence to `sentences-<tag>.txt`; fails on a script error |
| `wf_config` | proves `config.json` parses and passes `farm_config.validate()`; prints bands, `verified`, the derived runs / dates / projection, open regime |
| `wf_bands [tag]` | `derive_bands.py` on real inputs → `bands-<tag>.txt`, for before/after diffs |
| `wf_mutate f` / `wf_revert f` | byte-copy backup and verified restore, clears `__pycache__` |
| `wf_pyc` | clear bytecode (done for you by `wf_test` and `wf_revert`) |
| `wf_inputs` | export span, missing days, weather-cache coverage |
| `wf_triage` | open unblocked issues ranked by impact then cost, with area and status flag |
| `wf_link <main checkout>` | link `.venv` and `inputs/*` into a worktree |

## §0 Stores
| store | answers | length |
|---|---|---|
| the GitHub issue | what was asked, and the trail while answering | as long as the work took |
| code comments / docstrings | why the code is the way it is, with the dated incident that forced it | this repo's house style — match the surrounding density |
| `config.json` `_comment` / `_*_comment` / `*_note` keys | what is believed now about a threshold, plan or instrument | amend in place, dated |
| `config.json` `plan.schedule_log` | what the timer physically does and when it changed | `summary` ≤ 250 chars; rationale in `change` |

`schedule_log`, `regimes`, `manual_events` and the `plan.*` dates record the GARDEN, not the
code. An issue never edits them unless the issue is about them.

## §1 Pickup
```bash
wf_inputs && wf_config && wf_triage
```
Read both before trusting any number in an issue: figures quoted in issues were measured against
a specific export span, and `inputs/` grows weekly. `tasks/*.md` may be untracked work in
progress that is not yours — a dirty tree consisting only of those does not block starting.

## §2 Labels
One from each of area / type / cost / impact on every issue; more than one area when the work
spans them. The vocabulary is fixed — a new label is raised as a change, not minted in passing.

| dimension | labels |
|---|---|
| Area | `area:parse` `area:analyze` `area:events` `area:derive-bands` `area:weather` `area:template` `area:config` `area:tooling` |
| Type | `bug` `enhancement` `documentation` `question` |
| Cost | `cost:low` (under a session, one file) · `cost:med` (several consumers) · `cost:high` (needs a design decision first) |
| Impact | `impact:low` (changes what we know) · `impact:med` (changes a published figure or sentence) · `impact:high` (changes a verdict, a band, or an irrigation decision) |

Two status flags, optional, at most one:
- `published-figure` — a number or verdict on the live public page is wrong NOW.
- `known-withheld` — the affected output is currently withheld (`bands.verified: false`,
  `_partition` off), so nothing wrong is on the page, but it would be on re-enable. Drop the flag
  when the output is re-enabled; if the issue is still open then, it becomes `published-figure`.

Area maps to files: `parse` → `lib/parse_ecowitt.py`; `analyze` → `lib/analyze.py`; `events` →
`lib/events.py`; `derive-bands` → `lib/derive_bands.py`; `weather` → `fetch_weather.py`,
`weather.py`, `pull_weather_data.sh`; `template` → `dashboard_template.html`, `render.py`;
`config` → `config.json`; `tooling` → `tests/`, `.claude/`, `build_dashboard.py`.

```bash
gh issue edit <n> --add-label "area:events,bug,cost:med,impact:high,published-figure"
wf_triage            # open + unblocked, ranked impact desc then cost asc
```

The issue footer table keeps **Evidence**, **Opened** and **Related** only — Impact, Cost and Area
live in labels, where they can be filtered, and are not repeated in the body.

## §3 Filing test
"Could change what the system does" means here: **a number, verdict, colour or sentence on the
published dashboard would differ, or a band / plan decision in `config.json` would.** Dead code,
style and comment accuracy do not pass on their own; a wrong comment that a future re-derivation
would act on does.

A figure that is currently withheld (`bands.verified: false`, `_partition` returning `None`)
still passes if it would be wrong the moment it is re-enabled — say so in `## Why it matters`.

## §4 Plan sections
Add `## Publication rule` to any issue that changes a published figure: what the page shows
before, what it will show after, and whether the figure should be WITHHELD rather than corrected
until verified. The repo's standing rule is that a silently wrong figure is worse than a missing
one.

## §5 Commits
Subject: imperative sentence, optional `file:` or `area:` prefix, no trailing period, specifics
over topics. Examples from the log:

- `events.py: windowed contiguous-ascent rise detection; MIN_RISE 3->2`
- `Withhold every figure scored against unverified bands`

Rules that bind while committing:
- Interpreter is `.venv/bin/python`, always. Bare `python3` is Homebrew's and has no `openpyxl`.
- **Never stage `docs/index.html` in an issue commit** (see Repo). `git status --short` before
  every commit; `inputs/`, `outputs/`, `node_modules/`, `package*.json` are ignored on purpose.
- `config.json`: edit the raw text surgically. Do not round-trip it through `json.dump` — that
  reflows 700+ lines and buries the change. Run `wf_config` after every edit.
- Amending a `_comment`: append `AMENDED <date> (#n): …` to the existing string; leave the
  original standing. If the same paragraph is duplicated under both probes, amend both.
- Before/after numbers in a commit body come from `wf_bands before` / `wf_bands after` or from a
  scratch script, and name the export span they were measured on.

## §5a Worktree setup
Run from inside the new worktree, with the main checkout's path:

```bash
source .claude/workflow-helpers.sh && wf_link /Users/justin/Git/claude/claude-farmer && wf_test && wf_build
```

`inputs/` is a tracked directory holding ignored files, so `wf_link` links its contents, not the
directory. Nothing writes into `inputs/` except `pull_weather_data.sh`; do not run that from a
worktree (it would replace the link target's `weather.csv` for every session).

## §6 Tests and gates
Runner: `wf_test` (= `.venv/bin/python -m unittest discover -v tests`). Gate: `wf_test` and
`wf_build` both `EXIT=0`.

- Discovery, stdlib `unittest`, files `tests/test_*.py`. Tests add `lib/` to `sys.path`
  themselves; there is no package. Confirm a new test's name appears in the `-v` log.
- **Unit tests use synthetic readings** (lists of `{"dt", "tom", "pep", …}` dicts) and never read
  `inputs/` — that data is gitignored, grows weekly, and would make yesterday's green today's red.
- `wf_build` is the only check that exercises real data and the template. It needs `inputs/`;
  with none it fails with `FileNotFoundError`, which is the environment, not your change.
- The template's JS has no unit tests. A change to `lib/dashboard_template.html` is checked with
  `wf_sentences before` / `after` (jsdom; needs the local, gitignored `npm i jsdom`), and a branch
  today's data does not take is forced by editing the DATA/CFG JSON in a copy of the built page.
  Charts are stubbed: a drawing change is still eyeballed, and the closing comment says which.
- Network: `fetch_weather.py` / `pull_weather_data.sh` need Open-Meteo. Nothing in the gate does.
- Before writing a test on a statistical claim, check the claim survives the ideal case. (#1: a
  constant-rate series could not distinguish the two estimators; the first test survived
  mutation because its premise was wrong.)

## §8 Closing
The decision record is `config.json`'s comment keys (§0). Amend when the outcome changes what is
believed about a band, a plan or an instrument — including "no change, and here is the number".
A change to an actual threshold or to `verified` is the user's call: propose it in the closing
comment, do not make it.

Cross-post to the issues the evidence bears on; the 2026-09-19 audit issues (#1–#18) cite each
other's numbers, and several quote figures that a fix elsewhere supersedes.
