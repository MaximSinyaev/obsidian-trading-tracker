"""End-to-end tests based on real Freedom Finance broker report.

Tests follow TDD: written from broker's actual P&L data first, then code
is adjusted to make them pass.

Key rules:
- add_trade should auto-create closed_trades when a counter-trade reduces
  an existing position (SELL vs long, BUY vs short).
- P&L = avg cost method (verified to match broker's calculations).
- Broker's 'profit' field = gross P&L (without commission).
- Our net P&L = gross - exit commission.
- Options: summ = q * price * 100 (contract multiplier).
  In tests we use summ/q as price to keep math simple.
- trade stats should work with just add_trade — no close_position needed.
"""

import sqlite3

import pytest

from trading_tracker import analytics, db


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


def _get_last_closed(conn, ticker: str) -> dict:
    """Get the most recently created closed_trade for a ticker."""
    row = conn.execute(
        "SELECT * FROM closed_trades WHERE ticker = ? ORDER BY id DESC LIMIT 1",
        (ticker.upper(),),
    ).fetchone()
    assert row is not None, f"No closed_trade for {ticker}"
    return dict(row)


# ── Core auto-close behavior ────────────────────────────────────────────────


class TestAutoCloseOnAddTrade:
    """add_trade must auto-create closed_trades when reducing a position."""

    def test_buy_does_not_create_closed_trade(self, conn):
        db.add_trade(conn, "AAPL", "buy", 10, 150.0)
        assert db.get_closed_trades(conn) == []

    def test_sell_against_long_creates_closed_trade(self, conn):
        db.add_trade(conn, "AAPL", "buy", 10, 150.0)
        db.add_trade(conn, "AAPL", "sell", 10, 160.0)
        closed = db.get_closed_trades(conn)
        assert len(closed) == 1
        assert closed[0]["ticker"] == "AAPL"
        assert closed[0]["direction"] == "long"

    def test_buy_against_short_creates_closed_trade(self, conn):
        db.add_trade(conn, "TSLA", "sell", 5, 200.0)
        db.add_trade(conn, "TSLA", "buy", 5, 180.0)
        closed = db.get_closed_trades(conn)
        assert len(closed) == 1
        assert closed[0]["direction"] == "short"

    def test_adding_to_position_no_close(self, conn):
        """Buying more of an existing long = no close."""
        db.add_trade(conn, "AAPL", "buy", 5, 150.0)
        db.add_trade(conn, "AAPL", "buy", 5, 155.0)
        assert db.get_closed_trades(conn) == []
        assert db.get_position(conn, "AAPL")["net_shares"] == 10

    def test_adding_to_short_no_close(self, conn):
        """Selling more of an existing short = no close."""
        db.add_trade(conn, "TSLA", "sell", 5, 200.0)
        db.add_trade(conn, "TSLA", "sell", 5, 210.0)
        assert db.get_closed_trades(conn) == []
        assert db.get_position(conn, "TSLA")["net_shares"] == -10

    def test_stats_work_without_close_position(self, conn):
        """The whole point: add trades → stats just work."""
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        db.add_trade(conn, "FRO", "sell", 3, 43.0)
        db.add_trade(conn, "RBLX", "buy", 1, 57.0)
        db.add_trade(conn, "RBLX", "sell", 1, 102.88)

        stats = analytics.compute_stats(db.get_closed_trades(conn))
        assert stats["total_trades"] == 2
        assert stats["wins"] == 2
        assert stats["total_pnl"] > 0


# ── Broker P&L verification (avg cost, no commission) ──────────────────────


class TestFRO:
    """FRO.US: Buy 3 @ $39.546, Sell 3 @ $35.946.
    Broker profit: -10.8 (gross). Commission: buy=1.83, sell=1.78."""

    def test_gross_pnl_matches_broker(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.546,
                     commission=1.83, timestamp="2026-03-02T12:00:00")
        db.add_trade(conn, "FRO", "sell", 3, 35.946,
                     commission=1.78, timestamp="2026-03-03T12:00:00")

        ct = _get_last_closed(conn, "FRO")
        assert ct["direction"] == "long"
        assert ct["shares"] == 3
        assert ct["avg_entry_price"] == pytest.approx(39.546, abs=0.001)
        assert ct["avg_exit_price"] == pytest.approx(35.946, abs=0.001)
        # Broker says gross = -10.8
        assert ct["gross_pnl"] == pytest.approx(-10.8, abs=0.01)
        # Net = -10.8 - 1.78 (exit commission) = -12.58
        assert ct["net_pnl"] == pytest.approx(-12.58, abs=0.01)

    def test_position_fully_closed(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.546, timestamp="2026-03-02T12:00:00")
        db.add_trade(conn, "FRO", "sell", 3, 35.946, timestamp="2026-03-03T12:00:00")
        assert db.get_position(conn, "FRO") is None


class TestRBLX:
    """RBLX.US: Buy 1 @ $57, Sell 1 @ $102.88.
    Broker profit: 45.88. Commission: buy=0.30, sell=1.72."""

    def test_gross_pnl_matches_broker(self, conn):
        db.add_trade(conn, "RBLX", "buy", 1, 57.0,
                     commission=0.30, timestamp="2025-03-18T10:00:00")
        db.add_trade(conn, "RBLX", "sell", 1, 102.88,
                     commission=1.72, timestamp="2025-06-26T10:00:00")

        ct = _get_last_closed(conn, "RBLX")
        assert ct["gross_pnl"] == pytest.approx(45.88, abs=0.01)
        assert ct["net_pnl"] == pytest.approx(44.16, abs=0.01)


class TestKMGZ:
    """KMGZ.KZ: Buy 1 @ 14248.98 KZT, Sell 1 @ 20095.
    Broker profit: 5846.02."""

    def test_gross_pnl_matches_broker(self, conn):
        db.add_trade(conn, "KMGZ", "buy", 1, 14248.98,
                     commission=0.01, timestamp="2024-12-04T10:00:00")
        db.add_trade(conn, "KMGZ", "sell", 1, 20095.0,
                     commission=0.03, timestamp="2025-08-05T10:00:00")

        ct = _get_last_closed(conn, "KMGZ")
        assert ct["gross_pnl"] == pytest.approx(5846.02, abs=0.01)
        assert ct["net_pnl"] == pytest.approx(5845.99, abs=0.01)


class TestALT:
    """ALT.US: Buy 3 @ $3.46, Sell 3 @ $4.50.
    Broker profit: 3.12."""

    def test_gross_pnl_matches_broker(self, conn):
        db.add_trade(conn, "ALT", "buy", 3, 3.46,
                     commission=1.29, timestamp="2025-06-30T10:00:00")
        db.add_trade(conn, "ALT", "sell", 3, 4.50,
                     commission=0.11, timestamp="2025-07-02T10:00:00")

        ct = _get_last_closed(conn, "ALT")
        assert ct["gross_pnl"] == pytest.approx(3.12, abs=0.01)
        assert ct["net_pnl"] == pytest.approx(3.01, abs=0.01)


class TestWBTN:
    """WBTN.US: Buy 1 @ $9.7885, Sell 1 @ $16.41.
    Broker profit: 6.62."""

    def test_gross_pnl_matches_broker(self, conn):
        db.add_trade(conn, "WBTN", "buy", 1, 9.7885,
                     commission=1.26, timestamp="2025-07-10T10:00:00")
        db.add_trade(conn, "WBTN", "sell", 1, 16.41,
                     commission=1.29, timestamp="2025-08-15T10:00:00")

        ct = _get_last_closed(conn, "WBTN")
        assert ct["gross_pnl"] == pytest.approx(6.62, abs=0.01)
        assert ct["net_pnl"] == pytest.approx(5.33, abs=0.01)


class TestATYR:
    """ATYR.US: Buy 3 @ $3.88, Sell 3 @ $4.8007.
    Broker profit: 2.76."""

    def test_gross_pnl_matches_broker(self, conn):
        db.add_trade(conn, "ATYR", "buy", 3, 3.88,
                     commission=1.30, timestamp="2025-03-20T10:00:00")
        db.add_trade(conn, "ATYR", "sell", 3, 4.8007,
                     commission=1.31, timestamp="2025-06-03T10:00:00")

        ct = _get_last_closed(conn, "ATYR")
        assert ct["gross_pnl"] == pytest.approx(2.76, abs=0.01)
        assert ct["net_pnl"] == pytest.approx(1.45, abs=0.01)


class TestTMF:
    """TMF.US: Buy 1 @ $38.67, Sell 1 @ $39.
    Broker profit: 0.33. Commission eats the profit."""

    def test_commission_exceeds_gross(self, conn):
        db.add_trade(conn, "TMF", "buy", 1, 38.67,
                     commission=0.20, timestamp="2025-04-23T10:00:00")
        db.add_trade(conn, "TMF", "sell", 1, 39.0,
                     commission=1.41, timestamp="2025-06-27T10:00:00")

        ct = _get_last_closed(conn, "TMF")
        assert ct["gross_pnl"] == pytest.approx(0.33, abs=0.01)
        assert ct["net_pnl"] == pytest.approx(-1.08, abs=0.01)


class TestSRTS:
    """SRTS.US: Buy 4 @ $3.39, Sell 4 @ $5.30.
    Broker profit: 7.64."""

    def test_gross_pnl_matches_broker(self, conn):
        db.add_trade(conn, "SRTS", "buy", 4, 3.39,
                     commission=0.12, timestamp="2025-08-19T10:00:00")
        db.add_trade(conn, "SRTS", "sell", 4, 5.30,
                     commission=0.16, timestamp="2026-01-20T10:00:00")

        ct = _get_last_closed(conn, "SRTS")
        assert ct["gross_pnl"] == pytest.approx(7.64, abs=0.01)
        assert ct["net_pnl"] == pytest.approx(7.48, abs=0.01)


# ── Partial closes (HSBK: 40 shares closed via 4 separate SELLs) ────────────


class TestHSBKPartialCloses:
    """HSBK.KZ: 3 buys (40 shares), then 4 partial sells.

    Buys: 20@250.95, 10@246, 10@269.86 → avg cost = 254.44
    Sells: 10@310.96 (profit=565.2), 10@333 (785.6),
           18@333 (1414.08), 2@333 (157.12)
    Then: 2 more buys at higher prices (new position).
    """

    def _setup_buys(self, conn):
        db.add_trade(conn, "HSBK", "buy", 20, 250.95,
                     timestamp="2024-12-04T12:00:00")
        db.add_trade(conn, "HSBK", "buy", 10, 246.0,
                     timestamp="2024-12-18T12:00:00")
        db.add_trade(conn, "HSBK", "buy", 10, 269.86,
                     timestamp="2025-01-24T12:00:00")

    def test_avg_cost_after_buys(self, conn):
        self._setup_buys(conn)
        pos = db.get_position(conn, "HSBK")
        assert pos["net_shares"] == 40
        # avg = (20*250.95 + 10*246 + 10*269.86) / 40 = 254.44
        assert pos["avg_cost"] == pytest.approx(254.44, abs=0.01)

    def test_first_partial_sell(self, conn):
        """Sell 10 @ 310.96 → broker profit = 565.2."""
        self._setup_buys(conn)
        db.add_trade(conn, "HSBK", "sell", 10, 310.96,
                     commission=0.01, timestamp="2025-06-26T10:00:00")

        ct = _get_last_closed(conn, "HSBK")
        assert ct["shares"] == 10
        assert ct["gross_pnl"] == pytest.approx(565.2, abs=0.01)

        pos = db.get_position(conn, "HSBK")
        assert pos["net_shares"] == 30

    def test_all_four_partial_sells(self, conn):
        """4 sells close all 40 shares. Verify each P&L matches broker."""
        self._setup_buys(conn)

        # Sell 1: 10 @ 310.96 → profit 565.20
        db.add_trade(conn, "HSBK", "sell", 10, 310.96,
                     commission=0.01, timestamp="2025-06-26T10:00:00")
        # Sell 2: 10 @ 333 → profit 785.60
        db.add_trade(conn, "HSBK", "sell", 10, 333.0,
                     commission=0.01, timestamp="2025-08-05T10:00:00")
        # Sell 3: 18 @ 333 → profit 1414.08
        db.add_trade(conn, "HSBK", "sell", 18, 333.0,
                     commission=0.01, timestamp="2025-08-05T11:00:00")
        # Sell 4: 2 @ 333 → profit 157.12
        db.add_trade(conn, "HSBK", "sell", 2, 333.0,
                     commission=0, timestamp="2025-08-05T12:00:00")

        # All 4 closed_trades records
        closed = db.get_closed_trades(conn)
        assert len(closed) == 4

        # Verify gross P&L for each (most recent first)
        gross_pnls = sorted([c["gross_pnl"] for c in closed])
        assert gross_pnls == pytest.approx(
            sorted([565.2, 785.6, 1414.08, 157.12]), abs=0.01
        )

        # Total gross P&L
        total_gross = sum(c["gross_pnl"] for c in closed)
        assert total_gross == pytest.approx(2922.0, abs=0.1)

        # Position fully closed
        assert db.get_position(conn, "HSBK") is None

    def test_new_position_after_full_close(self, conn):
        """After closing all 40, buying again starts a fresh position."""
        self._setup_buys(conn)
        db.add_trade(conn, "HSBK", "sell", 40, 333.0,
                     timestamp="2025-08-05T10:00:00")
        assert db.get_position(conn, "HSBK") is None

        db.add_trade(conn, "HSBK", "buy", 15, 372.0,
                     timestamp="2025-08-19T10:00:00")
        db.add_trade(conn, "HSBK", "buy", 4, 367.84,
                     timestamp="2025-09-04T10:00:00")

        pos = db.get_position(conn, "HSBK")
        assert pos["net_shares"] == 19
        # New avg: (15*372 + 4*367.84) / 19 = (5580 + 1471.36) / 19
        assert pos["avg_cost"] == pytest.approx(371.12, abs=0.01)


# ── IE_FXBF: sell from middle of position, then buy more ────────────────────


class TestIEFXBFSellThenBuyMore:
    """IE_FXBF.KZ: 3 buys, partial sell, then 3 more buys.
    Broker profit on sell: 323.86."""

    def test_partial_sell_then_more_buys(self, conn):
        # 3 initial buys
        db.add_trade(conn, "IE_FXBF", "buy", 5, 3182.70,
                     timestamp="2024-12-04T10:00:00")
        db.add_trade(conn, "IE_FXBF", "buy", 2, 3193.0,
                     timestamp="2024-12-06T10:00:00")
        db.add_trade(conn, "IE_FXBF", "buy", 2, 3192.0,
                     timestamp="2024-12-20T10:00:00")

        # Avg cost = (5*3182.70 + 2*3193 + 2*3192) / 9 = 3187.0556
        pos = db.get_position(conn, "IE_FXBF")
        assert pos["avg_cost"] == pytest.approx(3187.06, abs=0.01)

        # Partial sell: 3 @ 3295.01 → broker profit = 323.86
        db.add_trade(conn, "IE_FXBF", "sell", 3, 3295.01,
                     commission=0.02, timestamp="2025-06-26T10:00:00")

        ct = _get_last_closed(conn, "IE_FXBF")
        assert ct["shares"] == 3
        assert ct["gross_pnl"] == pytest.approx(323.86, abs=0.01)

        # 6 shares remain
        pos = db.get_position(conn, "IE_FXBF")
        assert pos["net_shares"] == 6

        # Buy 3 more at much higher price — avg cost should increase
        db.add_trade(conn, "IE_FXBF", "buy", 3, 4379.99,
                     timestamp="2025-11-11T10:00:00")

        pos = db.get_position(conn, "IE_FXBF")
        assert pos["net_shares"] == 9
        # Avg = (6*3187.0556 + 3*4379.99) / 9 ≈ 3584.70
        assert pos["avg_cost"] == pytest.approx(3584.70, abs=0.1)


# ── USO: partial close (sell 1 of 2) ────────────────────────────────────────


class TestUSOPartialClose:
    """USO.US: Buy 2 @ $86.738, Sell 1 @ $92.28.
    Broker profit on sell: 5.54. One share remains open."""

    def test_partial_close(self, conn):
        db.add_trade(conn, "USO", "buy", 2, 86.738,
                     commission=2.09, timestamp="2026-03-02T10:00:00")
        db.add_trade(conn, "USO", "sell", 1, 92.28,
                     commission=1.67, timestamp="2026-03-03T10:00:00")

        ct = _get_last_closed(conn, "USO")
        assert ct["shares"] == 1
        assert ct["gross_pnl"] == pytest.approx(5.54, abs=0.01)
        assert ct["net_pnl"] == pytest.approx(3.87, abs=0.01)

        pos = db.get_position(conn, "USO")
        assert pos["net_shares"] == 1
        assert pos["avg_cost"] == pytest.approx(86.738, abs=0.001)


# ── Options with contract multiplier (using summ as effective price) ────────


class TestOptionXLU:
    """XLU Mar2025 $78 Call: Buy 1 @ summ=$90, Sell 1 @ summ=$155.
    Broker profit: 65 (= 155-90). Commission: buy=13, sell=13."""

    def test_option_profit_using_summ(self, conn):
        # Use summ/q as price (90/1=90, 155/1=155)
        db.add_trade(conn, "XLU-C78-MAR25", "buy", 1, 90.0,
                     commission=13.0, instrument="option",
                     timestamp="2025-03-07T10:00:00")
        db.add_trade(conn, "XLU-C78-MAR25", "sell", 1, 155.0,
                     commission=13.0, instrument="option",
                     timestamp="2025-03-17T10:00:00")

        ct = _get_last_closed(conn, "XLU-C78-MAR25")
        assert ct["gross_pnl"] == pytest.approx(65.0, abs=0.01)
        assert ct["net_pnl"] == pytest.approx(52.0, abs=0.01)


class TestOptionXLV:
    """XLV Jul2025 $137 Call: Buy 1 @ summ=$97, Sell 1 @ summ=$150.
    Broker profit: 53."""

    def test_option_profit_using_summ(self, conn):
        db.add_trade(conn, "XLV-C137-JUL25", "buy", 1, 97.0,
                     commission=13.0, instrument="option",
                     timestamp="2025-07-03T10:00:00")
        db.add_trade(conn, "XLV-C137-JUL25", "sell", 1, 150.0,
                     commission=13.0, instrument="option",
                     timestamp="2025-07-10T10:00:00")

        ct = _get_last_closed(conn, "XLV-C137-JUL25")
        assert ct["gross_pnl"] == pytest.approx(53.0, abs=0.01)
        assert ct["net_pnl"] == pytest.approx(40.0, abs=0.01)


class TestOptionBOTZExpired:
    """BOTZ Mar2025 $32 Call: Buy 1 @ summ=$15, expired @ $0.
    Broker profit: -15."""

    def test_option_expired_worthless(self, conn):
        db.add_trade(conn, "BOTZ-C32-MAR25", "buy", 1, 15.0,
                     commission=13.0, instrument="option",
                     timestamp="2025-03-11T10:00:00")
        db.add_trade(conn, "BOTZ-C32-MAR25", "sell", 1, 0,
                     commission=0, instrument="option",
                     timestamp="2025-03-21T10:00:00")

        ct = _get_last_closed(conn, "BOTZ-C32-MAR25")
        assert ct["gross_pnl"] == pytest.approx(-15.0, abs=0.01)
        assert ct["net_pnl"] == pytest.approx(-15.0, abs=0.01)


class TestOptionBTGExpired:
    """BTG Dec2025 $5 Call: Buy 2 @ summ=$40, expired @ $0.
    Broker profit: -40."""

    def test_option_expired_two_contracts(self, conn):
        # summ/q = 40/2 = 20 per contract
        db.add_trade(conn, "BTG-C5-DEC25", "buy", 2, 20.0,
                     commission=16.0, instrument="option",
                     timestamp="2025-11-04T10:00:00")
        db.add_trade(conn, "BTG-C5-DEC25", "sell", 2, 0,
                     commission=0, instrument="option",
                     timestamp="2025-12-19T10:00:00")

        ct = _get_last_closed(conn, "BTG-C5-DEC25")
        assert ct["gross_pnl"] == pytest.approx(-40.0, abs=0.01)
        assert ct["net_pnl"] == pytest.approx(-40.0, abs=0.01)


class TestOptionUSOQuickFlip:
    """USO Mar2026 $86 Call: Buy 1 @ summ=$601, Sell 1 @ summ=$620 same day.
    Broker profit: 19."""

    def test_same_day_option_flip(self, conn):
        db.add_trade(conn, "USO-C86-MAR26", "buy", 1, 601.0,
                     commission=13.0, instrument="option",
                     timestamp="2026-03-02T10:00:00")
        db.add_trade(conn, "USO-C86-MAR26", "sell", 1, 620.0,
                     commission=13.0, instrument="option",
                     timestamp="2026-03-02T11:00:00")

        ct = _get_last_closed(conn, "USO-C86-MAR26")
        assert ct["gross_pnl"] == pytest.approx(19.0, abs=0.01)
        assert ct["net_pnl"] == pytest.approx(6.0, abs=0.01)


# ── Open positions (only buys, no sells) ────────────────────────────────────


class TestOpenPositions:
    """Positions with only buys should NOT create any closed_trades."""

    def test_asbn_dca_open(self, conn):
        """ASBN.KZ: 6 DCA buys, no sells."""
        buys = [
            (100, 24.0, "2025-07-11"), (100, 23.98, "2025-08-19"),
            (33, 22.39, "2025-09-04"), (55, 12.65, "2025-10-28"),
            (200, 12.86, "2025-12-08"), (30, 15.95, "2026-01-22"),
        ]
        for q, p, date in buys:
            db.add_trade(conn, "ASBN", "buy", q, p,
                         timestamp=f"{date}T10:00:00")

        assert db.get_closed_trades(conn) == []
        pos = db.get_position(conn, "ASBN")
        assert pos["net_shares"] == 518
        total_cost = sum(q * p for q, p, _ in buys)
        assert pos["avg_cost"] == pytest.approx(total_cost / 518, abs=0.01)

    def test_snap_two_buys_same_day(self, conn):
        """SNAP.US: Two buys at same price on same day."""
        db.add_trade(conn, "SNAP", "buy", 1, 7.96, timestamp="2025-12-08T10:00:00")
        db.add_trade(conn, "SNAP", "buy", 1, 7.96, timestamp="2025-12-08T10:01:00")

        assert db.get_closed_trades(conn) == []
        pos = db.get_position(conn, "SNAP")
        assert pos["net_shares"] == 2
        assert pos["avg_cost"] == 7.96


# ── Aggregate stats from all closed broker trades ───────────────────────────


class TestAggregateStats:
    """Replay all fully-closed trades from broker report, verify overall stats."""

    def _add_all_closed_trades(self, conn):
        """Add all trades for tickers that were fully closed."""
        # FRO: loss
        db.add_trade(conn, "FRO", "buy", 3, 39.546, commission=1.83,
                     timestamp="2026-03-02T12:00:00")
        db.add_trade(conn, "FRO", "sell", 3, 35.946, commission=1.78,
                     timestamp="2026-03-03T12:00:00")
        # RBLX: big win
        db.add_trade(conn, "RBLX", "buy", 1, 57.0, commission=0.30,
                     timestamp="2025-03-18T10:00:00")
        db.add_trade(conn, "RBLX", "sell", 1, 102.88, commission=1.72,
                     timestamp="2025-06-26T10:00:00")
        # ALT: small win
        db.add_trade(conn, "ALT", "buy", 3, 3.46, commission=1.29,
                     timestamp="2025-06-30T10:00:00")
        db.add_trade(conn, "ALT", "sell", 3, 4.50, commission=0.11,
                     timestamp="2025-07-02T10:00:00")
        # TMF: commission > profit
        db.add_trade(conn, "TMF", "buy", 1, 38.67, commission=0.20,
                     timestamp="2025-04-23T10:00:00")
        db.add_trade(conn, "TMF", "sell", 1, 39.0, commission=1.41,
                     timestamp="2025-06-27T10:00:00")
        # WBTN: win
        db.add_trade(conn, "WBTN", "buy", 1, 9.7885, commission=1.26,
                     timestamp="2025-07-10T10:00:00")
        db.add_trade(conn, "WBTN", "sell", 1, 16.41, commission=1.29,
                     timestamp="2025-08-15T10:00:00")
        # ATYR: win
        db.add_trade(conn, "ATYR", "buy", 3, 3.88, commission=1.30,
                     timestamp="2025-03-20T10:00:00")
        db.add_trade(conn, "ATYR", "sell", 3, 4.8007, commission=1.31,
                     timestamp="2025-06-03T10:00:00")
        # SRTS: win
        db.add_trade(conn, "SRTS", "buy", 4, 3.39, commission=0.12,
                     timestamp="2025-08-19T10:00:00")
        db.add_trade(conn, "SRTS", "sell", 4, 5.30, commission=0.16,
                     timestamp="2026-01-20T10:00:00")
        # KMGZ: big win
        db.add_trade(conn, "KMGZ", "buy", 1, 14248.98, commission=0.01,
                     timestamp="2024-12-04T10:00:00")
        db.add_trade(conn, "KMGZ", "sell", 1, 20095.0, commission=0.03,
                     timestamp="2025-08-05T10:00:00")

    def test_total_closed_trades(self, conn):
        self._add_all_closed_trades(conn)
        closed = db.get_closed_trades(conn)
        assert len(closed) == 8

    def test_win_count(self, conn):
        """Winners: RBLX, ALT, WBTN, ATYR, SRTS, KMGZ = 6 wins."""
        self._add_all_closed_trades(conn)
        stats = analytics.compute_stats(db.get_closed_trades(conn))
        assert stats["wins"] == 6

    def test_loss_count(self, conn):
        """Losers: FRO (gross loss), TMF (net loss from commission) = 2 losses."""
        self._add_all_closed_trades(conn)
        stats = analytics.compute_stats(db.get_closed_trades(conn))
        assert stats["losses"] == 2

    def test_total_pnl_positive(self, conn):
        self._add_all_closed_trades(conn)
        stats = analytics.compute_stats(db.get_closed_trades(conn))
        # KMGZ alone is +5846, so total should be well positive
        assert stats["total_pnl"] > 5000


# ── close_position still works as convenience wrapper ────────────────────────


class TestClosePositionStillWorks:
    """close_position should still work and add review notes."""

    def test_close_with_review_notes(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        result = db.close_position(
            conn, "FRO", 3, 43.0,
            commission=10, what_worked="Good timing", lesson="Trust the plan",
        )
        assert result["direction"] == "long"
        assert result["gross_pnl"] == pytest.approx(10.35, abs=0.01)

        ct = _get_last_closed(conn, "FRO")
        assert ct["what_worked"] == "Good timing"
        assert ct["lesson"] == "Trust the plan"

    def test_close_position_validates(self, conn):
        with pytest.raises(ValueError, match="No open position"):
            db.close_position(conn, "NOPE", 1, 10.0)

    def test_close_more_than_held_raises(self, conn):
        db.add_trade(conn, "FRO", "buy", 3, 39.55)
        with pytest.raises(ValueError, match="Cannot close"):
            db.close_position(conn, "FRO", 5, 43.0)
