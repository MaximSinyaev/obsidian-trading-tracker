"""Tests for analytics calculations."""

import pytest

from trading_tracker.analytics import (
    calculate_partial_close,
    compute_stats,
    strategy_breakdown,
)


class TestPartialClose:
    def test_full_close_profit(self):
        result = calculate_partial_close(
            avg_cost=39.50, total_shares=5, shares_to_close=5, exit_price=43.0
        )
        assert result.pnl == 17.5  # 5*(43-39.50) = 17.5
        assert result.remaining_shares == 0
        assert result.remaining_avg_cost == 39.50

    def test_partial_close_with_commission(self):
        result = calculate_partial_close(
            avg_cost=39.50, total_shares=5, shares_to_close=2,
            exit_price=43.0, commission=10,
        )
        # gross = 2*(43-39.50) = 7.0, net = 7.0 - 10 = -3.0
        assert result.pnl == -3.0
        assert result.remaining_shares == 3
        assert result.remaining_avg_cost == 39.50  # unchanged

    def test_close_at_loss(self):
        result = calculate_partial_close(
            avg_cost=50.0, total_shares=10, shares_to_close=10, exit_price=45.0
        )
        assert result.pnl == -50.0  # 10*(45-50) = -50

    def test_close_more_than_held_raises(self):
        with pytest.raises(ValueError, match="Cannot close"):
            calculate_partial_close(
                avg_cost=39.50, total_shares=3, shares_to_close=5, exit_price=43.0
            )

    def test_breakeven(self):
        result = calculate_partial_close(
            avg_cost=100.0, total_shares=10, shares_to_close=10, exit_price=100.0
        )
        assert result.pnl == 0.0

    def test_zero_commission(self):
        result = calculate_partial_close(
            avg_cost=10.0, total_shares=100, shares_to_close=50, exit_price=12.0
        )
        assert result.pnl == 100.0  # 50*(12-10)
        assert result.remaining_shares == 50


class TestComputeStats:
    def test_empty(self):
        stats = compute_stats([])
        assert stats["total_trades"] == 0
        assert stats["win_rate"] == 0.0
        assert stats["total_pnl"] == 0.0

    def test_single_win(self):
        trades = [{"net_pnl": 100.0, "total_commission": 5.0, "hold_duration_days": 3.0}]
        stats = compute_stats(trades)
        assert stats["total_trades"] == 1
        assert stats["wins"] == 1
        assert stats["losses"] == 0
        assert stats["win_rate"] == 100.0
        assert stats["total_pnl"] == 100.0
        assert stats["avg_hold_days"] == 3.0

    def test_single_loss(self):
        trades = [{"net_pnl": -50.0, "total_commission": 10.0, "hold_duration_days": 1.0}]
        stats = compute_stats(trades)
        assert stats["wins"] == 0
        assert stats["losses"] == 1
        assert stats["win_rate"] == 0.0

    def test_mixed(self):
        trades = [
            {"net_pnl": 200.0, "total_commission": 5.0, "hold_duration_days": 5.0},
            {"net_pnl": -50.0, "total_commission": 5.0, "hold_duration_days": 2.0},
            {"net_pnl": 100.0, "total_commission": 5.0, "hold_duration_days": 3.0},
        ]
        stats = compute_stats(trades)
        assert stats["total_trades"] == 3
        assert stats["wins"] == 2
        assert stats["losses"] == 1
        assert stats["win_rate"] == 66.7
        assert stats["total_pnl"] == 250.0
        assert stats["best_trade"] == 200.0
        assert stats["worst_trade"] == -50.0

    def test_no_hold_duration(self):
        trades = [{"net_pnl": 10.0, "total_commission": 0, "hold_duration_days": None}]
        stats = compute_stats(trades)
        assert stats["avg_hold_days"] == 0.0


class TestStrategyBreakdown:
    def test_single_strategy(self):
        trades = [
            {"net_pnl": 100.0, "total_commission": 5.0, "hold_duration_days": 3.0, "strategy": "momentum"},
            {"net_pnl": -20.0, "total_commission": 5.0, "hold_duration_days": 1.0, "strategy": "momentum"},
        ]
        result = strategy_breakdown(trades)
        assert len(result) == 1
        assert result[0]["strategy"] == "momentum"
        assert result[0]["total_trades"] == 2

    def test_multiple_strategies(self):
        trades = [
            {"net_pnl": 100.0, "total_commission": 5.0, "hold_duration_days": 3.0, "strategy": "momentum"},
            {"net_pnl": 50.0, "total_commission": 5.0, "hold_duration_days": 2.0, "strategy": "value"},
        ]
        result = strategy_breakdown(trades)
        assert len(result) == 2
        strategies = {r["strategy"] for r in result}
        assert strategies == {"momentum", "value"}

    def test_none_strategy_grouped_as_unknown(self):
        trades = [
            {"net_pnl": 10.0, "total_commission": 0, "hold_duration_days": 1.0, "strategy": None},
        ]
        result = strategy_breakdown(trades)
        assert result[0]["strategy"] == "unknown"
