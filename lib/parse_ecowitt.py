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


def _find_column(group_row, sub_row, group_name, sub_name) -> Optional[int]:
    """
    Return the column index whose group header == group_name (prefix match ok)
    and sub header == sub_name. Group headers are merged cells, so the group
    label only appears on the first column of the group and the following
    columns carry None — we forward-fill it.
    """
    filled_group = []
    last = None
    for g in group_row:
        if g is not None and str(g).strip() != "":
            last = str(g).strip()
        filled_group.append(last)

    gn = _norm(group_name)
    for i, (g, s) in enumerate(zip(filled_group, sub_row)):
        if g is None or s is None:
            continue
        g, s = str(g).strip(), str(s).strip()
        # Group match tolerates EcoWitt header renames (e.g. 'WFC01-00003D29'
        # -> '[WFC01] Water Flow'): exact, prefix, or normalized-substring.
        group_ok = g == group_name or g.startswith(group_name) or gn in _norm(g)
        if s == sub_name and group_ok:
            return i
    return None


def _find_voltage(group_row, sub_row, ch: str) -> Optional[int]:
    """
    Locate a per-channel sensor voltage column. EcoWitt has renamed these
    across firmware versions, e.g. 'Soil Moisture Sensor CH1(V)' ->
    '[CH1] Tomato Soil Sensor(V)', so match on the channel tag + '(V)'.
    """
    for i, s in enumerate(sub_row):
        if s is None:
            continue
        s = str(s).strip()
        if ch.lower() in s.lower() and "(v)" in s.lower():
            return i
    return None


def _resolve_columns(group_row, sub_row, config: dict) -> dict[str, Optional[int]]:
    """Column index per channel for one file's header pair, None where the
    header does not carry that channel."""
    cols = config["columns"]
    tom_group = config["probes"]["tomato"]["group"]
    pep_group = config["probes"]["pepper"]["group"]
    sm_sub = cols["soil_moisture_sub"]
    return {
        "tom": _find_column(group_row, sub_row, tom_group, sm_sub),
        "pep": _find_column(group_row, sub_row, pep_group, sm_sub),
        "water": _find_column(group_row, sub_row, config["water"]["group_prefix"],
                              cols["water_total_sub"]),
        # Diagnostic channels: raw AD count per probe + per-channel sensor
        # voltage. Used for sensor-health checks (a battery swap or chemistry
        # change shifts the derived %, and a failing/uncoupled probe shows a
        # collapsing daily AD range) — see analyze._sensor_health.
        "tom_ad": _find_column(group_row, sub_row, tom_group, "AD"),
        "pep_ad": _find_column(group_row, sub_row, pep_group, "AD"),
        "v_tom": _find_voltage(group_row, sub_row, "CH1"),
        "v_pep": _find_voltage(group_row, sub_row, "CH2"),
    }


def load_readings(inputs_dir: str, config: dict) -> tuple[list[dict], dict]:
    """
    Load every .xlsx in inputs_dir and return (readings, report).

    readings is a de-duplicated, time-sorted list of {"dt": datetime, "tom",
    "pep", "water", "tom_ad", "pep_ad", "v_tom", "v_pep"}.

    report is what the load itself found, for the build log to print:
      files      workbooks that contributed rows
      rows       data rows with a parseable timestamp
      readings   distinct timestamps kept
      skipped    [(filename, why)] -- a workbook that contributed nothing
      unresolved {channel: [filename, ...]} -- header did not carry it
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
                    "skipped": [], "unresolved": {}, "missing": {}}

    for path in files:
        name = os.path.basename(path)
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[DATA_SHEET] if DATA_SHEET in wb.sheetnames else wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if len(rows) < 3:
            report["skipped"].append((name, f"{len(rows)} row(s), no data"))
            continue

        idx = _resolve_columns(rows[0], rows[1], config)
        for ch in CHANNELS:
            if idx[ch] is None:
                report["unresolved"].setdefault(ch, []).append(name)

        kept = 0
        for r in rows[2:]:
            dt = _parse_time(r[0])
            if dt is None:
                continue
            kept += 1
            by_dt[dt] = {"dt": dt,
                         **{ch: (_num(r[idx[ch]]) if idx[ch] is not None else None)
                            for ch in CHANNELS}}
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
