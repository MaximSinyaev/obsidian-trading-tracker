"""SQLite database: initialization, migrations, CRUD operations."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"


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
    """Insert a trade and return its id.

    If this trade reduces an existing position (e.g. SELL against a long),
    a closed_trades record with P&L is created automatically.
    """
    ticker_upper = ticker.upper()
    action_upper = action.upper()
    ts = timestamp or datetime.now().isoformat(timespec="seconds")

    # Snapshot position BEFORE inserting this trade
    pos_before = get_position(conn, ticker_upper)

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
            ticker_upper,
            action_upper,
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
    trade_id: int = cur.lastrowid  # type: ignore[assignment]

    # Auto-close: if this trade reduces an existing position, record it
    _auto_record_close(
        conn, ticker_upper, action_upper, shares, price,
        commission, ts, trade_id, pos_before, strategy, currency, instrument,
    )

    conn.commit()
    return trade_id


def _auto_record_close(
    conn: sqlite3.Connection,
    ticker: str,
    action: str,
    shares: float,
    price: float,
    commission: float,
    timestamp: str,
    exit_trade_id: int,
    pos_before: dict[str, Any] | None,
    strategy: str | None = None,
    currency: str = "USD",
    instrument: str = "stock",
) -> None:
    """Create a closed_trades record if this trade reduces a position."""
    if pos_before is None:
        return

    net_before = pos_before["net_shares"]
    is_long = net_before > 0

    # Is this a counter-trade? SELL against long, or BUY against short
    is_counter = (is_long and action == "SELL") or (not is_long and action == "BUY")
    if not is_counter:
        return

    abs_before = abs(net_before)
    shares_closed = min(shares, abs_before)
    avg_entry = pos_before["avg_cost"]

    # Entry trade IDs
    entry_action = "BUY" if is_long else "SELL"
    entry_rows = conn.execute(
        "SELECT id FROM trades WHERE ticker = ? AND action = ? ORDER BY timestamp",
        (ticker, entry_action),
    ).fetchall()
    entry_trade_ids = [r["id"] for r in entry_rows]

    # P&L
    if is_long:
        gross_pnl = shares_closed * price - shares_closed * avg_entry
    else:
        gross_pnl = shares_closed * avg_entry - shares_closed * price

    net_pnl = gross_pnl - commission
    cost_basis = shares_closed * avg_entry
    pnl_percent = (net_pnl / cost_basis) * 100 if cost_basis else 0

    # Hold duration
    try:
        dt_entry = datetime.fromisoformat(pos_before["first_trade"])
        dt_exit = datetime.fromisoformat(timestamp)
        hold_days = (dt_exit - dt_entry).total_seconds() / 86400
    except (ValueError, TypeError):
        hold_days = None

    conn.execute(
        """
        INSERT INTO closed_trades
            (ticker, direction, entry_trade_ids, exit_trade_ids, shares,
             avg_entry_price, avg_exit_price, entry_avg_cost, total_commission,
             gross_pnl, net_pnl, pnl_percent, hold_duration_days, strategy,
             currency, instrument)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker,
            "long" if is_long else "short",
            json.dumps(entry_trade_ids),
            json.dumps([exit_trade_id]),
            shares_closed,
            avg_entry,
            price,
            avg_entry,
            commission,
            round(gross_pnl, 2),
            round(net_pnl, 2),
            round(pnl_percent, 2),
            round(hold_days, 2) if hold_days is not None else None,
            strategy,
            currency,
            instrument,
        ),
    )


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
        "currency",
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

    This is a convenience wrapper around add_trade that:
    1. Validates the position exists and shares are valid
    2. Creates the counter-trade (add_trade auto-creates closed_trades with P&L)
    3. Adds optional review notes (what_worked, lesson, etc.)
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
    close_action = "SELL" if is_long else "BUY"

    # add_trade auto-creates closed_trades via _auto_record_close
    currency = pos.get("currency", "USD")
    instrument = pos.get("instrument", "stock")
    add_trade(
        conn, ticker, close_action, shares, exit_price,
        commission=commission, strategy=strategy, currency=currency,
        instrument=instrument,
    )

    # Add review notes to the auto-created closed_trades record
    review_fields: dict[str, Any] = {}
    if what_worked is not None:
        review_fields["what_worked"] = what_worked
    if what_failed is not None:
        review_fields["what_failed"] = what_failed
    if lesson is not None:
        review_fields["lesson"] = lesson
    if rating is not None:
        review_fields["rating"] = rating

    if review_fields:
        ct = conn.execute(
            "SELECT id FROM closed_trades WHERE ticker = ? ORDER BY id DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        if ct:
            set_clause = ", ".join(f"{k} = ?" for k in review_fields)
            values = list(review_fields.values()) + [ct["id"]]
            conn.execute(
                f"UPDATE closed_trades SET {set_clause} WHERE id = ?", values
            )
            conn.commit()

    # Compute result for caller
    if is_long:
        gross_pnl = shares * exit_price - shares * avg_entry
    else:
        gross_pnl = shares * avg_entry - shares * exit_price

    net_pnl = gross_pnl - commission
    cost_basis = shares * avg_entry
    pnl_percent = (net_pnl / cost_basis) * 100 if cost_basis else 0
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


def _compute_position_from_trades(trades: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Compute position by walking trades chronologically (avg cost method).

    - Adding to a position: avg_cost = weighted average of old + new
    - Reducing a position: avg_cost stays the same
    - Full close (net=0): avg_cost resets
    - Position flip: avg_cost = price of the flip trade
    """
    net = 0.0
    avg = 0.0
    total_commission = 0.0
    first_ts: str | None = None
    last_ts: str | None = None
    count = 0

    for t in trades:
        action = t["action"]
        shares = t["shares"]
        price = t["price"]
        count += 1
        total_commission += t.get("commission", 0) or 0
        last_ts = t["timestamp"]

        if action == "BUY":
            if net >= 0:
                # Flat or long — adding to long
                avg = (net * avg + shares * price) / (net + shares)
                net += shares
            else:
                # Short — covering
                net += shares
                if net > 0:
                    avg = price
                elif net == 0:
                    avg = 0.0
        elif action == "SELL":
            if net <= 0:
                # Flat or short — adding to short
                avg = (abs(net) * avg + shares * price) / (abs(net) + shares)
                net -= shares
            else:
                # Long — selling
                net -= shares
                if net < 0:
                    avg = price
                elif net == 0:
                    avg = 0.0

        # Track first trade of current position epoch
        if net != 0 and first_ts is None:
            first_ts = t["timestamp"]
        elif net == 0:
            first_ts = None

    if net == 0:
        return None

    return {
        "ticker": trades[0]["ticker"],
        "net_shares": net,
        "avg_cost": avg,
        "total_commission": total_commission,
        "first_trade": first_ts,
        "last_trade": last_ts,
        "trade_count": count,
        "currency": trades[0].get("currency", "USD"),
        "instrument": trades[0].get("instrument", "stock"),
    }


def get_position(conn: sqlite3.Connection, ticker: str) -> dict[str, Any] | None:
    """Get a single open position by ticker."""
    rows = conn.execute(
        "SELECT * FROM trades WHERE ticker = ? ORDER BY timestamp, id",
        (ticker.upper(),),
    ).fetchall()
    if not rows:
        return None
    return _compute_position_from_trades([dict(r) for r in rows])


def get_positions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Get all open positions."""
    rows = conn.execute(
        "SELECT * FROM trades ORDER BY ticker, timestamp, id"
    ).fetchall()
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        d = dict(r)
        by_ticker.setdefault(d["ticker"], []).append(d)
    positions = []
    for trades in by_ticker.values():
        pos = _compute_position_from_trades(trades)
        if pos is not None:
            positions.append(pos)
    return positions


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
