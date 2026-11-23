# Personas and scenarios

Global first. Each persona has a fixture folder in `tests/fixtures/personas/` with a realistic export
and hand-checked expected results. Pain points behind them: [pain-points.md](pain-points.md).

## 1. Daniel: swing trader on IBKR, EU, multi-currency (primary)
29, Berlin. Base currency EUR, trades US and EU stocks, sometimes shorts. Keeps notes in Obsidian.
- "Import my Flex export from last month and tell me what I made in each currency."
- "Why is my NVDA average 115 when I paid 1150?" (split)
- "Add a thesis to my TSLA short and put the trade in my vault."
- Weekly review: wins/losses, R multiples, worst mistake, open positions without a stop.

## 2. Jess: options seller on Schwab, US (primary)
37, Austin. Sells cash-secured puts, buys calls now and then.
- "Import my Schwab transactions; which puts expired and which got assigned?"
- "What's my premium income this month, after fees?"
- Expiry and assignment must close the option and open the stock leg.

## 3. Sofia: fractional-share investor on Trading 212, EU (primary)
31, Lisbon. EUR account, US shares and a UK ETF priced in pence, small amounts every month.
- "How am I doing in euros, and per position?"
- "How much did FX fees and US withholding tax cost me this year?"

## 4. Ken: spot crypto trader on Binance (primary)
26, remote. Trades BTC/ETH against USDT at any hour, fees in USDT, BNB or the coin.
- "Load my Binance history and show realized P&L in USDT."
- "What's my BTC average cost after fees?"

## 5. Aidar: investor on Freedom Finance, KZT + USD (regional)
34, Almaty. KASE stocks in KZT, US stocks and options in USD, quarterly KZT->USD conversions.
- "Import my broker report and match the broker's profit column."
- Dividends with withholding tax; option expiry as a sell at 0.

## 6. Olga: investor on T-Invest, RUB (regional)
41, Kazan. Russian stocks, government bonds with accrued interest, some CNY.
- Commissions and taxes come as separate rows; coupons and dividends with 13% tax.

## Cross-persona asks
- One command to import any broker file, with a clear list of what was skipped and why.
- Never invent numbers; every figure traceable to trades.
- Obsidian notes that do not overwrite what the user wrote.
- Local-first: data stays on the machine.
