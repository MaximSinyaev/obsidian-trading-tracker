---
name: trading-idea-analyst
description: Structured research process for generating and stress-testing trading ideas from the full macro picture instead of the latest headline. Use when the user asks for trade ideas, a market outlook, "what should I look at", a thesis check on a ticker or theme, or a review of an open position's thesis. Produces tagged ideas with thesis, stop, exit conditions and event risk, and hands them to the trading journal (`trade` CLI) so AI ideas can be scored later. Research aid, not financial advice.
metadata: {"openclaw":{"emoji":"🔭","homepage":"https://github.com/MaximSinyaev/agent-trading-journal"}}
---

# Trading Idea Analyst

A research process, not a signal service. Its job is to make idea generation
**disciplined and auditable**: every idea starts from a broad macro scan, carries
an explicit thesis, an invalidation point and a stop, and is written to the
journal so its outcome can be measured later.

The main failure it protects against is **recency / headline bias**: an assistant
reads the loudest recent story, searches for tickers that fit it, and returns
confirmation dressed up as research.

> Always show the disclaimer in `references/disclaimer.md` at the end of any
> output that contains ideas. Never present an idea as a recommendation to a
> specific person, never promise returns, never size a position for the user
> beyond applying the user's own stated rules.

## When to use which mode

| User asks | Mode | Steps |
|---|---|---|
| "Give me ideas", "what's interesting this week", "market outlook" | **Full scan** | 1 → 6 |
| "What do you think about X / theme Y?" | **Thesis check** | 1 (short), 3 → 6 for that one idea |
| "Should I keep holding X?" | **Position review** | Stop question first, then `references/position-review.md` |
| "Check my portfolio", "where should this month's contribution go?" (long-term / DCA investor) | **Portfolio review** | `references/position-review.md` → Long-term portfolio |
| "How did your ideas do?" | **Scorecard** | `references/journal-handoff.md` → Scoring: paper idea quality and real execution, reported separately, every idea counted |

## The pipeline (full scan)

```
1. MACRO SCAN      broad, fixed checklist, before any ticker is named
2. THESES          2–4 themes that explain the scan, each with evidence + counter-evidence
3. IDEAS           instruments derived from theses (never from headlines)
4. DEEP RESEARCH   one isolated sub-agent per thesis, in parallel
5. SYNTHESIS       concept + execution variants + risks + stop + exit triggers
6. HANDOFF         record each idea in the journal with tags and an idea id
```

### 1. Macro scan (no tickers yet)

Walk the **whole** checklist in `references/macro-checklist.md`, even the parts
that seem quiet. Quiet areas are information too. Use the sources configured in
`sources.toml` (see `references/sources.example.toml`); if none is configured,
use web search plus whatever market data tools are available, and say which
sources you actually used.

Rules:
- Collect before you interpret. Write the scan as short dated facts with sources.
- Do not open with the top headline. If one story dominates, cap it at one line
  in the scan and keep going through the checklist.
- Note the data date for every number. Stale data must be labelled stale.
- Read the user's open positions (`trade --json positions`) and recent journal
  (`trade --json journal list --limit 20`) if the CLI is available, so ideas
  account for existing exposure. Do this after the scan, not before, so the
  portfolio does not steer what you look for.

### 2. Thesis extraction

From the scan, pick 2–4 theses. For each write:
- **Claim** in one sentence ("Rates stay higher for longer; mega-cap
  concentration is fragile").
- **Evidence** (3+ points from the scan, with dates).
- **Counter-evidence** (at least 2 points). A thesis without counter-evidence
  has not been examined.
- **Horizon**: tactical (days–weeks), medium (1–6 months), structural (6+ months).
- **What would kill it**: the observable event or level that invalidates it.

Run the bias check in `references/bias-checklist.md` before moving on.

### 3. Idea generation

For each thesis list candidate instruments (stocks, ETFs, sectors, pairs,
hedges). Prefer liquid, plain instruments; leveraged or derivative variants are
listed only as *variants* with their extra rules (see Risk rules). Generate at
least one idea that would profit if the thesis is **wrong** (the hedge or the
other side), so the user sees both.

### 4. Deep research with sub-agents

If the environment can spawn sub-agents (Agent / Task tool), run one per thesis
**in parallel**, each in its own context, using the brief template in
`references/research-brief.md`. Each returns a bounded report: candidates,
fundamentals, catalysts with dates, risks, invalidation levels, sources.

If sub-agents are not available, research each thesis sequentially, keeping a
separate notes section per thesis, and say that you did so.

Never let a sub-agent's conclusion stand without checking it against the
counter-evidence from step 2.

### 5. Synthesis

Use the output template in `references/output-template.md`. Every idea must
have all of these fields, or it is not an idea yet:

- idea id (`idea-YYYYMMDD-<slug>`), thesis link, direction, type tag
- instrument(s) and execution variants (plain / smaller / hedged / leveraged)
- reference price with timestamp and source
- entry zone, **stop**, targets, horizon and **time stop**
- exit triggers (thesis-level, not just price) and event calendar
- risks and what would make you wrong
- confidence 1–5, with one line on why

### 6. Journal handoff

Each plan entry ends with a `JSON:` line (ticker, direction, reference price
and time, stop, targets, time stop) so ideas can be scored without parsing
prose.

Record every idea, taken or not, following `references/journal-handoff.md`:
a `plan` journal entry for each idea, and, when the user reports a fill, the
trade itself with `--sl`, targets, `--strategy`, `--setup`, the thesis in
`--notes` and the same tags. Ideas that are never recorded cannot be scored,
and unscored ideas let the assistant remember only its wins.

## Risk rules (always applied)

These are process rules. The user can override the numbers in `sources.toml`
(`[risk]` section); the defaults below come from real losses.

1. **Stop question first.** Before analysing any existing position, ask "What
   is your stop level, and your planned exit date?" A position with a stop is a
   defined-risk trade; without one it is a different trade. Never give a
   hold/close view before you know it.
2. **No idea without a stop.** If a sensible stop cannot be defined, the idea
   is not ready. Say so instead of inventing one.
3. **Event / headline risk.** For geopolitical, commodity, or single-event
   ideas, ask "What if a key decision-maker says the opposite tomorrow?" and
   apply the event size factor (default 0.5× the user's normal size) and an
   event time stop (default 3 trading days unless hedged; calendar days for
   markets that trade 24/7, such as crypto).
4. **Leveraged and inverse ETFs.** Size factor default 0.4×, hard time stop
   default 5 trading days for 3× products (calendar days for 24/7 markets,
   including leveraged crypto perpetuals, where funding replaces decay), and
   state the decay or funding cost. Never the default variant.
5. **Binary events** (CPI, central bank decisions, earnings) inside the idea's
   horizon must be listed with date and time in the user's timezone, plus the
   plan for each outcome.
6. **Concentration.** Flag if an idea pushes any ticker above the user's max
   per-ticker share (default 30%) or adds a third correlated position. The
   journal does not sum across currencies, so compute the share either within
   one currency (each position in `trade --json positions` carries its
   `currency`; `trade --json report` groups by `by_currency`) or by
   converting every currency to `[user].base_currency` with
   `trade --json fx rate <CCY> <BASE>` and stating the rates and their time.
   Say which method you used.
7. **Broken thesis = exit.** When an exit trigger fires, the default view is to
   exit, not to wait for a bounce. Record it as a lesson in the journal.
8. **P&L from the journal only.** Never compute the user's P&L by hand; read
   it from `trade --json history` / `trade --json report`.
9. **Stop on the right side.** A long's stop is below the reference price, a
   short's above it; targets on the opposite side. Check this before writing
   any idea or trade. `trade add` returns `warnings` for a missing or
   wrong-side stop: read them and fix the record if one appears.
10. **Options** (only if allowed in `instruments_allowed`): state whether the
   stop is on the premium or on the underlying, and the maximum loss (premium
   paid for long options; for short puts strike × multiplier − premium, for
   short calls unlimited unless covered or spread). Expiry is the hard time
   stop. Flag assignment / early-exercise risk around ex-dividend dates and
   expiry, and prefer defined-risk structures. Record with
   `--instrument option --multiplier 100` (or the contract's multiplier).

## Tags

| Tag | Values | Meaning |
|---|---|---|
| origin | `ai-idea` | Generated by this skill (required, the journal splits AI vs manual on it) |
| direction | `ai-bullish`, `ai-bearish`, `ai-neutral` | Expected direction |
| type | `ai-macro`, `ai-news`, `ai-technical`, `ai-event` | What the idea rests on |
| strategy | `event-driven`, `leveraged`, `hedge`, `structural`, `tactical` | Extra risk class |
| id | `idea-YYYYMMDD-<slug>` | Links the idea, its trades and its review |

## Scheduled runs (optional)

The skill can run on a schedule (pre-market scan, post-close recap, daily
digest). Times, timezone and sources live in `sources.toml`, never in this file.

The full pipeline with parallel sub-agents is expensive, so scheduled runs
default to **delta mode** (`[schedule].mode = "delta"`): one agent, no
sub-agents, scan only what changed since the last scan (read the previous
scan from the journal), check open ideas' exit triggers and time stops, and
flag whether any thesis now needs a full run. The full pipeline runs only when
the user asks for it, or when `mode = "full"`. Scheduled runs write nothing to
the journal except `CLOSED` entries for ideas whose time stop passed, unless
`[schedule].auto_record = true`.

## Reference files

- `references/macro-checklist.md`: the scan checklist
- `references/bias-checklist.md`: checks to run before ideas
- `references/research-brief.md`: sub-agent brief template
- `references/output-template.md`: idea card and report layout
- `references/position-review.md`: reviewing an open position
- `references/journal-handoff.md`: how ideas are recorded and scored
- `references/sources.example.toml`: configurable sources, timezone, risk numbers
- `references/disclaimer.md`: text to show with every set of ideas

## Follow-up (not built yet)

A first-class `trade idea add/list/close` command would let untaken ideas be
scored against fetched prices without parsing journal text. Until then,
`plan` and `review` journal entries are the contract.
