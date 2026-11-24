# Installing the trading journal skills

The project has two parts:

- **The `trade` CLI**: a Python program that keeps your trades in a local SQLite
  file and does every calculation.
- **The skills** in [`skills/`](../skills/): instructions that teach an AI agent
  to drive the CLI (`trading-journal`), and `trading-idea-analyst` for macro-first
  trade ideas, which works on its own and uses the journal when it is installed.

Agents that support skills install the skill folder; the skill then installs
the CLI on first use. You need [uv](https://docs.astral.sh/uv/) (recommended) or
pipx, and Python 3.12+ (uv downloads it for you if missing).

## Claude Code

```text
/plugin marketplace add MaximSinyaev/agent-trading-journal
/plugin install trading-journal@trading-journal
```

Restart Claude Code, then talk about your trades ("I bought 10 NVDA at 118.40,
stop 109", "import ~/Downloads/trades.csv", "show my dashboard"). To check it
loaded: `/plugin` lists `trading-journal` as installed.

From a terminal instead of the chat:

```bash
claude plugin marketplace add MaximSinyaev/agent-trading-journal
claude plugin install trading-journal@trading-journal
```

Update: `claude plugin marketplace update trading-journal`, then
`/plugin` → update. Remove: `claude plugin uninstall trading-journal@trading-journal`.

## Codex (CLI and IDE extension)

Codex reads skills from `.agents/skills/` in your project and from
`~/.agents/skills/` for all projects. Copy the skill folder there:

```bash
git clone --depth 1 https://github.com/MaximSinyaev/agent-trading-journal /tmp/ttt
mkdir -p ~/.agents/skills
cp -r /tmp/ttt/skills/trading-journal ~/.agents/skills/
cp -r /tmp/ttt/skills/trading-idea-analyst ~/.agents/skills/   # optional: trade ideas
```

Codex picks the skill automatically when you talk about trades. To call it
explicitly, type `$trading-journal` (or run `/skills`). Codex also reads
[`AGENTS.md`](../AGENTS.md) when you work inside a clone of this repository.

## Cursor

Cursor reads skills from `.cursor/skills/` or `.agents/skills/` in the project,
and from `~/.cursor/skills/` or `~/.agents/skills/` for all projects. It also
loads `~/.claude/skills/` and `~/.codex/skills/`, so one copy serves several agents:

```bash
git clone --depth 1 https://github.com/MaximSinyaev/agent-trading-journal /tmp/ttt
mkdir -p ~/.agents/skills
cp -r /tmp/ttt/skills/trading-journal ~/.agents/skills/
cp -r /tmp/ttt/skills/trading-idea-analyst ~/.agents/skills/   # optional: trade ideas
```

The agent applies the skill when it is relevant. To force it, type `/` in the
Agent chat and pick `trading-journal`.

## Other agents (Gemini CLI, OpenCode, Aider, ...)

If the agent supports `SKILL.md` skills, copy `skills/trading-journal/` (and,
for trade ideas, `skills/trading-idea-analyst/`) into its skills folder. Otherwise install the CLI yourself and point the agent at
[`AGENTS.md`](../AGENTS.md) and
[`skills/trading-journal/SKILL.md`](../skills/trading-journal/SKILL.md)
(for example by adding them to its context or rules file).

## The CLI on its own

```bash
uv tool install "agent-trading-journal[yahoo] @ git+https://github.com/MaximSinyaev/agent-trading-journal@v1.3.0"
# or
pipx install "agent-trading-journal[yahoo] @ git+https://github.com/MaximSinyaev/agent-trading-journal@v1.3.0"
trade --version
```

Pin a release by adding `@v1.3.0` after the URL. Drop `[yahoo]` if you do not
want optional Yahoo Finance quotes; without it the CLI makes no network calls.

## Where your data lives

`~/.trading-journal/trades.db` by default. Change it with `TRADE_DB=/path/to/trades.db`,
`trade --db PATH`, or `.traderc.toml` (see the README). Back this file up; it is
your whole journal.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `trade CLI not found` | Install uv (`curl -LsSf https://astral.sh/uv/install.sh \| sh`) or run the `uv tool install` line above. |
| `No such option: --json` | You have a pre-1.0 version (package `obsidian-trading-tracker`). `uv tool uninstall obsidian-trading-tracker`, then run the install line above. Your database is kept. |
| `trade: command not found` after install | Run `uv tool update-shell` and open a new terminal. |
| The agent does arithmetic itself | Remind it: "use the trading-journal skill". Report it as a bug with the prompt. |
| Import skipped rows | Run `trade import FILE --dry-run` and map columns with `--map ticker=... --map timestamp=...`. |
