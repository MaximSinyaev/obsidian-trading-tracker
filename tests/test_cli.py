"""Tests for CLI commands via typer.testing.CliRunner."""

import os

import pytest
from typer.testing import CliRunner

from trading_tracker.cli import app

runner = CliRunner()


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
""")
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path, db_path
    os.chdir(old_cwd)


class TestDbInit:
    def test_init_creates_db(self, tmp_config):
        tmp_path, db_path = tmp_config
        result = runner.invoke(app, ["db", "init"])
        assert result.exit_code == 0
        assert "initialized" in result.output.lower()
        assert db_path.exists()


class TestAdd:
    def test_add_buy(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        assert result.exit_code == 0
        assert "FRO" in result.output
        assert "BUY" in result.output

    def test_add_with_options(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, [
            "add", "FRO", "buy", "3", "39.55",
            "--commission", "10",
            "--strategy", "dividend-capture",
            "--sl", "36",
            "--tp1", "43",
        ])
        assert result.exit_code == 0


class TestPositions:
    def test_no_positions(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["positions"])
        assert result.exit_code == 0
        assert "No open positions" in result.output

    def test_shows_position(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        result = runner.invoke(app, ["positions"])
        assert result.exit_code == 0
        assert "FRO" in result.output


class TestCloseDeprecated:
    def test_close_shows_deprecation(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["close", "FRO", "3", "43.00"])
        assert result.exit_code == 1
        assert "deprecated" in result.output.lower()
        assert "trade add" in result.output


class TestEdit:
    def test_edit_price(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        result = runner.invoke(app, ["edit", "1", "--price", "39.60"])
        assert result.exit_code == 0
        assert "updated" in result.output.lower()

    def test_edit_nonexistent(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["edit", "999", "--price", "10"])
        assert result.exit_code == 1


class TestShow:
    def test_show_trade(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55", "--strategy", "dividend"])
        result = runner.invoke(app, ["show", "1"])
        assert result.exit_code == 0
        assert "FRO" in result.output
        assert "dividend" in result.output

    def test_show_nonexistent(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["show", "999"])
        assert result.exit_code == 1


class TestDelete:
    def test_delete_with_yes(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        result = runner.invoke(app, ["delete", "1", "--yes"])
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()

    def test_delete_nonexistent(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["delete", "999", "--yes"])
        assert result.exit_code == 1


class TestAddTimestamp:
    def test_add_with_timestamp(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, [
            "add", "FRO", "buy", "3", "39.55",
            "--date", "2025-01-15T10:30:00",
        ])
        assert result.exit_code == 0
        # Verify the trade was stored with the right timestamp
        result = runner.invoke(app, ["show", "1"])
        assert "2025-01-15" in result.output

    def test_add_with_instrument(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, [
            "add", "AAPL", "buy", "1", "5.0",
            "--instrument", "option",
        ])
        assert result.exit_code == 0
        result = runner.invoke(app, ["show", "1"])
        assert "option" in result.output

    def test_add_with_leverage(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, [
            "add", "EURUSD", "buy", "1000", "1.08",
            "--leverage", "10",
        ])
        assert result.exit_code == 0
        result = runner.invoke(app, ["show", "1"])
        assert "10" in result.output


class TestCurrency:
    def test_add_with_currency(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, [
            "add", "HSBK", "buy", "10", "350",
            "--currency", "KZT",
        ])
        assert result.exit_code == 0
        result = runner.invoke(app, ["show", "1"])
        assert "KZT" in result.output

    def test_default_currency_usd(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        result = runner.invoke(app, ["show", "1"])
        assert "USD" in result.output


class TestAutoCloseCli:
    def test_add_sell_shows_auto_close(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        result = runner.invoke(app, ["add", "FRO", "sell", "3", "43.00"])
        assert result.exit_code == 0
        assert "Closed" in result.output
        assert "P&L" in result.output

    def test_stats_work_after_add_only(self, tmp_config):
        """Stats should work using only add_trade (no close needed)."""
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        runner.invoke(app, ["add", "FRO", "sell", "3", "43.00"])
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 0
        assert "Win Rate" in result.output

    def test_add_sell_partial_shows_remaining(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "AAPL", "buy", "10", "150"])
        result = runner.invoke(app, ["add", "AAPL", "sell", "4", "160"])
        assert result.exit_code == 0
        assert "Remaining" in result.output


class TestShortCli:
    def test_short_position_display(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "TSLA", "sell", "5", "200"])
        result = runner.invoke(app, ["positions", "--no-live"])
        assert result.exit_code == 0
        assert "TSLA" in result.output
        assert "SHORT" in result.output

    def test_close_short_via_add_buy(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "TSLA", "sell", "5", "200"])
        result = runner.invoke(app, ["add", "TSLA", "buy", "5", "180"])
        assert result.exit_code == 0
        assert "SHORT" in result.output
        assert "Closed" in result.output

    def test_close_long_via_add_sell(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        result = runner.invoke(app, ["add", "FRO", "sell", "3", "43"])
        assert result.exit_code == 0
        assert "LONG" in result.output

    def test_option_expiry_at_zero(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "AAPL240119C190", "buy", "1", "5.0"])
        result = runner.invoke(app, ["add", "AAPL240119C190", "sell", "1", "0"])
        assert result.exit_code == 0
        assert "Closed" in result.output


class TestPositionsNoLive:
    def test_positions_no_live(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        result = runner.invoke(app, ["positions", "--no-live"])
        assert result.exit_code == 0
        assert "FRO" in result.output


class TestHistory:
    def test_empty_history(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["history"])
        assert result.exit_code == 0

    def test_history_with_trades(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        result = runner.invoke(app, ["history"])
        assert result.exit_code == 0
        assert "FRO" in result.output


class TestStats:
    def test_no_closed_trades(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 0
        assert "No closed trades" in result.output

    def test_stats_with_closed(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        runner.invoke(app, ["add", "FRO", "sell", "3", "43.00"])
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 0
        assert "Win Rate" in result.output


class TestValidate:
    def test_clean_db(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        result = runner.invoke(app, ["db", "validate"])
        assert result.exit_code == 0
        assert "consistent" in result.output.lower()
