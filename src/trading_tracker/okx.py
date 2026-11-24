"""OKX "Trading History" (bill history) export: perpetual swaps, liquidations and funding.

The export lists every bill: fills (``Buy``/``Sell``/``Liquidation buy|sell``), the extra
``Liquidation fees`` row of a liquidation, ``Funding fee income|expense`` and margin
transfers. Quantities are in **contracts**; one contract of ``ETH-USDT-SWAP`` is 0.1 ETH
(its contract value, ``ctVal``). The ledger books ``contracts × price × ctVal``, so ctVal is
the trade's multiplier.

The export does not carry ctVal. Known instruments use the table below. For any other
instrument ctVal is solved from the file itself: OKX prints the realized ``PnL`` of every
closing fill, which is linear in ctVal, so one number per instrument must explain every
close. The fitted value is snapped to a round contract size and then checked against
every closing fill; the import reports how many closes reconcile.
"""

from __future__ import annotations

import csv
import io
import math
import re
import sqlite3
from collections import Counter
from statistics import median
from typing import Any

from trading_tracker import db

# Contract values (base coin per contract) of common OKX USDT-margined perpetuals.
KNOWN_CTVAL: dict[str, float] = {
    "BTC-USDT-SWAP": 0.01, "ETH-USDT-SWAP": 0.1, "SOL-USDT-SWAP": 1, "XRP-USDT-SWAP": 100,
    "DOGE-USDT-SWAP": 1000, "LTC-USDT-SWAP": 1, "BCH-USDT-SWAP": 0.1, "ADA-USDT-SWAP": 100,
    "LINK-USDT-SWAP": 1, "DOT-USDT-SWAP": 1, "AVAX-USDT-SWAP": 1, "TRX-USDT-SWAP": 1000,
    "BTC-USD-SWAP": 100, "ETH-USD-SWAP": 10,
}

TRADE_ACTIONS = {"buy": "BUY", "sell": "SELL", "liquidation buy": "BUY", "liquidation sell": "SELL"}


def is_okx_bills(headers: list[str]) -> bool:
    norm = {h.lstrip("\ufeff").strip().lower() for h in headers}
    return {"trading unit", "position balance", "fee unit", "filled price", "action"} <= norm


def _f(value: Any) -> float:
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _tz_suffix(preamble: str) -> str:
    """'Time Zone:UTC+9' → '+09:00'. Empty when the file does not say."""
    m = re.search(r"Time Zone:\s*UTC\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", preamble)
    if not m:
        return ""
    return f"{m.group(1)}{int(m.group(2)):02d}:{m.group(3) or '00'}"


def _nice(value: float) -> float:
    """Snap a fitted contract value to 1, 2 or 5 × 10^k when it is within 1.5%."""
    if value <= 0:
        return value
    k = math.floor(math.log10(value))
    best = min((c * 10 ** e for e in (k - 1, k, k + 1) for c in (1, 2, 5)), key=lambda c: abs(math.log(value / c)))
    return best if abs(best / value - 1) <= 0.015 else float(f"{value:.6g}")


def _unit_closes(fills: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Replay one instrument with multiplier 1. Returns (unit gross, OKX PnL) for every close."""
    net = avg = 0.0
    out: list[tuple[float, float]] = []
    for f in fills:
        q = f["qty"] if f["side"] == "BUY" else -f["qty"]
        if net == 0 or (net > 0) == (q > 0):
            avg = (avg * abs(net) + f["price"] * abs(q)) / (abs(net) + abs(q))
            net += q
            continue
        closed = min(abs(q), abs(net))
        out.append(((f["price"] - avg) * closed * (1 if net > 0 else -1), f["pnl"]))
        if abs(q) > abs(net) + 1e-12:  # flip: the rest opens the other side at this price
            net, avg = (abs(q) - abs(net)) * (1 if q > 0 else -1), f["price"]
        else:
            net += q
            if abs(net) <= 1e-12:
                net = avg = 0.0
    return out


def _matches(fills: list[dict[str, Any]], ct: float) -> int:
    return sum(1 for u, p in _unit_closes(fills) if abs(u) > 1e-9 and p != 0
               and abs(u * ct - p) <= max(0.011, abs(p) * 1e-4))


def fit_ctval(fills: list[dict[str, Any]]) -> tuple[float | None, int, int]:
    """Solve ctVal from the PnL OKX printed on closing fills. Returns (ctVal, matched, closes)."""
    closes = [(u, p) for u, p in _unit_closes(fills) if abs(u) > 1e-9 and p != 0]
    if not closes:
        return None, 0, 0
    # The most common snapped ratio: closes after a replay divergence scatter, real ones agree.
    snapped = Counter(_nice(p / u) for u, p in closes if p / u > 0)
    ct = snapped.most_common(1)[0][0] if snapped else _nice(median(p / u for u, p in closes))
    return ct, _matches(fills, ct), len(closes)


def read_okx(text: str) -> tuple[str, list[dict[str, Any]]]:
    lines = text.splitlines()
    preamble = lines[0] if lines and not is_okx_bills(next(csv.reader([lines[0]]))) else ""
    body = "\n".join(lines[1:] if preamble else lines)
    reader = csv.DictReader(io.StringIO(body))
    rows = [{(k or "").lstrip("\ufeff").strip(): (v or "").lstrip("\ufeff").strip() for k, v in r.items()}
            for r in reader]
    return _tz_suffix(preamble), rows


def import_okx(conn: sqlite3.Connection, text: str, result: Any, *, dry_run: bool = False,
               source: str = "okx", multipliers: dict[str, float] | None = None) -> None:
    """Import an OKX bill-history CSV into ``result`` (an importers.ImportResult)."""
    tz, rows = read_okx(text)
    rows.reverse()  # the export is newest first
    rows.sort(key=lambda r: r.get("Time", ""))  # stable: keeps order inside a second
    liq_fees: dict[str, float] = {}
    fills: list[dict[str, Any]] = []
    funding: list[dict[str, Any]] = []
    for i, r in enumerate(rows, start=1):
        action = r.get("Action", "").strip().lower()
        ts = db.normalize_timestamp(f"{r['Time'].replace(' ', 'T')}{tz}") if r.get("Time") else None
        if action in TRADE_ACTIONS:
            fills.append({"row": i, "id": r.get("id"), "order": r.get("Order id"), "ticker": r["Symbol"].upper(),
                          "side": TRADE_ACTIONS[action], "qty": abs(_f(r.get("Amount"))),
                          "price": _f(r.get("Filled Price")), "fee": abs(_f(r.get("Fee"))),
                          "fee_ccy": (r.get("Fee Unit") or "USDT").upper(), "pnl": _f(r.get("PnL")), "ts": ts,
                          "liquidation": action.startswith("liquidation")})
        elif action == "liquidation fees":
            liq_fees[r.get("Order id", "")] = liq_fees.get(r.get("Order id", ""), 0.0) + abs(_f(r.get("PnL")) or _f(r.get("Fee")) or _f(r.get("Balance Change")))
        elif action.startswith("funding fee"):
            amount = _f(r.get("PnL")) or _f(r.get("Balance Change"))
            funding.append({"row": i, "id": r.get("id"), "ticker": r["Symbol"].upper(), "amount": amount,
                            "ccy": (r.get("Balance Unit") or r.get("Fee Unit") or "USDT").upper(), "ts": ts})
        else:
            result.skipped.append({"row": i, "reason": f"{r.get('Action')!r} is not a trade (margin or transfer)"})

    # Contract value per instrument.
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for f in fills:
        by_ticker.setdefault(f["ticker"], []).append(f)
    ctval: dict[str, float] = {}
    total_closes = total_matched = 0
    unresolved = []
    for tk, fl in by_ticker.items():
        fitted, matched, closes = fit_ctval(fl)
        ct = fitted
        for cand in ((multipliers or {}).get(tk), KNOWN_CTVAL.get(tk)):
            if not cand:
                continue
            m = _matches(fl, cand)
            if cand == (multipliers or {}).get(tk) or m >= matched:  # an explicit value always wins
                ct, matched = cand, m
                break
        if ct is None:
            ct = 1.0
            unresolved.append(tk)
        ctval[tk] = ct
        total_closes += closes
        total_matched += matched
    result.reconciliation = {"closing_fills": total_closes, "match_okx_pnl": total_matched,
                             "contract_values": {k: ctval[k] for k in sorted(ctval)}}
    result.notes.append(f"OKX: {total_matched} of {total_closes} closing fills reconcile with the PnL column "
                        f"of the export (gross P&L, before fees)")
    if unresolved:
        result.notes.append("contract value unknown (no closes to solve it from), assumed 1 for: "
                            + ", ".join(sorted(unresolved)) + "; fix with `trade edit ID --multiplier`")

    touched: set[str] = set()
    for f in fills:
        ext = f"bill:{f['id']}"
        if db.find_by_external_id(conn, source, ext) is not None:
            result.duplicates.append({"row": f["row"], "external_id": ext, "ticker": f["ticker"]})
            continue
        fee = f["fee"] + (liq_fees.pop(f["order"], 0.0) if f["liquidation"] else 0.0)
        record = {"row": f["row"], "ticker": f["ticker"], "action": f["side"], "shares": f["qty"],
                  "price": f["price"], "commission": round(fee, 10), "timestamp": f["ts"], "currency": f["fee_ccy"],
                  "multiplier": ctval[f["ticker"]], "instrument": "future", "external_id": ext,
                  "notes": "liquidation" if f["liquidation"] else None, "tags": ["liquidation"] if f["liquidation"] else []}
        if f["qty"] <= 0:
            result.skipped.append({"row": f["row"], "reason": "zero quantity"})
            continue
        if not dry_run:
            record["id"] = db.add_trade(
                conn, f["ticker"], f["side"], f["qty"], f["price"], commission=record["commission"],
                timestamp=f["ts"], currency=f["fee_ccy"], multiplier=ctval[f["ticker"]], instrument="future",
                asset_type="future", external_id=ext, source=source, notes=record["notes"], tags=record["tags"],
                commit=False, rebuild=False)
            touched.add(f["ticker"])
        result.imported.append(record)

    for fu in funding:
        ext = f"funding:{fu['id']}"
        if conn.execute("SELECT 1 FROM events WHERE source = ? AND external_id = ?", (source, ext)).fetchone():
            continue
        if not dry_run:
            db.add_event(conn, fu["ticker"], "funding", amount=fu["amount"], currency=fu["ccy"],
                         timestamp=fu["ts"], source=source, external_id=ext, commit=False, rebuild=False)
            touched.add(fu["ticker"])
        result.events.append({"kind": "funding", "ticker": fu["ticker"], "amount": fu["amount"],
                              "currency": fu["ccy"], "date": (fu["ts"] or "")[:10]})
    if funding:
        total = sum(fu["amount"] for fu in funding)
        result.notes.append(f"OKX: {len(funding)} funding payments recorded as `funding` events "
                            f"(net {total:+.2f}); they are reported next to trade P&L, not inside it")
    if not dry_run:
        db.rebuild_closed_trades(conn, touched)
        conn.commit()
