"""OKX bill-history import. The fixture is synthetic: an invented instrument and round numbers."""

from __future__ import annotations

import pytest

from trading_tracker import dashboard, db, importers, okx, report
from trading_tracker.importers import ImportResult

B = "\ufeff"


@pytest.fixture
def conn(tmp_path):
    db.set_local_timezone("UTC")
    c = db.init_db(tmp_path / "t.db")
    yield c
    c.close()
    db.set_local_timezone(None)


HEADER = ("id,Order id,Time,Trade Type,Symbol,Action,Amount,Trading Unit,Filled Price,PnL,Fee,Fee Unit,"
          "Position Change,Position Balance,Balance Change,Balance,Balance Unit")
# Oldest first here; the file is written newest first like the real export.
# FOO-USDT-SWAP is not in KNOWN_CTVAL, so its contract value (0.1) must be solved from the PnL column.
ROWS = [
    ("1", "o1", "2025-01-01 10:00:00", "Buy", "10", "100", "0", "-0.05", "0"),
    ("2", "", "2025-01-02 08:00:00", "Funding fee expense", "", "", "-0.3", "0", "-0.3"),
    ("3", "o2", "2025-01-02 10:00:00", "Sell", "4", "110", "4", "-0.02", "0"),
    ("4", "", "2025-01-03 08:00:00", "Funding fee income", "", "", "0.5", "0", "0.5"),
    ("5", "o3", "2025-01-03 10:00:00", "Sell", "6", "90", "-6", "-0.03", "0"),
    ("6", "o4", "2025-01-04 10:00:00", "Sell", "5", "100", "0", "-0.025", "0"),
    ("7", "", "2025-01-04 11:00:00", "Manual margin increase", "", "", "0", "0", "-20"),
    ("8", "o5", "2025-01-05 10:00:00", "Liquidation buy", "5", "120", "-10", "-0.01", "0"),
    ("9", "o5", "2025-01-05 10:00:00", "Liquidation fees", "", "", "0", "0", "-1.5"),
]


def _csv(bom: bool = True) -> str:
    b = B if bom else ""
    lines = [f"{b}UID:000,Account Type:Main,Time Zone:UTC+9", b + HEADER]
    for rid, order, ts, action, amount, price, pnl, fee, change in reversed(ROWS):
        vals = [rid, order, ts, "Swap", "FOO-USDT-SWAP", action, amount, "cont", price, pnl, fee, "USDT",
                "", "", change, "", "USDT"]
        lines.append(",".join(b + v for v in vals))
    return "\n".join(lines) + "\n"


def test_detects_and_fits_contract_value(conn):
    res = importers.import_file(conn, text=_csv())
    assert len(res.imported) == 5
    assert len(res.events) == 2
    assert [s["reason"] for s in res.skipped] == ["'Manual margin increase' is not a trade (margin or transfer)"]
    assert res.reconciliation == {"closing_fills": 3, "match_okx_pnl": 3,
                                  "contract_values": {"FOO-USDT-SWAP": 0.1}}
    assert all(r["multiplier"] == 0.1 and r["instrument"] == "future" for r in res.imported)


def test_timezone_and_liquidation(conn):
    res = importers.import_file(conn, text=_csv())
    first = res.imported[0]
    assert first["timestamp"].startswith("2025-01-01T01:00")  # 10:00 UTC+9
    liq = [r for r in res.imported if r["tags"] == ["liquidation"]]
    assert len(liq) == 1 and liq[0]["commission"] == pytest.approx(1.51)


def test_report_pnl_fees_and_funding(conn):
    importers.import_file(conn, text=_csv())
    s = report.build_report(conn)["by_currency"]["USDT"]["summary"]
    assert s["gross_pnl"] == pytest.approx(4 - 6 - 10)
    assert s["commissions"] == pytest.approx(0.05 + 0.02 + 0.03 + 0.025 + 0.01 + 1.5, abs=0.02)
    assert s["funding"] == pytest.approx(0.2)
    assert s["dividends"] == 0
    assert s["net_pnl_with_income"] == pytest.approx(s["net_pnl"] + 0.2)


def test_reimport_is_idempotent(conn):
    importers.import_file(conn, text=_csv())
    res = importers.import_file(conn, text=_csv())
    assert not res.imported and not res.events and len(res.duplicates) == 5
    assert conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'funding'").fetchone()[0] == 2


def test_without_bom_and_explicit_preset(conn):
    res = importers.import_file(conn, text=_csv(bom=False), preset="okx", dry_run=True)
    assert len(res.imported) == 5
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_explicit_multiplier_wins(conn):
    res = ImportResult(dry_run=True, source="okx", columns={})
    okx.import_okx(conn, _csv(), res, dry_run=True, multipliers={"FOO-USDT-SWAP": 1})
    assert res.reconciliation["contract_values"] == {"FOO-USDT-SWAP": 1}
    assert res.reconciliation["match_okx_pnl"] == 0


def test_nice_snaps_round_contract_sizes():
    assert okx._nice(0.0999) == 0.1
    assert okx._nice(98.9) == 100
    assert okx._nice(0.37) == 0.37


def test_dashboard_compacts_long_lot_lists():
    rep = {"closed_trades": [{"id": 1, "entry_trade_ids": list(range(30)), "notes": None, "strategy": ""}]}
    row = dashboard._compact(rep)["closed_trades"][0]
    assert row["entry_trade_ids"] == [0, 1, 2, 3, 4, "… 20 more", 25, 26, 27, 28, 29]
    assert "strategy" not in row and "notes" not in row


def test_migration_from_v5_keeps_events(tmp_path):
    c = db.get_connection(tmp_path / "m.db")
    for m in sorted(db.SCHEMA_DIR.glob("*.sql")):
        if int(m.stem.split("_")[0]) <= 5:
            c.executescript(m.read_text())
    c.execute("INSERT INTO events (ticker, kind, amount, currency, timestamp) "
              "VALUES ('AAA', 'dividend', 5, 'USD', '2024-01-02T00:00:00Z')")
    c.commit()
    assert db.migrate(c) == 6
    assert [tuple(r) for r in c.execute("SELECT kind, amount FROM events")] == [("dividend", 5)]
    db.add_event(c, "AAA", "funding", amount=-0.4, currency="USD", timestamp="2024-01-03T00:00:00")
    with pytest.raises(ValueError):
        db.add_event(c, "AAA", "funding", currency="USD", timestamp="2024-01-04T00:00:00")
    c.close()


def test_offset_times_use_the_configured_zone_not_the_machine(monkeypatch):
    import time
    ts = "2025-01-15T22:00:00Z"
    try:
        db.set_local_timezone("Asia/Tokyo")
        stored = []
        for machine in ("UTC", "America/New_York"):
            monkeypatch.setenv("TZ", machine)
            time.tzset()
            stored.append(db.normalize_timestamp(ts))
        assert stored == ["2025-01-16T07:00:00", "2025-01-16T07:00:00"]
        with pytest.raises(ValueError):
            db.set_local_timezone("Mars/Base")
    finally:
        db.set_local_timezone(None)
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_cli_reads_trade_tz(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from trading_tracker.cli import app
    monkeypatch.setenv("TRADE_TZ", "Asia/Tokyo")
    try:
        out = CliRunner().invoke(app, ["--json", "--db", str(tmp_path / "c.db"), "add", "AAA", "BUY", "1", "10",
                                       "--date", "2025-01-15T22:00:00Z"])
        assert out.exit_code == 0, out.output
        c = db.get_connection(tmp_path / "c.db")
        assert c.execute("SELECT timestamp FROM trades").fetchone()[0] == "2025-01-16T07:00:00"
    finally:
        db.set_local_timezone(None)
