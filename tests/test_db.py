"""Tests for database CRUD operations."""

import json
import sqlite3

import pytest

from trading_tracker import db


@pytest.fixture
def conn():
    """In-memory SQLite database with schema applied."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    schema = db.SCHEMA_DIR / "001_initial.sql"
    connection.executescript(schema.read_text())
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

    def test_zero_price_rejected(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            db.add_trade(conn, "FRO", "BUY", 1, 0)


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
