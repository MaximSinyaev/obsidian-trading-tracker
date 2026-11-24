# Importing trades

`trade --json import FILE` accepts CSV (any delimiter, UTF-8 or Windows-1251) or a
JSON list. Always run with `--dry-run` first on an unfamiliar file and check
`columns` (which column was used for which field) and `skipped`.

## Fields and recognised column names

| Field | Required | Recognised names (case/spacing ignored) |
|---|---|---|
| ticker | yes | ticker, symbol, secid, code, тикер, инструмент |
| action | yes* | side, action, type, buy/sell, operation, direction, тип операции, операция |
| shares | yes | quantity, qty, shares, units, volume, количество |
| price | yes | price, trade price, fill price, avg price, цена |
| timestamp | yes | date, datetime, time, trade date, executed at, дата, дата сделки |
| commission | no | commission, fee, fees, ibcommission, комиссия |
| currency | no | currency, currencyprimary, ccy, валюта |
| external_id | no | id, trade id, order id, execution id, номер сделки |
| multiplier | no | multiplier, contract multiplier, мультипликатор |
| instrument | no | asset class, instrument type, security type (STK, OPT, FUT, ETF...) |
| strategy, notes, tags, stop_loss | no | strategy/setup, notes/comment, tags, stop/sl |

\* If there is no side column, a negative quantity means SELL.

Values: `Buy/BOT/B/Market buy/Buy to Open` → BUY, `Sell/SLD/S/Limit sell/Sell to Close`
→ SELL; `Expired/Assigned/Exercised` close the open position at 0. `Long`/`Short` are
not accepted as sides (they often describe a position). Other rows (dividends,
transfers, interest, repo) are skipped and listed in `skipped` with a hint.

Also handled automatically:

- currency conversions (`EUR.USD`, `USD/KZT`, asset class `CASH`) are skipped, never positions;
- option symbols (`SPY 250321P00560000`, `SPY 03/21/2025 560.00 P`, `+XLU.21MAR2025.C78`) get
  `instrument=option`, multiplier 100;
- crypto pairs (`BTCUSDT`) are priced in the quote currency; units glued to numbers
  (`0.05000000BTC`) are fine; a fee paid in the bought coin reduces the quantity;
- London prices in pence (`GBX`) are converted to GBP;
- a fee charged in another currency than the trade is **not** converted: it is left out
  of P&L, noted on the trade and reported in `notes`;
- fee rows reported separately with the same time and ticker are attached to the fill;
- newest-first files are imported oldest-first. Numbers like
`1 234,56`, `1,234.56`, `(12.50)` are understood; commissions are taken as
absolute values. Dates: ISO, `20250115;103000` (IBKR), `15.01.2025 10:30`,
`01/15/2025` (US; use `--date-format %d/%m/%Y` for day-first). A separate
`Time` column is joined to the date.

Override detection with `--map field=Column` (repeatable).

## Presets

- `--preset generic` (default) — any CSV/XLSX/JSON, including Trading 212, Binance,
  Schwab-style histories.
- `--preset ibkr` — Interactive Brokers Flex Query (Trades plus CashTransactions and
  CorporateActions sections: dividends, withholding tax and splits become events) and
  Activity Statements. Uses `DateTime`, `Buy/Sell`, `TradeID`, `IBCommission`, `Multiplier`.
- `--preset okx` — OKX "Trading History" (bill history) CSV of perpetual swaps, detected
  automatically. Quantities are contracts; the contract value (`ctVal`, e.g. 0.1 ETH for
  `ETH-USDT-SWAP`) becomes the trade's multiplier. The export does not include it, so it is
  solved per instrument from the `PnL` column OKX prints on every closing fill (an explicit
  `trade edit ID --multiplier` always wins). Times use the time zone in the file's first line.
  Liquidations are tagged `liquidation` and their separate liquidation fee is added to the
  fill's commission; funding payments become `funding` events; margin transfers are skipped.
  The result has `reconciliation {closing_fills, match_okx_pnl, contract_values}`: how many
  closes match OKX's own PnL. A close can disagree when the position was opened before the
  export window or the account used hedge mode (long and short at once).
- Optional regional presets: `--preset freedom` (Freedom Finance / Tradernet CSV or the
  JSON broker report, which is detected automatically and also brings dividends;
  `.US` suffixes are dropped) and `--preset tinvest` (T-Invest operations export).

## De-duplication

Each imported row gets `external_id` = the broker id, or a fingerprint of
ticker, side, quantity, price and timestamp. A row whose `(source, external_id)`
already exists is skipped, so re-importing overlapping exports is safe. The
source defaults to the preset name (`ibkr`, `okx`, `tinvest`) or `import`; pass
`--source` to keep accounts apart.

Without `--source`, a row that matches a trade from another source exactly
(ticker, side, quantity, price and time) is also a duplicate, reported with
`matches_trade_id` and `matches_source`. This catches the same file imported
first without a preset and then with one. With `--source`, identical fills in
two accounts are both kept.

## JSON (for agents pulling fills from an API)

```json
[
  {"ticker": "BTC-USD", "action": "buy", "shares": 0.05, "price": 42000,
   "commission": 4.2, "timestamp": "2025-01-05T12:00:00", "currency": "USD",
   "instrument": "crypto", "external_id": "binance-123456"}
]
```

Pipe it in with `trade --json import - < fills.json` or save to a file. Always set
`external_id` from the exchange's trade id when you have it.
