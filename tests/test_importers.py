"""CSV / JSON import: column detection, broker presets, de-duplication."""

from pathlib import Path

import pytest

from trading_tracker import db, importers

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "t.db")
    yield c
    c.close()


class TestParsing:
    @pytest.mark.parametrize("raw,expected", [
        ("1 234,56", 1234.56), ("1,234.56", 1234.56), ("1.234,56", 1234.56), ("270,50", 270.5),
        ("-1.02", -1.02), ("(12.50)", -12.5), ("$10", 10.0), ("", None), ("—", None), (5, 5.0),
    ])
    def test_numbers(self, raw, expected):
        assert importers.parse_number(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("2025-01-15", "2025-01-15T00:00:00"),
        ("2025-01-15 10:30", "2025-01-15T10:30:00"),
        ("20250115;103000", "2025-01-15T10:30:00"),
        ("15.01.2025 10:30:00", "2025-01-15T10:30:00"),
        ("01/15/2025", "2025-01-15T00:00:00"),
    ])
    def test_dates(self, raw, expected):
        assert importers.parse_timestamp(raw) == expected

    def test_dayfirst(self):
        assert importers.parse_timestamp("03/02/2025", dayfirst=True) == "2025-02-03T00:00:00"

    @pytest.mark.parametrize("raw,qty,expected", [
        ("Buy", 1, "BUY"), ("SLD", 1, "SELL"), ("Покупка", 1, "BUY"), ("Продажа", 1, "SELL"),
        (None, -3, "SELL"), (None, 3, "BUY"), ("Dividend", 1, None),
    ])
    def test_side(self, raw, qty, expected):
        assert importers.parse_side(raw, qty) == expected


class TestImport:
    def test_generic_csv(self, conn):
        res = importers.import_file(conn, EXAMPLES / "generic.csv")
        assert len(res.imported) == 6 and not res.skipped
        closed = {c["exit_trade_id"]: c for c in db.get_closed_trades(conn)}
        assert sorted(c["net_pnl"] for c in closed.values()) == [-31.6, 58.6, 98.0]
        assert db.get_position(conn, "NVDA")["shares"] == 4

    def test_reimport_is_idempotent(self, conn):
        importers.import_file(conn, EXAMPLES / "generic.csv")
        res = importers.import_file(conn, EXAMPLES / "generic.csv")
        assert len(res.imported) == 0 and len(res.duplicates) == 6
        assert len(db.get_trades(conn)) == 6

    def test_rows_without_id_dedupe_by_fingerprint(self, conn):
        text = "date,ticker,side,qty,price\n2025-01-01,AAA,buy,1,10\n"
        importers.import_file(conn, text=text)
        res = importers.import_file(conn, text=text)
        assert len(res.duplicates) == 1

    def test_dry_run_writes_nothing(self, conn):
        res = importers.import_file(conn, EXAMPLES / "generic.csv", dry_run=True)
        assert len(res.imported) == 6 and db.get_trades(conn) == []

    def test_ibkr_flex(self, conn):
        res = importers.import_file(conn, EXAMPLES / "ibkr_flex.csv", preset="ibkr")
        assert len(res.imported) == 4, res.skipped
        msft = [c for c in db.get_closed_trades(conn) if c["ticker"] == "MSFT"][0]
        assert msft["gross_pnl"] == pytest.approx(197.5)
        assert msft["net_pnl"] == pytest.approx(197.5 - 1 - 1.02)
        opt = [c for c in db.get_closed_trades(conn) if c["ticker"].startswith("SPY")][0]
        assert opt["gross_pnl"] == pytest.approx(225.0)  # (6.35-4.10)*100
        assert opt["instrument"] == "option"
        assert db.get_trades(conn)[0]["source"] == "ibkr"

    def test_tinvest(self, conn):
        res = importers.import_file(conn, EXAMPLES / "tinvest.csv", preset="tinvest")
        assert len(res.imported) == 2
        assert len(res.skipped) == 1 and "event dividend" in res.skipped[0]["reason"]
        (c,) = db.get_closed_trades(conn)
        assert c["currency"] == "RUB"
        assert c["gross_pnl"] == pytest.approx(307.0)
        assert c["net_pnl"] == pytest.approx(307.0 - 1.35 - 1.51)

    def test_agent_json(self, conn):
        res = importers.import_file(conn, EXAMPLES / "agent.json")
        assert len(res.imported) == 2
        (c,) = db.get_closed_trades(conn)
        assert c["net_pnl"] == pytest.approx(0.05 * 19000 - 4.2 - 6.1)

    def test_column_override(self, conn):
        text = "when,what,dir,n,px\n2025-01-01,AAA,B,2,10\n"
        res = importers.import_file(conn, text=text, overrides={
            "timestamp": "when", "ticker": "what", "action": "dir", "shares": "n", "price": "px"})
        assert len(res.imported) == 1

    def test_missing_columns_explains(self, conn):
        with pytest.raises(ValueError, match="could not find columns"):
            importers.import_file(conn, text="foo,bar\n1,2\n")

    def test_separate_date_and_time_columns(self, conn):
        text = "Date,Time,Symbol,Action,Qty,Price\n2025-01-01,10:30:00,AAA,BUY,1,10\n"
        res = importers.import_file(conn, text=text)
        assert res.imported[0]["timestamp"] == "2025-01-01T10:30:00"

    def test_bad_rows_are_reported_not_fatal(self, conn):
        text = "date,ticker,side,qty,price\n2025-01-01,AAA,buy,1,10\nnot-a-date,BBB,buy,1,10\n"
        res = importers.import_file(conn, text=text)
        assert len(res.imported) == 1 and res.skipped[0]["row"] == 2
