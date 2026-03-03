"""P&L calculations, statistics, and strategy analytics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PartialCloseResult:
    pnl: float
    remaining_shares: float
    remaining_avg_cost: float


def calculate_partial_close(
    avg_cost: float,
    total_shares: float,
    shares_to_close: float,
    exit_price: float,
    commission: float = 0,
) -> PartialCloseResult:
    """
    Calculate P&L for a partial (or full) position close using avg cost method.

    Example:
        Position: 5 FRO @ avg $39.50 (total cost $197.50)
        Close: 2 FRO @ $43.00, commission $10

        Cost basis: 2 * $39.50 = $79.00
        Sale proceeds: 2 * $43.00 = $86.00
        Gross P&L: $86.00 - $79.00 = $7.00
        Net P&L: $7.00 - $10.00 = -$3.00 (loss due to commission)

        Remaining position: 3 FRO @ $39.50 (avg cost unchanged)
    """
    if shares_to_close > total_shares:
        raise ValueError(
            f"Cannot close {shares_to_close} shares, only {total_shares} held"
        )

    cost_basis = shares_to_close * avg_cost
    sale_proceeds = shares_to_close * exit_price
    gross_pnl = sale_proceeds - cost_basis
    net_pnl = gross_pnl - commission

    return PartialCloseResult(
        pnl=round(net_pnl, 2),
        remaining_shares=round(total_shares - shares_to_close, 8),
        remaining_avg_cost=avg_cost,  # avg cost doesn't change on close
    )


def compute_stats(closed_trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute overall trading statistics from closed trades."""
    if not closed_trades:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "total_commission": 0.0,
            "avg_hold_days": 0.0,
        }

    pnls = [t["net_pnl"] for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    commissions = [t.get("total_commission", 0) for t in closed_trades]
    hold_days = [
        t["hold_duration_days"]
        for t in closed_trades
        if t.get("hold_duration_days") is not None
    ]

    return {
        "total_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100 * len(wins) / len(pnls), 1) if pnls else 0.0,
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "best_trade": round(max(pnls), 2) if pnls else 0.0,
        "worst_trade": round(min(pnls), 2) if pnls else 0.0,
        "total_commission": round(sum(commissions), 2),
        "avg_hold_days": round(sum(hold_days) / len(hold_days), 1) if hold_days else 0.0,
    }


def fetch_live_prices(tickers: list[str]) -> dict[str, float | None]:
    """Fetch current prices for a list of tickers via yfinance.

    Returns a dict of ticker -> price. Missing/failed tickers get None.
    """
    if not tickers:
        return {}
    import yfinance as yf

    result: dict[str, float | None] = {}
    try:
        data = yf.download(tickers, period="1d", progress=False, threads=True)
        if data.empty:
            return {t: None for t in tickers}
        close = data["Close"]
        if len(tickers) == 1:
            # yf.download returns a Series for single ticker
            last = close.dropna().iloc[-1] if not close.dropna().empty else None
            result[tickers[0]] = round(float(last), 2) if last is not None else None
        else:
            for t in tickers:
                if t in close.columns:
                    col = close[t].dropna()
                    result[t] = round(float(col.iloc[-1]), 2) if not col.empty else None
                else:
                    result[t] = None
    except Exception:
        return {t: None for t in tickers}
    # Fill any missing
    for t in tickers:
        if t not in result:
            result[t] = None
    return result


def enrich_positions_with_prices(
    positions: list[dict[str, Any]],
    *,
    live: bool = True,
) -> list[dict[str, Any]]:
    """Add current price and unrealized P&L to positions."""
    if not positions:
        return positions

    if not live:
        for pos in positions:
            pos["current_price"] = None
            pos["unrealized_pnl"] = None
            pos["unrealized_pnl_pct"] = None
        return positions

    tickers = [p["ticker"] for p in positions]
    prices = fetch_live_prices(tickers)

    for pos in positions:
        price = prices.get(pos["ticker"])
        pos["current_price"] = price
        if price is not None:
            cost_basis = pos["net_shares"] * pos["avg_cost"]
            market_value = pos["net_shares"] * price
            pos["unrealized_pnl"] = round(market_value - cost_basis, 2)
            pos["unrealized_pnl_pct"] = (
                round((price - pos["avg_cost"]) / pos["avg_cost"] * 100, 2)
                if pos["avg_cost"] else 0.0
            )
        else:
            pos["unrealized_pnl"] = None
            pos["unrealized_pnl_pct"] = None
    return positions


def _extract_close(data, symbol: str | None = None) -> float | None:
    """Extract last close price from yfinance DataFrame, handling MultiIndex columns."""
    if data.empty:
        return None
    close = data["Close"]
    # yfinance may return MultiIndex columns with ticker as second level
    if hasattr(close, "columns"):
        # It's a DataFrame — pick the right column
        if symbol and symbol in close.columns:
            close = close[symbol]
        else:
            close = close.iloc[:, 0]
    series = close.dropna()
    if series.empty:
        return None
    return float(series.iloc[-1])


def fetch_fx_rate(base: str, quote: str) -> float | None:
    """Fetch a single FX rate (e.g. base=USD, quote=KZT → USDKZT=X)."""
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return 1.0
    import logging
    import yfinance as yf

    symbol = f"{base}{quote}=X"
    try:
        logging.disable(logging.CRITICAL)
        data = yf.download(symbol, period="5d", progress=False)
        logging.disable(logging.NOTSET)
        val = _extract_close(data, symbol)
        return round(val, 6) if val is not None else None
    except Exception:
        logging.disable(logging.NOTSET)
        return None


def fetch_fx_matrix(currencies: list[str]) -> dict[tuple[str, str], float | None]:
    """Fetch cross-rate matrix for a list of currencies.

    Returns dict of (base, quote) → rate for all pairs.
    Batches downloads for efficiency.
    """
    currencies = [c.upper() for c in currencies]
    pairs: list[tuple[str, str]] = []
    symbols: list[str] = []
    for base in currencies:
        for quote in currencies:
            if base != quote:
                pairs.append((base, quote))
                symbols.append(f"{base}{quote}=X")

    if not symbols:
        return {}

    import yfinance as yf

    result: dict[tuple[str, str], float | None] = {}
    # Set identity
    for c in currencies:
        result[(c, c)] = 1.0

    try:
        import logging
        logging.disable(logging.CRITICAL)
        data = yf.download(symbols, period="5d", progress=False, threads=True)
        logging.disable(logging.NOTSET)
        if data.empty:
            for p in pairs:
                result[p] = None
            return result

        close = data["Close"]
        if len(symbols) == 1:
            val = _extract_close(data, symbols[0])
            result[pairs[0]] = round(val, 6) if val is not None else None
        else:
            for pair, sym in zip(pairs, symbols):
                if sym in close.columns:
                    col = close[sym].dropna()
                    result[pair] = round(float(col.iloc[-1]), 6) if not col.empty else None
                else:
                    result[pair] = None
    except Exception:
        logging.disable(logging.NOTSET)
        for p in pairs:
            result[p] = None

    return result


def convert_amount(
    amount: float, from_currency: str, to_currency: str, rates: dict[tuple[str, str], float | None] | None = None,
) -> float | None:
    """Convert amount between currencies using provided or fetched rates."""
    from_currency, to_currency = from_currency.upper(), to_currency.upper()
    if from_currency == to_currency:
        return amount
    if rates:
        rate = rates.get((from_currency, to_currency))
    else:
        rate = fetch_fx_rate(from_currency, to_currency)
    if rate is None:
        return None
    return round(amount * rate, 2)


def strategy_breakdown(closed_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Break down stats by strategy."""
    by_strategy: dict[str, list[dict[str, Any]]] = {}
    for t in closed_trades:
        key = t.get("strategy") or "unknown"
        by_strategy.setdefault(key, []).append(t)

    results = []
    for strategy, trades in sorted(by_strategy.items()):
        stats = compute_stats(trades)
        stats["strategy"] = strategy
        results.append(stats)
    return results
