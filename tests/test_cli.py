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


class TestClose:
    def test_close_position(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        runner.invoke(app, ["add", "FRO", "buy", "3", "39.55"])
        result = runner.invoke(app, [
            "close", "FRO", "3", "43.00",
            "--commission", "10",
            "--what-worked", "Good timing",
        ])
        assert result.exit_code == 0
        assert "Closed" in result.output

    def test_close_nonexistent(self, tmp_config):
        runner.invoke(app, ["db", "init"])
        result = runner.invoke(app, ["close", "NOPE", "1", "10"])
        assert result.exit_code == 1


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
        runner.invoke(app, ["close", "FRO", "3", "43.00"])
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
