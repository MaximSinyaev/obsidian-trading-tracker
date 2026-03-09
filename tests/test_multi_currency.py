"""Tests for multi-currency P&L display."""

import os
import sqlite3

import pytest
from typer.testing import CliRunner

from trading_tracker import analytics, db
from trading_tracker.cli import app
from trading_tracker.models import currency_symbol

runner = CliRunner()


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


@pytest.fixture
def tmp_config(tmp_path):
    """Create a temp config pointing to a temp database."""
    db_path = tmp_path / "test_trades.db"
    config_path = tmp_path / ".traderc.toml"
    config_path.write_text(f"""
[database]
path = "{db_path}"

[defaults]
commission = 0.0
timezone = "Asia/Almaty"
asset_type = "stock"
source = "manual"

[obsidian]
vault_path = ""
trading_folder = "Trading"

[fx]
base_currency = "USD"
currencies = ["USD", "KZT"]
""")
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path, db_path
    os.chdir(old_cwd)


# ── currency_symbol tests ───────────────────────────────────────────────────


class TestCurrencySymbol:
    def test_usd(self):
        assert currency_symbol("USD") == "$"

    def test_kzt(self):
        assert currency_symbol("KZT") == "₸"

    def test_eur(self):
        assert currency_symbol("EUR") == "€"

    def test_rub(self):
        assert currency_symbol("RUB") == "₽"

    def test_gbp(self):
        assert currency_symbol("GBP") == "£"

    def test_case_insensitive(self):
        assert currency_symbol("usd") == "$"
        assert currency_symbol("kzt") == "₸"

    def test_unknown_returns_code(self):
        assert currency_symbol("XYZ") == "XYZ"
        assert currency_symbol("abc") == "ABC"


# ── position includes currency ───────────────────────────────────────────────


class TestPositionIncludesCurrency:
    def test_kzt_trade_position_currency(self, conn):
        """KZT trade → position.currency == 'KZT'."""
        db.add_trade(conn, "HSBK", "buy", 10, 350, currency="KZT")
        pos = db.get_position(conn, "HSBK")
        assert pos is not None
        assert pos["currency"] == "KZT"

    def test_default_usd_position_currency(self, conn):
        """Default trade → position.currency == 'USD'."""
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        pos = db.get_position(conn, "FRO")
        assert pos is not None
        assert pos["currency"] == "USD"


# ── auto-close writes currency ───────────────────────────────────────────────


class TestAutoCloseWritesCurrency:
    def test_kzt_buy_sell_closed_trade_currency(self, conn):
        """KZT buy+sell → closed_trade.currency == 'KZT'."""
        db.add_trade(conn, "HSBK", "buy", 10, 350, currency="KZT")
        db.add_trade(conn, "HSBK", "sell", 10, 370, currency="KZT")
        closed = db.get_closed_trades(conn)
        assert len(closed) == 1
        assert closed[0]["currency"] == "KZT"

    def test_usd_closed_trade_currency(self, conn):
        """USD buy+sell → closed_trade.currency == 'USD'."""
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        db.add_trade(conn, "FRO", "sell", 3, 43.00)
        closed = db.get_closed_trades(conn)
        assert len(closed) == 1
        assert closed[0]["currency"] == "USD"

    def test_close_position_passes_currency(self, conn):
        """close_position should pass currency from the position."""
        db.add_trade(conn, "HSBK", "buy", 10, 350, currency="KZT")
        result = db.close_position(conn, "HSBK", 10, 370)
        assert result["ticker"] == "HSBK"
        closed = db.get_closed_trades(conn)
        assert closed[0]["currency"] == "KZT"


# ── compute_stats_by_currency ────────────────────────────────────────────────


class TestComputeStatsByCurrency:
    def test_single_currency(self):
        trades = [
            {"net_pnl": 100.0, "total_commission": 5.0, "hold_duration_days": 3.0, "currency": "USD"},
            {"net_pnl": -20.0, "total_commission": 5.0, "hold_duration_days": 1.0, "currency": "USD"},
        ]
        result = analytics.compute_stats_by_currency(trades, "USD")
        assert result["currencies"] == ["USD"]
        assert result["by_currency"]["USD"]["total_pnl"] == 80.0
        assert result["total_pnl_base"] == 80.0

    def test_mixed_usd_kzt_grouping(self):
        """Mixed USD+KZT trades grouped correctly."""
        mock_rates = {("KZT", "USD"): 1 / 450, ("USD", "KZT"): 450.0, ("USD", "USD"): 1.0, ("KZT", "KZT"): 1.0}
        trades = [
            {"net_pnl": 20.37, "total_commission": 5.0, "hold_duration_days": 3.0, "currency": "USD"},
            {"net_pnl": 9092.0, "total_commission": 1.0, "hold_duration_days": 10.0, "currency": "KZT"},
        ]
        result = analytics.compute_stats_by_currency(trades, "USD", rates=mock_rates)
        assert result["currencies"] == ["KZT", "USD"]
        assert result["by_currency"]["USD"]["total_pnl"] == 20.37
        assert result["by_currency"]["KZT"]["total_pnl"] == 9092.0
        # KZT P&L in USD = 9092 / 450 ≈ 20.20
        assert result["by_currency"]["KZT"]["pnl_in_base"] == pytest.approx(20.20, abs=0.1)
        # Total ≈ 20.37 + 20.20 = 40.57
        assert result["total_pnl_base"] == pytest.approx(40.57, abs=0.2)

    def test_no_trades(self):
        result = analytics.compute_stats_by_currency([], "USD")
        assert result["currencies"] == []
        assert result["total_pnl_base"] == 0.0

    def test_missing_currency_defaults_to_usd(self):
        """Trades without currency field default to USD."""
        trades = [{"net_pnl": 50.0, "total_commission": 0, "hold_duration_days": 1.0}]
        result = analytics.compute_stats_by_currency(trades, "USD")
        assert "USD" in result["by_currency"]


# ── CLI integration ──────────────────────────────────────────────────────────


class TestCLIMultiCurrency:
    def test_add_kzt_shows_tenge(self, tmp_config):
        """Adding a KZT trade shows ₸ in output instead of $."""
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, [
            "add", "HSBK", "buy", "10", "350",
            "--currency", "KZT",
        ])
        assert result.exit_code == 0
        assert "₸" in result.output
        assert "350" in result.output

    def test_add_usd_shows_dollar(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        assert result.exit_code == 0
        assert "$" in result.output

    def test_auto_close_kzt_shows_tenge(self, tmp_config):
        """Auto-close with KZT trades shows ₸ symbols."""
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "HSBK", "buy", "10", "350", "--currency", "KZT"])
        result = runner.invoke(app, ["add", "HSBK", "sell", "10", "370", "--currency", "KZT"])
        assert result.exit_code == 0
        assert "₸" in result.output
        assert "Closed" in result.output

    def test_history_closed_kzt_shows_tenge(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "HSBK", "buy", "10", "350", "--currency", "KZT"])
        runner.invoke(app, ["add", "HSBK", "sell", "10", "370", "--currency", "KZT"])
        result = runner.invoke(app, ["history", "--closed"])
        assert result.exit_code == 0
        assert "₸" in result.output

    def test_positions_ccy_column_with_multi_currency(self, tmp_config):
        """When positions have multiple currencies, CCY column should appear."""
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "HSBK", "buy", "10", "350", "--currency", "KZT"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        result = runner.invoke(app, ["positions", "--no-live"])
        assert result.exit_code == 0
        assert "CCY" in result.output
        assert "KZT" in result.output
        assert "USD" in result.output

    def test_positions_no_ccy_column_single_currency(self, tmp_config):
        """With single currency, no CCY column."""
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        result = runner.invoke(app, ["positions", "--no-live"])
        assert result.exit_code == 0
        assert "CCY" not in result.output
