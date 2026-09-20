"""
parse_ecowitt.py
================
Read EcoWitt GW1200 daily-log .xlsx exports from the inputs/ folder and return a
single, clean, time-sorted list of readings.

EcoWitt "all_..." exports have a two-row header:

    row 0 (group):  Time | Indoor | ... | Tomato Probe | ... | Pepper Probe | ... | WFC01-... | ...
    row 1 (sub):         | Temperature(F) | ... | Soil Moisture(%) | AD | Soil Moisture(%) | AD | Water Total(L) | ...

Columns are located by matching (group, sub) names from config rather than by a
fixed index, so re-ordered or renamed exports keep working as long as the probe
group names in config.json match what you set on the EcoWitt console.

Missing sensor values are exported as the literal string "-"; those become None.

A column that fails to resolve used to be indistinguishable from a sensor that
dropped samples: `cell(None)` returned None for every row of that day and the
channel simply went quiet (#18). load_readings() now returns a report naming
every file a channel failed in, and refuses to return at all when a REQUIRED
channel is missing from the NEWEST export -- the one case where a console
rename would blank the live end of the series.
"""

from __future__ import annotations

import glob
import os
import re
from datetime import datetime, timedelta
from typing import Optional

import openpyxl

DATA_SHEET = "result_list"

# Channels whose absence makes the build wrong rather than merely thinner: every
# verdict on the page is scored on these. The rest are diagnostic (sensor health,
# _sensor_health) and are reported, never fatal.
REQUIRED = ("tom", "pep", "water")
DIAGNOSTIC = ("tom_ad", "pep_ad", "v_tom", "v_pep")
CHANNELS = REQUIRED + DIAGNOSTIC
LABEL = {"tom": "tomato moisture", "pep": "pepper moisture", "water": "water total",
         "tom_ad": "tomato AD", "pep_ad": "pepper AD",
         "v_tom": "tomato voltage", "v_pep": "pepper voltage"}


class ParseError(RuntimeError):
    """The exports cannot be read as configured; str() is one problem per line."""


def _norm(x) -> str:
    """Lowercase and strip non-alphanumerics, so 'WFC01-00003D29' and
    '[WFC01] Water Flow' both normalize to a string containing 'wfc01'."""
    return re.sub(r"[^a-z0-9]", "", str(x).lower())


def _num(v) -> Optional[float]:
    """Coerce a cell to float, treating '-'/blank/text as missing (None)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_time(v) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _find_column(group_row, sub_row, group_name, sub_name,
                 by_prefix: bool = False) -> tuple[Optional[int], Optional[str]]:
    """
    Return (column index, note) for the column whose sub header == sub_name and
    whose group header names group_name. Group headers are merged cells, so the
    group label only appears on the first column of the group and the following
    columns carry None — we forward-fill it.

    An EXACT group name wins outright. Only when no column matches exactly do we
    fall back to prefix / normalized-substring, which tolerates EcoWitt header
    renames ('WFC01-00003D29' -> '[WFC01] Water Flow') but would otherwise let a
    look-alike group ('Tomato Probe 2') capture the column on export order
    alone (#18).

    by_prefix says the configured name is a PREFIX by design (water.group_prefix
    is 'WFC01', which no header ever equals), so a fuzzy hit there is the
    contract and is not worth a note; a probe's `group` is a full name, so a
    fuzzy hit there means the console was renamed and the load report says so.
    Either way an ambiguous match is noted, since the loser is arbitrary.
    """
    filled_group = []
    last = None
    for g in group_row:
        if g is not None and str(g).strip() != "":
            last = str(g).strip()
        filled_group.append(last)

    pairs = [(i, str(g).strip(), str(sub).strip())
             for i, (g, sub) in enumerate(zip(filled_group, sub_row))
             if g is not None and sub is not None]
    subs = [(i, g) for i, g, sub in pairs if sub == sub_name]

    exact = [(i, g) for i, g in subs if g == group_name]
    if exact:
        return exact[0][0], (None if len(exact) == 1 else
                             f"{len(exact)} columns are {group_name!r}/{sub_name!r}; took column {exact[0][0]}")

    gn = _norm(group_name)
    fuzzy = [(i, g) for i, g in subs if g.startswith(group_name) or gn in _norm(g)]
    if fuzzy:
        note = None if by_prefix else f"no exact {group_name!r}; matched {fuzzy[0][1]!r}"
        if len(fuzzy) > 1:
            note = ((note or f"matched {fuzzy[0][1]!r}")
                    + f" over {len(fuzzy) - 1} other candidate(s)")
        return fuzzy[0][0], note
    return None, None


def _find_voltage(group_row, sub_row, ch: str) -> Optional[int]:
    """
    Locate a per-channel sensor voltage column. EcoWitt has renamed these
    across firmware versions, e.g. 'Soil Moisture Sensor CH1(V)' ->
    '[CH1] Tomato Soil Sensor(V)', so match on the channel tag + '(V)'.

    The tag has to be a whole token: a bare `'ch1' in s` also matched CH10-CH16,
    so adding the planned deep WH51L probe would have silently re-pointed the
    health check at the wrong sensor (#18).
    """
    tag = re.compile(r"(?<![a-z0-9])" + re.escape(ch.lower()) + r"(?![0-9])")
    for i, s in enumerate(sub_row):
        if s is None:
            continue
        s = str(s).strip().lower()
        if "(v)" in s and tag.search(s):
            return i
    return None


def _resolve_columns(group_row, sub_row, config: dict) -> tuple[dict, dict]:
    """(column index per channel, notes) for one file's header pair. An index is
    None where the header does not carry that channel; `notes` holds one line
    per channel that needed anything but an unambiguous exact match."""
    cols = config["columns"]
    tom_group = config["probes"]["tomato"]["group"]
    pep_group = config["probes"]["pepper"]["group"]
    sm_sub = cols["soil_moisture_sub"]
    found = {
        "tom": _find_column(group_row, sub_row, tom_group, sm_sub),
        "pep": _find_column(group_row, sub_row, pep_group, sm_sub),
        "water": _find_column(group_row, sub_row, config["water"]["group_prefix"],
                              cols["water_total_sub"], by_prefix=True),
        # Diagnostic channels: raw AD count per probe + per-channel sensor
        # voltage. Used for sensor-health checks (a battery swap or chemistry
        # change shifts the derived %, and a failing/uncoupled probe shows a
        # collapsing daily AD range) — see analyze._sensor_health.
        "tom_ad": _find_column(group_row, sub_row, tom_group, "AD"),
        "pep_ad": _find_column(group_row, sub_row, pep_group, "AD"),
        "v_tom": (_find_voltage(group_row, sub_row,
                                config["probes"]["tomato"]["voltage_channel"]), None),
        "v_pep": (_find_voltage(group_row, sub_row,
                                config["probes"]["pepper"]["voltage_channel"]), None),
    }
    idx = {ch: found[ch][0] for ch in CHANNELS}
    notes = {ch: found[ch][1] for ch in CHANNELS if found[ch][1]}
    return idx, notes


def load_readings(inputs_dir: str, config: dict) -> tuple[list[dict], dict]:
    """
    Load every .xlsx in inputs_dir and return (readings, report).

    readings is a de-duplicated, time-sorted list of {"dt": datetime, "tom",
    "pep", "water", "tom_ad", "pep_ad", "v_tom", "v_pep"}.

    report is what the load itself found, for the build log to print:
      files      workbooks that contributed rows
      rows       data rows with a parseable timestamp
      readings   distinct timestamps kept (duplicates merged field-wise)
      skipped    [(filename, why)] -- a workbook that contributed nothing
      unresolved {channel: [filename, ...]} -- header did not carry it
      notes      [(filename, channel, what the match settled for)]
      missing    {channel: pct} -- kept readings whose value is None

    Raises ParseError when a REQUIRED channel is unresolved in the NEWEST file:
    that is a live console rename, and every later build would score the page
    on a channel that is silently all-None.
    """
    files = sorted(glob.glob(os.path.join(inputs_dir, "*.xlsx")))
    if not files:
        raise FileNotFoundError(f"No .xlsx files found in {inputs_dir}")
    newest = os.path.basename(files[-1])

    by_dt: dict[datetime, dict] = {}
    report: dict = {"files": 0, "rows": 0, "readings": 0,
                    "skipped": [], "unresolved": {}, "notes": [], "missing": {}}

    for path in files:
        name = os.path.basename(path)
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[DATA_SHEET] if DATA_SHEET in wb.sheetnames else wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if len(rows) < 3:
            report["skipped"].append((name, f"{len(rows)} row(s), no data"))
            continue

        idx, notes = _resolve_columns(rows[0], rows[1], config)
        for ch, note in notes.items():
            report["notes"].append((name, ch, note))
        for ch in CHANNELS:
            if idx[ch] is None:
                report["unresolved"].setdefault(ch, []).append(name)

        kept = 0
        for r in rows[2:]:
            dt = _parse_time(r[0])
            if dt is None:
                continue
            kept += 1
            rec = by_dt.setdefault(dt, {"dt": dt, **{ch: None for ch in CHANNELS}})
            # Merge FIELD-WISE. `by_dt[dt] = {...}` replaced the whole record
            # with whichever file sorted last, so a re-downloaded partial day
            # carrying "-" for a channel dropped a good value from the file
            # before it (#18). Later wins only where it has something to say.
            for ch in CHANNELS:
                v = _num(r[idx[ch]]) if idx[ch] is not None else None
                if v is not None:
                    rec[ch] = v
        if kept:
            report["files"] += 1
            report["rows"] += kept
        else:
            report["skipped"].append((name, f"{len(rows) - 2} data row(s), none timestamped"))

    stale = [ch for ch in REQUIRED if newest in report["unresolved"].get(ch, ())]
    if stale:
        raise ParseError(
            "\n".join(f"{LABEL[ch]}: no column in {newest}, the newest export"
                       for ch in stale)
            + "\nThe EcoWitt console group names have changed. Update "
              "probes.*.group / water.group_prefix in config.json to match.")

    readings = [by_dt[k] for k in sorted(by_dt)]
    report["readings"] = len(readings)
    for ch in CHANNELS:
        n = sum(1 for r in readings if r[ch] is None)
        if n:
            report["missing"][ch] = round(100.0 * n / len(readings), 1)
    return readings, report


def summary_lines(report: dict) -> list[str]:
    """The report's diagnostics, one line each, for the build log. Silent when
    every file resolved every channel and nothing was skipped."""
    out = []
    for name, why in report["skipped"]:
        out.append(f"skipped {name}: {why}")
    for ch, names in report["unresolved"].items():
        where = names[0] if len(names) == 1 else f"{len(names)} files, first {names[0]}"
        out.append(f"{LABEL[ch]}: no column in {where}"
                   + ("" if ch in REQUIRED else " (diagnostic)"))
    seen: dict = {}
    for name, ch, note in report["notes"]:
        seen.setdefault((ch, note), []).append(name)
    for (ch, note), names in seen.items():
        where = names[0] if len(names) == 1 else f"{len(names)} files, first {names[0]}"
        out.append(f"{LABEL[ch]}: {note} -- {where}")
    if report["missing"]:
        out.append("missing values: "
                   + ", ".join(f"{LABEL[ch]} {p}%" for ch, p in report["missing"].items()))
    return out


def missing_days(readings: list[dict]) -> list[str]:
    """
    Calendar dates between the first and last reading that have no reading at
    all -- an export that was never pulled. Nothing downstream can infer this:
    the WFC01 is an odometer, so the day after a gap silently books the missing
    day's water as its own (2026-09-03 was absent and 09-04 showed 108.4 L
    against ~55 either side, #8). analyze._water() handles the draw; this is
    the list the build log and the page name.
    """
    if not readings:
        return []
    have = {r["dt"].date() for r in readings}
    d, end = min(have), max(have)
    out = []
    while d < end:
        d += timedelta(days=1)
        if d not in have:
            out.append(d.isoformat())
    return out


if __name__ == "__main__":
    import json
    import sys

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = json.load(open(os.path.join(here, "config.json")))
    recs, rep = load_readings(os.path.join(here, "inputs"), cfg)
    print(f"Loaded {len(recs)} readings from {rep['files']} files")
    for line in summary_lines(rep):
        print(" ", line)
    print("first:", recs[0])
    print("last :", recs[-1])
