"""Deterministic trade ledger: the single place where positions and P&L are computed.

Everything the tool reports (open positions, realized P&L, stats, dashboards,
Obsidian notes) is derived by replaying the ``trades`` table through
:func:`replay`. Nothing is computed incrementally, so editing, deleting or
back-dating a trade can never leave stale P&L behind.

Accounting rules (average-cost method, the same method most retail brokers use):

* Opening / adding to a position: the average price is the quantity-weighted
  average of fill prices. Entry commissions are tracked separately and
  attributed to the open quantity.
* Reducing a position: the average price does not change. Realized P&L for the
  closed quantity is

      gross = (exit_price - avg_price) * qty * multiplier * (+1 long / -1 short)
      net   = gross - entry_commission_share - exit_commission_share

  where ``entry_commission_share`` is the pro-rata part of the commissions paid
  to open the position, and ``exit_commission_share`` is the part of this
  trade's commission that belongs to the closed quantity.
* Flipping (e.g. selling 10 while long 6): the first 6 close the long, the
  remaining 4 open a short at the fill price; the trade's commission is split
  between the two parts by quantity.
* Trades are replayed in (timestamp, id) order, so back-dated imports land in
  the right place.

All arithmetic uses :class:`decimal.Decimal` to avoid float drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable

ZERO = Decimal(0)
EPS = Decimal("1e-9")
CENT = Decimal("0.01")


def D(value: Any) -> Decimal:
    """Convert a number (float/int/str/Decimal/None) to Decimal without float noise."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money(value: Decimal) -> float:
    """Round to cents (half-up, like brokers) and return a float."""
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def precise(value: Decimal, places: int = 6) -> float:
    q = Decimal(1).scaleb(-places)
    return float(value.quantize(q, rounding=ROUND_HALF_UP))


def qty(value: Decimal) -> float:
    return precise(value, 8)


@dataclass
class RealizedLot:
    """One position reduction = one realized P&L record (one row of closed_trades)."""

    ticker: str
    direction: str  # "long" | "short"
    exit_trade_id: int
    entry_trade_ids: list[int]
    shares: Decimal
    avg_entry_price: Decimal
    exit_price: Decimal
    multiplier: Decimal
    entry_commission: Decimal
    exit_commission: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    cost_basis: Decimal
    opened_at: str | None
    closed_at: str
    currency: str
    instrument: str
    strategy: str | None

    @property
    def pnl_percent(self) -> Decimal:
        return (self.net_pnl / self.cost_basis * 100) if self.cost_basis else ZERO

    @property
    def hold_duration_days(self) -> float | None:
        if not self.opened_at:
            return None
        try:
            start = datetime.fromisoformat(self.opened_at)
            end = datetime.fromisoformat(self.closed_at)
        except (TypeError, ValueError):
            return None
        try:
            return round((end - start).total_seconds() / 86400, 2)
        except TypeError:  # naive vs aware mix
            return None

    def to_row(self) -> dict[str, Any]:
        """Shape used by the closed_trades table and analytics."""
        return {
            "ticker": self.ticker,
            "direction": self.direction,
            "exit_trade_id": self.exit_trade_id,
            "entry_trade_ids": list(self.entry_trade_ids),
            "exit_trade_ids": [self.exit_trade_id],
            "shares": qty(self.shares),
            "avg_entry_price": precise(self.avg_entry_price),
            "avg_exit_price": precise(self.exit_price),
            "entry_avg_cost": precise(self.avg_entry_price),
            "multiplier": precise(self.multiplier),
            "entry_commission": money(self.entry_commission),
            "exit_commission": money(self.exit_commission),
            "total_commission": money(self.entry_commission + self.exit_commission),
            "gross_pnl": money(self.gross_pnl),
            "net_pnl": money(self.net_pnl),
            "pnl_percent": money(self.pnl_percent),
            "hold_duration_days": self.hold_duration_days,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "currency": self.currency,
            "instrument": self.instrument,
            "strategy": self.strategy,
        }


@dataclass
class _State:
    ticker: str
    net: Decimal = ZERO  # signed quantity: >0 long, <0 short
    avg: Decimal = ZERO  # average fill price of the open quantity
    open_commission: Decimal = ZERO  # entry commissions not yet realized
    opened_at: str | None = None
    entry_ids: list[int] = field(default_factory=list)
    strategy: str | None = None
    multiplier: Decimal = Decimal(1)
    currency: str = "USD"
    instrument: str = "stock"
    trade_count: int = 0  # trades in the current position epoch
    total_commission: Decimal = ZERO  # all commissions ever paid on this ticker
    last_trade: str | None = None
    first_trade_ever: str | None = None
    realized_net: Decimal = ZERO  # realized P&L across all epochs

    def reset(self) -> None:
        self.net = ZERO
        self.avg = ZERO
        self.open_commission = ZERO
        self.opened_at = None
        self.entry_ids = []
        self.strategy = None
        self.trade_count = 0

    def open(self, trade_id: int, ts: str, shares: Decimal, price: Decimal,
             commission: Decimal, sign: int, strategy: str | None) -> None:
        """Open or add to a position in direction ``sign`` (+1 long, -1 short)."""
        held = abs(self.net)
        if held <= EPS:
            self.reset()
            self.opened_at = ts
            self.strategy = strategy
            self.avg = price
        else:
            self.avg = (held * self.avg + shares * price) / (held + shares)
        self.net += sign * shares
        self.open_commission += commission
        self.entry_ids.append(trade_id)
        if self.strategy is None:
            self.strategy = strategy


@dataclass
class LedgerResult:
    positions: dict[str, dict[str, Any]]
    realized: list[RealizedLot]
    warnings: list[str]
    tickers: dict[str, dict[str, Any]]  # per-ticker lifetime summary


def _normalize(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(trade["id"]),
        "ticker": str(trade["ticker"]).upper(),
        "action": str(trade["action"]).upper(),
        "shares": D(trade["shares"]),
        "price": D(trade["price"]),
        "commission": D(trade.get("commission") or 0),
        "timestamp": str(trade["timestamp"]),
        "multiplier": D(trade.get("multiplier") or 1),
        "currency": (trade.get("currency") or "USD").upper(),
        "instrument": trade.get("instrument") or "stock",
        "strategy": trade.get("strategy"),
    }


def sort_key(trade: dict[str, Any]) -> tuple[str, int]:
    return (str(trade["timestamp"]), int(trade["id"]))


def replay(trades: Iterable[dict[str, Any]]) -> LedgerResult:
    """Replay trades (any order) and return positions + realized lots."""
    ordered = sorted((_normalize(t) for t in trades), key=sort_key)
    states: dict[str, _State] = {}
    realized: list[RealizedLot] = []
    warnings: list[str] = []

    for t in ordered:
        if t["action"] not in ("BUY", "SELL"):
            warnings.append(f"trade {t['id']}: unknown action {t['action']!r}, skipped")
            continue
        if t["shares"] <= 0:
            warnings.append(f"trade {t['id']}: non-positive quantity, skipped")
            continue

        st = states.get(t["ticker"])
        if st is None:
            st = _State(
                ticker=t["ticker"],
                multiplier=t["multiplier"],
                currency=t["currency"],
                instrument=t["instrument"],
                first_trade_ever=t["timestamp"],
            )
            states[t["ticker"]] = st
        else:
            if t["currency"] != st.currency:
                warnings.append(
                    f"trade {t['id']}: {t['ticker']} currency {t['currency']} differs from "
                    f"earlier trades ({st.currency}); P&L is computed in {st.currency}"
                )
            if t["multiplier"] != st.multiplier:
                warnings.append(
                    f"trade {t['id']}: {t['ticker']} multiplier {t['multiplier']} differs from "
                    f"earlier trades ({st.multiplier}); using {st.multiplier}"
                )

        st.total_commission += t["commission"]
        st.last_trade = t["timestamp"]
        sign = 1 if t["action"] == "BUY" else -1
        shares, price, commission = t["shares"], t["price"], t["commission"]
        held = abs(st.net)
        is_counter = held > EPS and ((st.net > 0 and sign < 0) or (st.net < 0 and sign > 0))

        if not is_counter:
            st.open(t["id"], t["timestamp"], shares, price, commission, sign, t["strategy"])
            st.trade_count += 1
            continue

        # ── Reduce / close / flip ────────────────────────────────────────
        st.trade_count += 1
        closed = min(shares, held)
        direction = 1 if st.net > 0 else -1
        entry_comm = st.open_commission * closed / held
        exit_comm = commission * closed / shares
        gross = (price - st.avg) * closed * st.multiplier * direction
        net = gross - entry_comm - exit_comm
        lot = RealizedLot(
            ticker=st.ticker,
            direction="long" if direction > 0 else "short",
            exit_trade_id=t["id"],
            entry_trade_ids=list(st.entry_ids),
            shares=closed,
            avg_entry_price=st.avg,
            exit_price=price,
            multiplier=st.multiplier,
            entry_commission=entry_comm,
            exit_commission=exit_comm,
            gross_pnl=gross,
            net_pnl=net,
            cost_basis=st.avg * closed * st.multiplier,
            opened_at=st.opened_at,
            closed_at=t["timestamp"],
            currency=st.currency,
            instrument=st.instrument,
            strategy=st.strategy or t["strategy"],
        )
        realized.append(lot)
        st.realized_net += net
        st.open_commission -= entry_comm
        st.net -= direction * closed

        remainder = shares - closed
        if abs(st.net) <= EPS:
            st.reset()
        if remainder > EPS:
            # Position flip: the rest of the trade opens the opposite side.
            st.reset()
            st.open(t["id"], t["timestamp"], remainder, price,
                    commission - exit_comm, sign, t["strategy"])
            st.trade_count = 1

    positions: dict[str, dict[str, Any]] = {}
    tickers: dict[str, dict[str, Any]] = {}
    for tk, st in states.items():
        tickers[tk] = {
            "ticker": tk,
            "currency": st.currency,
            "realized_pnl": money(st.realized_net),
            "total_commission": money(st.total_commission),
            "first_trade": st.first_trade_ever,
            "last_trade": st.last_trade,
        }
        if abs(st.net) <= EPS:
            continue
        held = abs(st.net)
        per_unit_fee = st.open_commission / (held * st.multiplier) if held else ZERO
        breakeven = st.avg + per_unit_fee if st.net > 0 else st.avg - per_unit_fee
        positions[tk] = {
            "ticker": tk,
            "direction": "long" if st.net > 0 else "short",
            "net_shares": qty(st.net),
            "shares": qty(held),
            "avg_cost": precise(st.avg),
            "breakeven_price": precise(breakeven),
            "multiplier": precise(st.multiplier),
            "cost_basis": money(st.avg * held * st.multiplier),
            "open_commission": money(st.open_commission),
            "total_commission": money(st.total_commission),
            "first_trade": st.opened_at,
            "last_trade": st.last_trade,
            "trade_count": st.trade_count,
            "entry_trade_ids": list(st.entry_ids),
            "currency": st.currency,
            "instrument": st.instrument,
            "strategy": st.strategy,
            "realized_pnl": money(st.realized_net),
        }

    return LedgerResult(positions=positions, realized=realized, warnings=warnings, tickers=tickers)


def unrealized(position: dict[str, Any], price: float | Decimal | None) -> dict[str, Any]:
    """Mark a position to market. Returns market value and unrealized P&L (gross and net)."""
    if price is None:
        return {"current_price": None, "market_value": None,
                "unrealized_pnl": None, "unrealized_pnl_net": None, "unrealized_pnl_pct": None}
    p = D(price)
    held = D(position["shares"])
    mult = D(position.get("multiplier") or 1)
    avg = D(position["avg_cost"])
    direction = 1 if position.get("direction", "long") == "long" else -1
    gross = (p - avg) * held * mult * direction
    net = gross - D(position.get("open_commission") or 0)
    basis = avg * held * mult
    return {
        "current_price": float(p),
        "market_value": money(p * held * mult),
        "unrealized_pnl": money(gross),
        "unrealized_pnl_net": money(net),
        "unrealized_pnl_pct": money(gross / basis * 100) if basis else 0.0,
    }
