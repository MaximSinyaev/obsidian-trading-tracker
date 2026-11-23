"""Persona fixtures: realistic broker exports with hand-checked expected results.

See tests/fixtures/personas/README.md. Each scenario is imported through the CLI
(the same path an agent uses), then open positions, realized P&L per currency and
"must not be a position" rows are compared with expected.json.

Checks that fail today are listed in KNOWN_ISSUES and run as strict xfail, so a
fix makes the test XPASS (fail) until its entry is removed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from trading_tracker import db, report
from trading_tracker.cli import app

ROOT = Path(__file__).resolve().parent / "fixtures" / "personas"
runner = CliRunner()

KNOWN_ISSUES: dict[str, str] = {
    # Freedom Finance
    "aidar_freedom_kz/freedom_csv::not_positions": "USD/KZT conversion row imported as a stock position",
    "aidar_freedom_kz/freedom_csv::positions": "USD/KZT conversion shows up as an open position",
    "aidar_freedom_kz/freedom_json::import": "nested broker-report JSON crashes: 'dictionary update sequence element #0'",
    "aidar_freedom_kz/freedom_json::positions": "JSON import fails",
    "aidar_freedom_kz/freedom_json::realized": "JSON import fails",
    "aidar_freedom_kz/freedom_json::not_positions": "JSON import fails",
    # T-Invest
    "olga_tinvest_ru/tinvest_operations_cp1251::positions": "CNYRUB_TOM currency purchase becomes a position",
    "olga_tinvest_ru/tinvest_operations_cp1251::not_positions": "CNYRUB_TOM currency purchase becomes a position",
    "olga_tinvest_ru/tinvest_operations_cp1251::realized": "commissions come as separate rows and are dropped: net == gross",
    # IBKR
    "daniel_ibkr_eu/ibkr_flex_multi_section::positions": "EUR.USD FX row becomes a position",
    "daniel_ibkr_eu/ibkr_flex_multi_section::not_positions": "EUR.USD FX row becomes a position",
    "daniel_ibkr_eu/ibkr_activity_statement::import": "Activity Statement format (Section,Header/Data,...) not supported",
    "daniel_ibkr_eu/ibkr_activity_statement::positions": "Activity Statement import fails",
    "daniel_ibkr_eu/ibkr_activity_statement::realized": "Activity Statement import fails",
    "daniel_ibkr_eu/ibkr_activity_statement::not_positions": "Activity Statement import fails",
    # Schwab options
    "jess_options_us/schwab_transactions::positions": "'as of' dates skipped, so the assignment stock buy is lost",
    "jess_options_us/schwab_transactions::not_positions": "Expired/Assigned rows ignored: options stay open forever",
    "jess_options_us/schwab_transactions::realized": "options get multiplier 1, 'Fees & Comm' not read, expiry/assignment not closed",
    "aidar_freedom_kz/freedom_csv::realized": "option +XLU.21MAR2025.C78 gets multiplier 1: -2.15 instead of -215",
    # Trading 212
    "sofia_trading212_eu/trading212_history::import": "'No. of shares'/'Price / share' not detected; 'Market buy'/'Limit sell' not parsed as sides",
    "sofia_trading212_eu/trading212_history::positions": "import fails",
    "sofia_trading212_eu/trading212_history::not_positions": "import fails",
    "sofia_trading212_eu/trading212_history::realized": "import fails; GBX pence not normalised",
    # Binance
    "ken_crypto_binance/binance_spot_history::import": "'Pair', 'Date(UTC)', 'Executed' not detected",
    "ken_crypto_binance/binance_spot_history::positions": "import fails; with --map: base-asset fee ignored, currency USD not USDT",
    "ken_crypto_binance/binance_spot_history::not_positions": "import fails; with --map: '1.20000000ETH' parsed as missing quantity (E read as exponent)",
    "ken_crypto_binance/binance_spot_history::realized": "import fails; with --map: identical fills de-duplicated by fingerprint",
}


def _canon(ticker: str) -> str:
    t = ticker.upper()
    return t[:-3] if t.endswith(".US") else t


def _scenarios():
    for exp_path in sorted(ROOT.glob("*/expected.json")):
        exp = json.loads(exp_path.read_text(encoding="utf-8"))
        for sc in exp["scenarios"]:
            yield exp_path.parent, exp, sc


CASES = [(d, e, s) for d, e, s in _scenarios()]
IDS = [f"{d.name}/{s['name']}" for d, _e, s in CASES]


def _mark(request, key: str) -> None:
    if key in KNOWN_ISSUES:
        request.applymarker(pytest.mark.xfail(reason=KNOWN_ISSUES[key], strict=True))


def _import(tmp_path, monkeypatch, folder: Path, sc: dict):
    path = tmp_path / "persona.db"
    monkeypatch.setenv("TRADE_DB", str(path))
    for f in sc["files"]:
        res = runner.invoke(app, ["--json", "import", str(folder / f["path"]), *f["args"]])
        payload = json.loads(res.output.strip().splitlines()[-1])
        assert payload["ok"], payload.get("error")
    conn = db.init_db(path)
    try:
        return report.build_report(conn)
    finally:
        conn.close()


@pytest.mark.parametrize("folder,exp,sc", CASES, ids=IDS)
def test_import_succeeds(folder, exp, sc, tmp_path, monkeypatch, request):
    _mark(request, f"{folder.name}/{sc['name']}::import")
    _import(tmp_path, monkeypatch, folder, sc)


@pytest.mark.parametrize("folder,exp,sc", CASES, ids=IDS)
def test_open_positions(folder, exp, sc, tmp_path, monkeypatch, request):
    _mark(request, f"{folder.name}/{sc['name']}::positions")
    rep = _import(tmp_path, monkeypatch, folder, sc)
    only = set(sc.get("only") or [])
    want = {_canon(p["ticker"]): p for p in exp["open_positions"]
            if not only or _canon(p["ticker"]) in only}
    got = {_canon(p["ticker"]): p for p in rep["open_positions"]}
    assert set(got) == set(want)
    for t, w in want.items():
        g = got[t]
        assert g["shares"] == pytest.approx(w["shares"]), t
        assert g["avg_cost"] == pytest.approx(w["avg_cost"], abs=0.005), t
        assert g["currency"] == w["currency"], t


@pytest.mark.parametrize("folder,exp,sc", CASES, ids=IDS)
def test_not_positions(folder, exp, sc, tmp_path, monkeypatch, request):
    _mark(request, f"{folder.name}/{sc['name']}::not_positions")
    rep = _import(tmp_path, monkeypatch, folder, sc)
    forbidden = {_canon(t) for t in exp.get("not_positions", [])}
    got = {_canon(p["ticker"]) for p in rep["open_positions"]}
    assert not (got & forbidden)


@pytest.mark.parametrize("folder,exp,sc", CASES, ids=IDS)
def test_realized_pnl(folder, exp, sc, tmp_path, monkeypatch, request):
    _mark(request, f"{folder.name}/{sc['name']}::realized")
    rep = _import(tmp_path, monkeypatch, folder, sc)
    want = exp["realized_only_subset"] if sc.get("only") else exp["realized"]
    for ccy, w in want.items():
        s = rep["by_currency"].get(ccy, {}).get("summary", {})
        assert s.get("closed_trades") == w["closed_trades"], ccy
        assert s.get("gross_pnl") == pytest.approx(w["gross"], abs=0.005), ccy
        if "net" in w:
            assert s.get("net_pnl") == pytest.approx(w["net"], abs=0.005), ccy
