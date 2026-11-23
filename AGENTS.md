# Instructions for AI agents (Codex, Cursor, Claude Code, others)

This repository ships a trading journal CLI, `trade`, built for agents. If the
user asks about their trades, positions, P&L or trading performance, use it.

1. **Never compute trade numbers yourself** (P&L, averages, win rate, drawdown).
   Record facts with the CLI and quote numbers from its output.
2. Always call it in JSON mode: `trade --json <command>`. Responses are
   `{"ok": true, "data": ...}` or `{"ok": false, "error": "..."}`.
3. Install once if missing:
   `uv tool install "obsidian-trading-tracker[yahoo] @ git+https://github.com/MaximSinyaev/obsidian-trading-tracker"`
   (or use `skills/trading-journal/scripts/trade`, which falls back to `uvx`).

The full playbook is in [`skills/trading-journal/SKILL.md`](skills/trading-journal/SKILL.md),
with the command reference in `skills/trading-journal/references/`.

## Working on this codebase

- Python 3.12+, `uv sync`, tests: `uv run pytest`.
- `src/trading_tracker/ledger.py` is the only place that computes positions and P&L.
  `closed_trades` is a cache rebuilt from `trades`; never write P&L anywhere else.
- Schema changes go in a new numbered file in `src/trading_tracker/schema/`.
- Every CLI command must support `--json` through `_emit` / `_fail` in `cli.py`.
