-- Migration 005: ledger-derived closed_trades, imports, price cache, journal.
--
-- closed_trades becomes a cache that is fully rebuilt from `trades` by the
-- ledger engine (see ledger.py) after every change. Human-written reviews move
-- to trade_reviews so they survive rebuilds.

ALTER TABLE trades ADD COLUMN multiplier REAL NOT NULL DEFAULT 1.0;
ALTER TABLE trades ADD COLUMN external_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_external_id
    ON trades(source, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trades_ticker_ts ON trades(ticker, timestamp, id);

ALTER TABLE closed_trades ADD COLUMN exit_trade_id INTEGER;
ALTER TABLE closed_trades ADD COLUMN entry_commission REAL NOT NULL DEFAULT 0;
ALTER TABLE closed_trades ADD COLUMN exit_commission REAL NOT NULL DEFAULT 0;
ALTER TABLE closed_trades ADD COLUMN multiplier REAL NOT NULL DEFAULT 1.0;
ALTER TABLE closed_trades ADD COLUMN opened_at TEXT;

-- Reviews of closed trades, keyed by the trade that realized the P&L.
CREATE TABLE IF NOT EXISTS trade_reviews (
    exit_trade_id INTEGER PRIMARY KEY,
    what_worked TEXT,
    what_failed TEXT,
    lesson TEXT,
    rating INTEGER CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5)),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Carry over reviews written before this migration.
INSERT OR IGNORE INTO trade_reviews (exit_trade_id, what_worked, what_failed, lesson, rating)
SELECT CAST(json_extract(exit_trade_ids, '$[0]') AS INTEGER),
       what_worked, what_failed, lesson, rating
FROM closed_trades
WHERE json_valid(exit_trade_ids)
  AND json_extract(exit_trade_ids, '$[0]') IS NOT NULL
  AND (what_worked IS NOT NULL OR what_failed IS NOT NULL
       OR lesson IS NOT NULL OR rating IS NOT NULL);

-- Last known prices (set by an agent, a user, or a price provider).
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT PRIMARY KEY,
    price REAL NOT NULL CHECK (price >= 0),
    currency TEXT,
    as_of TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual'
);

-- Corporate actions and cash income: splits (ratio), dividends / coupons (amount, net of tax).
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('split', 'dividend')),
    timestamp TEXT NOT NULL,
    ratio REAL CHECK (ratio IS NULL OR ratio > 0),
    amount REAL,
    tax REAL NOT NULL DEFAULT 0,
    currency TEXT,
    notes TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    external_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_external_id
    ON events(source, external_id) WHERE external_id IS NOT NULL;

-- Free-form trading journal (daily reflections, plans, mistakes).
CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    ticker TEXT,
    kind TEXT NOT NULL DEFAULT 'note',
    mood INTEGER CHECK (mood IS NULL OR (mood >= 1 AND mood <= 5)),
    text TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The old SQL positions view averaged ALL buys ever made, which is wrong after
-- a full close + reopen. Positions come from the ledger now.
DROP VIEW IF EXISTS positions;

DROP VIEW IF EXISTS trade_history;
CREATE VIEW trade_history AS
SELECT
    t.id, t.ticker, t.action, t.shares, t.price, t.commission,
    t.timestamp, t.strategy, t.instrument, t.leverage, t.currency,
    t.multiplier, t.source, t.external_id,
    t.tags, t.notes, t.position_group
FROM trades t
ORDER BY t.timestamp DESC, t.id DESC;

INSERT INTO schema_version (version) VALUES (5);
