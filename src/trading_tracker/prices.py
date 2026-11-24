"""Market prices for marking open positions.

Prices are *inputs*, never computed: they come from a provider and are stored
in the ``prices`` table with their source and timestamp, so every unrealized
P&L number can be traced back to a quote.

Providers:

* ``cache`` (default) — whatever was last stored, e.g. by an AI agent that
  fetched a quote with its own market-data tool and ran
  ``trade price set AAPL 191.20 --source agensi``.
* ``yahoo`` — Yahoo Finance through ``yfinance`` (optional extra:
  ``pip install 'agent-trading-journal[yahoo]'``). Fetched quotes are cached.
* ``none`` — do not mark to market.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from trading_tracker import db

PROVIDERS = ("cache", "yahoo", "none")


class ProviderUnavailable(RuntimeError):
    pass


def yahoo_available() -> bool:
    try:
        import yfinance  # noqa: F401
    except ImportError:
        return False
    return True


def fetch_yahoo(tickers: list[str]) -> dict[str, float | None]:
    if not yahoo_available():
        raise ProviderUnavailable(
            "Yahoo provider needs yfinance: pip install 'agent-trading-journal[yahoo]' "
            "(or set prices yourself with `trade price set TICKER PRICE`)"
        )
    from trading_tracker.analytics import fetch_live_prices

    return fetch_live_prices(tickers)


def get_quotes(conn: sqlite3.Connection, tickers: list[str], provider: str = "cache") -> dict[str, dict[str, Any]]:
    """Return ticker → {price, as_of, source} for the requested tickers (missing → absent)."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown price provider {provider!r}; choose from {', '.join(PROVIDERS)}")
    if provider == "none" or not tickers:
        return {}
    if provider == "yahoo":
        fetched = fetch_yahoo(tickers)
        now = datetime.now().isoformat(timespec="seconds")
        for t, p in fetched.items():
            if p is not None:
                db.set_price(conn, t, p, as_of=now, source="yahoo")
    cached = db.get_prices(conn)
    return {t: cached[t.upper()] for t in tickers if t.upper() in cached}
