-- v1.1: perpetual-futures funding payments as their own event kind.
-- SQLite cannot alter a CHECK constraint, so the events table is rebuilt.
CREATE TABLE events_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('split', 'dividend', 'funding')),
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
INSERT INTO events_new (id, ticker, kind, timestamp, ratio, amount, tax, currency, notes, source, external_id, created_at)
    SELECT id, ticker, kind, timestamp, ratio, amount, tax, currency, notes, source, external_id, created_at FROM events;
DROP TABLE events;
ALTER TABLE events_new RENAME TO events;
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_external_id
    ON events(source, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_events_ticker_ts ON events(ticker, timestamp);

INSERT INTO schema_version (version) VALUES (6);
