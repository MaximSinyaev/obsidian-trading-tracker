"""SQLite database: initialization, migrations, CRUD operations."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent / "schema"


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Create database and apply all migrations."""
    conn = get_connection(db_path)
    current = _get_schema_version(conn)
    migrations = sorted(SCHEMA_DIR.glob("*.sql"))
    for migration in migrations:
        version = int(migration.stem.split("_")[0])
        if version > current:
            conn.executescript(migration.read_text())
    return conn


def _get_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] or 0
    except sqlite3.OperationalError:
        return 0


def add_trade(
    conn: sqlite3.Connection,
    ticker: str,
    action: str,
    shares: float,
    price: float,
    *,
    commission: float = 0,
    timestamp: str | None = None,
    strategy: str | None = None,
    setup: str | None = None,
    confidence: int | None = None,
    stop_loss: float | None = None,
    target_1: float | None = None,
    target_2: float | None = None,
    entry_plan: str | None = None,
    note_path: str | None = None,
    source: str = "manual",
    tags: list[str] | None = None,
    notes: str | None = None,
    position_group: str | None = None,
    asset_type: str = "stock",
    instrument: str = "stock",
    currency: str = "USD",
    leverage: float = 1.0,
) -> int:
    """Insert a trade and return its id."""
    ts = timestamp or datetime.now().isoformat(timespec="seconds")
    tags_json = json.dumps(tags or [])
    cur = conn.execute(
        """
        INSERT INTO trades
            (ticker, action, shares, price, commission, timestamp, strategy, setup,
             confidence, stop_loss, target_1, target_2, entry_plan, note_path,
             source, tags, notes, position_group, asset_type, instrument, currency, leverage)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker.upper(),
            action.upper(),
            shares,
            price,
            commission,
            ts,
            strategy,
            setup,
            confidence,
            stop_loss,
            target_1,
            target_2,
            entry_plan,
            note_path,
            source,
            tags_json,
            notes,
            position_group,
            asset_type,
            instrument,
            currency,
            leverage,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def edit_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    **fields: Any,
) -> bool:
    """Update fields on an existing trade. Returns True if a row was updated."""
    allowed = {
        "ticker",
        "action",
        "shares",
        "price",
        "commission",
        "timestamp",
        "strategy",
        "setup",
        "confidence",
        "stop_loss",
        "target_1",
        "target_2",
        "entry_plan",
        "note_path",
        "source",
        "tags",
        "notes",
        "position_group",
        "asset_type",
        "instrument",
        "leverage",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False
    if "tags" in updates and isinstance(updates["tags"], list):
        updates["tags"] = json.dumps(updates["tags"])
    if "ticker" in updates:
        updates["ticker"] = updates["ticker"].upper()
    if "action" in updates:
        updates["action"] = updates["action"].upper()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [trade_id]
    cur = conn.execute(f"UPDATE trades SET {set_clause} WHERE id = ?", values)
    conn.commit()
    return cur.rowcount > 0


def delete_trade(conn: sqlite3.Connection, trade_id: int) -> bool:
    """Delete a trade by ID. Returns True if a row was deleted."""
    cur = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    conn.commit()
    return cur.rowcount > 0


def close_position(
    conn: sqlite3.Connection,
    ticker: str,
    shares: float,
    exit_price: float,
    *,
    commission: float = 0,
    strategy: str | None = None,
    what_worked: str | None = None,
    what_failed: str | None = None,
    lesson: str | None = None,
    rating: int | None = None,
) -> dict[str, Any]:
    """Close (fully or partially) a position — works for both long and short.

    Long position (net_shares > 0): creates SELL trade, P&L = exit - entry
    Short position (net_shares < 0): creates BUY trade, P&L = entry - exit

    Wrapped in a transaction for atomicity.
    """
    ticker = ticker.upper()

    pos = get_position(conn, ticker)
    if pos is None:
        raise ValueError(f"No open position for {ticker}")

    net = pos["net_shares"]
    is_long = net > 0
    abs_held = abs(net)

    if shares > abs_held:
        raise ValueError(
            f"Cannot close {shares} shares, only {abs_held} held"
            f" ({'long' if is_long else 'short'})"
        )

    avg_entry = pos["avg_cost"]
    # Entry side: BUY for long, SELL for short
    entry_action = "BUY" if is_long else "SELL"
    close_action = "SELL" if is_long else "BUY"

    entry_rows = conn.execute(
        "SELECT id FROM trades WHERE ticker = ? AND action = ? ORDER BY timestamp",
        (ticker, entry_action),
    ).fetchall()
    entry_trade_ids = [r["id"] for r in entry_rows]

    try:
        ts = datetime.now().isoformat(timespec="seconds")
        tags_json = json.dumps([])
        cur = conn.execute(
            """
            INSERT INTO trades
                (ticker, action, shares, price, commission, timestamp, strategy, setup,
                 confidence, stop_loss, target_1, target_2, entry_plan, note_path,
                 source, tags, notes, position_group, asset_type, instrument, currency, leverage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker, close_action, shares, exit_price, commission, ts,
             strategy, None, None, None, None, None, None, None,
             "manual", tags_json, None, None, "stock", "stock", "USD", 1.0),
        )
        exit_trade_id = cur.lastrowid

        # P&L calculation
        if is_long:
            # Long: profit when exit > entry
            gross_pnl = shares * exit_price - shares * avg_entry
        else:
            # Short: profit when entry > exit
            gross_pnl = shares * avg_entry - shares * exit_price

        total_comm = commission
        net_pnl = gross_pnl - total_comm
        cost_basis = shares * avg_entry
        pnl_percent = (net_pnl / cost_basis) * 100 if cost_basis else 0

        # Hold duration
        first_entry = pos["first_trade"]
        try:
            dt_entry = datetime.fromisoformat(first_entry)
            dt_exit = datetime.fromisoformat(ts)
            hold_days = (dt_exit - dt_entry).total_seconds() / 86400
        except (ValueError, TypeError):
            hold_days = None

        conn.execute(
            """
            INSERT INTO closed_trades
                (ticker, direction, entry_trade_ids, exit_trade_ids, shares,
                 avg_entry_price, avg_exit_price, entry_avg_cost, total_commission,
                 gross_pnl, net_pnl, pnl_percent, hold_duration_days, strategy,
                 what_worked, what_failed, lesson, rating)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker,
                "long" if is_long else "short",
                json.dumps(entry_trade_ids),
                json.dumps([exit_trade_id]),
                shares,
                avg_entry,
                exit_price,
                avg_entry,
                total_comm,
                round(gross_pnl, 2),
                round(net_pnl, 2),
                round(pnl_percent, 2),
                round(hold_days, 2) if hold_days is not None else None,
                strategy,
                what_worked,
                what_failed,
                lesson,
                rating,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    remaining = abs_held - shares
    return {
        "ticker": ticker,
        "direction": "long" if is_long else "short",
        "shares": shares,
        "avg_entry": avg_entry,
        "exit_price": exit_price,
        "gross_pnl": round(gross_pnl, 2),
        "net_pnl": round(net_pnl, 2),
        "pnl_percent": round(pnl_percent, 2),
        "remaining_shares": remaining,
        "remaining_avg_cost": avg_entry,
    }


def get_position(conn: sqlite3.Connection, ticker: str) -> dict[str, Any] | None:
    """Get a single open position by ticker."""
    row = conn.execute(
        "SELECT * FROM positions WHERE ticker = ?", (ticker.upper(),)
    ).fetchone()
    return dict(row) if row else None


def get_positions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Get all open positions."""
    rows = conn.execute("SELECT * FROM positions").fetchall()
    return [dict(r) for r in rows]


def get_trade(conn: sqlite3.Connection, trade_id: int) -> dict[str, Any] | None:
    """Get a single trade by id."""
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return dict(row) if row else None


def get_history(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    ticker: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    closed_only: bool = False,
) -> list[dict[str, Any]]:
    """Get trade history with optional filters."""
    if closed_only:
        query = "SELECT * FROM closed_trades WHERE 1=1"
        params: list[Any] = []
        if ticker:
            query += " AND ticker = ?"
            params.append(ticker.upper())
        if from_date:
            query += " AND closed_at >= ?"
            params.append(from_date)
        if to_date:
            query += " AND closed_at <= ?"
            params.append(to_date)
        query += " ORDER BY closed_at DESC LIMIT ?"
        params.append(limit)
    else:
        query = "SELECT * FROM trade_history WHERE 1=1"
        params = []
        if ticker:
            query += " AND ticker = ?"
            params.append(ticker.upper())
        if from_date:
            query += " AND timestamp >= ?"
            params.append(from_date)
        if to_date:
            query += " AND timestamp <= ?"
            params.append(to_date)
        query += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_closed_trades(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Get all closed trades for analytics."""
    rows = conn.execute(
        "SELECT * FROM closed_trades ORDER BY closed_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def validate_db(conn: sqlite3.Connection) -> list[str]:
    """Run consistency checks. Returns list of issues found."""
    issues: list[str] = []

    # Check closed_trades reference valid trade IDs
    closed = conn.execute("SELECT id, entry_trade_ids, exit_trade_ids FROM closed_trades").fetchall()
    for ct in closed:
        for field in ("entry_trade_ids", "exit_trade_ids"):
            try:
                ids = json.loads(ct[field])
                for tid in ids:
                    exists = conn.execute(
                        "SELECT 1 FROM trades WHERE id = ?", (tid,)
                    ).fetchone()
                    if not exists:
                        issues.append(
                            f"closed_trade {ct['id']}: references missing trade {tid} in {field}"
                        )
            except (json.JSONDecodeError, TypeError):
                issues.append(f"closed_trade {ct['id']}: invalid JSON in {field}")

    return issues
