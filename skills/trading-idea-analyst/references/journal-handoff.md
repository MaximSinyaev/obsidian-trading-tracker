# Journal handoff and scoring

This skill writes to the trading journal through its CLI (`trade`, from this
repository). Always call it with `--json`; errors come back as
`{"ok": false, "error": "..."}`. If the CLI is not installed, print the
commands for the user instead of skipping the step.

## Idea id

`idea-YYYYMMDD-<slug>`, for example `idea-20261124-rsp-rotation`. The id goes
into the tags of every record that belongs to the idea, because tags are what
the journal reports on.

## 1. Every idea: a plan entry

Recorded at the end of synthesis, whether or not the user takes it.

```bash
trade --json journal add "$(cat <<'IDEA'
IDEA idea-20261124-rsp-rotation
Direction: bullish | Type: macro | Horizon: structural
Thesis: Mega-cap concentration is at an extreme; equal weight should outperform as earnings broaden.
Reference: RSP 182.40 @ 2026-11-24 15:30 UTC (source)
Entry zone: 178-184
Stop: 170 | Targets: T1 196, T2 205 | Time stop: 2027-05-24
Exit triggers: top-10 earnings revisions re-accelerate; breadth makes new lows
Events: 2026-12-10 FOMC
Variants: plain RSP | hedge: long RSP / short SPY
Confidence: 3
Sources: ...
IDEA
)" --ticker RSP --kind plan --tags ai-idea,ai-bullish,ai-macro,structural,idea-20261124-rsp-rotation
```

Keep the field names exactly as above (`IDEA`, `Direction`, `Thesis`,
`Reference`, `Entry zone`, `Stop`, `Targets`, `Time stop`, `Exit triggers`,
`Confidence`) so the entries can be parsed later.

## 2. When the user takes the idea: the trade carries it

Only when the user says they placed the order, with their actual fill.

```bash
trade --json add RSP buy 5 181.90 --commission 0.22 \
  --sl 170 --tp1 196 --tp2 205 --confidence 3 \
  --strategy structural --setup equal-weight-rotation \
  --notes "idea-20261124-rsp-rotation: <thesis>. Exit if <triggers>. Time stop 2027-05-24." \
  --tags ai-idea,ai-bullish,ai-macro,structural,idea-20261124-rsp-rotation \
  --source ai-analyst --group idea-20261124-rsp-rotation
```

- `--sl` (alias `--stop`) is the stop; the journal uses it for R multiples and
  risk to stop. Never record an AI idea trade without it.
- Any tag starting with `ai` puts the trade in the AI bucket of
  `trade report` (`by_origin`), so AI vs. manual results appear without extra
  work. Do not put `ai-*` tags on the user's own ideas.
- `--group` is stored but not yet reported; the idea id must also be in tags.

## 3. Reviews, invalidations, expiry

```bash
# Thesis review (position-review.md)
trade --json journal add "REVIEW idea-...: thesis weakened because ..." --ticker RSP --kind review --tags ai-idea,idea-...
# Exit trigger fired or time stop hit for an idea that was never taken
trade --json journal add "CLOSED idea-...: invalidated | expired | target hit. Price at close: ... @ ... (source)" --ticker RSP --kind review --tags ai-idea,idea-...
# Lesson after a broken thesis
trade --json journal add "LESSON idea-...: ..." --ticker RSP --kind lesson --tags ai-idea,idea-...
```

Every idea ends with one `CLOSED` review entry, taken or not, so the
scorecard has an outcome for it.

## Scoring ("how did your ideas do?")

1. Taken ideas: `trade --json report` and read
   `data.by_currency.<CCY>.by_origin` (ai vs manual) and
   `data.by_currency.<CCY>.by_tag` (per idea type and per idea id). Quote those numbers; do not
   recompute P&L.
2. All ideas, taken or not: `trade --json journal list --limit 200`, keep
   entries tagged `ai-idea`, group by idea id. For each idea take the `IDEA`
   plan entry and its `CLOSED` entry and compute, from the recorded reference
   and close prices: move in the idea's direction (%), and whether stop or T1
   was reached first if known. Label it "paper result, not P&L".
3. Show: hit rate by type tag (`ai-macro`, `ai-news`, `ai-technical`,
   `ai-event`), by horizon, by confidence level, and how many ideas were taken.
4. Feed the result back: if a type tag keeps losing, lower confidence for
   that type in the next scan (bias checklist item 8).

## Planned improvement

A first-class `trade idea add/list/close` command (reference price, stop,
target, horizon, status open/taken/invalidated/expired, scored against
`trade price fetch`) would replace the text parsing in step 2. Until it
exists, plan and review journal entries are the contract.
