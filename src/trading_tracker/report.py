"""A complete, JSON-serialisable snapshot of the journal.

This is the one structure agents, the HTML dashboard and the Obsidian export
read from. Every number in it is produced here or in the ledger, never by an
LLM. Money is always reported per currency; nothing is silently converted.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from trading_tracker import analytics, db, ledger, prices

SCHEMA_VERSION = 1


def _r(x: float | None, n: int = 2) -> float | None:
    return None if x is None else round(x, n)


def _in_range(ts: str | None, start: str | None, end: str | None) -> bool:
    if ts is None:
        return False
    if start and ts[: len(start)] < start:
        return False
    if end and ts[: len(end)] > end:
        return False
    return True


def equity_curve(closed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Daily cumulative realized P&L with drawdown from the running peak."""
    by_day: dict[str, float] = defaultdict(float)
    for c in closed:
        by_day[c["closed_at"][:10]] += c["net_pnl"]
    out = []
    equity = peak = 0.0
    for day in sorted(by_day):
        equity += by_day[day]
        peak = max(peak, equity)
        out.append({"date": day, "pnl": _r(by_day[day]), "equity": _r(equity),
                    "drawdown": _r(equity - peak)})
    return out


def max_drawdown(curve: list[dict[str, Any]]) -> dict[str, Any]:
    if not curve:
        return {"amount": 0.0, "peak_date": None, "trough_date": None}
    worst, peak_date, trough_date = 0.0, None, None
    running_peak, running_peak_date = 0.0, curve[0]["date"]
    for p in curve:
        if p["equity"] >= running_peak:
            running_peak, running_peak_date = p["equity"], p["date"]
        dd = p["equity"] - running_peak
        if dd < worst:
            worst, peak_date, trough_date = dd, running_peak_date, p["date"]
    return {"amount": _r(worst), "peak_date": peak_date, "trough_date": trough_date}


def _group(closed: list[dict[str, Any]], key) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in closed:
        for k in key(c):
            groups[k].append(c)
    rows = []
    for k, items in groups.items():
        s = analytics.compute_stats(items)
        rows.append({"key": k, "trades": s["total_trades"], "wins": s["wins"],
                     "win_rate": s["win_rate"], "net_pnl": s["total_pnl"],
                     "avg_pnl": s["avg_pnl"], "profit_factor": s["profit_factor"]})
    rows.sort(key=lambda r: r["net_pnl"], reverse=True)
    return rows


def _summary(closed: list[dict[str, Any]], income: list[dict[str, Any]],
             open_positions: list[dict[str, Any]]) -> dict[str, Any]:
    s = analytics.compute_stats(closed)
    curve = equity_curve(closed)
    gross = sum(c["gross_pnl"] for c in closed)
    rs = [c["r_multiple"] for c in closed if c.get("r_multiple") is not None]
    holds_w = [c["hold_duration_days"] for c in closed if c["net_pnl"] > 0 and c.get("hold_duration_days") is not None]
    holds_l = [c["hold_duration_days"] for c in closed if c["net_pnl"] <= 0 and c.get("hold_duration_days") is not None]
    unreal = [p["unrealized_pnl"] for p in open_positions if p.get("unrealized_pnl") is not None]
    dividends = sum(i["net"] for i in income)
    return {
        "closed_trades": s["total_trades"],
        "wins": s["wins"],
        "losses": s["losses"],
        "win_rate": s["win_rate"],
        "gross_pnl": _r(gross),
        "commissions": s["total_commission"],
        "net_pnl": s["total_pnl"],
        "dividends": _r(dividends),
        "net_pnl_with_income": _r(s["total_pnl"] + dividends),
        "avg_win": s["avg_win"],
        "avg_loss": s["avg_loss"],
        "best_trade": s["best_trade"],
        "worst_trade": s["worst_trade"],
        "profit_factor": s["profit_factor"],
        "expectancy": s["expectancy"],
        "avg_hold_days_winners": _r(sum(holds_w) / len(holds_w), 1) if holds_w else None,
        "avg_hold_days_losers": _r(sum(holds_l) / len(holds_l), 1) if holds_l else None,
        "avg_r": _r(sum(rs) / len(rs)) if rs else None,
        "trades_with_r": len(rs),
        "max_drawdown": max_drawdown(curve),
        "open_positions": len(open_positions),
        "open_cost_basis": _r(sum(p["cost_basis"] for p in open_positions)),
        "unrealized_pnl": _r(sum(unreal)) if unreal else None,
        "positions_without_price": sum(1 for p in open_positions if p.get("current_price") is None),
    }


def _split_factor(events: list[dict[str, Any]], ticker: str, after: str, until: str | None = None) -> float:
    """Product of split ratios for ``ticker`` strictly after ``after`` (and up to ``until``)."""
    f = 1.0
    for e in events:
        if e["kind"] == "split" and e["ticker"] == ticker and e["timestamp"] > after[:19] \
                and (until is None or e["timestamp"] <= until) and e.get("ratio"):
            f *= float(e["ratio"])
    return f


def _adjusted_stop(entries: list[dict[str, Any]], events: list[dict[str, Any]], ticker: str,
                   until: str | None = None, latest: bool = True) -> float | None:
    """Stop-loss from the entry fills (latest, or the initial one for R), split-adjusted."""
    for t in (reversed(entries) if latest else entries):
        if t.get("stop_loss"):
            return t["stop_loss"] / _split_factor(events, ticker, t["timestamp"], until)
    return None


def build_report(
    conn: sqlite3.Connection,
    *,
    start: str | None = None,
    end: str | None = None,
    ticker: str | None = None,
    strategy: str | None = None,
    price_provider: str = "cache",
) -> dict[str, Any]:
    """Build the full snapshot. Filters apply to realized trades by close date."""
    result = db.run_ledger(conn)
    trades_by_id = {t["id"]: t for t in db.get_trades(conn)}
    events = db.get_events(conn)
    closed_rows = {r["exit_trade_id"]: r for r in db.get_closed_trades(conn)}

    closed: list[dict[str, Any]] = []
    for lot in result.realized:
        row = lot.to_row()
        cached = closed_rows.get(lot.exit_trade_id, {})
        for k in ("what_worked", "what_failed", "lesson", "rating"):
            row[k] = cached.get(k)
        exit_t = trades_by_id.get(lot.exit_trade_id, {})
        entries = [trades_by_id[i] for i in lot.entry_trade_ids if i in trades_by_id]
        tags: list[str] = []
        for t in entries + [exit_t]:
            try:
                for tag in json.loads(t.get("tags") or "[]"):
                    if tag not in tags:
                        tags.append(tag)
            except (TypeError, json.JSONDecodeError):
                pass
        row["tags"] = tags
        row["setup"] = next((t.get("setup") for t in entries if t.get("setup")), None)
        row["confidence"] = next((t.get("confidence") for t in entries if t.get("confidence")), None)
        row["entry_notes"] = next((t.get("notes") for t in entries if t.get("notes")), None)
        row["exit_notes"] = exit_t.get("notes")
        stop = _adjusted_stop(entries, events, lot.ticker, lot.closed_at, latest=False)
        row["stop_loss"] = _r(stop, 6)
        risk = abs(float(lot.avg_entry_price) - stop) * float(lot.shares) * float(lot.multiplier) if stop else None
        row["initial_risk"] = _r(risk)
        row["r_multiple"] = _r(row["net_pnl"] / risk) if risk else None
        if ticker and row["ticker"] != ticker.upper():
            continue
        if strategy and (row.get("strategy") or "") != strategy:
            continue
        if not _in_range(row["closed_at"], start, end):
            continue
        closed.append(row)
    closed.sort(key=lambda c: (c["closed_at"], c["exit_trade_id"]))

    positions = [p for k, p in sorted(result.positions.items())
                 if not ticker or k == ticker.upper()]
    quote_error = None
    try:
        quotes = prices.get_quotes(conn, [p["ticker"] for p in positions], price_provider)
    except prices.ProviderUnavailable as exc:
        quote_error = str(exc)
        quotes = prices.get_quotes(conn, [p["ticker"] for p in positions], "cache")
    open_positions = []
    for p in positions:
        q = quotes.get(p["ticker"])
        mark = ledger.unrealized(p, q["price"] if q else None)
        entries = [trades_by_id[i] for i in p["entry_trade_ids"] if i in trades_by_id]
        stop = _adjusted_stop(entries, events, p["ticker"])
        sign = 1 if p["direction"] == "long" else -1
        # Money lost if the stop fills (negative = profit locked in by the stop).
        risk = (p["avg_cost"] - stop) * sign * p["shares"] * p["multiplier"] if stop else None
        tags: list[str] = []
        for t in entries:
            try:
                tags += [x for x in json.loads(t.get("tags") or "[]") if x not in tags]
            except (TypeError, json.JSONDecodeError):
                pass
        open_positions.append({
            **p, **mark, "price_as_of": q["as_of"] if q else None,
            "price_source": q["source"] if q else None,
            "stop_loss": stop, "risk_to_stop": _r(risk),
            "target": next((t.get("target_1") for t in reversed(entries) if t.get("target_1")), None),
            "thesis": next((t.get("notes") for t in entries if t.get("notes")), None),
            "tags": tags,
        })

    income = [i for i in result.income
              if (not ticker or i["ticker"] == ticker.upper()) and _in_range(i["timestamp"], start, end)]
    journal = [j for j in db.get_journal(conn, ticker=ticker)
               if _in_range(j["date"], start, end)]

    currencies = sorted({c["currency"] for c in closed} | {p["currency"] for p in open_positions}
                        | {i["currency"] for i in income}) or ["USD"]
    by_currency: dict[str, Any] = {}
    for ccy in currencies:
        c_closed = [c for c in closed if c["currency"] == ccy]
        c_income = [i for i in income if i["currency"] == ccy]
        c_open = [p for p in open_positions if p["currency"] == ccy]
        curve = equity_curve(c_closed)
        by_currency[ccy] = {
            "summary": _summary(c_closed, c_income, c_open),
            "equity_curve": curve,
            "by_month": [dict(r, month=r.pop("key")) for r in
                         sorted(_group(c_closed, lambda c: [c["closed_at"][:7]]), key=lambda r: r["key"])],
            "by_ticker": [dict(r, ticker=r.pop("key")) for r in _group(c_closed, lambda c: [c["ticker"]])],
            "by_strategy": [dict(r, strategy=r.pop("key")) for r in
                            _group(c_closed, lambda c: [c.get("strategy") or "unspecified"])],
            "by_tag": [dict(r, tag=r.pop("key")) for r in _group(c_closed, lambda c: c["tags"] or ["untagged"])],
            "by_instrument": [dict(r, instrument=r.pop("key")) for r in
                              _group(c_closed, lambda c: [c.get("instrument") or "stock"])],
            "by_origin": [dict(r, origin=r.pop("key")) for r in _group(c_closed, _origin)],
            "by_weekday": [dict(r, weekday=r.pop("key")) for r in
                           _group(c_closed, lambda c: [_weekday(c["closed_at"])])],
        }

    warnings = list(result.warnings)
    no_stop = [p["ticker"] for p in open_positions if p["stop_loss"] is None]
    if no_stop:
        warnings.append("open positions without a stop-loss: " + ", ".join(no_stop))
    if quote_error:
        warnings.append(quote_error)
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "filters": {"from": start, "to": end, "ticker": ticker, "strategy": strategy},
        "currencies": currencies,
        "by_currency": by_currency,
        "open_positions": open_positions,
        "closed_trades": closed,
        "income": income,
        "journal": journal,
        "trade_count": len(trades_by_id),
        "warnings": warnings,
        "method": "average cost; net P&L = gross - entry commission share - exit commission share",
    }


def _origin(c: dict[str, Any]) -> list[str]:
    """'ai' when a tag is 'ai' or starts with 'ai-' / 'ai_' (ai-idea, ai-bullish...), else 'manual'."""
    def is_ai(tag: str) -> bool:
        t = str(tag).lower()
        return t == "ai" or t.startswith(("ai-", "ai_"))
    return ["ai" if any(is_ai(t) for t in c.get("tags") or []) else "manual"]


def _weekday(ts: str) -> str:
    try:
        return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][date.fromisoformat(ts[:10]).weekday()]
    except ValueError:
        return "?"
