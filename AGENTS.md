# Instructions for AI agents (Codex, Cursor, Claude Code, others)

This repository ships a trading journal CLI, `trade`, built for agents. If the
user asks about their trades, positions, P&L or trading performance, use it.

1. **Never compute trade numbers yourself** (P&L, averages, win rate, drawdown).
   Record facts with the CLI and quote numbers from its output.
2. Always call it in JSON mode: `trade --json <command>`. Responses are
   `{"ok": true, "data": ...}` or `{"ok": false, "error": "..."}`.
3. Install once if missing:
   `uv tool install "agent-trading-journal[yahoo] @ git+https://github.com/MaximSinyaev/agent-trading-journal@v1.3.0"`
   (or use `skills/trading-journal/scripts/trade`, which falls back to `uvx`).

The full playbook is in [`skills/trading-journal/SKILL.md`](skills/trading-journal/SKILL.md),
with the command reference in `skills/trading-journal/references/`.

## Trade ideas and research

If the user asks for trade ideas, a market outlook, a thesis check or a review
of whether to hold a position, follow
[`skills/trading-idea-analyst/SKILL.md`](skills/trading-idea-analyst/SKILL.md):
macro scan before any ticker, theses with counter-evidence, a stop for every
idea, and every idea logged with `ai-idea` tags and a `JSON:` line so its
outcome can be scored later. It works without the `trade` CLI (markdown idea
log); when the CLI is present, ideas go to the journal (`--kind plan`). It is a research process,
not financial advice; show its disclaimer with every set of ideas.

## Working on this codebase

- Python 3.12+, `uv sync`, tests: `uv run pytest`.
- `src/trading_tracker/ledger.py` is the only place that computes positions and P&L.
  `closed_trades` is a cache rebuilt from `trades`; never write P&L anywhere else.
- Schema changes go in a new numbered file in `src/trading_tracker/schema/`.
- Every CLI command must support `--json` through `_emit` / `_fail` in `cli.py`.
