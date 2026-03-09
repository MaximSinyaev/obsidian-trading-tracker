-- Migration: add instrument to closed_trades for instrument breakdown analytics

ALTER TABLE closed_trades ADD COLUMN instrument TEXT NOT NULL DEFAULT 'stock';

-- Backfill instrument from entry trades
UPDATE closed_trades SET instrument = (
    SELECT t.instrument FROM trades t WHERE t.ticker = closed_trades.ticker LIMIT 1
) WHERE EXISTS (
    SELECT 1 FROM trades t WHERE t.ticker = closed_trades.ticker
);

INSERT INTO schema_version (version) VALUES (4);
