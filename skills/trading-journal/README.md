# trading-journal

Part of [Agent Trading Journal](https://github.com/MaximSinyaev/agent-trading-journal): source code, issues and releases are there.

## What this skill does and does not do

- Runs only the local `trade` CLI (open source, in the repository above). The CLI stores trades in a SQLite file on your machine (`~/.trading-journal/trades.db` by default).
- If `trade` is missing, `scripts/trade` runs it through `uvx` from the tagged release `v1.3.0` of the repository above. Nothing else is downloaded.
- No API keys, tokens or credentials are needed or read. Optional env vars: `TRADE_DB`, `TRADE_CONFIG`, `TRADE_TZ`.
- Network: none, except optional Yahoo Finance quotes when you ask for live prices (the `[yahoo]` extra). No telemetry.
- Whatever your agent reads from the CLI (your trades, P&L) goes to your agent's model provider, as with any tool output.

## Disclaimer

Record-keeping and research aid, not financial, investment or tax advice. Provided as is, without warranty; use at your own risk. Check results against your broker statements before relying on them.

License: MIT in the source repository; copies published on ClawHub are distributed under MIT-0, as ClawHub requires.
