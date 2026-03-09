# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
