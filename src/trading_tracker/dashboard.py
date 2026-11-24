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


# Fields of a realized trade the page reads; everything else stays in `trade report`.
TRADE_FIELDS = (
    "id", "exit_trade_id", "entry_trade_ids", "ticker", "direction", "shares", "avg_entry_price",
    "avg_exit_price", "gross_pnl", "entry_commission", "exit_commission", "total_commission", "net_pnl",
    "pnl_percent", "opened_at", "closed_at", "hold_duration_days", "currency", "strategy", "setup", "tags",
    "stop_loss", "initial_risk", "r_multiple", "entry_notes", "exit_notes", "what_worked", "what_failed",
    "lesson", "rating",
)


def _compact(report: dict[str, Any]) -> dict[str, Any]:
    """Keep the embedded data small for books with tens of thousands of fills (scalping, perps)."""
    trades = []
    for t in report.get("closed_trades", []):
        row = {k: t.get(k) for k in TRADE_FIELDS if t.get(k) not in (None, "", [])}
        ids = row.get("entry_trade_ids")
        if isinstance(ids, list) and len(ids) > 12:
            row["entry_trade_ids"] = ids[:5] + [f"… {len(ids) - 10} more"] + ids[-5:]
        trades.append(row)
    return {**report, "closed_trades": trades}


def render(report: dict[str, Any], *, title: str = "Trading Journal") -> str:
    data = json.dumps(_compact(report), ensure_ascii=False, default=str, separators=(",", ":"))
    # Keep the JSON inert inside <script>: no "</script>" or "<!--" can break out.
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = TEMPLATE.read_text(encoding="utf-8")
    return page.replace("__TITLE__", html.escape(title)).replace("__DATA__", data)
