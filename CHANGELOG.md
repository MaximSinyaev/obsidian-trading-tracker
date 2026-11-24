# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- trading-idea-analyst, bias check: a thesis that rests on one signal or on recent news is kept,
  labelled and capped at confidence 2 instead of being sent back (which made the model pad it
  with weaker confirming points). Missing counter-evidence and portfolio steering still send it
  back. The report shows only the checks that flagged something.
- trading-idea-analyst, risk rule 3: the event size factor and the 3-day event time stop apply
  only when a thesis rests on a single event or decision. Geopolitical and commodity ideas
  still get the "what if a decision-maker says the opposite tomorrow?" check, but a macro
  commodity thesis keeps its own horizon instead of becoming a 3-day trade.

### Fixed
- trading-idea-analyst, journal handoff: scoring an expired idea no longer tells the agent to
  run `trade price set --as-of <time stop>`, which replaced the live mark of an open position
  (also from unattended scheduled runs). The close price goes into the `CLOSED` entry instead.
- The plan-entry example now contains the `JSON:` line the scorecard parses; the `trade add`
  example passes `--ccy` and `--date`, so non-USD ideas are not booked as USD.
- Delta mode no longer reads a "previous scan" that was never stored: the window is the time
  since the previous scheduled run (or the last 7 days).
- Option maximum loss is stated per contract (`premium × multiplier`, short puts
  `(strike − premium) × multiplier`); a broken sentence in risk rule 6.
- One outcome list in both modes (`target hit | stop hit | invalidated | expired`), with a
  `JSON:` line on `CLOSED` entries; concrete idea ids in the examples.
- Thesis checks keep step 2 (counter-evidence, kill condition), with bias answers as notes.
- The Trading Journal is also detected through the journal skill's `scripts/trade` wrapper.
- `sources.toml` lives outside the skill folder (`$TRADING_IDEA_SOURCES` or
  `~/.config/trading-idea-analyst/`); the FRED example needs no API key.
- Install docs cover `trading-idea-analyst` for Codex, Cursor and other agents.

## [1.2.0] - 2026-11-24

### Changed
- **trading-idea-analyst works standalone.** It no longer needs the `trade` CLI: without it,
  idea cards stay in chat and, if the user agrees, go to an append-only markdown idea log
  (`trading-ideas.md`, configurable via `[storage].ideas_file`) with the same `JSON:` line and
  its own scorecard (`references/idea-log.md`). When `trade` is found, ideas and taken trades
  are recorded in the journal exactly as before (`references/journal-handoff.md`, now marked
  optional). The CLI itself is unchanged; install pins stay at v1.1.1.

## [1.1.1] - 2026-11-24

### Fixed
- Importing the same file twice under different presets (first without `--preset`, then with
  `--preset ibkr`) booked every fill twice. Without `--source`, rows that match a trade from
  another source by ticker, side, quantity, price and time are now duplicates and are reported
  with `matches_trade_id` and `matches_source`.
- OpenClaw metadata in SKILL.md pinned the CLI to v1.0.0 and described `TRADE_TZ` wrongly.

## [1.1.0] - 2026-11-24

### Added
- **OKX import** (`--preset okx`, detected automatically): the "Trading History" bill export of
  perpetual swaps. Contract values are solved from the PnL column OKX prints on closing fills
  (with a table of common instruments as a cross-check), liquidations are tagged and carry their
  liquidation fee, and the import reports how many closes reconcile with OKX's own PnL.
- **Funding events** (schema v6): `funding` joins `split` and `dividend`. The report adds a
  `funding` total, included in `net_pnl_with_income`, and the dashboard shows it in the income tile.

### Fixed
- Broker times with an offset were converted to the zone of whichever machine ran the CLI, so a
  laptop and a cloud agent stored different dates in one database. They now use
  `[defaults] timezone` or `TRADE_TZ` (an IANA name); empty still means this machine's zone.
  The unused `Asia/Almaty` default is gone.

### Changed
- The dashboard embeds only the fields it draws, so books with tens of thousands of fills
  stay small (about 3× smaller); chart axes use compact labels such as `−12.5k`.

## [1.0.0] - 2026-11-24

Turns the tracker into a journal that AI agents can drive safely, packaged as a
Claude Code plugin / agent skill.

### Added
- **Deterministic ledger** (`ledger.py`): positions and realized P&L are replayed from
  `trades` in `Decimal`; `closed_trades` is a cache rebuilt after every change.
- **`--json` on every command** with `{"ok": ..., "data"|"error": ...}` responses; `--db` global option,
  `TRADE_DB` / `TRADE_CONFIG` environment variables.
- **Import** (`trade import`): any CSV/XLSX/JSON with auto-detected columns, Interactive Brokers
  Flex and Activity Statements (dividends and splits become events), Trading 212, Binance (pairs,
  coin fees), Schwab-style option histories (expiry/assignment), optional Freedom Finance and
  T-Invest presets, JSON lists from agents, `--dry-run`, `--map`, idempotent re-imports.
  FX conversions are never booked as positions; fees in another currency are never silently converted.
- **Prices** (`trade price set/list/fetch`): stored quotes with source and time mark positions to market.
- **Corporate events**: `trade event split` and `trade event dividend`.
- **Journal**: `trade journal add/list/delete`, `trade review` for post-trade reviews (kept across edits).
- **Report** (`trade report`): full JSON snapshot per currency — summary, equity curve with drawdown,
  breakdowns by month, ticker, strategy, tag, AI vs manual, weekday, instrument; R-multiples from stops.
- **Interactive dashboard** (`trade dashboard`): one self-contained offline HTML file.
- Contract multiplier (`--multiplier`), `--setup`, `--external-id`, breakeven price, risk to stop,
  warnings for positions without a stop-loss.
- Claude Code plugin (`.claude-plugin/`), skill `skills/trading-journal/`, `AGENTS.md`, examples.
- `trade add` returns `warnings` for a missing or wrong-side stop; `journal list --tag --kind --from --to`.
- Tests: ledger invariants on randomized books, importers, six broker personas, report, JSON CLI,
  migration (420+ total).

### Fixed after review
- `--json` errors are always JSON, including bad arguments and unexpected exceptions.
- Invalid `--from/--to` dates are rejected instead of returning wrong numbers.
- Fills with a time-zone offset are ordered by the actual instant; v0.5 timestamps and
  lowercase tickers are normalised on upgrade.
- `1,000` is read as a thousand (US exports); comma-decimal presets are explicit.
- Unrealized P&L and risk to stop use the exact cost basis; expectancy is the true average.
- A dividend while short is a payment, not income; position dividends stay in their currency.
- A stop on the wrong side gives no R-multiple and a warning.
- Currency codes are validated (closes an HTML-injection path in the dashboard).

### Changed
- **Net P&L now subtracts the entry commission pro-rata** as well as the exit commission
  (previously only the exit fee, which overstated profit). Gross P&L is unchanged.
- Editing, deleting or back-dating a trade recomputes all affected P&L (previously stale).
- Obsidian export uses the ledger (the old SQL `positions` view averaged all buys ever made) and
  preserves text below `%% notes %%`; adds `Dashboard.html`.
- `yfinance` is an optional extra (`[yahoo]`); `trade positions` uses stored prices unless `--live`.
- Default database: `~/.trading-journal/trades.db` when nothing is configured (an existing `./trades.db`
  is still used). Relative paths in a config file resolve next to that file.
- Stats never mix currencies.

### Fixed
- `entry_trade_ids` listed every buy in the ticker's history instead of the current position's.

## [0.5.0] - 2026-03-09

### Added
- **Auto-close on add** — `trade add TICKER sell` against a long (or `buy` against a short) automatically creates closed_trades with P&L. No need for explicit `close` command
- **Correct avg cost** — positions computed by walking trades chronologically; handles partial closes, full close + reopen, and position flips correctly
- **`--currency` option** — `trade add ... --currency KZT` saves currency to database (was always defaulting to USD)
- **E2E tests from real broker data** — 35 tests verifying P&L against Freedom Finance report (HSBK partial closes, options, shorts, DCA)
- CLI shows auto-close details: entry/exit prices, P&L, remaining shares

### Changed
- `trade close` command deprecated — use `trade add TICKER sell/buy` instead
- Positions no longer use SQL view for avg_cost — computed in Python for correctness
- 129 tests total (was 83)

### Fixed
- Currency not saved to database when using `--currency` flag
- Avg cost wrong after full close + reopen (was averaging ALL buys, not just current position)
- Avg cost wrong after partial sell + new buys (same root cause)

## [0.4.0] - 2026-03-04

### Added
- **Short positions** — SELL to open, BUY to close, with correct P&L (entry - exit)
- **Leverage** field — `trade add ... --leverage 5` for margined positions
- **Instrument type** — `trade add ... --instrument option` for filtering and analytics
- **Historical imports** — `trade add ... --date 2025-01-15T10:30:00` for backfilling trades
- **Price=0 support** — record option expiration or worthless exits
- Short position direction shown in `positions` (LONG/SHORT labels) and `close` output
- 19 new tests (83 total): shorts lifecycle, price=0 expiry, leverage, instrument, --date flag
- Database migration `002_shorts_leverage.sql` for existing databases

### Changed
- Positions view now shows both long and short positions (`HAVING net_shares != 0`)
- `close_position` auto-detects direction and creates appropriate trade (SELL for long, BUY for short)
- Avg cost for shorts calculated from SELL (entry) trades

## [0.3.0] - 2026-03-04

### Added
- `trade fx rate <BASE> <QUOTE>` — fetch exchange rate for any currency pair
- `trade fx convert <AMOUNT> <FROM> <TO>` — quick currency conversion
- `trade fx matrix` — cross-rate table for configured currencies (default: USD, EUR, RUB, KZT)
- `[fx]` section in `.traderc.toml` for default currencies and base currency
- `convert_amount()` function in analytics for programmatic FX conversion

## [0.2.0] - 2026-03-04

### Added
- `trade positions` now fetches **live prices** from Yahoo Finance (via yfinance)
  - Shows current price, market value, unrealized P&L and P&L %
  - Portfolio total line with aggregated P&L
  - `--no-live` flag to skip price fetching (offline mode)
- `trade show <ID>` — display all fields of a single trade
- `trade delete <ID>` — remove a trade with confirmation (`--yes` to skip)
- Shell completion support for bash/zsh/fish (`trade --install-completion`)

## [0.1.0] - 2026-03-04

### Added
- `trade add` — record BUY/SELL trades with strategy, stop-loss, targets, tags, notes
- `trade edit` — fix any field on an existing trade
- `trade close` — close positions (full or partial) with review notes
- `trade positions` — view open positions table
- `trade history` — browse trade history with filters (ticker, date range, closed-only)
- `trade stats` — win rate, P&L, best/worst trade, per-strategy breakdown
- `trade db init` / `trade db validate` — database initialization and consistency checks
- `trade sync export` — export daily logs and position notes to Obsidian vault
- SQLite backend with schema migrations, views (positions, daily_pnl, strategy_performance)
- P&L calculation via average cost method, commissions tracked separately
- Partial close support — avg cost unchanged on partial exit
- Transaction-safe position closing (rollback on failure)
- TOML configuration (`.traderc.toml`) with search in cwd → home → defaults
- Jinja2 templates for Obsidian markdown with Dataview-compatible frontmatter
- 64 tests (pytest), ruff lint clean
