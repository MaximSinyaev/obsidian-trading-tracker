# Persona fixtures

Realistic broker exports for four user personas, with hand-checked expected results
(average-cost method; net = gross - pro-rata entry commission - exit commission).
`tests/test_persona_fixtures.py` imports each scenario and compares open positions,
realized P&L per currency and positions that must not exist. Checks that fail today
are listed in `KNOWN_ISSUES` in the test and marked `xfail(strict=True)`: when a fix
lands, the test turns into XPASS and fails, so remove the entry.

| Persona | Broker / format | Main traps |
|---|---|---|
| `aidar_freedom_kz` | Freedom Finance (Tradernet) CSV + broker-report JSON, USD+KZT | FX conversion row, commission in EUR, `.US` tickers, dividends with tax |
| `olga_tinvest_ru` | T-Invest operations, cp1251, newest first | commissions as separate rows, currency purchase, OFZ with accrued interest, coupons |
| `daniel_ibkr_eu` | IBKR Flex (multi-section) + Activity Statement | split in CorporateActions, short with reversed rows, partial fills, `-1,000` FX |
| `jess_options_us` | Schwab transactions | options symbols, `Fees & Comm`, `as of` dates, expiry and assignment |

The Freedom format is reconstructed from public descriptions of the Tradernet broker report;
verify it against a real anonymised export.
