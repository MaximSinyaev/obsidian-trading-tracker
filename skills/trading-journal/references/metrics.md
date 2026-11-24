# How the numbers are defined

Use these definitions when you explain results to the user. Do not recompute them.

**Average-cost method.** Buying more of a position re-averages the entry price
(quantity-weighted). Selling part of it does not change the average. When a
position goes flat the average resets; the next buy starts a new position.

**Realized P&L** is recorded for every fill that reduces a position:

- gross = (exit price − average entry price) × quantity × multiplier (reversed for shorts)
- net = gross − entry commission share − exit commission share
- entry commission share = commissions paid to open the position × closed qty / open qty
- If a fill flips the position (sell 10 while long 6), the first 6 close the long and
  the other 4 open a short; that fill's commission is split 6/4.

**pnl_percent** = net P&L / (average entry × quantity × multiplier).

**Breakeven price** of an open position = average price adjusted by the entry
commissions still attributed to it.

**Unrealized P&L** = (last stored price − average) × quantity × multiplier;
`unrealized_pnl_net` also subtracts the open entry commissions. Depends on the
quote time shown in `price_as_of`.

**Win** = a realized record with net P&L > 0; zero or negative counts as a loss.
Each partial close is its own record.

**Profit factor** = sum of winning net P&L / |sum of losing net P&L| (null when there
are no losses). **Expectancy** = average net P&L per realized record.

**R-multiple** = net P&L / initial risk, where initial risk = |average entry −
first stop-loss set on the entry fills| × quantity × multiplier (stops are adjusted
for later splits). Only for trades entered with `--sl`.

**Max drawdown** = largest fall of cumulative realized net P&L (by close date)
from its running peak, per currency.

**Origin** = `ai` if any tag on the entry or exit fills is `ai` or starts with
`ai-` (`ai-idea`, `ai-bullish`...), otherwise `manual`.

**Dividends** are tracked as income (net of withholding tax) and are not part of
trade P&L; `net_pnl_with_income` adds them.

**Funding** on perpetual swaps (imported from OKX as `funding` events) is income too:
signed as received (+) or paid (−), reported as `funding` next to trade P&L and
included in `net_pnl_with_income`, never inside a trade's P&L.

**Currencies** are never converted or mixed: each currency has its own summary.
A ticker's P&L is in the currency of its fills.

Splits: quantity × ratio and price ÷ ratio for the position held at the split
date; cost basis is unchanged.
