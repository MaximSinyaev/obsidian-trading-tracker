"""Export trades and positions to Obsidian vault as markdown files."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from trading_tracker.models import Config

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _get_env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), keep_trailing_newline=True)


def export_to_obsidian(conn: sqlite3.Connection, cfg: Config) -> int:
    """Export daily logs and position notes to the Obsidian vault. Returns file count."""
    vault = Path(cfg.obsidian.vault_path).expanduser()
    folder = vault / cfg.obsidian.trading_folder
    folder.mkdir(parents=True, exist_ok=True)
    daily_dir = folder / "Daily"
    daily_dir.mkdir(exist_ok=True)
    positions_dir = folder / "Positions"
    positions_dir.mkdir(exist_ok=True)

    env = _get_env()
    count = 0

    # ── Daily logs ───────────────────────────────────────────────────────
    trades = conn.execute("SELECT * FROM trades ORDER BY timestamp").fetchall()
    closed = conn.execute("SELECT * FROM closed_trades ORDER BY closed_at").fetchall()

    # Group trades by date
    by_date: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        date = t["timestamp"][:10]
        by_date[date].append(dict(t))

    # Group closed by date
    closed_by_date: dict[str, list[dict]] = defaultdict(list)
    for c in closed:
        date = c["closed_at"][:10]
        closed_by_date[date].append(dict(c))

    daily_tmpl = env.get_template("daily_log.md.j2")
    for date, day_trades in by_date.items():
        day_closed = closed_by_date.get(date, [])
        total_pnl = sum(c["net_pnl"] for c in day_closed)
        content = daily_tmpl.render(
            date=date,
            trades=day_trades,
            trade_count=len(day_trades),
            closed_trades=day_closed,
            total_pnl=round(total_pnl, 2),
        )
        (daily_dir / f"{date}.md").write_text(content)
        count += 1

    # ── Position notes ───────────────────────────────────────────────────
    # Open positions
    open_positions = conn.execute("SELECT * FROM positions").fetchall()
    pos_tmpl = env.get_template("position_note.md.j2")

    for pos in open_positions:
        ticker = pos["ticker"]
        ticker_trades = conn.execute(
            "SELECT * FROM trades WHERE ticker = ? ORDER BY timestamp", (ticker,)
        ).fetchall()
        content = pos_tmpl.render(
            ticker=ticker,
            status="open",
            shares=pos["net_shares"],
            avg_cost=pos["avg_cost"],
            cost_basis=pos["net_shares"] * pos["avg_cost"],
            strategy=ticker_trades[0]["strategy"] if ticker_trades else None,
            first_trade=pos["first_trade"],
            last_trade=pos["last_trade"],
            trade_count=pos["trade_count"],
            trades=[dict(t) for t in ticker_trades],
            closed_info=None,
        )
        (positions_dir / f"{ticker}.md").write_text(content)
        count += 1

    # Closed positions
    for ct in closed:
        ct = dict(ct)
        ticker = ct["ticker"]
        # Skip if there's still an open position (partial close)
        if any(p["ticker"] == ticker for p in open_positions):
            continue
        ticker_trades = conn.execute(
            "SELECT * FROM trades WHERE ticker = ? ORDER BY timestamp", (ticker,)
        ).fetchall()
        content = pos_tmpl.render(
            ticker=ticker,
            status="closed",
            shares=ct["shares"],
            avg_cost=ct["avg_entry_price"],
            cost_basis=ct["shares"] * ct["avg_entry_price"],
            strategy=ct.get("strategy"),
            first_trade=ticker_trades[0]["timestamp"] if ticker_trades else ct["closed_at"],
            last_trade=ct["closed_at"],
            trade_count=len(ticker_trades),
            trades=[dict(t) for t in ticker_trades],
            closed_info=ct,
        )
        (positions_dir / f"{ticker}.md").write_text(content)
        count += 1

    return count
