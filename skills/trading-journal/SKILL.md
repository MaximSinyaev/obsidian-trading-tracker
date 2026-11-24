---
name: trading-journal
description: Keep an exact trading journal for the user - record buys/sells, import broker exports (IBKR, Trading 212, Binance, any CSV/XLSX/JSON), show open positions and realized/unrealized P&L, stats (win rate, profit factor, R, drawdown), post-trade reviews, dividends and splits, and render an interactive HTML dashboard or Obsidian notes. Use whenever the user mentions their trades, positions, portfolio P&L, trading journal or trading performance. All numbers come from a deterministic local ledger, never from the model.
metadata: {"openclaw":{"emoji":"📒","homepage":"https://github.com/MaximSinyaev/agent-trading-journal","requires":{"anyBins":["trade","uvx"]},"install":[{"id":"uv","kind":"uv","label":"Install the trade CLI (uv)","package":"agent-trading-journal[yahoo] @ git+https://github.com/MaximSinyaev/agent-trading-journal@v1.3.0","bins":["trade"]}],"envVars":[{"name":"TRADE_DB","required":false,"description":"Path to the SQLite journal (default ~/.trading-journal/trades.db)"},{"name":"TRADE_CONFIG","required":false,"description":"Path to an optional .traderc.toml config"},{"name":"TRADE_TZ","required":false,"description":"IANA time zone that stored times use, e.g. Europe/Berlin (default: this machine's zone)"}]}}
---

# Trading journal

You drive a local CLI called `trade`. It stores every fill in a SQLite database
and computes positions and P&L with a deterministic ledger (average-cost method,
fees, shorts, options multipliers, splits, dividends, several currencies).

## The one rule

**Never calculate P&L, average prices, position sizes, win rates or any other
trade number yourself.** LLM arithmetic on trades is where journals go wrong.
Record facts with the CLI, then read numbers back from it and quote them as-is.
If a number you need is not in the output, say so instead of deriving it.

## Running the CLI

Always use JSON mode and parse the result:

```bash
trade --json <command> ...          # if `trade` is on PATH
scripts/trade --json <command> ...  # wrapper in this skill's folder: falls back to uvx
```

Every response is one line: `{"ok": true, "data": {...}}` or
`{"ok": false, "error": "..."}` (exit code 1). On an error, read the message,
fix the input and retry; do not guess the result.

First use: run `trade --json db path` to see which database is used
(default `~/.trading-journal/trades.db`, override with `TRADE_DB=/path/x.db`
or `trade --db /path/x.db`). Its `timezone` field is the zone broker times are
stored in. If it says `machine local` and you may not be on the user's own computer
(a cloud container is usually UTC), ask the user for their timezone and set
`TRADE_TZ=Area/City` (or `timezone` under `[defaults]` in `~/.traderc.toml`);
otherwise the same fill lands on a different date than on their laptop.
If the CLI is missing, install it once:

```bash
uv tool install "agent-trading-journal[yahoo] @ git+https://github.com/MaximSinyaev/agent-trading-journal@v1.3.0"
```

## Workflows

**Record a trade the user describes.** Ask for anything missing that matters:
ticker, side, quantity, price, commission, date (default now), currency (default
USD) and, for new positions, the stop-loss. Then:

```bash
trade --json add NVDA buy 10 118.40 -c 1.20 --sl 109 --tp1 135 \
  --strategy swing --setup breakout --tags ai-idea,semis \
  --notes "Thesis: ... Exit if: ..." --date 2025-03-10T15:42
```

Selling against a long (or buying against a short) closes it automatically; the
response contains `realized` (gross, fees, net P&L) and the remaining `position`.
Options: `--instrument option --multiplier 100` and the per-share premium as price.

**Import from a broker.** Prefer importing over typing many trades:

```bash
trade --json import trades.csv --dry-run          # check columns and rows first
trade --json import trades.csv                    # then import
trade --json import flex.csv --preset ibkr        # Interactive Brokers Flex Query
trade --json import - < fills.json                # JSON list you built from an API
```

Columns are auto-detected (English and several other languages). Currency
conversions are skipped, option symbols get ×100, expirations close at 0, and
dividends or splits in the file become events (`dividends`, `events` in the response).
Read `notes` and tell the user about them (e.g. fees in another currency that were
not converted). If detection fails, map
them: `--map ticker=Instrument --map timestamp="Exec Time"`. Re-importing the
same file is safe (duplicates are skipped). Report skipped rows to the user.
See `references/import.md` for formats and the JSON schema.

**Mark positions to market.** The CLI does not need internet. If you have a
market-data tool, fetch quotes and store them, then read positions:

```bash
trade --json price set NVDA 131.05 --source <tool name> --as-of 2025-03-14T16:00
trade --json positions
```

Or `trade --json price fetch` (Yahoo, needs the `yahoo` extra). Mention the quote
time (`price_as_of`) when you report unrealized P&L.

**Answer performance questions** ("how did I do in March?", "is my swing
strategy working?", "AI ideas vs my own?"):

```bash
trade --json stats --from 2025-03-01 --to 2025-03-31
trade --json report --strategy swing      # full snapshot incl. equity curve, breakdowns
```

Breakdowns available: by month, ticker, strategy, tag, origin (`ai` = a tag `ai`
or starting with `ai-`, else `manual`), weekday, instrument. Money is always per
currency; never add USD and KZT together. Definitions: `references/metrics.md`.

**Post-trade review.** After a position closes, offer a short review and store it
on the closing trade id (from `realized.exit_trade_id`):

```bash
trade --json review 42 --worked "..." --failed "..." --lesson "..." --rating 4
```

**Journal entries** (plans, emotions, mistakes, market notes):

```bash
trade --json journal add "Chased the open again" --kind mistake --mood 2 --ticker TSLA
```

**Dashboard.** When the user wants to see their trading visually:

```bash
trade --json dashboard -o ~/trading-dashboard.html [--from 2025-01-01] [--strategy swing]
```

It writes one self-contained offline HTML file (equity curve and drawdown,
monthly P&L, calendar heatmap, breakdowns, R distribution, positions with stops,
filterable trade table, journal). Give the user the path. If your environment can
show HTML (e.g. an artifact), you may display that file's content unchanged; do
not rebuild charts from your own numbers.

**Obsidian.** `trade --json sync export --vault ~/Notes` writes daily notes,
one note per ticker and the dashboard into `<vault>/Trading/`. Text the user writes
below `%% notes %%` in those files is preserved.

**Corrections.** `trade --json edit <id> --price 12.30` or `trade --json delete <id> --yes`.
All dependent P&L is recomputed. Corporate actions
(check `trade --json event list --ticker AAPL` first: broker imports may already
contain the split, and a duplicate within 10 days is refused):
`trade --json event split AAPL 4 --date 2020-08-31`,
`trade --json event dividend KO 12.40 --tax 1.86 --date 2025-04-01`.

## Good habits

- Confirm what you are about to record when the user's message is ambiguous
  (side, quantity vs amount, currency). Never invent fills or prices.
- Ask for a stop-loss on every new position; positions without one are flagged.
- Show numbers with their currency and the date range they cover.
- `trade --json db validate` checks consistency if something looks off.

Full command reference with response shapes: `references/cli.md`.
