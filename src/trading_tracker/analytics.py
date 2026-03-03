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


def enrich_positions_with_prices(
    positions: list[dict[str, Any]],
    *,
    api_enabled: bool = False,
) -> list[dict[str, Any]]:
    """Add unrealized P&L to positions if live prices available.

    For MVP: returns positions as-is with unrealized_pnl = None.
    """
    # TODO: Phase 3 — integrate yfinance or another price API
    #   for pos in positions:
    #       pos["current_price"] = fetch_live_price(pos["ticker"])
    #       pos["unrealized_pnl"] = (pos["current_price"] - pos["avg_cost"]) * pos["net_shares"]
    if not api_enabled:
        for pos in positions:
            pos["current_price"] = None
            pos["unrealized_pnl"] = None
        return positions
    raise NotImplementedError("Live price API not yet implemented")


# TODO: Phase 3 — multi-currency support
# def convert_to_base_currency(amount, from_currency, to_currency="USD"):
#     if from_currency == to_currency:
#         return amount
#     raise NotImplementedError("Multi-currency not yet supported")


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
