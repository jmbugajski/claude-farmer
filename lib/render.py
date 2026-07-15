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

import json
import os

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_template.html")


def render(data: dict, cfg: dict) -> str:
    html = open(TEMPLATE, encoding="utf-8").read()
    html = html.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    html = html.replace("__CFG__", json.dumps(cfg, ensure_ascii=False))
    html = html.replace("__FOOTER_META__", cfg.get("footer_meta", ""))
    return html
