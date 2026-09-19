"""
render.py
=========
Fill lib/dashboard_template.html with the computed DATA / CFG objects and return
the final standalone HTML string.

The template carries three placeholders:
  __DATA__        -> the DATA object (JSON, read by the in-page charts)
  __CFG__         -> the CFG object  (JSON: setpoints, plan, gauge narrative, ...)
  __FOOTER_META__ -> a short "interval, window, count" footer string
"""

from __future__ import annotations

import html as html_mod
import json
import os
import re

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_template.html")


def _script_json(obj) -> str:
    r"""JSON for a <script> block. The HTML parser ends the block at the first
    `</script` and changes mode at `<!--`, whatever JS string they sit in, and
    schedule_log is free text; `\u003c` is the same character to JSON and JS."""
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c")


def render(data: dict, cfg: dict) -> str:
    with open(TEMPLATE, encoding="utf-8") as fh:
        html = fh.read()
    fill = {
        "DATA": _script_json(data),
        "CFG": _script_json(cfg),
        "FOOTER_META": html_mod.escape(cfg.get("footer_meta", "")),
    }
    # One pass: a placeholder name inside a filled-in value is left as text.
    return re.sub(r"__(DATA|CFG|FOOTER_META)__", lambda m: fill[m.group(1)], html)
