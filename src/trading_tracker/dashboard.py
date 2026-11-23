"""Render the journal report as one self-contained, interactive HTML file.

No network, no build step, no external libraries: data is embedded as JSON and
drawn with a few hundred lines of vanilla JS + SVG. The file opens anywhere
(browser, Obsidian, a Claude artifact) and every number on it comes from
:func:`trading_tracker.report.build_report`.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

TEMPLATE = Path(__file__).parent / "templates" / "dashboard.html"


def render(report: dict[str, Any], *, title: str = "Trading Journal") -> str:
    data = json.dumps(report, ensure_ascii=False, default=str)
    # Keep the JSON inert inside <script>: no "</script>" or "<!--" can break out.
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = TEMPLATE.read_text(encoding="utf-8")
    return page.replace("__TITLE__", html.escape(title)).replace("__DATA__", data)
