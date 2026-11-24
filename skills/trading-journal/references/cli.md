# `trade` CLI reference (for agents)

Global options go before the command: `trade --json [--db PATH] <command>`.
Every JSON response is one line: `{"ok": true, "data": ...}` or
`{"ok": false, "error": "message"}` with exit code 1. That includes bad arguments
(`"usage_error": true`) and invalid dates. Date filters accept `YYYY`, `YYYY-MM`,
`YYYY-MM-DD` or a full timestamp; anything else is an error, never a silent empty result.

Dates are ISO (`2025-03-10`, `2025-03-10T15:42`, `2025-03-10T15:42:00+05:00`).
Money fields are rounded to cents; prices and quantities keep full precision.

## Trades

| Command | Purpose | `data` |
|---|---|---|
| `add TICKER buy\|sell QTY PRICE [opts]` | record a fill (`b/bot/s/sld` accepted) | `{trade, realized|null, position|null, warnings[]}` |
| `edit ID [--price --shares --commission --date --ticker --action --sl --tp1 --tp2 --notes --tags --strategy --setup --currency --multiplier --instrument --confidence]` | fix a fill, P&L recomputed | `{updated, fields, trade}` |
| `delete ID --yes` | remove a fill (`--yes` is required in JSON mode) | `{deleted}` |
| `show ID` | one fill and the P&L it realized | `{trade, realized}` |
| `history [--limit N --ticker T --from D --to D]` | fills, newest first | `{rows: [trade]}` |
| `history --closed [...]` | realized P&L records | `{rows: [realized]}` |
| `review EXIT_ID [--worked --failed --lesson --rating 1-5]` | review a closing fill | `{review: realized}` |

`add` options: `-c/--commission`, `--sl/--stop`, `--tp1`, `--tp2`, `-s/--strategy`,
`--setup`, `-n/--notes` (thesis), `-t/--tags a,b`, `--confidence 1-5`,
`--date`, `--ccy/--currency`, `-i/--instrument stock|etf|option|future|crypto|bond`,
`-m/--multiplier` (100 for US equity options), `--external-id` (broker id; a
duplicate id is rejected), `--source`, `--group`, `--leverage`.

**trade** fields: `id, ticker, action, shares, price, commission, timestamp,
currency, multiplier, instrument, strategy, setup, stop_loss, target_1, target_2,
confidence, notes, tags[], source, external_id, ...`

**realized** (one row per reducing fill): `exit_trade_id, entry_trade_ids[],
ticker, direction (long|short), shares, avg_entry_price, avg_exit_price,
multiplier, gross_pnl, entry_commission, exit_commission, total_commission,
net_pnl, pnl_percent (net / cost basis), opened_at, closed_at,
hold_duration_days, currency, instrument, strategy, what_worked, what_failed,
lesson, rating`

**position**: `ticker, direction, shares, net_shares (signed), avg_cost,
breakeven_price (incl. entry fees), cost_basis, open_commission, multiplier,
first_trade (start of this position), last_trade, trade_count,
entry_trade_ids[], currency, instrument, strategy, realized_pnl (all time),
dividends`

## Positions, prices

| Command | `data` |
|---|---|
| `positions [--prices cache\|yahoo\|none] [--live] [--no-live]` | `{positions: [position + mark], warnings[]}` |
| `price set TICKER PRICE [--ccy --as-of --source]` | `{price}` |
| `price list` | `{prices: [{ticker, price, currency, as_of, source}]}` |
| `price fetch [TICKERS...]` (Yahoo, `yahoo` extra) | `{prices, missing[]}` |

mark fields: `current_price, market_value, unrealized_pnl (gross),
unrealized_pnl_net (minus open entry fees), unrealized_pnl_pct, price_as_of,
price_source, stop_loss (split-adjusted), risk_to_stop (loss if the stop fills;
negative = profit locked), target, thesis, tags[]`

## Analysis

| Command | `data` |
|---|---|
| `stats [--from --to --ticker --strategy]` | `{CCY: {summary, by_month, by_ticker, by_strategy, by_tag, by_origin, by_weekday, by_instrument}}` |
| `report [--from --to --ticker --strategy --prices P] [-o file.json]` | full snapshot (below) |
| `dashboard [-o file.html --from --to --ticker --strategy --title --open]` | `{path, closed_trades, open_positions, currencies}` |

**summary**: `closed_trades, wins, losses, win_rate, gross_pnl, commissions,
net_pnl, dividends, funding, net_pnl_with_income, avg_win, avg_loss, best_trade,
worst_trade, profit_factor (null = no losses), expectancy, avg_hold_days_winners,
avg_hold_days_losers, avg_r, trades_with_r, max_drawdown {amount, peak_date,
trough_date}, open_positions, open_cost_basis, unrealized_pnl,
positions_without_price`

Breakdown rows: `{<key>, trades, wins, win_rate, net_pnl, avg_pnl, profit_factor}`.

**report**: `{schema, generated_at, filters, currencies[], by_currency: {CCY:
{summary, equity_curve: [{date, pnl, equity, drawdown}], by_*}}, open_positions[],
closed_trades[] (realized + tags, setup, stop_loss, initial_risk, r_multiple,
entry_notes, exit_notes), income[], journal[], warnings[], method}`

## Journal, events

| Command | `data` |
|---|---|
| `journal add TEXT [--ticker --mood 1-5 --kind note\|plan\|lesson\|mistake\|review --tags --date]` | `{entry_id}` |
| `journal list [--ticker --kind --tag --from --to --limit]` | `{entries: [{id, date, ticker, kind, mood, text, tags[]}]}` |
| `journal delete ID` | `{deleted}` |
| `event split TICKER RATIO [--date]` (4 = 4-for-1, 0.1 = 1-for-10) | `{event_id, position}` |
| `event dividend TICKER AMOUNT [--tax --ccy --date]` | `{event_id, net}` |
| `event list [--ticker]` / `event delete ID` | |

## Import, export, maintenance

| Command | `data` |
|---|---|
| `import FILE\|- [--preset generic\|ibkr\|okx\|freedom\|tinvest --map field=Column --dry-run --ccy --source --strategy --date-format FMT]` | `{imported_count, duplicate_count, skipped_count, dividend_count, columns, imported[], duplicates[], skipped[{row, reason}], dividends[], events[], notes[], reconciliation (okx only)}` |
| `sync export [--vault PATH]` | `{files, vault}` |
| `db path` / `db init` / `db validate` / `db rebuild` | `db path`: `{path, exists, timezone}` |
| `fx rate USD KZT`, `fx convert 100 USD KZT`, `fx matrix` (Yahoo) | human output |
