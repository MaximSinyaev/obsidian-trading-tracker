"""Regression tests for issues found by the review round (math audit, code review, fresh-user trial)."""

import json
import sqlite3
import sys

import pytest

from trading_tracker import analytics, cli, dashboard, db, importers, ledger, report


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "t.db")
    yield c
    c.close()


# ── parsing ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw, dc, expected", [
    ("1,000", None, 1000.0),
    ("$1,234", None, 1234.0),
    ("1,234.56", None, 1234.56),
    ("1 555 200,00", True, 1555200.0),
    ("270,50", True, 270.5),
    ("12,5", None, 12.5),
    ("1.20000000ETH", None, 1.2),
    ("0.0001BTC", None, 0.0001),
    ("1.5E-5", None, 1.5e-05),
    ("(12.50)", None, -12.5),
])
def test_parse_number(raw, dc, expected):
    assert importers.parse_number(raw, dc) == expected


@pytest.mark.parametrize("side, expected", [
    ("Buy", "BUY"), ("BOT", "BUY"), ("Покупка", "BUY"), ("Sell", "SELL"), ("SLD", "SELL"),
    ("Продажа РЕПО", None), ("Dividend", None), ("Long", None), ("FX conversion", None),
    ("Market buy", "BUY"), ("Limit sell", "SELL"), ("Stop sell", "SELL"),
])
def test_parse_side(side, expected):
    assert importers.parse_side(side, 10) == expected


def test_add_accepts_broker_spellings(conn):
    tid = db.add_trade(conn, "A", "b", 1, 10, timestamp="2025-01-01")
    assert db.get_trade(conn, tid)["action"] == "BUY"
    with pytest.raises(ValueError):
        db.add_trade(conn, "A", "long", 1, 10)


def test_aware_timestamps_sort_by_instant(conn):
    db.add_trade(conn, "TZ", "buy", 10, 100, timestamp="2025-09-01T10:00:00+00:00")
    db.add_trade(conn, "TZ", "sell", 10, 110, timestamp="2025-09-01T09:00:00-05:00")  # 14:00 UTC
    (ct,) = db.get_closed_trades(conn)
    assert ct["direction"] == "long" and ct["net_pnl"] == 100.0


@pytest.mark.parametrize("bad", ["2025-13-45", "2025-02-30", "yesterday"])
def test_date_filters_are_validated(conn, bad):
    with pytest.raises(ValueError):
        report.build_report(conn, start=bad)
    with pytest.raises(ValueError):
        db.get_history(conn, from_date=bad)


def test_month_filter_includes_whole_month(conn):
    db.add_trade(conn, "A", "buy", 1, 10, timestamp="2025-03-01")
    db.add_trade(conn, "A", "sell", 1, 11, timestamp="2025-03-31T23:59:59")
    assert len(db.get_history(conn, from_date="2025-03", to_date="2025-03", closed_only=True)) == 1
    assert len(report.build_report(conn, start="2025-03", end="2025-03")["closed_trades"]) == 1


# ── ledger and report math ──────────────────────────────────────────────────


def test_expectancy_is_average_net():
    s = analytics.compute_stats([{"net_pnl": 100}, {"net_pnl": -1}, {"net_pnl": -1}])
    assert s["expectancy"] == 32.67


def test_unrealized_uses_exact_cost(conn):
    db.add_trade(conn, "BIG", "buy", 1_000_000, 100, stop_loss=90, timestamp="2025-01-01")
    db.add_event(conn, "BIG", "split", ratio=3, timestamp="2025-02-01")
    db.set_price(conn, "BIG", 40)
    p = report.build_report(conn)["open_positions"][0]
    assert p["unrealized_pnl"] == 20_000_000.0
    assert p["risk_to_stop"] == 10_000_000.0


def test_dividend_on_short_is_a_payment(conn):
    db.add_trade(conn, "S", "sell", 10, 50, timestamp="2025-01-01")
    db.add_event(conn, "S", "dividend", amount=5, timestamp="2025-01-10")
    res = db.run_ledger(conn)
    assert res.positions["S"]["dividends"] == -5.0
    assert any("while short" in w for w in res.warnings)


def test_position_dividends_stay_in_position_currency(conn):
    db.add_trade(conn, "Q", "buy", 10, 50, timestamp="2025-01-01")
    db.add_event(conn, "Q", "dividend", amount=5, currency="EUR", timestamp="2025-01-10")
    db.add_event(conn, "Q", "dividend", amount=3, timestamp="2025-01-11")
    assert db.run_ledger(conn).positions["Q"]["dividends"] == 3.0


def test_wrong_side_stop_gives_no_r(conn):
    db.add_trade(conn, "W", "buy", 10, 100, stop_loss=110, timestamp="2025-01-01")
    db.add_trade(conn, "W", "sell", 10, 105, timestamp="2025-01-02")
    rep = report.build_report(conn)
    assert rep["closed_trades"][0]["r_multiple"] is None
    assert any("stop" in w for w in rep["warnings"])


def test_drawdown_peak_is_start_when_first_day_loses():
    curve = [{"date": "2025-08-01", "equity": -10.0}, {"date": "2025-08-02", "equity": -5.0},
             {"date": "2025-08-03", "equity": -25.0}]
    dd = report.max_drawdown(curve)
    assert dd["amount"] == -25.0 and dd["peak_date"] is None


def test_currency_code_is_validated_and_escaped(conn):
    with pytest.raises(ValueError):
        db.add_trade(conn, "X", "buy", 1, 1, currency="<img src=x onerror=alert(1)>")
    html = dashboard.render(report.build_report(conn))
    assert "esc(c || ccy)" in html


# ── import ──────────────────────────────────────────────────────────────────


def test_newest_first_file_is_reversed(conn):
    text = "Date,Symbol,Side,Qty,Price\n2025-01-02,A,SELL,1,12\n2025-01-01,A,BUY,1,10\n"
    res = importers.import_file(conn, text=text)
    assert res.notes and db.get_closed_trades(conn)[0]["net_pnl"] == 2.0


def test_identical_fills_are_both_imported_and_reimport_is_idempotent(conn):
    text = "Date,Symbol,Side,Qty,Price\n2025-01-02,AAPL,BUY,10,100\n2025-01-02,AAPL,BUY,10,100\n"
    assert len(importers.import_file(conn, text=text).imported) == 2
    again = importers.import_file(conn, text=text)
    assert not again.imported and len(again.duplicates) == 2


def test_json_tags_list_and_bad_rows(conn):
    rows = [
        {"ticker": "A", "side": "buy", "qty": 1, "price": 10, "date": "2025-01-01", "tags": ["swing", "ai-idea"]},
        {"ticker": "B", "side": "buy", "qty": 1, "price": 10, "date": "2025-01-01", "multiplier": -5},
    ]
    res = importers.import_file(conn, text=json.dumps(rows))
    assert len(res.imported) == 1 and res.skipped[0]["row"] == 2
    tags = db.get_trade(conn, res.imported[0]["id"])["tags"]
    assert (json.loads(tags) if isinstance(tags, str) else tags) == ["swing", "ai-idea"]


def test_dividend_row_is_skipped_with_hint(conn):
    text = "Date,Symbol,Type,Qty,Price\n2025-01-02,KO,Dividend,1,0.5\n"
    res = importers.import_file(conn, text=text)
    assert "event dividend" in res.skipped[0]["reason"]


def test_xlsx_import(conn, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Account statement"])
    ws.append([])
    ws.append(["Date", "Symbol", "Side", "Quantity", "Price", "Commission"])
    ws.append(["2025-01-02", "MSFT", "Buy", 5, 400, 1])
    ws.append(["2025-01-03", "MSFT", "Sell", 5, 410, 1])
    path = tmp_path / "t.xlsx"
    wb.save(path)
    res = importers.import_file(conn, path)
    assert len(res.imported) == 2
    assert db.get_closed_trades(conn)[0]["net_pnl"] == 48.0


def test_ibkr_activity_statement_sections(conn):
    text = (
        "Statement,Header,Field Name,Field Value\n"
        "Statement,Data,Period,2025\n"
        "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,Comm/Fee\n"
        "Trades,Data,Order,Stocks,USD,AAPL,\"2025-01-02, 10:00:00\",10,100,-1\n"
        "Trades,Data,Order,Stocks,USD,AAPL,\"2025-01-03, 10:00:00\",-10,110,-1\n"
        "Trades,Total,,,,,,,,\n"
    )
    res = importers.import_file(conn, text=text, preset="ibkr")
    assert len(res.imported) == 2
    assert db.get_closed_trades(conn)[0]["net_pnl"] == 98.0


# Synthetic report shaped like a Freedom Finance / Tradernet JSON export (no real data).
FREEDOM_REPORT = {
    "date_start": "2025-01-01",
    "trades": {"detailed": [
        {"trade_id": 9001, "date": "2025-02-03 17:31:08", "instr_nm": "AAPL.US", "instr_kind": "акция обыкновенная",
         "operation": "buy", "p": 200.0, "q": 10, "curr_c": "USD", "commission": 1.2,
         "commission_currency": "USD", "profit": 0, "comment": "Market: usa"},
        {"trade_id": 9002, "date": "2025-02-04 11:00:00", "instr_nm": "EUR/USD", "instr_kind": "валюта",
         "operation": "buy", "p": 1.05, "q": 1000, "curr_c": "USD", "commission": 0,
         "commission_currency": "USD", "profit": 0, "comment": ""},
        {"trade_id": 9003, "date": "2025-03-14 16:40:12", "instr_nm": "AAPL.US", "instr_kind": "акция обыкновенная",
         "operation": "sell", "p": 210.0, "q": 6, "curr_c": "USD", "commission": 0.6,
         "commission_currency": "USD", "profit": 60, "comment": ""},
        {"trade_id": 9004, "date": "2025-03-01 15:00:00", "instr_nm": "+SPY.21MAR2025.C600",
         "instr_kind": "поставочный опцион", "operation": "buy", "p": 2.5, "q": 1, "curr_c": "USD",
         "commission": 0.65, "commission_currency": "USD", "profit": 0, "comment": ""},
        {"trade_id": 9005, "date": "2025-03-21 23:59:59", "instr_nm": "+SPY.21MAR2025.C600",
         "instr_kind": "поставочный опцион", "operation": "sell", "p": 0, "q": 1, "curr_c": "USD",
         "commission": 0, "commission_currency": "USD", "profit": -250, "comment": "expiration"},
        {"trade_id": 9006, "date": "2025-04-01 10:00:00", "instr_nm": "SAP.EU", "instr_kind": "акция обыкновенная",
         "operation": "buy", "p": 100.0, "q": 2, "curr_c": "EUR", "commission": 1.5,
         "commission_currency": "USD", "profit": 0, "comment": ""},
    ]},
    "cash_flows": {"detailed": [
        {"date": "2025-05-10", "amount": "1.3", "currency": "USD", "type_id": "dividend",
         "comment": "Dividends (Apple Inc. (AAPL.US)), record date 2025-05-09"},
        {"date": "2025-05-10", "amount": "-0.13", "currency": "USD", "type_id": "tax", "comment": "tax"},
        {"date": "2025-05-11", "amount": "100", "currency": "USD", "type_id": "bank", "comment": "deposit"},
    ]},
}


def test_freedom_json_report(conn):
    text = json.dumps(FREEDOM_REPORT, ensure_ascii=False)
    res = importers.import_file(conn, text=text)
    assert res.source == "freedom"
    assert {r["ticker"] for r in res.imported} == {"AAPL", "SPY.21MAR2025.C600", "SAP"}
    assert any("currency conversion" in s["reason"] for s in res.skipped)
    opt = [r for r in res.imported if r["instrument"] == "option"]
    assert opt and all(r["multiplier"] == 100 for r in opt)
    closed = {c["ticker"]: c for c in db.get_closed_trades(conn)}
    assert closed["AAPL"]["gross_pnl"] == 60.0  # matches the broker's "profit"
    assert closed["SPY.21MAR2025.C600"]["gross_pnl"] == -250.0
    assert any("not included" in n for n in res.notes)  # USD fee on an EUR trade is not converted
    sap = [r for r in res.imported if r["ticker"] == "SAP"][0]
    assert sap["commission"] == 0.0 and "1.5 USD" in sap["notes"]
    assert len(res.dividends) == 1 and res.dividends[0]["ticker"] == "AAPL"
    again = importers.import_file(conn, text=text)
    assert not again.imported and not again.dividends


def test_freedom_csv_preset(conn):
    text = (
        "Дата сделки;Дата расчетов;ID сделки;ID приказа;Тикер;Операция;Кол-во;Цена;Валюта;Сумма;Комиссия;Валюта комиссии\n"
        "03.02.2025 17:31:08;04.02.2025;4410002;9910002;AAPL.US;Покупка;1 000;228,50;USD;228 500,00;1,20;USD\n"
        "14.03.2025 16:40:12;17.03.2025;4410005;9910005;AAPL.US;Продажа;600;214,00;USD;128 400,00;0,65;USD\n"
    )
    res = importers.import_file(conn, text=text, preset="freedom")
    assert [r["shares"] for r in res.imported] == [1000, 600]
    assert res.imported[0]["external_id"] == "4410002" and res.imported[0]["ticker"] == "AAPL"
    assert res.imported[0]["timestamp"].startswith("2025-02-03")


# ── migration from v0.5 ─────────────────────────────────────────────────────


def test_migration_normalises_legacy_timestamps_and_tickers(tmp_path):
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    for m in sorted(db.SCHEMA_DIR.glob("*.sql")):
        if int(m.stem.split("_")[0]) <= 4:
            c.executescript(m.read_text())
    c.execute("INSERT INTO trades (ticker, action, shares, price, commission, timestamp) "
              "VALUES ('aapl','BUY',10,100,0,'2025-07-01 15:00:00')")
    c.commit()
    c.close()
    conn = db.init_db(path)
    db.add_trade(conn, "AAPL", "sell", 10, 110, timestamp="2025-07-01T16:00")
    (ct,) = db.get_closed_trades(conn)
    assert ct["direction"] == "long" and ct["net_pnl"] == 100.0
    assert db.validate_db(conn) == []


# ── CLI JSON contract ───────────────────────────────────────────────────────


@pytest.mark.parametrize("args", [
    ["add", "AMD", "buy", "1", "158,20"],
    ["add", "AMD"],
    ["review", "1", "--rating", "7"],
    ["nosuch"],
    ["stats", "--from", "2025-13-45"],
])
def test_usage_errors_are_json(args, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TRADE_DB", str(tmp_path / "x.db"))
    monkeypatch.setattr(sys, "argv", ["trade", "--json", *args])
    with pytest.raises(SystemExit) as exc:
        cli.run()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is False and out["error"]


def test_review_error_suggests_closing_fill(conn):
    db.add_trade(conn, "A", "buy", 1, 10, timestamp="2025-01-01")
    db.add_trade(conn, "A", "sell", 1, 11, timestamp="2025-01-02")
    with pytest.raises(ValueError, match="#2"):
        db.set_review(conn, 1, lesson="x")
