# Obsidian Trading Tracker

Personal trading journal CLI. Tracks trades in SQLite, calculates P&L (avg cost method), and exports markdown notes to Obsidian.

## Why this exists

Most trading journals are either paid SaaS ($30-80/mo), heavy web apps requiring Docker+MongoDB, or raw Obsidian templates that can't handle math. This tool fills the gap: a local-first CLI with a real database that plays nicely with Obsidian.

See [Alternatives](#alternatives) for a full comparison.

## Quick Start

```bash
# Install (requires uv — https://docs.astral.sh/uv/)
cd obsidian-trading-tracker
uv sync

# Initialize the database
uv run trade db init

# Add your first trade
uv run trade add FRO buy 3 39.55 --commission 10 --strategy dividend-capture

# Check positions
uv run trade positions

# Close the position
uv run trade close FRO 3 43.00 --commission 10 --what-worked "Good timing"

# View stats
uv run trade stats
```

## Installation

**Prerequisites:** Python 3.12+, [uv](https://docs.astral.sh/uv/)

```bash
git clone <repo-url>
cd obsidian-trading-tracker
uv sync
```

The `trade` command becomes available via `uv run trade`.

To make it globally available without `uv run`:

```bash
uv tool install -e .
trade --help
```

### Shell Completion

```bash
# Bash
trade --install-completion bash

# Zsh
trade --install-completion zsh

# Fish
trade --install-completion fish
```

After installing, restart your shell. You'll get tab-completion for all commands and options.

## Configuration

Create `.traderc.toml` in your project directory or home (`~/.traderc.toml`):

```toml
[database]
path = "trades.db"                    # path to SQLite file

[defaults]
commission = 0.0                       # default commission per trade
timezone = "Asia/Almaty"
asset_type = "stock"                   # stock, etf, crypto, option
source = "manual"                      # manual, broker-import, api

[obsidian]
vault_path = "/path/to/your/vault"     # Obsidian vault root
trading_folder = "Trading"             # subfolder inside vault

[fx]
currencies = ["USD", "EUR", "RUB", "KZT"]  # for `trade fx matrix`
base_currency = "USD"
```

The tool searches for config in this order:
1. `.traderc.toml` in current directory
2. `~/.traderc.toml`
3. Built-in defaults (no config file needed for basic usage)

## Commands

### `trade add` — Record a trade

```bash
trade add <TICKER> <ACTION> <SHARES> <PRICE> [OPTIONS]
```

| Argument | Description |
|----------|-------------|
| `TICKER` | Stock symbol (auto-uppercased) |
| `ACTION` | `buy` or `sell` |
| `SHARES` | Number of shares |
| `PRICE`  | Price per share |

| Option | Short | Description |
|--------|-------|-------------|
| `--commission` | `-c` | Commission paid |
| `--strategy` | `-s` | Strategy name (e.g. `momentum`, `dividend-capture`) |
| `--sl` | | Stop loss price |
| `--tp1` | | Target price 1 |
| `--tp2` | | Target price 2 |
| `--confidence` | | Confidence level 1-5 |
| `--notes` | `-n` | Free-text notes |
| `--tags` | `-t` | Comma-separated tags |
| `--source` | | Data source (`manual`, `broker-import`) |
| `--group` | | Position group name |

**Examples:**

```bash
# Simple buy
trade add AAPL buy 10 150.00

# Detailed entry
trade add FRO buy 3 39.55 \
  --commission 10 \
  --strategy dividend-capture \
  --sl 36 --tp1 43 --tp2 48 \
  --confidence 4 \
  --tags "shipping,value" \
  --notes "Earnings next week, expecting dividend announcement"
```

### `trade edit` — Fix a trade

```bash
trade edit <TRADE_ID> [OPTIONS]
```

Pass any field to update. Only specified fields change.

```bash
# Fix a price typo
trade edit 1 --price 39.60

# Update multiple fields
trade edit 1 --shares 5 --notes "Corrected share count"

# Change strategy and tags
trade edit 1 --strategy momentum --tags "tech,breakout"
```

### `trade show` — View trade details

```bash
trade show <TRADE_ID>
```

Displays all fields of a single trade in a formatted table.

### `trade delete` — Remove a trade

```bash
trade delete <TRADE_ID>
```

Shows the trade and asks for confirmation before deleting. Use `--yes` / `-y` to skip confirmation.

```bash
trade delete 5          # asks for confirmation
trade delete 5 --yes    # deletes immediately
```

### `trade close` — Close a position

```bash
trade close <TICKER> <SHARES> <PRICE> [OPTIONS]
```

Supports **partial closes** — close fewer shares than held.

| Option | Description |
|--------|-------------|
| `--commission`, `-c` | Exit commission |
| `--strategy`, `-s` | Strategy (overrides entry strategy) |
| `--what-worked` | What went well (for review) |
| `--what-failed` | What went wrong |
| `--lesson` | Key takeaway |
| `--rating` | Trade quality rating 1-5 |

**Examples:**

```bash
# Full close
trade close FRO 3 43.00 --commission 10 --what-worked "Timed the dividend"

# Partial close (sell 2 of 5 shares)
trade close FRO 2 43.00 --commission 10

# With review notes
trade close AAPL 10 165.00 \
  --what-worked "Held through dip" \
  --what-failed "Exited too early" \
  --lesson "Use trailing stop next time" \
  --rating 4
```

### `trade positions` — View open positions with live prices

```bash
trade positions            # with live Yahoo Finance prices
trade positions --no-live  # skip price fetching (offline)
```

Shows open positions with current market price, unrealized P&L, and a portfolio total:

```
                                 Open Positions
┏━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃ Ticker ┃ Shares ┃ Avg Cost ┃ Cost Basis ┃  Price ┃ Mkt Value ┃     P&L ┃  P&L % ┃
┡━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ FRO    │   3.00 │   $39.55 │    $118.65 │ $42.10 │   $126.30 │  +$7.65 │ +6.4%  │
│ AAPL   │  10.00 │  $150.00 │  $1500.00  │$155.20 │  $1552.00 │ +$52.00 │ +3.5%  │
└────────┴────────┴──────────┴────────────┴────────┴───────────┴─────────┴────────┘

  Total: cost $1618.65 → value $1678.30 | P&L +$59.65 (+3.7%)
```

Prices are fetched from Yahoo Finance (free, no API key needed).

### `trade history` — Browse past trades

```bash
trade history [OPTIONS]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--limit` | `-l` | Number of trades to show (default: 20) |
| `--ticker` | `-t` | Filter by ticker |
| `--from` | | Start date (`YYYY-MM-DD`) |
| `--to` | | End date (`YYYY-MM-DD`) |
| `--closed` | | Show only closed trades |

```bash
trade history --limit 10
trade history --ticker FRO --from 2026-01-01
trade history --closed
```

### `trade stats` — Trading statistics

```bash
trade stats
```

Shows: win rate, total P&L, avg P&L, best/worst trade, total commission, avg hold time. If multiple strategies are used, includes a per-strategy breakdown.

```
     Trading Statistics
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Metric           ┃   Value ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ Total Trades     │       5 │
│ Wins / Losses    │   3 / 2 │
│ Win Rate         │  60.0%  │
│ Total P&L        │ $234.50 │
│ Avg P&L          │  $46.90 │
│ Best Trade       │ $150.00 │
│ Worst Trade      │ -$45.00 │
│ Total Commission │  $50.00 │
│ Avg Hold (days)  │     4.2 │
└──────────────────┴─────────┘
```

### `trade db` — Database management

```bash
trade db init       # Create/migrate the database
trade db validate   # Check data consistency
```

### `trade sync export` — Export to Obsidian

```bash
trade sync export
```

Generates markdown files in your Obsidian vault:

```
<vault>/Trading/
├── Daily/
│   ├── 2026-03-01.md    # daily log with all trades
│   └── 2026-03-03.md
└── Positions/
    ├── FRO.md           # open position note
    └── AAPL.md          # closed position note with review
```

Each file has YAML frontmatter compatible with [Dataview](https://github.com/blacksmithgu/obsidian-dataview):

```yaml
---
ticker: "FRO"
type: trading-position
status: "closed"
shares: 3
avg_cost: 39.55
strategy: "dividend-capture"
---
```

**Requires** `vault_path` set in `.traderc.toml`.

### `trade fx` — Currency exchange rates

Three subcommands for working with FX rates (powered by Yahoo Finance, free, no API key):

#### `trade fx rate` — Single pair

```bash
trade fx rate USD KZT
#  USD/KZT = 499.7300

trade fx rate EUR USD --amount 1000
#  EUR/USD = 1.1616
#  1000.00 EUR = 1161.60 USD
```

#### `trade fx convert` — Quick conversion

```bash
trade fx convert 1000 USD KZT
#  1,000.00 USD = 499,730.01 KZT  (rate: 499.7300)
```

#### `trade fx matrix` — Cross-rate table

```bash
trade fx matrix                    # uses currencies from config
trade fx matrix "USD,EUR,GBP,JPY"  # custom list
```

```
                 FX Cross Rates
┏━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┓
┃     ┃      USD ┃      EUR ┃     RUB ┃    KZT ┃
┡━━━━━╇━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━┩
│ USD │        1 │ 0.860900 │ 77.6860 │ 499.73 │
│ EUR │   1.1616 │        1 │ 90.1490 │ 580.32 │
│ RUB │ 0.012872 │ 0.011041 │       1 │      - │
│ KZT │ 0.002001 │ 0.001718 │       - │      1 │
└─────┴──────────┴──────────┴─────────┴────────┘
```

Default currencies are configured in `.traderc.toml`:

```toml
[fx]
currencies = ["USD", "EUR", "RUB", "KZT"]
base_currency = "USD"
```

## P&L Calculation

Uses the **Average Cost** method with commissions tracked separately:

```
Position: 5 FRO @ avg $39.50
Close: 2 shares @ $43.00, commission $10

  Cost basis:    2 x $39.50  = $79.00
  Sale proceeds: 2 x $43.00  = $86.00
  Gross P&L:     $86 - $79   = $7.00
  Net P&L:       $7 - $10    = -$3.00 (commission > profit)

  Remaining: 3 FRO @ $39.50 (avg cost unchanged)
```

Key rules:
- Commission is **not** baked into avg cost — tracked separately for transparency
- Partial close does **not** change the remaining position's avg cost
- Each close creates both a SELL trade and a `closed_trades` record with full audit trail

## Project Structure

```
obsidian-trading-tracker/
├── pyproject.toml              # dependencies, entry point
├── .traderc.toml               # config example
├── schema/
│   └── 001_initial.sql         # SQLite schema + views + triggers
├── src/trading_tracker/
│   ├── cli.py                  # Typer CLI commands
│   ├── db.py                   # SQLite CRUD, migrations
│   ├── models.py               # Pydantic validation models
│   ├── analytics.py            # P&L, stats, strategy breakdown
│   ├── config.py               # TOML config loader
│   ├── sync.py                 # Obsidian markdown export
│   └── templates/              # Jinja2 templates
│       ├── daily_log.md.j2
│       └── position_note.md.j2
└── tests/                      # 56 tests
    ├── test_db.py
    ├── test_cli.py
    └── test_analytics.py
```

## Development

```bash
# Run tests
uv run pytest -v

# Lint
uv run ruff check src/ tests/

# Auto-fix lint issues
uv run ruff check --fix src/ tests/
```

## Database

Data lives in a single SQLite file (default: `trades.db`).

**Tables:**
- `trades` — every BUY/SELL entry
- `closed_trades` — closed position records with P&L, review notes, and trade ID references
- `schema_version` — migration tracking

**Views (queryable directly with any SQLite tool):**
- `positions` — net open positions with avg cost
- `trade_history` — all trades ordered by time
- `daily_pnl` — daily P&L summary
- `strategy_performance` — win rate and P&L per strategy

You can query the database directly:

```bash
sqlite3 trades.db "SELECT * FROM positions"
sqlite3 trades.db "SELECT * FROM strategy_performance"
sqlite3 trades.db "SELECT * FROM daily_pnl"
```

## Alternatives

| Tool | Type | Database | Obsidian | CLI | Free | Self-hosted |
|------|------|----------|----------|-----|------|-------------|
| **This tool** | CLI | SQLite | Export | Yes | Yes | Yes |
| [Journalit](https://journalit.co) | Obsidian plugin | Markdown files | Native | No | Freemium | Yes |
| [TradeNote](https://github.com/Eleven-Trading/TradeNote) | Web app | MongoDB | No | No | Yes | Docker |
| [Deltalytix](https://github.com/hugodemenez/deltalytix) | Web app | PostgreSQL | No | No | Freemium | Supabase |
| [TradeLens](https://github.com/mingi3314/tradelens) | CLI | None | Export | Yes | Yes | Yes |
| TraderSync | SaaS | Cloud | No | No | $30-80/mo | No |
| Edgewonk | SaaS | Cloud | No | No | ~$170/yr | No |

**When to use this tool vs alternatives:**
- You want a **local-first** journal with no cloud dependency
- You already use **Obsidian** for notes and want trades integrated
- You prefer **CLI** over web UIs
- You want to **query your data** with raw SQL
- You need **partial close** tracking with avg cost method

**When to use something else:**
- You need a polished GUI with charts — TradeNote or Deltalytix
- You want everything inside Obsidian with no terminal — Journalit
- You need 100+ broker auto-imports — TraderSync (paid)
- You trade options with complex multi-leg strategies — TraderSync or Edgewonk

## Roadmap

- [ ] Broker CSV import (Interactive Brokers, Tradovate)
- [ ] Live price quotes via yfinance (unrealized P&L in `positions`)
- [ ] Multi-currency support with exchange rate conversion
- [ ] `trade undo` — reverse last action
- [ ] Obsidian Dataview query examples in export templates

## License

MIT
