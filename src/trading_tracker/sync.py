"""Export the journal to an Obsidian vault as Dataview-friendly markdown.

Layout inside ``<vault>/<trading_folder>/``:

* ``Daily/YYYY-MM-DD.md`` — fills, realized P&L and journal entries of the day.
* ``Positions/<TICKER>.md`` — one note per ticker: open position (if any),
  every realized result, fills, reviews.
* ``Dashboard.html`` — the interactive dashboard (open it from the vault).

All numbers come from the ledger report. Files are regenerated on each export;
anything you want to keep by hand belongs below the ``%% notes %%`` marker,
which is preserved.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from trading_tracker import dashboard, db, report
from trading_tracker.models import Config, currency_symbol

TEMPLATE_DIR = Path(__file__).parent / "templates"
NOTES_MARKER = "%% notes %%"


def _money(value: Any, ccy: str = "USD") -> str:
    if value is None:
        return "-"
    sign = "-" if value < 0 else ""
    return f"{sign}{currency_symbol(ccy)}{abs(value):,.2f}"


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), keep_trailing_newline=True)
    env.filters["money"] = _money
    env.filters["num"] = lambda x: (f"{x:,.8f}".rstrip("0").rstrip(".") if x is not None else "-")
    return env


def _write(path: Path, content: str) -> None:
    """Write a generated note, keeping the user's text below the notes marker."""
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if NOTES_MARKER in old:
            kept = old.split(NOTES_MARKER, 1)[1]
            content = content.split(NOTES_MARKER, 1)[0] + NOTES_MARKER + kept
    path.write_text(content, encoding="utf-8")


def export_to_obsidian(conn: sqlite3.Connection, cfg: Config) -> int:
    """Export daily logs, position notes and the dashboard. Returns the number of files."""
    folder = Path(cfg.obsidian.vault_path).expanduser() / cfg.obsidian.trading_folder
    daily_dir = folder / "Daily"
    positions_dir = folder / "Positions"
    daily_dir.mkdir(parents=True, exist_ok=True)
    positions_dir.mkdir(parents=True, exist_ok=True)

    rep = report.build_report(conn, price_provider="cache")
    trades = db.get_trades(conn)
    for t in trades:
        try:
            t["tags"] = json.loads(t.get("tags") or "[]")
        except (TypeError, json.JSONDecodeError):
            t["tags"] = []
    env = _env()
    count = 0

    # ── Daily notes ──────────────────────────────────────────────────────
    fills_by_day: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        fills_by_day[t["timestamp"][:10]].append(t)
    closed_by_day: dict[str, list[dict]] = defaultdict(list)
    for c in rep["closed_trades"]:
        closed_by_day[c["closed_at"][:10]].append(c)
    journal_by_day: dict[str, list[dict]] = defaultdict(list)
    for j in rep["journal"]:
        journal_by_day[j["date"]].append(j)

    daily = env.get_template("daily_log.md.j2")
    for day in sorted(set(fills_by_day) | set(closed_by_day) | set(journal_by_day)):
        closed = closed_by_day.get(day, [])
        pnl_by_ccy: dict[str, float] = defaultdict(float)
        for c in closed:
            pnl_by_ccy[c["currency"]] += c["net_pnl"]
        _write(daily_dir / f"{day}.md", daily.render(
            date=day,
            trades=fills_by_day.get(day, []),
            closed_trades=closed,
            journal=journal_by_day.get(day, []),
            pnl_by_ccy={k: round(v, 2) for k, v in sorted(pnl_by_ccy.items())},
            marker=NOTES_MARKER,
        ))
        count += 1

    # ── Position notes (one per ticker) ──────────────────────────────────
    open_by_ticker = {p["ticker"]: p for p in rep["open_positions"]}
    closed_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for c in rep["closed_trades"]:
        closed_by_ticker[c["ticker"]].append(c)
    fills_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        fills_by_ticker[t["ticker"]].append(t)

    note = env.get_template("position_note.md.j2")
    for ticker in sorted(fills_by_ticker):
        fills = fills_by_ticker[ticker]
        closed = closed_by_ticker.get(ticker, [])
        pos = open_by_ticker.get(ticker)
        ccy = (pos or {}).get("currency") or fills[0].get("currency") or "USD"
        _write(positions_dir / f"{_safe(ticker)}.md", note.render(
            ticker=ticker,
            status="open" if pos else "closed",
            position=pos,
            closed=closed,
            fills=fills,
            currency=ccy,
            realized=round(sum(c["net_pnl"] for c in closed), 2),
            strategy=(pos or {}).get("strategy") or next((c.get("strategy") for c in closed if c.get("strategy")), None),
            first_trade=fills[0]["timestamp"],
            last_trade=fills[-1]["timestamp"],
            tags=sorted({tag for t in fills for tag in t["tags"]}),
            marker=NOTES_MARKER,
        ))
        count += 1

    (folder / "Dashboard.html").write_text(dashboard.render(rep, title="Trading Journal"), encoding="utf-8")
    count += 1
    return count


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_. " else "_" for ch in name)
