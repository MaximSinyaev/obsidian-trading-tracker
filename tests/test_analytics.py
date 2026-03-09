"""Tests for analytics calculations."""

import pytest

from trading_tracker.analytics import (
    calculate_partial_close,
    compute_holding_analysis,
    compute_max_drawdown,
    compute_stats,
    compute_streaks,
    instrument_breakdown,
    monthly_breakdown,
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


class TestProfitFactor:
    def test_mixed_trades(self):
        trades = [
            {"net_pnl": 120.0, "total_commission": 0, "hold_duration_days": 1.0},
            {"net_pnl": -50.0, "total_commission": 0, "hold_duration_days": 1.0},
        ]
        stats = compute_stats(trades)
        # profit_factor = 120 / 50 = 2.4
        assert stats["profit_factor"] == 2.4

    def test_all_wins_no_losses(self):
        trades = [
            {"net_pnl": 100.0, "total_commission": 0, "hold_duration_days": 1.0},
            {"net_pnl": 50.0, "total_commission": 0, "hold_duration_days": 1.0},
        ]
        stats = compute_stats(trades)
        assert stats["profit_factor"] is None  # infinite (no losses)

    def test_all_losses(self):
        trades = [
            {"net_pnl": -30.0, "total_commission": 0, "hold_duration_days": 1.0},
            {"net_pnl": -20.0, "total_commission": 0, "hold_duration_days": 1.0},
        ]
        stats = compute_stats(trades)
        assert stats["profit_factor"] == 0.0

    def test_empty(self):
        stats = compute_stats([])
        assert stats["profit_factor"] is None


class TestExpectancy:
    def test_positive_expectancy(self):
        trades = [
            {"net_pnl": 100.0, "total_commission": 0, "hold_duration_days": 1.0},
            {"net_pnl": 100.0, "total_commission": 0, "hold_duration_days": 1.0},
            {"net_pnl": -50.0, "total_commission": 0, "hold_duration_days": 1.0},
        ]
        stats = compute_stats(trades)
        # win_rate=66.7%, avg_win=100, avg_loss=-50
        # expectancy = 0.667 * 100 + 0.333 * (-50) = 66.7 - 16.65 = 50.05
        assert stats["expectancy"] > 0
        assert stats["avg_win"] == 100.0
        assert stats["avg_loss"] == -50.0

    def test_negative_expectancy(self):
        trades = [
            {"net_pnl": 10.0, "total_commission": 0, "hold_duration_days": 1.0},
            {"net_pnl": -50.0, "total_commission": 0, "hold_duration_days": 1.0},
            {"net_pnl": -30.0, "total_commission": 0, "hold_duration_days": 1.0},
        ]
        stats = compute_stats(trades)
        assert stats["expectancy"] < 0

    def test_single_win(self):
        trades = [{"net_pnl": 100.0, "total_commission": 0, "hold_duration_days": 1.0}]
        stats = compute_stats(trades)
        # 100% win rate, avg_win=100, no losses
        assert stats["expectancy"] == 100.0

    def test_empty(self):
        stats = compute_stats([])
        assert stats["expectancy"] == 0.0


class TestStreaks:
    def test_mixed_sequence(self):
        # W-W-L-W-W-W-L-L
        trades = [
            {"net_pnl": 10.0, "closed_at": "2026-01-01"},  # W
            {"net_pnl": 20.0, "closed_at": "2026-01-02"},  # W
            {"net_pnl": -5.0, "closed_at": "2026-01-03"},  # L
            {"net_pnl": 15.0, "closed_at": "2026-01-04"},  # W
            {"net_pnl": 25.0, "closed_at": "2026-01-05"},  # W
            {"net_pnl": 30.0, "closed_at": "2026-01-06"},  # W
            {"net_pnl": -10.0, "closed_at": "2026-01-07"}, # L
            {"net_pnl": -8.0, "closed_at": "2026-01-08"},  # L
        ]
        result = compute_streaks(trades)
        assert result["longest_win_streak"] == 3
        assert result["longest_loss_streak"] == 2
        assert result["current_streak_type"] == "loss"
        assert result["current_streak_count"] == 2

    def test_all_wins(self):
        trades = [
            {"net_pnl": 10.0, "closed_at": "2026-01-01"},
            {"net_pnl": 20.0, "closed_at": "2026-01-02"},
            {"net_pnl": 30.0, "closed_at": "2026-01-03"},
        ]
        result = compute_streaks(trades)
        assert result["longest_win_streak"] == 3
        assert result["longest_loss_streak"] == 0
        assert result["current_streak_type"] == "win"
        assert result["current_streak_count"] == 3

    def test_empty(self):
        result = compute_streaks([])
        assert result["longest_win_streak"] == 0
        assert result["longest_loss_streak"] == 0
        assert result["current_streak_type"] is None

    def test_single_trade(self):
        result = compute_streaks([{"net_pnl": -5.0, "closed_at": "2026-01-01"}])
        assert result["longest_loss_streak"] == 1
        assert result["current_streak_type"] == "loss"


class TestMaxDrawdown:
    def test_mixed_sequence(self):
        # W, L, L, L, W, L
        trades = [
            {"net_pnl": 50.0, "ticker": "A", "closed_at": "2026-01-01"},
            {"net_pnl": -10.0, "ticker": "B", "closed_at": "2026-01-02"},
            {"net_pnl": -20.0, "ticker": "C", "closed_at": "2026-01-03"},
            {"net_pnl": -15.0, "ticker": "D", "closed_at": "2026-01-04"},
            {"net_pnl": 30.0, "ticker": "E", "closed_at": "2026-01-05"},
            {"net_pnl": -5.0, "ticker": "F", "closed_at": "2026-01-06"},
        ]
        result = compute_max_drawdown(trades)
        assert result["max_drawdown"] == -45.0  # -10 + -20 + -15
        assert result["max_drawdown_count"] == 3
        assert result["max_drawdown_tickers"] == ["B", "C", "D"]

    def test_all_wins(self):
        trades = [
            {"net_pnl": 10.0, "ticker": "A", "closed_at": "2026-01-01"},
            {"net_pnl": 20.0, "ticker": "B", "closed_at": "2026-01-02"},
        ]
        result = compute_max_drawdown(trades)
        assert result["max_drawdown"] == 0.0
        assert result["max_drawdown_count"] == 0

    def test_single_loss(self):
        trades = [{"net_pnl": -25.0, "ticker": "X", "closed_at": "2026-01-01"}]
        result = compute_max_drawdown(trades)
        assert result["max_drawdown"] == -25.0
        assert result["max_drawdown_count"] == 1
        assert result["max_drawdown_tickers"] == ["X"]

    def test_empty(self):
        result = compute_max_drawdown([])
        assert result["max_drawdown"] == 0.0


class TestHoldingAnalysis:
    def test_winners_vs_losers(self):
        trades = [
            {"net_pnl": 100.0, "hold_duration_days": 10.0},
            {"net_pnl": 50.0, "hold_duration_days": 14.0},
            {"net_pnl": -20.0, "hold_duration_days": 3.0},
            {"net_pnl": -10.0, "hold_duration_days": 5.0},
        ]
        result = compute_holding_analysis(trades)
        assert result["avg_hold_winners"] == 12.0  # (10+14)/2
        assert result["avg_hold_losers"] == 4.0    # (3+5)/2

    def test_no_hold_data(self):
        trades = [
            {"net_pnl": 100.0, "hold_duration_days": None},
        ]
        result = compute_holding_analysis(trades)
        assert result["avg_hold_winners"] == 0.0
        assert result["avg_hold_losers"] == 0.0

    def test_only_winners(self):
        trades = [
            {"net_pnl": 100.0, "hold_duration_days": 7.0},
        ]
        result = compute_holding_analysis(trades)
        assert result["avg_hold_winners"] == 7.0
        assert result["avg_hold_losers"] == 0.0


class TestInstrumentBreakdown:
    def test_stock_and_option(self):
        trades = [
            {"net_pnl": 100.0, "total_commission": 5.0, "hold_duration_days": 3.0, "instrument": "stock"},
            {"net_pnl": -20.0, "total_commission": 5.0, "hold_duration_days": 1.0, "instrument": "option"},
            {"net_pnl": 50.0, "total_commission": 5.0, "hold_duration_days": 2.0, "instrument": "stock"},
        ]
        result = instrument_breakdown(trades)
        assert len(result) == 2
        instruments = {r["instrument"] for r in result}
        assert instruments == {"stock", "option"}
        stock = next(r for r in result if r["instrument"] == "stock")
        assert stock["total_trades"] == 2
        assert stock["total_pnl"] == 150.0

    def test_single_instrument(self):
        trades = [
            {"net_pnl": 10.0, "total_commission": 0, "hold_duration_days": 1.0, "instrument": "stock"},
        ]
        result = instrument_breakdown(trades)
        assert len(result) == 1

    def test_default_instrument(self):
        trades = [{"net_pnl": 10.0, "total_commission": 0, "hold_duration_days": 1.0}]
        result = instrument_breakdown(trades)
        assert result[0]["instrument"] == "stock"


class TestMonthlyBreakdown:
    def test_three_months(self):
        trades = [
            {"net_pnl": 10.0, "total_commission": 0, "hold_duration_days": 1.0, "closed_at": "2026-01-15T10:00:00"},
            {"net_pnl": 20.0, "total_commission": 0, "hold_duration_days": 1.0, "closed_at": "2026-01-20T10:00:00"},
            {"net_pnl": -5.0, "total_commission": 0, "hold_duration_days": 1.0, "closed_at": "2026-02-10T10:00:00"},
            {"net_pnl": 30.0, "total_commission": 0, "hold_duration_days": 1.0, "closed_at": "2026-03-05T10:00:00"},
        ]
        result = monthly_breakdown(trades)
        assert result["total_months"] == 3
        assert result["profitable_months"] == 2  # Jan (+30) and Mar (+30), Feb (-5)
        assert len(result["months"]) == 3
        assert result["months"][0]["period"] == "2026-01"
        assert result["months"][0]["total_pnl"] == 30.0

    def test_single_month(self):
        trades = [
            {"net_pnl": 10.0, "total_commission": 0, "hold_duration_days": 1.0, "closed_at": "2026-01-15T10:00:00"},
        ]
        result = monthly_breakdown(trades)
        assert result["total_months"] == 1
        assert result["profitable_months"] == 1

    def test_empty(self):
        result = monthly_breakdown([])
        assert result["total_months"] == 0
        assert result["profitable_months"] == 0
