"""Ledger engine: hand-checked examples plus randomized accounting invariants."""

import random
from decimal import Decimal

import pytest

from trading_tracker import ledger


def T(id, action, shares, price, commission=0, ts=None, ticker="X", **kw):
    return {"id": id, "ticker": ticker, "action": action, "shares": shares, "price": price,
            "commission": commission, "timestamp": ts or f"2025-01-{id:02d}T10:00:00", **kw}


class TestExamples:
    def test_simple_long_with_fees(self):
        r = ledger.replay([T(1, "BUY", 10, 100, 1), T(2, "SELL", 10, 110, 1)])
        (lot,) = r.realized
        assert lot.to_row()["gross_pnl"] == 100.0
        assert lot.to_row()["net_pnl"] == 98.0
        assert lot.to_row()["entry_commission"] == 1.0
        assert lot.to_row()["exit_commission"] == 1.0
        assert r.positions == {}

    def test_partial_close_allocates_entry_fee_pro_rata(self):
        r = ledger.replay([T(1, "BUY", 10, 100, 10), T(2, "SELL", 4, 110, 2)])
        row = r.realized[0].to_row()
        assert row["entry_commission"] == 4.0  # 10 * 4/10
        assert row["net_pnl"] == 40 - 4 - 2
        pos = r.positions["X"]
        assert pos["shares"] == 6 and pos["avg_cost"] == 100
        assert pos["open_commission"] == 6.0
        assert pos["breakeven_price"] == pytest.approx(101.0)

    def test_averaging_up(self):
        r = ledger.replay([T(1, "BUY", 2, 10), T(2, "BUY", 2, 20), T(3, "SELL", 1, 30)])
        assert r.realized[0].to_row()["avg_entry_price"] == 15
        assert r.realized[0].to_row()["net_pnl"] == 15
        assert r.positions["X"]["avg_cost"] == 15

    def test_short(self):
        r = ledger.replay([T(1, "SELL", 5, 200, 1), T(2, "BUY", 5, 180, 1)])
        row = r.realized[0].to_row()
        assert row["direction"] == "short"
        assert row["gross_pnl"] == 100 and row["net_pnl"] == 98

    def test_flip_long_to_short_splits_commission(self):
        r = ledger.replay([T(1, "BUY", 6, 10, 0), T(2, "SELL", 10, 12, 10)])
        row = r.realized[0].to_row()
        assert row["shares"] == 6
        assert row["exit_commission"] == 6.0  # 10 * 6/10
        assert row["net_pnl"] == 12 - 6
        pos = r.positions["X"]
        assert pos["direction"] == "short" and pos["shares"] == 4
        assert pos["avg_cost"] == 12 and pos["open_commission"] == 4.0
        assert pos["entry_trade_ids"] == [2]

    def test_full_close_then_reopen_resets_average(self):
        r = ledger.replay([T(1, "BUY", 1, 100), T(2, "SELL", 1, 100), T(3, "BUY", 1, 50)])
        assert r.positions["X"]["avg_cost"] == 50
        assert r.positions["X"]["entry_trade_ids"] == [3]
        assert r.realized[0].entry_trade_ids == [1]

    def test_backdated_trade_is_replayed_in_time_order(self):
        # Inserted last (id 3) but happened first.
        r = ledger.replay([
            T(1, "BUY", 1, 100, ts="2025-02-01"),
            T(2, "SELL", 2, 120, ts="2025-03-01"),
            T(3, "BUY", 1, 80, ts="2025-01-01"),
        ])
        assert len(r.realized) == 1
        assert r.realized[0].to_row()["avg_entry_price"] == 90
        assert r.realized[0].to_row()["net_pnl"] == 60
        assert r.positions == {}

    def test_option_multiplier(self):
        r = ledger.replay([T(1, "BUY", 1, 0.90, 0.65, multiplier=100),
                           T(2, "SELL", 1, 1.55, 0.65, multiplier=100)])
        row = r.realized[0].to_row()
        assert row["gross_pnl"] == 65.0
        assert row["net_pnl"] == 63.7
        assert row["pnl_percent"] == pytest.approx(63.7 / 90 * 100, abs=0.01)

    def test_expired_worthless(self):
        r = ledger.replay([T(1, "BUY", 2, 1.2, 0, multiplier=100), T(2, "SELL", 2, 0, 0, multiplier=100)])
        assert r.realized[0].to_row()["net_pnl"] == -240.0

    def test_float_noise_does_not_leave_dust(self):
        trades = [T(1, "BUY", 0.1, 10), T(2, "BUY", 0.2, 10), T(3, "SELL", 0.3, 11)]
        r = ledger.replay(trades)
        assert r.positions == {}
        assert r.realized[0].to_row()["net_pnl"] == pytest.approx(0.3)

    def test_split_adjusts_quantity_and_price(self):
        r = ledger.replay(
            [T(1, "BUY", 4, 100, ts="2025-01-01"), T(2, "SELL", 8, 60, ts="2025-03-01")],
            [{"id": 1, "ticker": "X", "kind": "split", "timestamp": "2025-02-01T00:00:00", "ratio": 2}],
        )
        row = r.realized[0].to_row()
        assert row["shares"] == 8 and row["avg_entry_price"] == 50
        assert row["net_pnl"] == 80

    def test_reverse_split(self):
        r = ledger.replay(
            [T(1, "BUY", 100, 1, ts="2025-01-01")],
            [{"id": 1, "ticker": "X", "kind": "split", "timestamp": "2025-02-01", "ratio": 0.1}],
        )
        assert r.positions["X"]["shares"] == 10 and r.positions["X"]["avg_cost"] == 10

    def test_dividend_income(self):
        r = ledger.replay(
            [T(1, "BUY", 10, 10, ts="2025-01-01")],
            [{"id": 1, "ticker": "X", "kind": "dividend", "timestamp": "2025-02-01", "amount": 5, "tax": 0.5}],
        )
        assert r.income[0]["net"] == 4.5
        assert r.positions["X"]["dividends"] == 4.5

    def test_currency_mismatch_warns(self):
        r = ledger.replay([T(1, "BUY", 1, 10, currency="USD"), T(2, "BUY", 1, 10, currency="KZT")])
        assert any("currency" in w for w in r.warnings)

    def test_unrealized(self):
        pos = ledger.replay([T(1, "BUY", 10, 100, 2)]).positions["X"]
        m = ledger.unrealized(pos, 110)
        assert m["unrealized_pnl"] == 100.0 and m["unrealized_pnl_net"] == 98.0
        assert m["market_value"] == 1100.0
        short = ledger.replay([T(1, "SELL", 10, 100)]).positions["X"]
        assert ledger.unrealized(short, 90)["unrealized_pnl"] == 100.0
        assert ledger.unrealized(pos, None)["unrealized_pnl"] is None


def _random_trades(rng: random.Random, n: int, mult: int = 1):
    trades, net = [], 0
    for i in range(1, n + 1):
        qty = rng.randint(1, 20)
        action = rng.choice(["BUY", "SELL"])
        price = round(rng.uniform(1, 200), 2)
        fee = round(rng.uniform(0, 3), 2)
        trades.append(T(i, action, qty, price, fee, ts=f"2025-01-01T00:00:{i:02d}" if i < 60 else f"2025-01-02T00:{i // 60:02d}:{i % 60:02d}",
                        multiplier=mult))
        net += qty if action == "BUY" else -qty
    # Flatten at the end.
    if net:
        trades.append(T(n + 1, "SELL" if net > 0 else "BUY", abs(net), 100.0, 1.0,
                        ts="2025-12-31T00:00:00", multiplier=mult))
    return trades


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("mult", [1, 100])
def test_flat_book_realized_equals_cash_flow(seed, mult):
    """Once flat, total realized net P&L must equal net cash flow minus all fees."""
    rng = random.Random(seed)
    trades = _random_trades(rng, rng.randint(2, 50), mult)
    r = ledger.replay(trades)
    assert r.positions == {}
    cash = sum(Decimal(str(t["shares"])) * Decimal(str(t["price"])) * mult * (1 if t["action"] == "SELL" else -1)
               for t in trades)
    fees = sum(Decimal(str(t["commission"])) for t in trades)
    total = sum(lot.net_pnl for lot in r.realized)
    assert total == pytest.approx(cash - fees, abs=Decimal("1e-6"))
    # Rounded per-lot values may differ by at most half a cent each.
    assert sum(lot.to_row()["net_pnl"] for lot in r.realized) == pytest.approx(float(cash - fees), abs=0.005 * len(r.realized) + 1e-9)


@pytest.mark.parametrize("seed", range(20))
def test_open_book_realized_plus_unrealized_equals_cash_plus_value(seed):
    rng = random.Random(1000 + seed)
    trades = _random_trades(rng, rng.randint(2, 30))[:-1]  # leave the position open
    r = ledger.replay(trades)
    mark = Decimal("123.45")
    cash = sum(Decimal(str(t["shares"])) * Decimal(str(t["price"])) * (1 if t["action"] == "SELL" else -1)
               for t in trades)
    fees = sum(Decimal(str(t["commission"])) for t in trades)
    realized = sum(lot.net_pnl for lot in r.realized)
    pos = r.positions.get("X")
    held = Decimal(str(pos["net_shares"])) if pos else Decimal(0)
    unreal_net = Decimal(str(ledger.unrealized(pos, mark)["unrealized_pnl_net"])) if pos else Decimal(0)
    assert float(realized + unreal_net) == pytest.approx(float(cash - fees + held * mark), abs=0.02)
