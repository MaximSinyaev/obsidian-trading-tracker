"""Import trades from broker exports (CSV) or from agents (JSON).

The importer only *parses and normalises* rows; every number that ends up in a
report is still computed by the ledger. Design goals:

* Works with most broker CSVs without configuration: column names are matched
  against a list of aliases (English and Russian), delimiters are sniffed,
  ``1 234,56`` style numbers and several date formats are understood.
* Presets for common brokers (``ibkr`` for Interactive Brokers Flex Query
  trades, ``tinvest`` for T-Invest / Tinkoff operation exports) only add
  aliases and value conventions on top of the generic parser.
* Idempotent: a row with a broker trade id is never imported twice; rows
  without one are de-duplicated by a fingerprint of
  (ticker, side, quantity, price, timestamp).
* ``dry_run`` parses everything and reports what *would* be imported.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from trading_tracker import db

# Canonical field → accepted column names (compared after normalising: lower
# case, spaces/underscores/dashes/dots/slashes removed).
ALIASES: dict[str, list[str]] = {
    "ticker": ["ticker", "symbol", "tickersymbol", "secid", "code", "тикер", "инструмент",
               "кодинструмента", "бумага", "актив", "underlying"],
    "action": ["action", "side", "buysell", "type", "operation", "direction", "transactiontype",
               "operationtype", "тип", "типоперации", "операция", "направление", "видсделки"],
    "shares": ["shares", "quantity", "qty", "units", "volume", "filledqty", "количество",
               "колво", "кол", "объем", "количествобумаг"],
    "price": ["price", "tradeprice", "fillprice", "executionprice", "avgprice", "averageprice",
              "цена", "ценасделки", "ценазаединицу"],
    "commission": ["commission", "commissions", "fee", "fees", "ibcommission", "brokercommission",
                   "комиссия", "комиссияброкера", "комиссиябиржи"],
    "timestamp": ["timestamp", "datetime", "date", "time", "tradedate", "tradetime", "executedat",
                  "executiontime", "filledat", "дата", "датаивремя", "датасделки", "датаисполнения",
                  "времясделки"],
    "currency": ["currency", "currencyprimary", "ccy", "валюта", "валютацены", "валютарасчетов"],
    "external_id": ["externalid", "id", "tradeid", "orderid", "executionid", "execid",
                    "transactionid", "ibexecid", "номерсделки", "номер", "идентификатор", "idсделки"],
    "multiplier": ["multiplier", "contractmultiplier", "lotsize", "мультипликатор"],
    "instrument": ["instrument", "assetclass", "assettype", "instrumenttype", "securitytype",
                   "типинструмента", "классактива"],
    "strategy": ["strategy", "setup", "стратегия"],
    "notes": ["notes", "note", "comment", "description", "комментарий", "примечание"],
    "tags": ["tags", "теги"],
    "stop_loss": ["stoploss", "stop", "sl", "стоп"],
}

BUY_WORDS = {"buy", "b", "bot", "bought", "long", "buytoopen", "buytoclose", "покупка",
             "купить", "куплено", "buy_to_open", "buy_to_cover", "cover"}
SELL_WORDS = {"sell", "s", "sld", "sold", "short", "selltoopen", "selltoclose", "продажа",
              "продать", "продано", "sell_short", "sell_to_close"}

INSTRUMENT_MAP = {
    "stk": "stock", "stock": "stock", "share": "stock", "shares": "stock", "equity": "stock",
    "акция": "stock", "акции": "stock",
    "etf": "etf", "фонд": "etf",
    "opt": "option", "option": "option", "options": "option", "опцион": "option",
    "fut": "future", "future": "future", "futures": "future", "фьючерс": "future",
    "crypto": "crypto", "crypt": "crypto",
    "bond": "bond", "облигация": "bond",
    "cash": "fx", "fx": "fx", "forex": "fx", "валюта": "fx",
}

DATE_FORMATS = [
    "%Y%m%d;%H%M%S",  # IBKR Flex
    "%Y%m%d %H%M%S",
    "%Y%m%d",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
]

PRESETS: dict[str, dict[str, Any]] = {
    "generic": {},
    "ibkr": {
        # Flex Query "Trades" section. IBCommission is negative; DateTime is 20250115;103000.
        "prefer": {"timestamp": ["datetime", "tradedate"], "external_id": ["tradeid", "ibexecid"],
                   "action": ["buysell"]},
        "source": "ibkr",
    },
    "tinvest": {
        # T-Invest (Tinkoff) operations export: Russian headers, dd.mm.yyyy dates.
        "prefer": {"timestamp": ["дата", "датаивремя"], "action": ["типоперации", "операция"]},
        "source": "tinvest",
        "dayfirst": True,
    },
}


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-./\\()#№]+", "", name.strip().lower())


def parse_number(value: Any) -> float | None:
    """Parse '1 234,56', '1,234.56', '-12.5', '(12.50)', '$10' → float. Empty → None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(" ", "").replace(" ", "").replace(" ", "")
    if not s or s in {"-", "—", "n/a", "na", "null", "none"}:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    s = re.sub(r"[^\d,.\-+eE]", "", s)
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(",") < s.rfind(".") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".") if s.count(",") == 1 else s.replace(",", "")
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if negative else n


def parse_timestamp(value: Any, *, dayfirst: bool = False, date_format: str | None = None) -> str:
    if value is None or str(value).strip() == "":
        raise ValueError("missing date")
    raw = str(value).strip()
    if date_format:
        return datetime.strptime(raw, date_format).isoformat(timespec="seconds")
    try:
        return db.normalize_timestamp(raw)
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    for fmt in (("%d/%m/%Y %H:%M:%S", "%d/%m/%Y") if dayfirst else ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y")):
        try:
            return datetime.strptime(raw, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {raw!r} (use --date-format)")


def parse_side(value: Any, quantity: float | None) -> str | None:
    if value is not None and str(value).strip():
        word = _norm(str(value))
        if word in BUY_WORDS or any(word.startswith(w) for w in ("buy", "покуп")):
            return "BUY"
        if word in SELL_WORDS or any(word.startswith(w) for w in ("sell", "продаж")):
            return "SELL"
        return None
    if quantity is not None and quantity != 0:
        return "BUY" if quantity > 0 else "SELL"
    return None


@dataclass
class ImportResult:
    dry_run: bool
    source: str
    columns: dict[str, str]
    imported: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "source": self.source,
            "columns": self.columns,
            "imported_count": len(self.imported),
            "duplicate_count": len(self.duplicates),
            "skipped_count": len(self.skipped),
            "imported": self.imported,
            "duplicates": self.duplicates,
            "skipped": self.skipped,
        }


def map_columns(headers: list[str], preset: str = "generic",
                overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Return canonical field → actual header name."""
    by_norm = {_norm(h): h for h in headers if h is not None}
    mapping: dict[str, str] = {}
    prefer = PRESETS.get(preset, {}).get("prefer", {})
    for fld, aliases in ALIASES.items():
        for alias in list(prefer.get(fld, [])) + aliases:
            if alias in by_norm and by_norm[alias] not in mapping.values():
                mapping[fld] = by_norm[alias]
                break
    for fld, col in (overrides or {}).items():
        if fld not in ALIASES:
            raise ValueError(f"unknown field {fld!r}; valid: {', '.join(ALIASES)}")
        if col not in headers:
            raise ValueError(f"column {col!r} not found; columns: {', '.join(headers)}")
        mapping[fld] = col
    return mapping


def read_rows(path: str | Path | None = None, text: str | None = None) -> tuple[list[str], list[dict[str, Any]], str]:
    """Read CSV or JSON rows. Returns (headers, rows, format)."""
    if text is None:
        p = Path(path)  # type: ignore[arg-type]
        raw = p.read_bytes()
        for enc in ("utf-8-sig", "cp1251", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    assert text is not None
    stripped = text.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        data = json.loads(stripped)
        if isinstance(data, dict):
            data = data.get("trades", [data])
        rows = [dict(r) for r in data]
        headers: list[str] = []
        for r in rows:
            for k in r:
                if k not in headers:
                    headers.append(k)
        return headers, rows, "json"
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = [r for r in reader if any((v or "").strip() for v in r.values() if isinstance(v, str))]
    return list(reader.fieldnames or []), rows, "csv"


def import_rows(
    conn: sqlite3.Connection,
    headers: list[str],
    rows: list[dict[str, Any]],
    *,
    preset: str = "generic",
    source: str | None = None,
    overrides: dict[str, str] | None = None,
    defaults: dict[str, Any] | None = None,
    dry_run: bool = False,
    date_format: str | None = None,
) -> ImportResult:
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; choose from {', '.join(PRESETS)}")
    cfg = PRESETS[preset]
    source = source or cfg.get("source") or "import"
    defaults = defaults or {}
    mapping = map_columns(headers, preset, overrides)
    missing = [f for f in ("ticker", "price", "timestamp") if f not in mapping]
    if "shares" not in mapping:
        missing.append("shares")
    if missing:
        raise ValueError(
            "could not find columns for: " + ", ".join(missing)
            + f". Columns in file: {', '.join(headers)}. Map them with --map field=Column"
        )
    result = ImportResult(dry_run=dry_run, source=source, columns=mapping)
    touched: set[str] = set()
    seen_ids: set[str] = set()

    def get(row: dict[str, Any], fld: str) -> Any:
        col = mapping.get(fld)
        return row.get(col) if col else None

    # Separate "Date" and "Time" columns: join them.
    time_col = next((h for h in headers if _norm(h) in {"time", "время", "tradetime"}), None)
    if time_col == mapping.get("timestamp"):
        time_col = None

    for i, row in enumerate(rows, start=1):
        if time_col and row.get(time_col) and mapping.get("timestamp"):
            row = {**row, mapping["timestamp"]: f"{row[mapping['timestamp']]} {row[time_col]}".strip()}
        try:
            ticker = str(get(row, "ticker") or "").strip().upper()
            if not ticker:
                raise ValueError("missing ticker")
            qty = parse_number(get(row, "shares"))
            side = parse_side(get(row, "action"), qty)
            if side is None:
                raise ValueError(f"not a buy/sell row (side={get(row, 'action')!r})")
            if qty is None or qty == 0:
                raise ValueError("missing quantity")
            price = parse_number(get(row, "price"))
            if price is None:
                raise ValueError("missing price")
            ts = parse_timestamp(get(row, "timestamp"), dayfirst=cfg.get("dayfirst", False),
                                 date_format=date_format)
            commission = abs(parse_number(get(row, "commission")) or 0.0)
            currency = str(get(row, "currency") or defaults.get("currency") or "USD").strip().upper()
            multiplier = parse_number(get(row, "multiplier")) or defaults.get("multiplier") or 1.0
            instr_raw = get(row, "instrument")
            instrument = INSTRUMENT_MAP.get(_norm(str(instr_raw)), str(instr_raw).lower()) \
                if instr_raw else defaults.get("instrument", "stock")
            ext = get(row, "external_id")
            ext = str(ext).strip() if ext not in (None, "") else None
            ext = ext or db.trade_fingerprint(ticker, side, abs(qty), abs(price), ts)
            tags_raw = get(row, "tags")
            tags = [t.strip() for t in str(tags_raw).split(",") if t.strip()] if tags_raw else []
            record = {
                "row": i, "ticker": ticker, "action": side, "shares": abs(qty), "price": abs(price),
                "commission": commission, "timestamp": ts, "currency": currency,
                "multiplier": multiplier, "instrument": instrument, "external_id": ext,
                "strategy": get(row, "strategy") or defaults.get("strategy"),
                "notes": get(row, "notes"), "tags": tags,
                "stop_loss": parse_number(get(row, "stop_loss")),
            }
        except (ValueError, TypeError) as exc:
            result.skipped.append({"row": i, "reason": str(exc)})
            continue

        if ext in seen_ids or db.find_by_external_id(conn, source, ext) is not None:
            result.duplicates.append({"row": i, "external_id": ext, "ticker": ticker})
            continue
        seen_ids.add(ext)
        if not dry_run:
            record["id"] = db.add_trade(
                conn, ticker, side, record["shares"], record["price"],
                commission=commission, timestamp=ts, currency=currency, multiplier=multiplier,
                instrument=instrument, asset_type=instrument, external_id=ext, source=source,
                strategy=record["strategy"], notes=record["notes"], tags=tags,
                stop_loss=record["stop_loss"], commit=False, rebuild=False,
            )
            touched.add(ticker)
        result.imported.append(record)

    if not dry_run:
        db.rebuild_closed_trades(conn, touched)
        conn.commit()
    return result


def import_file(conn: sqlite3.Connection, path: str | Path | None = None, *, text: str | None = None,
                **kwargs: Any) -> ImportResult:
    headers, rows, _fmt = read_rows(path, text)
    return import_rows(conn, headers, rows, **kwargs)
