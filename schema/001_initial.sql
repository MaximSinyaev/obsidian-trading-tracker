-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Main trades table
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('BUY', 'SELL')),
    shares REAL NOT NULL CHECK (shares > 0),
    price REAL NOT NULL CHECK (price > 0),
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
    currency TEXT DEFAULT 'USD',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Closed trades table
CREATE TABLE IF NOT EXISTS closed_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    entry_trade_ids TEXT NOT NULL DEFAULT '[]',
    exit_trade_ids TEXT NOT NULL DEFAULT '[]',
    shares REAL NOT NULL CHECK (shares > 0),
    avg_entry_price REAL NOT NULL,
    avg_exit_price REAL NOT NULL,
    entry_avg_cost REAL NOT NULL,
    total_commission REAL NOT NULL DEFAULT 0,
    gross_pnl REAL NOT NULL,
    net_pnl REAL NOT NULL,
    pnl_percent REAL NOT NULL,
    hold_duration_days REAL,
    strategy TEXT,
    what_worked TEXT,
    what_failed TEXT,
    lesson TEXT,
    rating INTEGER CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5)),
    closed_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Trigger: auto-update updated_at on trades
CREATE TRIGGER IF NOT EXISTS trades_updated_at
    AFTER UPDATE ON trades
    FOR EACH ROW
BEGIN
    UPDATE trades SET updated_at = datetime('now') WHERE id = OLD.id;
END;

-- View: open positions (net shares per ticker)
CREATE VIEW IF NOT EXISTS positions AS
SELECT
    ticker,
    SUM(CASE WHEN action = 'BUY' THEN shares ELSE -shares END) AS net_shares,
    SUM(CASE WHEN action = 'BUY' THEN shares * price ELSE 0 END) /
        NULLIF(SUM(CASE WHEN action = 'BUY' THEN shares ELSE 0 END), 0) AS avg_cost,
    SUM(CASE WHEN action = 'BUY' THEN commission ELSE 0 END) +
        SUM(CASE WHEN action = 'SELL' THEN commission ELSE 0 END) AS total_commission,
    MIN(timestamp) AS first_trade,
    MAX(timestamp) AS last_trade,
    COUNT(*) AS trade_count
FROM trades
GROUP BY ticker
HAVING net_shares > 0;

-- View: trade history
CREATE VIEW IF NOT EXISTS trade_history AS
SELECT
    t.id,
    t.ticker,
    t.action,
    t.shares,
    t.price,
    t.commission,
    t.timestamp,
    t.strategy,
    t.tags,
    t.notes,
    t.position_group
FROM trades t
ORDER BY t.timestamp DESC;

-- View: daily P&L from closed trades
CREATE VIEW IF NOT EXISTS daily_pnl AS
SELECT
    date(closed_at) AS trade_date,
    COUNT(*) AS trades_closed,
    SUM(net_pnl) AS total_pnl,
    SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS wins,
    SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) AS losses
FROM closed_trades
GROUP BY date(closed_at)
ORDER BY trade_date DESC;

-- View: strategy performance
CREATE VIEW IF NOT EXISTS strategy_performance AS
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

INSERT INTO schema_version (version) VALUES (1);
