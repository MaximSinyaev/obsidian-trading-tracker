-- Migration: add currency to closed_trades for multi-currency P&L display

-- Add currency column to closed_trades
ALTER TABLE closed_trades ADD COLUMN currency TEXT NOT NULL DEFAULT 'USD';

-- Backfill currency from trades table
UPDATE closed_trades SET currency = (
    SELECT t.currency FROM trades t WHERE t.ticker = closed_trades.ticker LIMIT 1
) WHERE EXISTS (
    SELECT 1 FROM trades t WHERE t.ticker = closed_trades.ticker
);

-- Recreate trade_history view with currency field
DROP VIEW IF EXISTS trade_history;
CREATE VIEW trade_history AS
SELECT
    t.id, t.ticker, t.action, t.shares, t.price, t.commission,
    t.timestamp, t.strategy, t.instrument, t.leverage, t.currency,
    t.tags, t.notes, t.position_group
FROM trades t
ORDER BY t.timestamp DESC;

INSERT INTO schema_version (version) VALUES (3);
