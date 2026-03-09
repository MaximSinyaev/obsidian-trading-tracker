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
            "profit_factor": None,
            "expectancy": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
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

    win_rate = round(100 * len(wins) / len(pnls), 1) if pnls else 0.0
    avg_win = round(sum(wins) / len(wins), 2) if wins else 0.0
    avg_loss = round(sum(losses) / len(losses), 2) if losses else 0.0

    # Profit factor: gross wins / abs(gross losses)
    total_wins = sum(wins)
    total_losses = abs(sum(losses))
    if total_losses > 0:
        profit_factor = round(total_wins / total_losses, 2)
    else:
        profit_factor = None  # no losses = infinite

    # Expectancy: (win_rate * avg_win) - (loss_rate * avg_loss)
    wr = win_rate / 100
    expectancy = round(wr * avg_win + (1 - wr) * avg_loss, 2)  # avg_loss is already negative

    return {
        "total_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": round(sum(pnls), 2),
        "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "best_trade": round(max(pnls), 2) if pnls else 0.0,
        "worst_trade": round(min(pnls), 2) if pnls else 0.0,
        "total_commission": round(sum(commissions), 2),
        "avg_hold_days": round(sum(hold_days) / len(hold_days), 1) if hold_days else 0.0,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
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
            net = pos["net_shares"]
            is_long = net > 0
            abs_shares = abs(net)
            if is_long:
                # Long: profit when price goes up
                pnl = abs_shares * (price - pos["avg_cost"])
            else:
                # Short: profit when price goes down
                pnl = abs_shares * (pos["avg_cost"] - price)
            pos["unrealized_pnl"] = round(pnl, 2)
            pos["unrealized_pnl_pct"] = (
                round(pnl / (abs_shares * pos["avg_cost"]) * 100, 2)
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


def compute_stats_by_currency(
    closed_trades: list[dict[str, Any]],
    base_currency: str = "USD",
    rates: dict[tuple[str, str], float | None] | None = None,
) -> dict[str, Any]:
    """Compute stats grouped by currency with total in base currency.

    Returns:
        {
            "by_currency": {ccy: stats_dict, ...},
            "total_pnl_base": float,
            "base_currency": str,
            "currencies": [ccy, ...],
        }
    """
    by_ccy: dict[str, list[dict[str, Any]]] = {}
    for t in closed_trades:
        ccy = t.get("currency", "USD")
        by_ccy.setdefault(ccy, []).append(t)

    result_by_ccy: dict[str, dict[str, Any]] = {}
    total_pnl_base = 0.0

    for ccy, trades in sorted(by_ccy.items()):
        stats = compute_stats(trades)
        stats["currency"] = ccy
        # Convert P&L to base currency
        if ccy == base_currency:
            stats["pnl_in_base"] = stats["total_pnl"]
        else:
            converted = convert_amount(stats["total_pnl"], ccy, base_currency, rates)
            stats["pnl_in_base"] = converted
        if stats["pnl_in_base"] is not None:
            total_pnl_base += stats["pnl_in_base"]
        result_by_ccy[ccy] = stats

    return {
        "by_currency": result_by_ccy,
        "total_pnl_base": round(total_pnl_base, 2),
        "base_currency": base_currency,
        "currencies": sorted(by_ccy.keys()),
    }


def compute_streaks(closed_trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute win/loss streaks from chronologically sorted trades."""
    if not closed_trades:
        return {
            "longest_win_streak": 0, "longest_win_pnl": 0.0,
            "longest_loss_streak": 0, "longest_loss_pnl": 0.0,
            "current_streak_type": None, "current_streak_count": 0, "current_streak_pnl": 0.0,
        }

    sorted_trades = sorted(closed_trades, key=lambda t: t.get("closed_at", ""))
    best_win = best_win_pnl = 0
    best_loss = best_loss_pnl = 0
    cur_type = None
    cur_count = cur_pnl = 0

    for t in sorted_trades:
        is_win = t["net_pnl"] > 0
        streak_type = "win" if is_win else "loss"

        if streak_type == cur_type:
            cur_count += 1
            cur_pnl += t["net_pnl"]
        else:
            cur_type = streak_type
            cur_count = 1
            cur_pnl = t["net_pnl"]

        if is_win and cur_count > best_win:
            best_win = cur_count
            best_win_pnl = cur_pnl
        elif is_win and cur_count == best_win:
            best_win_pnl = max(best_win_pnl, cur_pnl)

        if not is_win and cur_count > best_loss:
            best_loss = cur_count
            best_loss_pnl = cur_pnl
        elif not is_win and cur_count == best_loss:
            best_loss_pnl = min(best_loss_pnl, cur_pnl)

    return {
        "longest_win_streak": best_win,
        "longest_win_pnl": round(best_win_pnl, 2),
        "longest_loss_streak": best_loss,
        "longest_loss_pnl": round(best_loss_pnl, 2),
        "current_streak_type": cur_type,
        "current_streak_count": cur_count,
        "current_streak_pnl": round(cur_pnl, 2),
    }


def compute_max_drawdown(closed_trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Find the worst consecutive losing streak (max drawdown by sequential losses)."""
    if not closed_trades:
        return {"max_drawdown": 0.0, "max_drawdown_count": 0, "max_drawdown_tickers": []}

    sorted_trades = sorted(closed_trades, key=lambda t: t.get("closed_at", ""))
    max_dd = 0.0
    max_dd_count = 0
    max_dd_tickers: list[str] = []
    cur_dd = 0.0
    cur_count = 0
    cur_tickers: list[str] = []

    for t in sorted_trades:
        if t["net_pnl"] <= 0:
            cur_dd += t["net_pnl"]
            cur_count += 1
            cur_tickers.append(t["ticker"])
            if cur_dd < max_dd:
                max_dd = cur_dd
                max_dd_count = cur_count
                max_dd_tickers = list(cur_tickers)
        else:
            cur_dd = 0.0
            cur_count = 0
            cur_tickers = []

    return {
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_count": max_dd_count,
        "max_drawdown_tickers": max_dd_tickers,
    }


def compute_holding_analysis(closed_trades: list[dict[str, Any]]) -> dict[str, float]:
    """Average holding period for winners vs losers."""
    win_days = [
        t["hold_duration_days"] for t in closed_trades
        if t["net_pnl"] > 0 and t.get("hold_duration_days") is not None
    ]
    loss_days = [
        t["hold_duration_days"] for t in closed_trades
        if t["net_pnl"] <= 0 and t.get("hold_duration_days") is not None
    ]
    return {
        "avg_hold_winners": round(sum(win_days) / len(win_days), 1) if win_days else 0.0,
        "avg_hold_losers": round(sum(loss_days) / len(loss_days), 1) if loss_days else 0.0,
    }


def instrument_breakdown(closed_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Break down stats by instrument (stock, option, etc.)."""
    by_instrument: dict[str, list[dict[str, Any]]] = {}
    for t in closed_trades:
        key = t.get("instrument") or "stock"
        by_instrument.setdefault(key, []).append(t)

    results = []
    for instr, trades in sorted(by_instrument.items()):
        stats = compute_stats(trades)
        stats["instrument"] = instr
        results.append(stats)
    return results


def monthly_breakdown(closed_trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Break down stats by month (YYYY-MM from closed_at)."""
    by_month: dict[str, list[dict[str, Any]]] = {}
    for t in closed_trades:
        period = t.get("closed_at", "")[:7]  # YYYY-MM
        if period:
            by_month.setdefault(period, []).append(t)

    months = []
    profitable = 0
    for period, trades in sorted(by_month.items()):
        stats = compute_stats(trades)
        stats["period"] = period
        months.append(stats)
        if stats["total_pnl"] > 0:
            profitable += 1

    return {
        "months": months,
        "profitable_months": profitable,
        "total_months": len(months),
    }


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
