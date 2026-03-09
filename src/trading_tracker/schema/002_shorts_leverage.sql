-- Migration: add shorts support, leverage, instrument, allow price=0
-- For existing databases that already ran 001_initial.sql

-- Drop views FIRST (they reference trades table which we'll recreate)
DROP VIEW IF EXISTS positions;
DROP VIEW IF EXISTS trade_history;
DROP VIEW IF EXISTS daily_pnl;
DROP VIEW IF EXISTS strategy_performance;

-- Add new columns to trades (needed for existing DBs, before we recreate)
ALTER TABLE trades ADD COLUMN instrument TEXT DEFAULT 'stock';
ALTER TABLE trades ADD COLUMN leverage REAL NOT NULL DEFAULT 1.0;

-- Add direction to closed_trades
ALTER TABLE closed_trades ADD COLUMN direction TEXT NOT NULL DEFAULT 'long';

-- Recreate trades table to change price constraint (>0 → >=0)
-- SQLite does not support ALTER CONSTRAINT, so we recreate
CREATE TABLE trades_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('BUY', 'SELL')),
    shares REAL NOT NULL CHECK (shares > 0),
    price REAL NOT NULL CHECK (price >= 0),
    commission REAL NOT NULL DEFAULT 0,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    strategy TEXT,
    setup TEXT,
    confidence INTEGER CHECK (confidence IS NULL OR (confidence >= 1 AND confidence <= 5)),
    stop_loss REAL,
    target_1 REAL,
    target_2 REAL,
    entry_plan TEXT,
    note_path TEXT,
    source TEXT DEFAULT 'manual',
    tags TEXT DEFAULT '[]',
    notes TEXT,
    position_group TEXT,
    asset_type TEXT DEFAULT 'stock',
    instrument TEXT DEFAULT 'stock',
    currency TEXT DEFAULT 'USD',
    leverage REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO trades_new SELECT
    id, ticker, action, shares, price, commission, timestamp, strategy, setup,
    confidence, stop_loss, target_1, target_2, entry_plan, note_path, source,
    tags, notes, position_group, asset_type,
    COALESCE(instrument, 'stock'),
    currency,
    COALESCE(leverage, 1.0),
    created_at, updated_at
FROM trades;

DROP TABLE trades;
ALTER TABLE trades_new RENAME TO trades;

-- Recreate trigger
CREATE TRIGGER IF NOT EXISTS trades_updated_at
    AFTER UPDATE ON trades
    FOR EACH ROW
BEGIN
    UPDATE trades SET updated_at = datetime('now') WHERE id = OLD.id;
END;

-- Recreate views with short support
CREATE VIEW positions AS
SELECT
    ticker,
    SUM(CASE WHEN action = 'BUY' THEN shares ELSE -shares END) AS net_shares,
    CASE
        WHEN SUM(CASE WHEN action = 'BUY' THEN shares ELSE -shares END) > 0 THEN
            SUM(CASE WHEN action = 'BUY' THEN shares * price ELSE 0 END) /
                NULLIF(SUM(CASE WHEN action = 'BUY' THEN shares ELSE 0 END), 0)
        ELSE
            SUM(CASE WHEN action = 'SELL' THEN shares * price ELSE 0 END) /
                NULLIF(SUM(CASE WHEN action = 'SELL' THEN shares ELSE 0 END), 0)
    END AS avg_cost,
    SUM(commission) AS total_commission,
    MIN(timestamp) AS first_trade,
    MAX(timestamp) AS last_trade,
    COUNT(*) AS trade_count
FROM trades
GROUP BY ticker
HAVING net_shares != 0;

CREATE VIEW trade_history AS
SELECT
    t.id, t.ticker, t.action, t.shares, t.price, t.commission,
    t.timestamp, t.strategy, t.instrument, t.leverage,
    t.tags, t.notes, t.position_group
FROM trades t
ORDER BY t.timestamp DESC;

-- Recreate daily_pnl and strategy_performance views
CREATE VIEW daily_pnl AS
SELECT
    date(closed_at) AS trade_date,
    COUNT(*) AS trades_closed,
    SUM(net_pnl) AS total_pnl,
    SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
    SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) AS losses
FROM closed_trades
GROUP BY date(closed_at)
ORDER BY trade_date DESC;

CREATE VIEW strategy_performance AS
SELECT
    COALESCE(strategy, 'unknown') AS strategy,
    COUNT(*) AS total_trades,
    SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
    SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) AS losses,
    ROUND(100.0 * SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
    ROUND(SUM(net_pnl), 2) AS total_pnl,
    ROUND(AVG(net_pnl), 2) AS avg_pnl,
    ROUND(MAX(net_pnl), 2) AS best_trade,
    ROUND(MIN(net_pnl), 2) AS worst_trade
FROM closed_trades
GROUP BY strategy
ORDER BY total_pnl DESC;

INSERT INTO schema_version (version) VALUES (2);
