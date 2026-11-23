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
    "price": ["price", "tprice", "tradeprice", "fillprice", "executionprice", "avgprice", "averageprice",
              "цена", "ценасделки", "ценазаединицу"],
    "commission": ["commission", "commissions", "fee", "fees", "ibcommission", "commfee", "brokercommission",
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
    "commission_currency": ["commissioncurrency", "commcurrency", "feecurrency", "ibcommissioncurrency",
                            "валютакомиссии"],
}

# "long"/"short" are deliberately absent: in many exports they describe a position, not a fill.
BUY_WORDS = {"buy", "b", "bot", "bought", "buytoopen", "buytoclose", "покупка",
             "купить", "куплено", "buy_to_open", "buy_to_cover", "cover"}
SELL_WORDS = {"sell", "s", "sld", "sold", "selltoopen", "selltoclose", "продажа",
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
    "%Y-%m-%d, %H:%M:%S",  # IBKR activity statement
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
        "decimal_comma": True,
    },
    "freedom": {
        # Freedom Finance / Freedom24 / Tradernet trade report (CSV or XLS): Russian or English
        # headers, "1 555 200,00" numbers, dd.mm.yyyy dates, tickers like AAPL.US.
        # The JSON report ("trades.detailed") uses short keys: p, q, instr_nm, curr_c.
        "prefer": {"timestamp": ["датасделки", "tradedate", "date"],
                   "external_id": ["idсделки", "tradeid", "номерсделки"],
                   "action": ["операция", "operation"], "ticker": ["тикер", "ticker", "instrnm"],
                   "price": ["цена", "p"], "shares": ["колво", "q"], "currency": ["валюта", "currc"],
                   "instrument": ["instrkind", "типбумаги"]},
        "ignore": ["notes"],  # the JSON "comment" is boilerplate (market, fee tariff)
        "option_multiplier": 100,
        "source": "freedom",
        "dayfirst": True,
        "decimal_comma": True,
        "strip_suffixes": [".US"],  # US listings match manual `trade add AAPL`; other markets keep theirs
        "skip_fx": True,
    },
}

# Operations that look like a sale or purchase but are not trades.
NON_TRADE_WORDS = ("репо", "repo", "конвертац", "conversion", "dividend", "дивиденд", "купон",
                   "coupon", "налог", "tax", "fee", "commission", "комиссия", "transfer", "перевод")


def parse_instrument(value: Any, default: str = "stock") -> str:
    if value is None or str(value).strip() == "":
        return default
    word = _norm(str(value))
    if word in INSTRUMENT_MAP:
        return INSTRUMENT_MAP[word]
    for needle, kind in (("опцион", "option"), ("option", "option"), ("фьючерс", "future"),
                         ("future", "future"), ("etf", "etf"), ("фонд", "etf"), ("облигац", "bond"),
                         ("bond", "bond"), ("акци", "stock"), ("валют", "fx"), ("crypto", "crypto")):
        if needle in word:
            return kind
    return str(value).strip().lower()


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-./\\()#№]+", "", name.strip().lower())


def parse_number(value: Any, decimal_comma: bool | None = None) -> float | None:
    """Parse '1 234,56', '1,234.56', '-12.5', '(12.50)', '$10' → float. Empty → None.

    A single comma is ambiguous: '1,000' is a thousand in US exports and 1.0 in
    Russian ones. With ``decimal_comma=None`` a comma followed by exactly three
    digits is read as a thousands separator; presets for comma-decimal brokers
    pass ``decimal_comma=True``.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(" ", "").replace(" ", "").replace(" ", "")
    if not s or s in {"-", "—", "n/a", "na", "null", "none"}:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    # Keep scientific notation (1.2E-5) but drop unit suffixes such as "1.20000000ETH" or "10 USD".
    m = re.match(r"^[^\d\-+.,]*([\-+]?[\d.,]+(?:[eE][\-+]?\d+)?)", s)
    s = m.group(1) if m else ""
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(",") < s.rfind(".") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        if s.count(",") > 1:
            s = s.replace(",", "")
        elif decimal_comma or (decimal_comma is None and not re.search(r",\d{3}$", s)):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
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
        if any(w in word for w in NON_TRADE_WORDS):
            return None
        # "Market buy", "Limit sell", "Stop sell" (Trading 212), "buy_to_open"...
        if word in BUY_WORDS or word.startswith(("buy", "покуп")) or word.endswith("buy"):
            return "BUY"
        if word in SELL_WORDS or word.startswith(("sell", "продаж")) or word.endswith("sell"):
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
    notes: list[str] = field(default_factory=list)
    dividends: list[dict[str, Any]] = field(default_factory=list)

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
            "dividend_count": len(self.dividends),
            "dividends": self.dividends,
            "notes": self.notes,
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
    for fld in PRESETS.get(preset, {}).get("ignore", []):
        if fld not in (overrides or {}):
            mapping.pop(fld, None)
    return mapping


def _header_score(cells: list[Any]) -> int:
    known = {a for aliases in ALIASES.values() for a in aliases}
    return sum(1 for c in cells if c is not None and _norm(str(c)) in known)


def _rows_from_table(table: list[list[Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """Find the header row (exports often start with a preamble) and build dict rows.

    Handles Interactive Brokers activity statements, where every line starts with a
    section name and a Header/Data marker: only Data lines of the chosen section are kept.
    """
    table = [r for r in table if any(c not in (None, "") for c in r)]
    if not table:
        return [], []
    best = max(range(min(len(table), 40)), key=lambda i: (_header_score(table[i]), -i))
    header = [str(c).strip() if c is not None else "" for c in table[best]]
    section = None
    if len(header) > 2 and header[1].lower() == "header":
        section = header[0]
    rows: list[dict[str, Any]] = []
    for r in table[best + 1:]:
        cells = [c.strip() if isinstance(c, str) else c for c in r]
        if section is not None:
            if len(cells) < 2 or cells[0] != section or str(cells[1]).lower() != "data":
                continue
        elif [str(c) if c is not None else "" for c in cells[: len(header)]] == header:
            continue  # repeated header line
        rows.append({h: (cells[i] if i < len(cells) else None) for i, h in enumerate(header) if h})
    return [h for h in header if h], rows


def _read_xlsx(path: Path) -> list[list[Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise ValueError("Reading .xlsx needs openpyxl: pip install openpyxl") from exc
    wb = load_workbook(path, read_only=True, data_only=True)
    best: list[list[Any]] = []
    for ws in wb.worksheets:  # pick the sheet that looks most like a trade list
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        head = max((_header_score(r) for r in rows[:40]), default=0)
        if head > (max((_header_score(r) for r in best[:40]), default=0) if best else -1):
            best = rows
    out = []
    for r in best:
        out.append([c.isoformat(sep=" ") if hasattr(c, "isoformat") else c for c in r])
    return out


def read_rows(path: str | Path | None = None, text: str | None = None) -> tuple[list[str], list[dict[str, Any]], str]:
    """Read CSV, XLSX or JSON rows. Returns (headers, rows, format)."""
    if text is None:
        p = Path(path)  # type: ignore[arg-type]
        if p.suffix.lower() in (".xlsx", ".xlsm"):
            headers, rows = _rows_from_table(_read_xlsx(p))
            return headers, rows, "xlsx"
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
        if is_freedom_report(data):
            data = data["trades"]["detailed"]
            fmt = "freedom-json"
        else:
            fmt = "json"
        if isinstance(data, dict):
            data = data.get("trades", [data])
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise ValueError("JSON must be a list of trade objects or {\"trades\": [...]}")
        rows = [dict(r) for r in data]
        headers: list[str] = []
        for r in rows:
            for k in r:
                if k not in headers:
                    headers.append(k)
        return headers, rows, fmt
    lines = text.splitlines()
    sample = "\n".join(lines[:60])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    table = list(csv.reader(io.StringIO(text), dialect=dialect))
    headers, rows = _rows_from_table(table)
    return headers, rows, "csv"


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
    fee_skipped: dict[tuple[str, str], int] = {}
    fp_count: dict[str, int] = {}

    dc = cfg.get("decimal_comma")

    def num(v: Any) -> float | None:
        return parse_number(v, dc)

    def get(row: dict[str, Any], fld: str) -> Any:
        col = mapping.get(fld)
        return row.get(col) if col else None

    # Separate "Date" and "Time" columns: join them.
    time_col = next((h for h in headers if _norm(h) in {"time", "время", "tradetime"}), None)
    if time_col == mapping.get("timestamp"):
        time_col = None

    # Many brokers export newest-first. Fills sharing a timestamp are replayed in
    # insertion order, so import oldest-first: reverse files sorted descending.
    numbered = list(enumerate(rows, start=1))
    stamps = []
    for _, row in numbered:
        try:
            raw = get(row, "timestamp")
            if time_col and row.get(time_col):
                raw = f"{raw} {row[time_col]}"
            stamps.append(parse_timestamp(raw, dayfirst=cfg.get("dayfirst", False), date_format=date_format))
        except (ValueError, TypeError):
            continue
    reversed_order = len(stamps) > 1 and stamps[0] > stamps[-1]
    if reversed_order:
        numbered.reverse()
        result.notes.append("file is sorted newest-first; rows were imported oldest-first")

    for i, row in numbered:
        if time_col and row.get(time_col) and mapping.get("timestamp"):
            row = {**row, mapping["timestamp"]: f"{row[mapping['timestamp']]} {row[time_col]}".strip()}
        try:
            ticker = str(get(row, "ticker") or "").strip().upper()
            if preset == "freedom":
                ticker = ticker.lstrip("+")
            if not ticker:
                raise ValueError("missing ticker")
            qty = num(get(row, "shares"))
            side = parse_side(get(row, "action"), qty)
            for suffix in cfg.get("strip_suffixes", []):
                if ticker.endswith(suffix):
                    ticker = ticker[: -len(suffix)]
            if cfg.get("skip_fx") and ("/" in ticker or parse_instrument(get(row, "instrument"), "") == "fx"):
                raise ValueError(f"currency conversion {ticker}, not a trade")
            if side is None:
                raw_side = get(row, "action")
                hint = ""
                if raw_side and any(w in _norm(str(raw_side)) for w in ("dividend", "дивиденд")):
                    hint = "; record it with `trade event dividend TICKER AMOUNT --tax T --date D`"
                raise ValueError(f"not a buy/sell row (side={raw_side!r}){hint}")
            if qty is None or qty == 0:
                raise ValueError("missing quantity")
            price = num(get(row, "price"))
            if price is None:
                raise ValueError("missing price")
            ts = parse_timestamp(get(row, "timestamp"), dayfirst=cfg.get("dayfirst", False),
                                 date_format=date_format)
            commission = abs(num(get(row, "commission")) or 0.0)
            currency = str(get(row, "currency") or defaults.get("currency") or "USD").strip().upper()
            fee_note = None
            fee_ccy = str(get(row, "commission_currency") or "").strip().upper()
            if commission and fee_ccy and fee_ccy != currency:
                # Never convert silently: keep the fee out of P&L and say so.
                fee_note = f"commission {commission:g} {fee_ccy} not included (trade currency {currency})"
                fee_skipped[(fee_ccy, currency)] = fee_skipped.get((fee_ccy, currency), 0) + 1
                commission = 0.0
            multiplier = num(get(row, "multiplier")) or defaults.get("multiplier") or 1.0
            instrument = parse_instrument(get(row, "instrument"), defaults.get("instrument", "stock"))
            if instrument == "option" and get(row, "multiplier") in (None, "") and cfg.get("option_multiplier"):
                multiplier = float(cfg["option_multiplier"])
            ext = get(row, "external_id")
            ext = str(ext).strip() if ext not in (None, "") else None
            if ext is None:
                # Identical fills without a broker id are legitimate (two 10-share lots at the same
                # second): number repeats within the file so re-imports still deduplicate.
                fp = db.trade_fingerprint(ticker, side, abs(qty), abs(price), ts)
                fp_count[fp] = fp_count.get(fp, 0) + 1
                ext = fp if fp_count[fp] == 1 else f"{fp}#{fp_count[fp]}"
            tags_raw = get(row, "tags")
            if isinstance(tags_raw, (list, tuple)):
                tags = [str(t).strip() for t in tags_raw if str(t).strip()]
            else:
                tags = [t.strip() for t in str(tags_raw).split(",") if t.strip()] if tags_raw else []
            notes_raw = get(row, "notes")
            if notes_raw is not None and not isinstance(notes_raw, str):
                notes_raw = json.dumps(notes_raw, ensure_ascii=False)
            record = {
                "row": i, "ticker": ticker, "action": side, "shares": abs(qty), "price": abs(price),
                "commission": commission, "timestamp": ts, "currency": currency,
                "multiplier": multiplier, "instrument": instrument, "external_id": ext,
                "strategy": get(row, "strategy") or defaults.get("strategy"),
                "notes": "; ".join(x for x in (notes_raw, fee_note) if x) or None, "tags": tags,
                "stop_loss": num(get(row, "stop_loss")),
            }
            record["currency"] = currency = db.normalize_currency(currency)
            db._validate_trade(side, abs(qty), abs(price), commission, multiplier)
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

    for (fee_ccy, ccy), n in fee_skipped.items():
        result.notes.append(f"{n} row(s): commission charged in {fee_ccy} on {ccy} trades was not "
                            f"included (no silent FX conversion); it is noted on each trade")
    if not dry_run:
        db.rebuild_closed_trades(conn, touched)
        conn.commit()
    return result


def is_freedom_report(data: Any) -> bool:
    """Freedom Finance / Tradernet broker report JSON: {"trades": {"detailed": [...]}, "cash_flows": ...}."""
    return (isinstance(data, dict) and isinstance(data.get("trades"), dict)
            and isinstance(data["trades"].get("detailed"), list))


_DIV_TICKER = re.compile(r"\(([A-Z0-9_\-]+(?:\.[A-Z]{2,3})?)\)")


def _freedom_dividends(conn: sqlite3.Connection, data: dict[str, Any], result: ImportResult,
                       dry_run: bool) -> None:
    flows = (data.get("cash_flows") or {}).get("detailed") or []
    taxes = 0
    for n, flow in enumerate(flows, start=1):
        kind = str(flow.get("type_id") or "").lower()
        if kind == "tax":
            taxes += 1
            continue
        if kind != "dividend":
            continue
        tickers = _DIV_TICKER.findall(str(flow.get("comment") or ""))
        amount = parse_number(flow.get("amount"))
        if not tickers or amount is None:
            result.skipped.append({"row": f"dividend {n}", "reason": "could not find ticker or amount"})
            continue
        ticker = tickers[-1]
        for suffix in PRESETS["freedom"]["strip_suffixes"]:
            if ticker.endswith(suffix):
                ticker = ticker[: -len(suffix)]
        ts = db.normalize_timestamp(str(flow.get("date")))
        ccy = str(flow.get("currency") or "USD").upper()
        ext = f"div:{ticker}:{ts[:10]}:{amount:g}:{ccy}"
        exists = conn.execute("SELECT 1 FROM events WHERE source = 'freedom' AND external_id = ?",
                              (ext,)).fetchone()
        rec = {"ticker": ticker, "amount": amount, "currency": ccy, "date": ts[:10]}
        if exists:
            continue
        if not dry_run:
            db.add_event(conn, ticker, "dividend", amount=amount, currency=ccy, timestamp=ts,
                         source="freedom", external_id=ext, commit=False)
        result.dividends.append(rec)
    if taxes:
        result.notes.append(f"{taxes} tax row(s) in cash flows are not linked to a dividend; add them with "
                            "`trade event dividend TICKER AMOUNT --tax T` if they are dividend withholding")


def import_file(conn: sqlite3.Connection, path: str | Path | None = None, *, text: str | None = None,
                **kwargs: Any) -> ImportResult:
    headers, rows, fmt = read_rows(path, text)
    if fmt == "freedom-json" and kwargs.get("preset", "generic") == "generic":
        kwargs["preset"] = "freedom"
    result = import_rows(conn, headers, rows, **kwargs)
    if fmt == "freedom-json":
        raw = Path(path).read_text(encoding="utf-8-sig") if path and str(path) != "-" else text
        _freedom_dividends(conn, json.loads(raw or "{}"), result, kwargs.get("dry_run", False))
        if not kwargs.get("dry_run"):
            conn.commit()
    return result
