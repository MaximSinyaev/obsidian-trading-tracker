"""SQLite storage: migrations, CRUD, and the closed_trades cache.

The ``trades`` table is the only source of truth. Positions and realized P&L
are produced by :mod:`trading_tracker.ledger`; ``closed_trades`` is a cache of
the ledger's output that is rebuilt for the affected tickers after every write,
so it can never drift from the trades.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from trading_tracker import ledger

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
LATEST_SCHEMA = max(int(p.stem.split("_")[0]) for p in SCHEMA_DIR.glob("*.sql"))

TRADE_FIELDS = {
    "ticker", "action", "shares", "price", "commission", "timestamp", "strategy",
    "setup", "confidence", "stop_loss", "target_1", "target_2", "entry_plan",
    "note_path", "source", "tags", "notes", "position_group", "asset_type",
    "instrument", "currency", "leverage", "multiplier", "external_id",
}
REVIEW_FIELDS = ("what_worked", "what_failed", "lesson", "rating")


# ── connection & migrations ─────────────────────────────────────────────────


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    if str(db_path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path) if str(db_path) != ":memory:" else ":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) a database and apply pending migrations."""
    conn = get_connection(db_path)
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations. Rebuilds the P&L cache when the schema changed."""
    current = _get_schema_version(conn)
    applied = False
    for migration in sorted(SCHEMA_DIR.glob("*.sql")):
        version = int(migration.stem.split("_")[0])
        if version > current:
            conn.executescript(migration.read_text())
            applied = True
    if applied:
        rebuild_closed_trades(conn)
        conn.commit()
    return _get_schema_version(conn)


def _get_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return row[0] or 0
    except sqlite3.OperationalError:
        return 0


# ── normalisation helpers ───────────────────────────────────────────────────


def normalize_timestamp(value: str | None) -> str:
    """Parse an ISO-ish timestamp and return a sortable ISO string.

    Accepts ``2025-01-15``, ``2025-01-15 10:30``, ``2025-01-15T10:30:00`` and
    timezone-aware variants. Timezone offsets are kept as given.
    """
    if value is None or str(value).strip() == "":
        return datetime.now().isoformat(timespec="seconds")
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"Unrecognised timestamp {value!r}; use ISO format YYYY-MM-DD[THH:MM[:SS]]") from exc
    return dt.isoformat(timespec="seconds")


def _validate_trade(action: str, shares: float, price: float, commission: float,
                    multiplier: float) -> None:
    if action not in ("BUY", "SELL"):
        raise ValueError(f"action must be BUY or SELL, got {action!r}")
    if shares is None or float(shares) <= 0:
        raise ValueError("shares must be > 0")
    if price is None or float(price) < 0:
        raise ValueError("price must be >= 0")
    if commission is not None and float(commission) < 0:
        raise ValueError("commission must be >= 0")
    if multiplier is not None and float(multiplier) <= 0:
        raise ValueError("multiplier must be > 0")


def trade_fingerprint(ticker: str, action: str, shares: float, price: float,
                      timestamp: str) -> str:
    """Stable id for a trade without a broker id (used to de-duplicate imports)."""
    key = f"{ticker.upper()}|{action.upper()}|{ledger.D(shares).normalize()}|" \
          f"{ledger.D(price).normalize()}|{timestamp}"
    return "fp:" + hashlib.sha1(key.encode()).hexdigest()[:16]


# ── trades ──────────────────────────────────────────────────────────────────


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
    multiplier: float = 1.0,
    external_id: str | None = None,
    commit: bool = True,
) -> int:
    """Insert a trade and return its id.

    Realized P&L for any position it reduces is recomputed automatically.
    """
    ticker_u = ticker.upper().strip()
    action_u = action.upper().strip()
    commission = commission or 0
    multiplier = multiplier or 1.0
    _validate_trade(action_u, shares, price, commission, multiplier)
    ts = normalize_timestamp(timestamp)

    cur = conn.execute(
        """
        INSERT INTO trades
            (ticker, action, shares, price, commission, timestamp, strategy, setup,
             confidence, stop_loss, target_1, target_2, entry_plan, note_path,
             source, tags, notes, position_group, asset_type, instrument, currency,
             leverage, multiplier, external_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker_u, action_u, shares, price, commission, ts, strategy, setup,
            confidence, stop_loss, target_1, target_2, entry_plan, note_path,
            source, json.dumps(tags or []), notes, position_group, asset_type,
            instrument, (currency or "USD").upper(), leverage, multiplier, external_id,
        ),
    )
    trade_id: int = cur.lastrowid  # type: ignore[assignment]
    rebuild_closed_trades(conn, [ticker_u])
    if commit:
        conn.commit()
    return trade_id


def edit_trade(conn: sqlite3.Connection, trade_id: int, **fields: Any) -> bool:
    """Update fields on an existing trade. Returns True if a row was updated."""
    existing = get_trade(conn, trade_id)
    if existing is None:
        return False
    updates = {k: v for k, v in fields.items() if k in TRADE_FIELDS and v is not None}
    if not updates:
        return False
    if "tags" in updates and isinstance(updates["tags"], list):
        updates["tags"] = json.dumps(updates["tags"])
    if "ticker" in updates:
        updates["ticker"] = updates["ticker"].upper().strip()
    if "action" in updates:
        updates["action"] = updates["action"].upper().strip()
    if "currency" in updates:
        updates["currency"] = updates["currency"].upper()
    if "timestamp" in updates:
        updates["timestamp"] = normalize_timestamp(updates["timestamp"])
    merged = {**existing, **updates}
    _validate_trade(merged["action"], merged["shares"], merged["price"],
                    merged.get("commission") or 0, merged.get("multiplier") or 1)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    cur = conn.execute(
        f"UPDATE trades SET {set_clause} WHERE id = ?", [*updates.values(), trade_id]
    )
    rebuild_closed_trades(conn, {existing["ticker"], merged["ticker"]})
    conn.commit()
    return cur.rowcount > 0


def delete_trade(conn: sqlite3.Connection, trade_id: int) -> bool:
    """Delete a trade by ID. Returns True if a row was deleted."""
    existing = get_trade(conn, trade_id)
    if existing is None:
        return False
    conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    conn.execute("DELETE FROM trade_reviews WHERE exit_trade_id = ?", (trade_id,))
    rebuild_closed_trades(conn, [existing["ticker"]])
    conn.commit()
    return True


def get_trade(conn: sqlite3.Connection, trade_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    return dict(row) if row else None


def get_trades(conn: sqlite3.Connection, ticker: str | None = None) -> list[dict[str, Any]]:
    if ticker:
        rows = conn.execute(
            "SELECT * FROM trades WHERE ticker = ? ORDER BY timestamp, id", (ticker.upper(),)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM trades ORDER BY timestamp, id").fetchall()
    return [dict(r) for r in rows]


def find_by_external_id(conn: sqlite3.Connection, source: str, external_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM trades WHERE source = ? AND external_id = ?", (source, external_id)
    ).fetchone()
    return row["id"] if row else None


# ── ledger-derived data ─────────────────────────────────────────────────────


def run_ledger(conn: sqlite3.Connection, ticker: str | None = None) -> ledger.LedgerResult:
    return ledger.replay(get_trades(conn, ticker))


def rebuild_closed_trades(conn: sqlite3.Connection, tickers: Iterable[str] | None = None) -> int:
    """Recompute closed_trades from trades (all tickers, or only the given ones)."""
    if tickers is None:
        conn.execute("DELETE FROM closed_trades")
        trades = get_trades(conn)
    else:
        tickers = sorted({t.upper() for t in tickers if t})
        if not tickers:
            return 0
        marks = ",".join("?" * len(tickers))
        conn.execute(f"DELETE FROM closed_trades WHERE ticker IN ({marks})", tickers)
        trades = [
            dict(r) for r in conn.execute(
                f"SELECT * FROM trades WHERE ticker IN ({marks}) ORDER BY timestamp, id", tickers
            ).fetchall()
        ]
    result = ledger.replay(trades)
    reviews = {
        r["exit_trade_id"]: dict(r)
        for r in conn.execute("SELECT * FROM trade_reviews").fetchall()
    }
    for lot in result.realized:
        row = lot.to_row()
        rv = reviews.get(lot.exit_trade_id, {})
        conn.execute(
            """
            INSERT INTO closed_trades
                (ticker, direction, entry_trade_ids, exit_trade_ids, exit_trade_id, shares,
                 avg_entry_price, avg_exit_price, entry_avg_cost, multiplier,
                 entry_commission, exit_commission, total_commission,
                 gross_pnl, net_pnl, pnl_percent, hold_duration_days, strategy,
                 currency, instrument, opened_at, closed_at,
                 what_worked, what_failed, lesson, rating)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["ticker"], row["direction"], json.dumps(row["entry_trade_ids"]),
                json.dumps(row["exit_trade_ids"]), row["exit_trade_id"], row["shares"],
                row["avg_entry_price"], row["avg_exit_price"], row["entry_avg_cost"],
                row["multiplier"], row["entry_commission"], row["exit_commission"],
                row["total_commission"], row["gross_pnl"], row["net_pnl"],
                row["pnl_percent"], row["hold_duration_days"], row["strategy"],
                row["currency"], row["instrument"], row["opened_at"], row["closed_at"],
                rv.get("what_worked"), rv.get("what_failed"), rv.get("lesson"), rv.get("rating"),
            ),
        )
    return len(result.realized)


def get_position(conn: sqlite3.Connection, ticker: str) -> dict[str, Any] | None:
    """Get a single open position by ticker (None if flat)."""
    return run_ledger(conn, ticker).positions.get(ticker.upper())


def get_positions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All open positions, sorted by ticker."""
    positions = run_ledger(conn).positions
    return [positions[k] for k in sorted(positions)]


def get_closed_trades(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Realized P&L records (most recent first)."""
    rows = conn.execute(
        "SELECT * FROM closed_trades ORDER BY closed_at DESC, exit_trade_id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_realized_for_trade(conn: sqlite3.Connection, trade_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM closed_trades WHERE exit_trade_id = ?", (trade_id,)
    ).fetchone()
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
    """Trade history (or realized P&L records) with optional filters."""
    table, ts_col = ("closed_trades", "closed_at") if closed_only else ("trade_history", "timestamp")
    query = f"SELECT * FROM {table} WHERE 1=1"
    params: list[Any] = []
    if ticker:
        query += " AND ticker = ?"
        params.append(ticker.upper())
    if from_date:
        query += f" AND {ts_col} >= ?"
        params.append(from_date)
    if to_date:
        # A bare date means "through the end of that day".
        query += f" AND {ts_col} <= ?"
        params.append(to_date + "T23:59:59" if len(to_date) == 10 else to_date)
    query += f" ORDER BY {ts_col} DESC" + (", exit_trade_id DESC" if closed_only else ", id DESC")
    query += " LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(query, params).fetchall()]


# ── reviews ─────────────────────────────────────────────────────────────────


def set_review(conn: sqlite3.Connection, exit_trade_id: int, **fields: Any) -> bool:
    """Attach a review (what_worked / what_failed / lesson / rating) to a realized trade."""
    review = {k: v for k, v in fields.items() if k in REVIEW_FIELDS and v is not None}
    if not review:
        return False
    if get_realized_for_trade(conn, exit_trade_id) is None:
        raise ValueError(f"Trade #{exit_trade_id} did not close (part of) a position")
    if "rating" in review and not 1 <= int(review["rating"]) <= 5:
        raise ValueError("rating must be 1-5")
    cols = ", ".join(review)
    marks = ", ".join("?" * len(review))
    updates = ", ".join(f"{k} = excluded.{k}" for k in review)
    conn.execute(
        f"INSERT INTO trade_reviews (exit_trade_id, {cols}) VALUES (?, {marks}) "
        f"ON CONFLICT(exit_trade_id) DO UPDATE SET {updates}, updated_at = datetime('now')",
        [exit_trade_id, *review.values()],
    )
    set_clause = ", ".join(f"{k} = ?" for k in review)
    conn.execute(
        f"UPDATE closed_trades SET {set_clause} WHERE exit_trade_id = ?",
        [*review.values(), exit_trade_id],
    )
    conn.commit()
    return True


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
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Close (fully or partially) a long or short position and optionally review it."""
    ticker = ticker.upper()
    pos = get_position(conn, ticker)
    if pos is None:
        raise ValueError(f"No open position for {ticker}")
    held = pos["shares"]
    if shares > held + 1e-9:
        raise ValueError(f"Cannot close {shares} shares, only {held} held ({pos['direction']})")

    trade_id = add_trade(
        conn, ticker, "SELL" if pos["direction"] == "long" else "BUY", shares, exit_price,
        commission=commission, strategy=strategy, currency=pos["currency"],
        instrument=pos["instrument"], multiplier=pos["multiplier"], timestamp=timestamp,
    )
    set_review(conn, trade_id, what_worked=what_worked, what_failed=what_failed,
               lesson=lesson, rating=rating)
    realized = get_realized_for_trade(conn, trade_id) or {}
    after = get_position(conn, ticker)
    return {
        "trade_id": trade_id,
        "ticker": ticker,
        "direction": pos["direction"],
        "shares": shares,
        "avg_entry": pos["avg_cost"],
        "exit_price": exit_price,
        "gross_pnl": realized.get("gross_pnl"),
        "net_pnl": realized.get("net_pnl"),
        "pnl_percent": realized.get("pnl_percent"),
        "remaining_shares": after["shares"] if after else 0.0,
        "remaining_avg_cost": after["avg_cost"] if after else None,
    }


# ── prices ──────────────────────────────────────────────────────────────────


def set_price(conn: sqlite3.Connection, ticker: str, price: float, *,
              currency: str | None = None, as_of: str | None = None,
              source: str = "manual") -> None:
    if price is None or float(price) < 0:
        raise ValueError("price must be >= 0")
    conn.execute(
        "INSERT INTO prices (ticker, price, currency, as_of, source) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET price = excluded.price, currency = excluded.currency, "
        "as_of = excluded.as_of, source = excluded.source",
        (ticker.upper(), price, currency.upper() if currency else None,
         normalize_timestamp(as_of), source),
    )
    conn.commit()


def get_prices(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {r["ticker"]: dict(r) for r in conn.execute("SELECT * FROM prices").fetchall()}


# ── journal ─────────────────────────────────────────────────────────────────


def add_journal_entry(conn: sqlite3.Connection, text: str, *, date: str | None = None,
                      ticker: str | None = None, kind: str = "note", mood: int | None = None,
                      tags: list[str] | None = None) -> int:
    if not text or not text.strip():
        raise ValueError("journal text is empty")
    if mood is not None and not 1 <= mood <= 5:
        raise ValueError("mood must be 1-5")
    day = normalize_timestamp(date)[:10] if date else datetime.now().date().isoformat()
    cur = conn.execute(
        "INSERT INTO journal_entries (date, ticker, kind, mood, text, tags) VALUES (?, ?, ?, ?, ?, ?)",
        (day, ticker.upper() if ticker else None, kind, mood, text.strip(), json.dumps(tags or [])),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def get_journal(conn: sqlite3.Connection, *, ticker: str | None = None,
                limit: int = 1000) -> list[dict[str, Any]]:
    q = "SELECT * FROM journal_entries"
    params: list[Any] = []
    if ticker:
        q += " WHERE ticker = ?"
        params.append(ticker.upper())
    q += " ORDER BY date DESC, id DESC LIMIT ?"
    params.append(limit)
    out = []
    for r in conn.execute(q, params).fetchall():
        d = dict(r)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except json.JSONDecodeError:
            d["tags"] = []
        out.append(d)
    return out


def delete_journal_entry(conn: sqlite3.Connection, entry_id: int) -> bool:
    cur = conn.execute("DELETE FROM journal_entries WHERE id = ?", (entry_id,))
    conn.commit()
    return cur.rowcount > 0


# ── validation ──────────────────────────────────────────────────────────────


def validate_db(conn: sqlite3.Connection) -> list[str]:
    """Consistency checks. Returns a list of human-readable issues."""
    issues: list[str] = []
    result = run_ledger(conn)
    issues.extend(result.warnings)

    cached = {r["exit_trade_id"]: r for r in get_closed_trades(conn)}
    fresh = {lot.exit_trade_id: lot.to_row() for lot in result.realized}
    if set(cached) != set(fresh):
        issues.append("closed_trades cache is out of date (run `trade db rebuild`)")
    else:
        for tid, row in fresh.items():
            if abs((cached[tid]["net_pnl"] or 0) - row["net_pnl"]) > 0.005:
                issues.append(f"closed_trades cache mismatch for trade {tid} (run `trade db rebuild`)")
                break

    orphan = conn.execute(
        "SELECT exit_trade_id FROM trade_reviews WHERE exit_trade_id NOT IN "
        "(SELECT exit_trade_id FROM closed_trades WHERE exit_trade_id IS NOT NULL)"
    ).fetchall()
    for r in orphan:
        issues.append(f"review for trade {r[0]} no longer matches a closed position")
    return issues
