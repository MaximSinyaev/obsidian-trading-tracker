"""Report snapshot, dashboard rendering, --json CLI contract, migrations."""

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from trading_tracker import dashboard, db, importers, report
from trading_tracker.cli import app

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
runner = CliRunner()


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def env_db(tmp_path, monkeypatch):
    path = tmp_path / "agent.db"
    monkeypatch.setenv("TRADE_DB", str(path))
    monkeypatch.chdir(tmp_path)
    return path


def run_json(*args):
    res = runner.invoke(app, ["--json", *args])
    payload = json.loads(res.output.strip().splitlines()[-1])
    return res.exit_code, payload


class TestReport:
    def test_summary_and_curve(self, conn):
        importers.import_file(conn, EXAMPLES / "generic.csv")
        rep = report.build_report(conn)
        usd = rep["by_currency"]["USD"]
        s = usd["summary"]
        assert s["closed_trades"] == 3 and s["wins"] == 2
        assert s["net_pnl"] == 125.0
        assert s["gross_pnl"] == 130.0
        assert s["max_drawdown"]["amount"] == -31.6
        assert [p["equity"] for p in usd["equity_curve"]] == [58.6, 27.0, 125.0]
        assert rep["open_positions"][0]["ticker"] == "NVDA"
        assert "open positions without a stop-loss: NVDA" in rep["warnings"]

    def test_r_multiple_and_origin(self, conn):
        db.add_trade(conn, "A", "buy", 10, 100, stop_loss=95, tags=["ai-idea"], timestamp="2025-01-01")
        db.add_trade(conn, "A", "sell", 10, 110, timestamp="2025-01-02")
        db.add_trade(conn, "B", "buy", 1, 10, timestamp="2025-01-01")
        db.add_trade(conn, "B", "sell", 1, 9, timestamp="2025-01-03")
        rep = report.build_report(conn)
        a = [c for c in rep["closed_trades"] if c["ticker"] == "A"][0]
        assert a["initial_risk"] == 50.0 and a["r_multiple"] == 2.0
        origin = {r["origin"]: r for r in rep["by_currency"]["USD"]["by_origin"]}
        assert origin["ai"]["net_pnl"] == 100.0 and origin["manual"]["net_pnl"] == -1.0

    def test_stop_is_split_adjusted(self, conn):
        db.add_trade(conn, "N", "buy", 4, 100, stop_loss=90, timestamp="2025-01-01")
        db.add_event(conn, "N", "split", ratio=2, timestamp="2025-02-01")
        rep = report.build_report(conn)
        p = rep["open_positions"][0]
        assert p["stop_loss"] == 45 and p["risk_to_stop"] == 40.0

    def test_currencies_are_never_mixed(self, conn):
        db.add_trade(conn, "U", "buy", 1, 10, timestamp="2025-01-01")
        db.add_trade(conn, "U", "sell", 1, 20, timestamp="2025-01-02")
        db.add_trade(conn, "K", "buy", 1, 1000, currency="KZT", timestamp="2025-01-01")
        db.add_trade(conn, "K", "sell", 1, 900, currency="KZT", timestamp="2025-01-02")
        rep = report.build_report(conn)
        assert rep["by_currency"]["USD"]["summary"]["net_pnl"] == 10
        assert rep["by_currency"]["KZT"]["summary"]["net_pnl"] == -100

    def test_filters(self, conn):
        importers.import_file(conn, EXAMPLES / "generic.csv")
        rep = report.build_report(conn, start="2025-03-01")
        assert [c["ticker"] for c in rep["closed_trades"]] == ["TSLA"]
        rep = report.build_report(conn, ticker="aapl")
        assert {c["ticker"] for c in rep["closed_trades"]} == {"AAPL"}

    def test_dashboard_is_self_contained_and_escaped(self, conn):
        db.add_trade(conn, "X", "buy", 1, 10, notes="</script><script>alert(1)</script>")
        html = dashboard.render(report.build_report(conn), title="<b>Mine</b>")
        assert "</script><script>alert" not in html
        assert "&lt;b&gt;Mine&lt;/b&gt;" in html
        assert "http://" not in html and "https://" not in html  # works offline


class TestAgentCli:
    def test_json_contract(self, env_db):
        code, out = run_json("add", "AAPL", "buy", "10", "150", "-c", "1", "--sl", "140")
        assert code == 0 and out["ok"] and out["data"]["trade"]["id"] == 1
        code, out = run_json("add", "AAPL", "sell", "4", "165", "-c", "1")
        assert out["data"]["realized"]["net_pnl"] == 58.6
        assert out["data"]["position"]["shares"] == 6
        code, out = run_json("positions")
        assert out["data"]["positions"][0]["ticker"] == "AAPL"
        code, out = run_json("stats")
        assert out["data"]["USD"]["summary"]["net_pnl"] == 58.6

    def test_json_errors(self, env_db):
        code, out = run_json("add", "AAPL", "hold", "1", "1")
        assert code == 1 and out["ok"] is False and "BUY or SELL" in out["error"]
        code, out = run_json("show", "999")
        assert code == 1 and not out["ok"]
        code, out = run_json("delete", "1")
        assert code == 1

    def test_duplicate_external_id_rejected(self, env_db):
        run_json("add", "A", "buy", "1", "1", "--external-id", "x1")
        code, out = run_json("add", "A", "buy", "1", "1", "--external-id", "x1")
        assert code == 1 and "already exists" in out["error"]

    def test_edit_recomputes_pnl(self, env_db):
        run_json("add", "A", "buy", "1", "10", "--date", "2025-01-01")
        run_json("add", "A", "sell", "1", "15", "--date", "2025-01-02")
        run_json("edit", "1", "--price", "12")
        code, out = run_json("history", "--closed")
        assert out["data"]["rows"][0]["net_pnl"] == 3.0
        run_json("delete", "2", "--yes")
        code, out = run_json("history", "--closed")
        assert out["data"]["rows"] == []

    def test_import_price_journal_review_dashboard(self, env_db, tmp_path):
        code, out = run_json("import", str(EXAMPLES / "generic.csv"))
        assert out["data"]["imported_count"] == 6
        code, out = run_json("price", "set", "NVDA", "120", "--source", "agent")
        code, out = run_json("positions")
        assert out["data"]["positions"][0]["unrealized_pnl"] == 80.0
        code, out = run_json("journal", "add", "Felt FOMO", "--mood", "2", "--ticker", "NVDA")
        assert out["ok"]
        code, out = run_json("review", "3", "--lesson", "Respect the stop", "--rating", "3")
        assert out["data"]["review"]["lesson"] == "Respect the stop"
        code, out = run_json("review", "1", "--lesson", "x")
        assert code == 1  # trade 1 opened a position, it did not close one
        code, out = run_json("dashboard", "-o", str(tmp_path / "d.html"))
        assert Path(out["data"]["path"]).is_file()
        code, out = run_json("report")
        assert out["data"]["journal"][0]["text"] == "Felt FOMO"

    def test_review_survives_edit_of_other_trade(self, env_db):
        run_json("add", "A", "buy", "2", "10", "--date", "2025-01-01")
        run_json("add", "A", "sell", "1", "15", "--date", "2025-01-02")
        run_json("review", "2", "--lesson", "keep me")
        run_json("edit", "1", "--price", "11")
        code, out = run_json("show", "2")
        assert out["data"]["realized"]["lesson"] == "keep me"
        assert out["data"]["realized"]["net_pnl"] == 4.0


def test_migration_from_v4_keeps_data_and_reviews(tmp_path):
    """A database created by v0.5.x upgrades in place; reviews and P&L survive."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for m in sorted(db.SCHEMA_DIR.glob("*.sql")):
        if int(m.stem.split("_")[0]) <= 4:
            conn.executescript(m.read_text())
    conn.execute("INSERT INTO trades (ticker, action, shares, price, commission, timestamp) "
                 "VALUES ('FRO','BUY',3,39.55,1,'2025-01-01T00:00:00')")
    conn.execute("INSERT INTO trades (ticker, action, shares, price, commission, timestamp) "
                 "VALUES ('FRO','SELL',3,43,1,'2025-01-05T00:00:00')")
    # Old-style closed_trades row: P&L ignored the entry commission.
    conn.execute(
        "INSERT INTO closed_trades (ticker, entry_trade_ids, exit_trade_ids, shares, avg_entry_price, "
        "avg_exit_price, entry_avg_cost, total_commission, gross_pnl, net_pnl, pnl_percent, lesson, closed_at) "
        "VALUES ('FRO','[1]','[2]',3,39.55,43,39.55,1,10.35,9.35,7.9,'Trust the plan','2025-01-05T00:00:00')")
    conn.commit()
    conn.close()

    conn = db.init_db(path)
    assert db._get_schema_version(conn) == db.LATEST_SCHEMA
    (ct,) = db.get_closed_trades(conn)
    assert ct["net_pnl"] == 8.35  # now includes the entry commission
    assert ct["lesson"] == "Trust the plan"
    assert db.validate_db(conn) == []
