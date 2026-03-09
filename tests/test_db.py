"""Tests for database CRUD operations."""

import json
import sqlite3

import pytest

from trading_tracker import db


@pytest.fixture
def conn():
    """In-memory SQLite database with all migrations applied."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in sorted(db.SCHEMA_DIR.glob("*.sql")):
        connection.executescript(migration.read_text())
    yield connection
    connection.close()


class TestAddTrade:
    def test_basic_buy(self, conn):
        tid = db.add_trade(conn, "FRO", "buy", 3, 39.55)
        assert tid == 1
        trade = db.get_trade(conn, tid)
        assert trade["ticker"] == "FRO"
        assert trade["action"] == "BUY"
        assert trade["shares"] == 3
        assert trade["price"] == 39.55
        assert trade["commission"] == 0

    def test_buy_with_options(self, conn):
        tid = db.add_trade(
            conn, "AAPL", "buy", 10, 150.00,
            commission=9.99,
            strategy="momentum",
            stop_loss=140.0,
            target_1=160.0,
            tags=["tech", "large-cap"],
            notes="Testing entry",
        )
        trade = db.get_trade(conn, tid)
        assert trade["strategy"] == "momentum"
        assert trade["stop_loss"] == 140.0
        assert json.loads(trade["tags"]) == ["tech", "large-cap"]

    def test_ticker_uppercased(self, conn):
        tid = db.add_trade(conn, "fro", "buy", 1, 10.0)
        trade = db.get_trade(conn, tid)
        assert trade["ticker"] == "FRO"

    def test_action_uppercased(self, conn):
        tid = db.add_trade(conn, "FRO", "sell", 1, 10.0)
        trade = db.get_trade(conn, tid)
        assert trade["action"] == "SELL"

    def test_invalid_action_rejected(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            db.add_trade(conn, "FRO", "HOLD", 1, 10.0)

    def test_negative_shares_rejected(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            db.add_trade(conn, "FRO", "BUY", -1, 10.0)

    def test_zero_price_allowed(self, conn):
        """Price=0 is allowed for option expiration."""
        tid = db.add_trade(conn, "AAPL240119C190", "sell", 1, 0)
        trade = db.get_trade(conn, tid)
        assert trade["price"] == 0

    def test_negative_price_rejected(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            db.add_trade(conn, "FRO", "BUY", 1, -1)

    def test_custom_timestamp(self, conn):
        tid = db.add_trade(conn, "FRO", "buy", 3, 39.55, timestamp="2025-01-15T10:30:00")
        trade = db.get_trade(conn, tid)
        assert trade["timestamp"] == "2025-01-15T10:30:00"

    def test_instrument_and_leverage(self, conn):
        tid = db.add_trade(
            conn, "AAPL", "buy", 10, 150.0,
            instrument="option", leverage=5.0,
        )
        trade = db.get_trade(conn, tid)
        assert trade["instrument"] == "option"
        assert trade["leverage"] == 5.0

    def test_default_instrument_and_leverage(self, conn):
        tid = db.add_trade(conn, "FRO", "buy", 1, 10.0)
        trade = db.get_trade(conn, tid)
        assert trade["instrument"] == "stock"
        assert trade["leverage"] == 1.0


class TestEditTrade:
    def test_edit_price(self, conn):
        tid = db.add_trade(conn, "FRO", "buy", 3, 39.55)
        assert db.edit_trade(conn, tid, price=39.60)
        trade = db.get_trade(conn, tid)
        assert trade["price"] == 39.60

    def test_edit_multiple_fields(self, conn):
        tid = db.add_trade(conn, "FRO", "buy", 3, 39.55)
        db.edit_trade(conn, tid, shares=5, notes="Fixed typo")
        trade = db.get_trade(conn, tid)
        assert trade["shares"] == 5
        assert trade["notes"] == "Fixed typo"

    def test_edit_nonexistent_returns_false(self, conn):
        assert not db.edit_trade(conn, 999, price=10.0)

    def test_edit_no_fields_returns_false(self, conn):
        tid = db.add_trade(conn, "FRO", "buy", 3, 39.55)
        assert not db.edit_trade(conn, tid)

    def test_edit_tags(self, conn):
        tid = db.add_trade(conn, "FRO", "buy", 3, 39.55)
        db.edit_trade(conn, tid, tags=["value", "shipping"])
        trade = db.get_trade(conn, tid)
        assert json.loads(trade["tags"]) == ["value", "shipping"]


class TestPositions:
    def test_open_position(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        positions = db.get_positions(conn)
        assert len(positions) == 1
        assert positions[0]["ticker"] == "FRO"
        assert positions[0]["net_shares"] == 3
        assert positions[0]["avg_cost"] == 39.55

    def test_multiple_buys_avg_cost(self, conn):
        db.add_trade(conn, "FRO", "buy", 2, 40.0)
        db.add_trade(conn, "FRO", "buy", 3, 38.0)
        pos = db.get_position(conn, "FRO")
        assert pos["net_shares"] == 5
        # avg cost = (2*40 + 3*38) / 5 = (80 + 114) / 5 = 38.8
        assert abs(pos["avg_cost"] - 38.8) < 0.01

    def test_no_position_returns_none(self, conn):
        assert db.get_position(conn, "NOPE") is None

    def test_fully_closed_not_in_positions(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 40.0)
        db.add_trade(conn, "FRO", "sell", 3, 43.0)
        positions = db.get_positions(conn)
        assert len(positions) == 0


class TestClosePosition:
    def test_full_close(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55, commission=10)
        result = db.close_position(conn, "FRO", 3, 43.00, commission=10)
        assert result["ticker"] == "FRO"
        assert result["shares"] == 3
        assert result["avg_entry"] == 39.55
        assert result["exit_price"] == 43.0
        # gross_pnl = 3 * 43.00 - 3 * 39.55 = 129.00 - 118.65 = 10.35
        assert result["gross_pnl"] == 10.35
        # net_pnl = 10.35 - 10 (exit commission) = 0.35
        assert result["net_pnl"] == 0.35
        assert result["remaining_shares"] == 0

    def test_partial_close(self, conn):
        db.add_trade(conn, "FRO", "buy", 5, 39.50)
        result = db.close_position(conn, "FRO", 2, 43.00, commission=10)
        assert result["remaining_shares"] == 3
        assert result["remaining_avg_cost"] == 39.50
        # gross_pnl = 2*43 - 2*39.50 = 86 - 79 = 7.0
        assert result["gross_pnl"] == 7.0
        # net_pnl = 7.0 - 10 = -3.0
        assert result["net_pnl"] == -3.0
        # Position still open
        pos = db.get_position(conn, "FRO")
        assert pos is not None
        assert pos["net_shares"] == 3

    def test_close_nonexistent_raises(self, conn):
        with pytest.raises(ValueError, match="No open position"):
            db.close_position(conn, "NOPE", 1, 10.0)

    def test_close_more_than_held_raises(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        with pytest.raises(ValueError, match="Cannot close"):
            db.close_position(conn, "FRO", 5, 43.0)

    def test_close_creates_sell_trade(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        db.close_position(conn, "FRO", 3, 43.0)
        history = db.get_history(conn)
        sells = [t for t in history if t["action"] == "SELL"]
        assert len(sells) == 1
        assert sells[0]["ticker"] == "FRO"
        assert sells[0]["shares"] == 3

    def test_close_creates_closed_trade_record(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        db.close_position(conn, "FRO", 3, 43.0, what_worked="Good timing", lesson="Trust the plan")
        closed = db.get_closed_trades(conn)
        assert len(closed) == 1
        assert closed[0]["what_worked"] == "Good timing"
        assert closed[0]["lesson"] == "Trust the plan"


class TestShortPositions:
    def test_short_position_shows_negative_shares(self, conn):
        """SELL first creates a short position with negative net_shares."""
        db.add_trade(conn, "TSLA", "sell", 5, 200.0)
        pos = db.get_position(conn, "TSLA")
        assert pos is not None
        assert pos["net_shares"] == -5
        assert pos["avg_cost"] == 200.0

    def test_close_short_position(self, conn):
        """Close short: SELL to open, BUY to close. P&L = entry - exit."""
        db.add_trade(conn, "TSLA", "sell", 5, 200.0)
        result = db.close_position(conn, "TSLA", 5, 180.0)
        assert result["direction"] == "short"
        assert result["avg_entry"] == 200.0
        assert result["exit_price"] == 180.0
        # Short profit: (200 - 180) * 5 = 100
        assert result["gross_pnl"] == 100.0
        assert result["net_pnl"] == 100.0
        assert result["remaining_shares"] == 0

    def test_close_short_creates_buy_trade(self, conn):
        """Closing a short creates a BUY trade (cover)."""
        db.add_trade(conn, "TSLA", "sell", 5, 200.0)
        db.close_position(conn, "TSLA", 5, 180.0)
        history = db.get_history(conn)
        buys = [t for t in history if t["action"] == "BUY"]
        assert len(buys) == 1
        assert buys[0]["ticker"] == "TSLA"
        assert buys[0]["shares"] == 5

    def test_short_loss(self, conn):
        """Short loses money when price goes up."""
        db.add_trade(conn, "TSLA", "sell", 5, 200.0)
        result = db.close_position(conn, "TSLA", 5, 220.0)
        # Short loss: (200 - 220) * 5 = -100
        assert result["gross_pnl"] == -100.0
        assert result["net_pnl"] == -100.0

    def test_partial_close_short(self, conn):
        db.add_trade(conn, "TSLA", "sell", 10, 200.0)
        result = db.close_position(conn, "TSLA", 4, 190.0)
        assert result["remaining_shares"] == 6
        # Profit: (200 - 190) * 4 = 40
        assert result["gross_pnl"] == 40.0
        pos = db.get_position(conn, "TSLA")
        assert pos is not None
        assert pos["net_shares"] == -6

    def test_close_short_with_commission(self, conn):
        db.add_trade(conn, "TSLA", "sell", 5, 200.0, commission=5)
        result = db.close_position(conn, "TSLA", 5, 180.0, commission=5)
        assert result["gross_pnl"] == 100.0
        assert result["net_pnl"] == 95.0  # 100 - 5 (exit commission only)

    def test_short_closed_trade_record(self, conn):
        db.add_trade(conn, "TSLA", "sell", 5, 200.0)
        db.close_position(conn, "TSLA", 5, 180.0, what_worked="Good short")
        closed = db.get_closed_trades(conn)
        assert len(closed) == 1
        assert closed[0]["direction"] == "short"
        assert closed[0]["what_worked"] == "Good short"

    def test_option_expiry_at_zero(self, conn):
        """Option expires worthless — close at price=0."""
        db.add_trade(conn, "AAPL240119C190", "buy", 1, 5.0)
        result = db.close_position(conn, "AAPL240119C190", 1, 0)
        assert result["exit_price"] == 0
        assert result["gross_pnl"] == -5.0  # Lost full premium


class TestAutoClose:
    """add_trade should auto-create closed_trades when it reduces a position."""

    def test_sell_after_buy_auto_closes(self, conn):
        """BUY then SELL via add_trade — closed_trades should be created."""
        db.add_trade(conn, "FRO", "buy", 3, 39.55, timestamp="2026-01-01T10:00:00")
        db.add_trade(conn, "FRO", "sell", 3, 43.00, timestamp="2026-01-05T10:00:00")

        # Position should be fully closed
        assert db.get_position(conn, "FRO") is None

        # closed_trades should exist
        closed = db.get_closed_trades(conn)
        assert len(closed) == 1
        assert closed[0]["ticker"] == "FRO"
        assert closed[0]["direction"] == "long"
        assert closed[0]["shares"] == 3
        assert closed[0]["avg_entry_price"] == 39.55
        assert closed[0]["avg_exit_price"] == 43.00
        # Gross P&L = 3*(43-39.55) = 10.35
        assert closed[0]["gross_pnl"] == 10.35

    def test_partial_sell_auto_closes(self, conn):
        db.add_trade(conn, "AAPL", "buy", 10, 150.0)
        db.add_trade(conn, "AAPL", "sell", 4, 160.0)

        # Position partially open
        pos = db.get_position(conn, "AAPL")
        assert pos is not None
        assert pos["net_shares"] == 6

        # closed_trades for the 4 shares
        closed = db.get_closed_trades(conn)
        assert len(closed) == 1
        assert closed[0]["shares"] == 4
        # Gross = 4*(160-150) = 40
        assert closed[0]["gross_pnl"] == 40.0

    def test_short_buy_auto_closes(self, conn):
        """SELL to open short, then BUY via add_trade should auto-close."""
        db.add_trade(conn, "TSLA", "sell", 5, 200.0)
        db.add_trade(conn, "TSLA", "buy", 5, 180.0)

        assert db.get_position(conn, "TSLA") is None

        closed = db.get_closed_trades(conn)
        assert len(closed) == 1
        assert closed[0]["direction"] == "short"
        # Short profit: (200-180)*5 = 100
        assert closed[0]["gross_pnl"] == 100.0

    def test_additional_buy_no_auto_close(self, conn):
        """Adding to a position should NOT create closed_trades."""
        db.add_trade(conn, "AAPL", "buy", 5, 150.0)
        db.add_trade(conn, "AAPL", "buy", 3, 155.0)

        closed = db.get_closed_trades(conn)
        assert len(closed) == 0

        pos = db.get_position(conn, "AAPL")
        assert pos["net_shares"] == 8

    def test_auto_close_with_commission(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55, commission=1.83)
        db.add_trade(conn, "FRO", "sell", 3, 35.946, commission=1.78)

        closed = db.get_closed_trades(conn)
        assert len(closed) == 1
        # Gross = 3*(35.946 - 39.55) = -10.812 → rounded to -10.81
        assert closed[0]["gross_pnl"] == pytest.approx(-10.81, abs=0.01)
        # Net = -10.81 - 1.78 = -12.59
        assert closed[0]["net_pnl"] == pytest.approx(-12.59, abs=0.01)

    def test_stats_work_after_add_trade(self, conn):
        """trade stats should work without ever calling close_position."""
        from trading_tracker import analytics

        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        db.add_trade(conn, "FRO", "sell", 3, 43.00)
        db.add_trade(conn, "RBLX", "buy", 1, 57.0)
        db.add_trade(conn, "RBLX", "sell", 1, 102.88)

        closed = db.get_closed_trades(conn)
        assert len(closed) == 2

        stats = analytics.compute_stats(closed)
        assert stats["total_trades"] == 2
        assert stats["wins"] == 2
        assert stats["total_pnl"] > 0

    def test_auto_close_preserves_strategy(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55, strategy="momentum")
        db.add_trade(conn, "FRO", "sell", 3, 43.00, strategy="momentum")

        closed = db.get_closed_trades(conn)
        assert closed[0]["strategy"] == "momentum"


class TestDeleteTrade:
    def test_delete_existing(self, conn):
        tid = db.add_trade(conn, "FRO", "buy", 3, 39.55)
        assert db.delete_trade(conn, tid)
        assert db.get_trade(conn, tid) is None

    def test_delete_nonexistent_returns_false(self, conn):
        assert not db.delete_trade(conn, 999)

    def test_delete_removes_from_positions(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        db.delete_trade(conn, 1)
        assert db.get_positions(conn) == []


class TestHistory:
    def test_basic_history(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        db.add_trade(conn, "AAPL", "buy", 10, 150.0)
        history = db.get_history(conn)
        assert len(history) == 2

    def test_filter_by_ticker(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        db.add_trade(conn, "AAPL", "buy", 10, 150.0)
        history = db.get_history(conn, ticker="FRO")
        assert len(history) == 1
        assert history[0]["ticker"] == "FRO"

    def test_limit(self, conn):
        for i in range(10):
            db.add_trade(conn, "FRO", "buy", 1, 10.0 + i)
        history = db.get_history(conn, limit=5)
        assert len(history) == 5

    def test_closed_only(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        db.close_position(conn, "FRO", 3, 43.0)
        history = db.get_history(conn, closed_only=True)
        assert len(history) == 1
        assert history[0]["ticker"] == "FRO"


class TestValidate:
    def test_clean_db(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        issues = db.validate_db(conn)
        assert issues == []

    def test_empty_db(self, conn):
        issues = db.validate_db(conn)
        assert issues == []
