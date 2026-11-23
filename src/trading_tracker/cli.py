"""CLI: `trade` — a trading journal that agents can drive safely.

Every command prints a human-readable table by default and machine-readable
JSON with the global ``--json`` flag (``trade --json positions``). In JSON mode
errors are printed as ``{"ok": false, "error": "..."}`` with exit code 1, so an
agent never has to parse tables or guess.
"""

from __future__ import annotations

import json
import sys
import webbrowser
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Annotated, Any, Callable, Optional

import typer
from rich.console import Console
from rich.table import Table

from trading_tracker import analytics, dashboard, db, importers, prices, report, sync
from trading_tracker.config import load_config
from trading_tracker.models import currency_symbol


class _State:
    json: bool = False
    db_override: str | None = None


STATE = _State()
console = Console()
err_console = Console(stderr=True)


def _version_callback(value: bool):
    if value:
        print(f"trade {pkg_version('obsidian-trading-tracker')}")
        raise typer.Exit()


app = typer.Typer(name="trade", no_args_is_help=True,
                  help="Trading journal with a deterministic P&L engine. Use --json for agents.")
db_app = typer.Typer(name="db", help="Database management.", no_args_is_help=True)
sync_app = typer.Typer(name="sync", help="Obsidian export.", no_args_is_help=True)
fx_app = typer.Typer(name="fx", help="Currency exchange rates (needs the yahoo extra).", no_args_is_help=True)
price_app = typer.Typer(name="price", help="Market prices used to mark open positions.", no_args_is_help=True)
event_app = typer.Typer(name="event", help="Corporate events: splits and dividends.", no_args_is_help=True)
journal_app = typer.Typer(name="journal", help="Free-form trading journal entries.", no_args_is_help=True)
app.add_typer(db_app)
app.add_typer(sync_app)
app.add_typer(fx_app)
app.add_typer(price_app)
app.add_typer(event_app)
app.add_typer(journal_app)


@app.callback()
def main(
    version: Annotated[
        Optional[bool], typer.Option("--version", "-v", callback=_version_callback, is_eager=True)
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable JSON output.")] = False,
    db_path: Annotated[Optional[str], typer.Option("--db", help="Path to the SQLite database.")] = None,
):
    """Trading journal with a deterministic P&L engine."""
    STATE.json = json_out
    STATE.db_override = db_path


# ── helpers ─────────────────────────────────────────────────────────────────


def _cfg():
    cfg = load_config()
    if STATE.db_override:
        cfg.database.path = STATE.db_override
    return cfg


def _get_conn():
    return db.init_db(_cfg().db_path)


def _emit(data: Any, human: Callable[[], None] | None = None) -> None:
    """Print JSON in --json mode, otherwise run the human renderer."""
    if STATE.json:
        sys.stdout.write(json.dumps({"ok": True, "data": data}, ensure_ascii=False, default=str) + "\n")
    elif human is not None:
        human()


def _fail(message: str, code: int = 1) -> None:
    if STATE.json:
        sys.stdout.write(json.dumps({"ok": False, "error": message}, ensure_ascii=False) + "\n")
    else:
        console.print(f"[red]{message}[/red]")
    raise typer.Exit(code)


def _fmt(amount: float | None, ccy: str = "USD") -> str:
    """Format amount with currency symbol: '$1,234.56' or '₸9,092.00'."""
    if amount is None:
        return "-"
    sym = currency_symbol(ccy)
    sign = "-" if amount < 0 else ""
    return f"{sign}{sym}{abs(amount):,.2f}"


def _pnl(amount: float | None, ccy: str = "USD") -> str:
    if amount is None:
        return "-"
    color = "green" if amount >= 0 else "red"
    return f"[{color}]{_fmt(amount, ccy)}[/{color}]"


def _num(x: float) -> str:
    return f"{x:,.8f}".rstrip("0").rstrip(".") if x != int(x) else f"{int(x):,}"


def _tags(raw: str | None) -> list[str]:
    return [t.strip() for t in raw.split(",") if t.strip()] if raw else []


def _print_streaks_table(dd, streaks, ccy, title="Drawdown & Streaks"):
    """Print a formatted drawdown & streaks table."""
    streak_table = Table(title=title)
    streak_table.add_column("Metric", style="cyan")
    streak_table.add_column("Value", justify="right")

    if dd["max_drawdown_count"] > 0:
        tickers_str = ", ".join(dd["max_drawdown_tickers"][:5])
        streak_table.add_row(
            "Max Drawdown",
            f"[red]{_fmt(dd['max_drawdown'], ccy)}[/red] ({dd['max_drawdown_count']} trades: {tickers_str})",
        )
    if streaks["longest_win_streak"] > 0:
        streak_table.add_row(
            "Win Streak",
            f"[green]{streaks['longest_win_streak']} trades (+{_fmt(streaks['longest_win_pnl'], ccy)})[/green]",
        )
    if streaks["longest_loss_streak"] > 0:
        streak_table.add_row(
            "Loss Streak",
            f"[red]{streaks['longest_loss_streak']} trades ({_fmt(streaks['longest_loss_pnl'], ccy)})[/red]",
        )
    if streaks["current_streak_type"]:
        cur_color = "green" if streaks["current_streak_type"] == "win" else "red"
        streak_table.add_row(
            "Current",
            f"[{cur_color}]{streaks['current_streak_count']} {streaks['current_streak_type']}s "
            f"({_fmt(streaks['current_streak_pnl'], ccy)})[/{cur_color}]",
        )
    console.print(streak_table)


# ── db commands ──────────────────────────────────────────────────────────────


@db_app.command("init")
def db_init():
    """Create the database (or apply pending migrations)."""
    cfg = _cfg()
    conn = db.init_db(cfg.db_path)
    version = db._get_schema_version(conn)
    _emit({"path": str(cfg.db_path), "schema_version": version},
          lambda: console.print(f"[green]Database initialized at {cfg.db_path}[/green]"))


@db_app.command("path")
def db_path_cmd():
    """Print where the database lives."""
    cfg = _cfg()
    _emit({"path": str(cfg.db_path), "exists": cfg.db_path.exists()},
          lambda: console.print(str(cfg.db_path)))


@db_app.command("validate")
def db_validate():
    """Check database consistency (ledger warnings, stale cache)."""
    conn = _get_conn()
    issues = db.validate_db(conn)

    def human():
        if issues:
            console.print("[red]Issues found:[/red]")
            for issue in issues:
                console.print(f"  - {issue}")
        else:
            console.print("[green]Database is consistent.[/green]")

    _emit({"ok": not issues, "issues": issues}, human)


@db_app.command("rebuild")
def db_rebuild():
    """Recompute all realized P&L from the trades table."""
    conn = _get_conn()
    n = db.rebuild_closed_trades(conn)
    conn.commit()
    _emit({"realized_records": n},
          lambda: console.print(f"[green]Rebuilt {n} realized P&L records from trades.[/green]"))


# ── trades ───────────────────────────────────────────────────────────────────


def _trade_view(conn, trade_id: int) -> dict[str, Any]:
    t = db.get_trade(conn, trade_id) or {}
    try:
        t["tags"] = json.loads(t.get("tags") or "[]")
    except (TypeError, json.JSONDecodeError):
        pass
    return t


@app.command()
def add(
    ticker: str,
    action: str,
    shares: float,
    price: float,
    commission: Annotated[Optional[float], typer.Option("--commission", "-c")] = None,
    strategy: Annotated[Optional[str], typer.Option("--strategy", "-s")] = None,
    setup: Annotated[Optional[str], typer.Option("--setup", help="Setup / pattern name")] = None,
    sl: Annotated[Optional[float], typer.Option("--sl", "--stop", help="Stop-loss price (enables R-multiples)")] = None,
    tp1: Annotated[Optional[float], typer.Option("--tp1")] = None,
    tp2: Annotated[Optional[float], typer.Option("--tp2")] = None,
    confidence: Annotated[Optional[int], typer.Option("--confidence", min=1, max=5)] = None,
    notes: Annotated[Optional[str], typer.Option("--notes", "-n", help="Thesis / reason for the trade")] = None,
    tags: Annotated[Optional[str], typer.Option("--tags", "-t")] = None,
    source: Annotated[Optional[str], typer.Option("--source")] = None,
    group: Annotated[Optional[str], typer.Option("--group")] = None,
    timestamp: Annotated[Optional[str], typer.Option("--date", "--timestamp", help="ISO date/time of the fill")] = None,
    instrument: Annotated[Optional[str], typer.Option("--instrument", "-i")] = None,
    leverage: Annotated[float, typer.Option("--leverage")] = 1.0,
    currency: Annotated[str, typer.Option("--currency", "--ccy")] = "USD",
    multiplier: Annotated[float, typer.Option("--multiplier", "-m", help="Contract multiplier (100 for US options)")] = 1.0,
    external_id: Annotated[Optional[str], typer.Option("--external-id", help="Broker trade id (prevents duplicates)")] = None,
):
    """Record a fill (BUY or SELL). Realized P&L is computed automatically."""
    cfg = _cfg()
    conn = db.init_db(cfg.db_path)
    ticker = ticker.strip().upper()
    src = source or cfg.defaults.source
    if external_id and db.find_by_external_id(conn, src, external_id):
        _fail(f"Trade with external id {external_id!r} from {src!r} already exists")
    try:
        trade_id = db.add_trade(
            conn, ticker, action, shares, price,
            commission=cfg.defaults.commission if commission is None else commission,
            timestamp=timestamp, strategy=strategy, setup=setup, stop_loss=sl, target_1=tp1,
            target_2=tp2, confidence=confidence, notes=notes, tags=_tags(tags), source=src,
            position_group=group, asset_type=instrument or cfg.defaults.asset_type,
            instrument=instrument or cfg.defaults.asset_type, currency=currency,
            leverage=leverage, multiplier=multiplier, external_id=external_id,
        )
    except (ValueError, Exception) as exc:  # sqlite constraint errors included
        _fail(str(exc))
        return
    realized = db.get_realized_for_trade(conn, trade_id)
    position = db.get_position(conn, ticker)
    trade = _trade_view(conn, trade_id)
    ccy = trade.get("currency", "USD")

    def human():
        sym = currency_symbol(ccy)
        console.print(
            f"[green]Trade #{trade_id}: {trade['action']} {_num(shares)} {trade['ticker']} "
            f"@ {sym}{price:,.4f}".rstrip("0").rstrip(".") + "[/green]"
        )
        if realized:
            dir_label = realized["direction"].upper()
            console.print(
                f"  ↳ Closed {dir_label} {_num(realized['shares'])}: "
                f"{_fmt(realized['avg_entry_price'], ccy)} → {_fmt(realized['avg_exit_price'], ccy)} | "
                f"P&L: {_pnl(realized['net_pnl'], ccy)} ({realized['pnl_percent']:.1f}%)"
                + (f" [dim]gross {_fmt(realized['gross_pnl'], ccy)}, fees {_fmt(realized['total_commission'], ccy)}[/dim]"
                   if realized["total_commission"] else "")
            )
            if position is None:
                console.print("  Position fully closed.")
        if position is not None:
            label = "Remaining" if realized else "Position"
            console.print(
                f"  {label}: {position['direction'].upper()} {_num(position['shares'])} "
                f"@ {_fmt(position['avg_cost'], ccy)} (breakeven {_fmt(position['breakeven_price'], ccy)})"
            )

    _emit({"trade": trade, "realized": realized, "position": position}, human)


@app.command()
def edit(
    trade_id: int,
    ticker: Annotated[Optional[str], typer.Option("--ticker")] = None,
    action: Annotated[Optional[str], typer.Option("--action")] = None,
    shares: Annotated[Optional[float], typer.Option("--shares")] = None,
    price: Annotated[Optional[float], typer.Option("--price")] = None,
    commission: Annotated[Optional[float], typer.Option("--commission", "-c")] = None,
    timestamp: Annotated[Optional[str], typer.Option("--date", "--timestamp")] = None,
    strategy: Annotated[Optional[str], typer.Option("--strategy", "-s")] = None,
    setup: Annotated[Optional[str], typer.Option("--setup")] = None,
    sl: Annotated[Optional[float], typer.Option("--sl", "--stop")] = None,
    tp1: Annotated[Optional[float], typer.Option("--tp1")] = None,
    tp2: Annotated[Optional[float], typer.Option("--tp2")] = None,
    confidence: Annotated[Optional[int], typer.Option("--confidence")] = None,
    notes: Annotated[Optional[str], typer.Option("--notes", "-n")] = None,
    tags: Annotated[Optional[str], typer.Option("--tags", "-t")] = None,
    currency: Annotated[Optional[str], typer.Option("--currency", "--ccy")] = None,
    multiplier: Annotated[Optional[float], typer.Option("--multiplier", "-m")] = None,
    instrument: Annotated[Optional[str], typer.Option("--instrument", "-i")] = None,
):
    """Edit a trade. All P&L depending on it is recomputed."""
    conn = _get_conn()
    if not db.get_trade(conn, trade_id):
        _fail(f"Trade #{trade_id} not found.")
    fields: dict[str, Any] = {
        "ticker": ticker, "action": action, "shares": shares, "price": price,
        "commission": commission, "timestamp": timestamp, "strategy": strategy, "setup": setup,
        "stop_loss": sl, "target_1": tp1, "target_2": tp2, "confidence": confidence,
        "notes": notes, "tags": _tags(tags) if tags is not None else None,
        "currency": currency, "multiplier": multiplier, "instrument": instrument,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        _emit({"updated": False, "fields": []},
              lambda: console.print("[yellow]No fields to update.[/yellow]"))
        return
    try:
        db.edit_trade(conn, trade_id, **fields)
    except ValueError as exc:
        _fail(str(exc))
    _emit({"updated": True, "fields": list(fields), "trade": _trade_view(conn, trade_id)},
          lambda: console.print(f"[green]Trade #{trade_id} updated: {', '.join(fields)}[/green]"))


@app.command()
def show(trade_id: int):
    """Show full details of a trade, including the P&L it realized."""
    conn = _get_conn()
    trade = db.get_trade(conn, trade_id)
    if not trade:
        _fail(f"Trade #{trade_id} not found.")
    view = _trade_view(conn, trade_id)
    realized = db.get_realized_for_trade(conn, trade_id)

    def human():
        table = Table(title=f"Trade #{trade_id}", show_header=False)
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        for key, value in view.items():
            if key in ("created_at", "updated_at"):
                continue
            if key == "tags":
                value = ", ".join(value) if isinstance(value, list) else value
            table.add_row(key, str(value) if value not in (None, "") else "-")
        if realized:
            ccy = realized["currency"]
            table.add_row("realized P&L", f"{_pnl(realized['net_pnl'], ccy)} "
                          f"(gross {_fmt(realized['gross_pnl'], ccy)})")
            for k in ("what_worked", "what_failed", "lesson", "rating"):
                if realized.get(k):
                    table.add_row(k, str(realized[k]))
        table.add_row("created_at", str(trade["created_at"]))
        table.add_row("updated_at", str(trade["updated_at"]))
        console.print(table)

    _emit({"trade": view, "realized": realized}, human)


@app.command()
def delete(
    trade_id: int,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
):
    """Delete a trade. All P&L depending on it is recomputed."""
    conn = _get_conn()
    trade = db.get_trade(conn, trade_id)
    if not trade:
        _fail(f"Trade #{trade_id} not found.")
    if not yes:
        if STATE.json:
            _fail("Refusing to delete without --yes in --json mode")
        console.print(
            f"  Trade #{trade_id}: {trade['action']} {trade['shares']} {trade['ticker']} "
            f"@ {_fmt(trade['price'], trade.get('currency', 'USD'))} ({trade['timestamp'][:10]})"
        )
        if not typer.confirm("Delete this trade?"):
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)
    db.delete_trade(conn, trade_id)
    _emit({"deleted": trade_id},
          lambda: console.print(f"[green]Trade #{trade_id} deleted.[/green]"))


@app.command()
def review(
    trade_id: Annotated[int, typer.Argument(help="Id of the trade that closed (part of) the position")],
    worked: Annotated[Optional[str], typer.Option("--worked", help="What worked")] = None,
    failed: Annotated[Optional[str], typer.Option("--failed", help="What did not work")] = None,
    lesson: Annotated[Optional[str], typer.Option("--lesson")] = None,
    rating: Annotated[Optional[int], typer.Option("--rating", min=1, max=5, help="Execution quality 1-5")] = None,
):
    """Write a post-trade review for a closed trade."""
    conn = _get_conn()
    try:
        ok = db.set_review(conn, trade_id, what_worked=worked, what_failed=failed,
                           lesson=lesson, rating=rating)
    except ValueError as exc:
        _fail(str(exc))
        return
    if not ok:
        _fail("Nothing to save: pass --worked, --failed, --lesson or --rating")
    _emit({"review": db.get_realized_for_trade(conn, trade_id)},
          lambda: console.print(f"[green]Review saved for trade #{trade_id}.[/green]"))


@app.command(deprecated=True)
def close(
    ticker: str = typer.Argument(default=""),
    shares: float = typer.Argument(default=0),
    price: float = typer.Argument(default=0),
):
    """Deprecated: use 'trade add <ticker> sell <shares> <price>' instead."""
    msg = ("The 'close' command is deprecated. Positions close automatically when you add a "
           "counter-trade: `trade add TICKER sell SHARES PRICE` (long) or `buy` (short); "
           "then `trade review <trade id>`.")
    if STATE.json:
        _fail(msg)
    console.print(
        "[yellow]The 'close' command is deprecated.[/yellow]\n"
        "Positions are now closed automatically when you add a counter-trade:\n"
        "  [cyan]trade add TICKER sell SHARES PRICE[/cyan]  (to close a long)\n"
        "  [cyan]trade add TICKER buy SHARES PRICE[/cyan]   (to close a short)"
    )
    raise typer.Exit(1)


# ── positions & history ─────────────────────────────────────────────────────


@app.command()
def positions(
    price_source: Annotated[str, typer.Option("--prices", help="cache | yahoo | none")] = "cache",
    live: Annotated[bool, typer.Option("--live", help="Shortcut for --prices yahoo")] = False,
    no_live: Annotated[bool, typer.Option("--no-live", help="Shortcut for --prices none")] = False,
):
    """Open positions, marked to market with the latest known prices."""
    conn = _get_conn()
    provider = "yahoo" if live else ("none" if no_live else price_source)
    rep = report.build_report(conn, price_provider=provider)
    pos = rep["open_positions"]
    data = {"positions": pos, "warnings": rep["warnings"]}

    def human():
        if not pos:
            console.print("[yellow]No open positions.[/yellow]")
            return
        has_prices = any(p.get("current_price") is not None for p in pos)
        multi = len({p["currency"] for p in pos}) > 1
        table = Table(title="Open Positions")
        table.add_column("Ticker", style="cyan bold")
        if multi:
            table.add_column("CCY")
        for col in ("Dir", "Shares", "Avg Cost", "Breakeven", "Cost Basis"):
            table.add_column(col, justify="right" if col != "Dir" else "left")
        if has_prices:
            for col in ("Price", "Mkt Value", "P&L", "P&L %", "As of"):
                table.add_column(col, justify="right")
        table.add_column("Opened")
        for p in pos:
            ccy = p["currency"]
            row = [p["ticker"]] + ([ccy] if multi else []) + [
                "[green]LONG[/green]" if p["direction"] == "long" else "[red]SHORT[/red]",
                _num(p["shares"]), _fmt(p["avg_cost"], ccy), _fmt(p["breakeven_price"], ccy),
                _fmt(p["cost_basis"], ccy),
            ]
            if has_prices:
                if p.get("current_price") is not None:
                    row += [_fmt(p["current_price"], ccy), _fmt(p["market_value"], ccy),
                            _pnl(p["unrealized_pnl"], ccy), f"{p['unrealized_pnl_pct']:+.1f}%",
                            (p.get("price_as_of") or "")[:10]]
                else:
                    row += ["-"] * 5
            row.append((p.get("first_trade") or "")[:10])
            table.add_row(*row)
        console.print(table)
        for ccy, block in rep["by_currency"].items():
            s = block["summary"]
            if s["open_positions"] and s["unrealized_pnl"] is not None:
                console.print(f"  {ccy}: cost {_fmt(s['open_cost_basis'], ccy)} | "
                              f"unrealized {_pnl(s['unrealized_pnl'], ccy)}")
        if not has_prices:
            console.print("[dim]No prices yet: `trade price set TICKER PRICE` or `trade positions --live`.[/dim]")
        for w in rep["warnings"]:
            console.print(f"[yellow]! {w}[/yellow]")

    _emit(data, human)


@app.command()
def history(
    limit: Annotated[int, typer.Option("--limit", "-l")] = 20,
    ticker: Annotated[Optional[str], typer.Option("--ticker", "-t")] = None,
    from_date: Annotated[Optional[str], typer.Option("--from")] = None,
    to_date: Annotated[Optional[str], typer.Option("--to")] = None,
    closed: Annotated[bool, typer.Option("--closed", help="Show realized P&L records")] = False,
):
    """Trade history, or realized P&L records with --closed."""
    conn = _get_conn()
    rows = db.get_history(conn, limit=limit, ticker=ticker, from_date=from_date,
                          to_date=to_date, closed_only=closed)
    for r in rows:
        for k in ("tags", "entry_trade_ids", "exit_trade_ids"):
            if isinstance(r.get(k), str):
                try:
                    r[k] = json.loads(r[k])
                except json.JSONDecodeError:
                    pass

    def human():
        if not rows:
            console.print("[yellow]No trades found.[/yellow]")
            return
        if closed:
            table = Table(title="Closed Trades")
            for col in ("Exit #", "Ticker", "Dir", "Shares", "Entry", "Exit", "Fees", "Net P&L", "P&L %", "Closed"):
                table.add_column(col, justify="left" if col in ("Ticker", "Dir", "Closed") else "right")
            for t in rows:
                ccy = t.get("currency", "USD")
                table.add_row(str(t["exit_trade_id"]), t["ticker"], t["direction"], _num(t["shares"]),
                              _fmt(t["avg_entry_price"], ccy), _fmt(t["avg_exit_price"], ccy),
                              _fmt(t["total_commission"], ccy), _pnl(t["net_pnl"], ccy),
                              f"{t['pnl_percent']:.1f}%", t["closed_at"][:10])
        else:
            table = Table(title="Trade History")
            for col in ("ID", "Ticker", "Action", "Shares", "Price", "Commission", "Strategy", "Time"):
                table.add_column(col, justify="right" if col in ("ID", "Shares", "Price", "Commission") else "left")
            for t in rows:
                ccy = t.get("currency", "USD")
                color = "green" if t["action"] == "BUY" else "red"
                table.add_row(str(t["id"]), t["ticker"], f"[{color}]{t['action']}[/{color}]",
                              _num(t["shares"]), _fmt(t["price"], ccy), _fmt(t["commission"], ccy),
                              t.get("strategy") or "-", t["timestamp"][:16])
        console.print(table)

    _emit({"rows": rows, "closed": closed}, human)


# ── import ───────────────────────────────────────────────────────────────────


@app.command("import")
def import_cmd(
    file: Annotated[str, typer.Argument(help="CSV or JSON file, or '-' for stdin")],
    preset: Annotated[str, typer.Option("--preset", "-p", help="generic | ibkr | tinvest | freedom")] = "generic",
    mapping: Annotated[Optional[list[str]], typer.Option("--map", help="field=Column override, repeatable")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Parse and report, write nothing")] = False,
    currency: Annotated[Optional[str], typer.Option("--currency", "--ccy", help="Default currency")] = None,
    source: Annotated[Optional[str], typer.Option("--source", help="Source label used for de-duplication")] = None,
    strategy: Annotated[Optional[str], typer.Option("--strategy", "-s", help="Default strategy")] = None,
    date_format: Annotated[Optional[str], typer.Option("--date-format", help="strptime format, e.g. %d/%m/%Y")] = None,
):
    """Import fills from a broker CSV or an agent-produced JSON list. Safe to re-run."""
    conn = _get_conn()
    overrides: dict[str, str] = {}
    for m in mapping or []:
        if "=" not in m:
            _fail(f"--map expects field=Column, got {m!r}")
        k, v = m.split("=", 1)
        overrides[k.strip()] = v.strip()
    try:
        if file == "-":
            res = importers.import_file(conn, text=sys.stdin.read(), preset=preset, source=source,
                                        overrides=overrides, dry_run=dry_run, date_format=date_format,
                                        defaults={"currency": currency, "strategy": strategy})
        else:
            if not Path(file).is_file():
                _fail(f"File not found: {file}")
            res = importers.import_file(conn, file, preset=preset, source=source, overrides=overrides,
                                        dry_run=dry_run, date_format=date_format,
                                        defaults={"currency": currency, "strategy": strategy})
    except (ValueError, json.JSONDecodeError) as exc:
        _fail(str(exc))
        return
    data = res.to_dict()

    def human():
        verb = "Would import" if dry_run else "Imported"
        console.print(f"[green]{verb} {len(res.imported)} trades[/green] "
                      f"(duplicates skipped: {len(res.duplicates)}, unparsable rows: {len(res.skipped)})")
        console.print(f"[dim]Columns: {', '.join(f'{k}←{v}' for k, v in res.columns.items())}[/dim]")
        for n in res.notes:
            console.print(f"[dim]{n}[/dim]")
        for s in res.skipped[:10]:
            console.print(f"  [yellow]row {s['row']}: {s['reason']}[/yellow]")
        if len(res.skipped) > 10:
            console.print(f"  [yellow]... {len(res.skipped) - 10} more[/yellow]")

    _emit(data, human)


# ── prices ───────────────────────────────────────────────────────────────────


@price_app.command("set")
def price_set(
    ticker: str,
    price: float,
    currency: Annotated[Optional[str], typer.Option("--currency", "--ccy")] = None,
    as_of: Annotated[Optional[str], typer.Option("--as-of", help="Quote time (ISO), default now")] = None,
    source: Annotated[str, typer.Option("--source", help="Where the quote came from")] = "manual",
):
    """Store a market price (e.g. one your agent fetched) for marking positions."""
    conn = _get_conn()
    try:
        db.set_price(conn, ticker, price, currency=currency, as_of=as_of, source=source)
    except ValueError as exc:
        _fail(str(exc))
    q = db.get_prices(conn)[ticker.upper()]
    _emit({"price": q}, lambda: console.print(
        f"[green]{q['ticker']} = {price} ({q['source']}, {q['as_of']})[/green]"))


@price_app.command("list")
def price_list():
    """Show stored prices."""
    conn = _get_conn()
    qs = sorted(db.get_prices(conn).values(), key=lambda q: q["ticker"])

    def human():
        if not qs:
            console.print("[yellow]No prices stored.[/yellow]")
            return
        table = Table(title="Prices")
        for c in ("Ticker", "Price", "CCY", "As of", "Source"):
            table.add_column(c)
        for q in qs:
            table.add_row(q["ticker"], str(q["price"]), q.get("currency") or "-", q["as_of"], q["source"])
        console.print(table)

    _emit({"prices": qs}, human)


@price_app.command("fetch")
def price_fetch(
    tickers: Annotated[Optional[list[str]], typer.Argument(help="Default: all open positions")] = None,
):
    """Fetch prices from Yahoo Finance (needs the yahoo extra) and store them."""
    conn = _get_conn()
    wanted = [t.upper() for t in tickers] if tickers else [p["ticker"] for p in db.get_positions(conn)]
    try:
        quotes = prices.get_quotes(conn, wanted, "yahoo")
    except prices.ProviderUnavailable as exc:
        _fail(str(exc))
        return
    missing = [t for t in wanted if t not in quotes]
    _emit({"prices": list(quotes.values()), "missing": missing},
          lambda: console.print(f"[green]Fetched {len(quotes)} prices[/green]"
                                + (f"; no quote for {', '.join(missing)}" if missing else "")))


# ── events ───────────────────────────────────────────────────────────────────


@event_app.command("split")
def event_split(
    ticker: str,
    ratio: Annotated[float, typer.Argument(help="New shares per old share: 4 for 4-for-1, 0.1 for 1-for-10")],
    timestamp: Annotated[Optional[str], typer.Option("--date")] = None,
    notes: Annotated[Optional[str], typer.Option("--notes", "-n")] = None,
):
    """Record a stock split (applies to the position held at that date)."""
    conn = _get_conn()
    try:
        eid = db.add_event(conn, ticker, "split", timestamp=timestamp, ratio=ratio, notes=notes)
    except ValueError as exc:
        _fail(str(exc))
        return
    pos = db.get_position(conn, ticker)
    _emit({"event_id": eid, "position": pos},
          lambda: console.print(f"[green]Split #{eid}: {ticker.upper()} x{ratio}[/green]"
                                + (f" → {_num(pos['shares'])} @ {_fmt(pos['avg_cost'], pos['currency'])}" if pos else "")))


@event_app.command("dividend")
def event_dividend(
    ticker: str,
    amount: Annotated[float, typer.Argument(help="Total cash received before tax")],
    tax: Annotated[float, typer.Option("--tax", help="Withholding tax")] = 0.0,
    currency: Annotated[Optional[str], typer.Option("--currency", "--ccy")] = None,
    timestamp: Annotated[Optional[str], typer.Option("--date")] = None,
    notes: Annotated[Optional[str], typer.Option("--notes", "-n")] = None,
):
    """Record a dividend or coupon payment."""
    conn = _get_conn()
    try:
        eid = db.add_event(conn, ticker, "dividend", timestamp=timestamp, amount=amount, tax=tax,
                           currency=currency, notes=notes)
    except ValueError as exc:
        _fail(str(exc))
        return
    _emit({"event_id": eid, "net": round(amount - tax, 2)},
          lambda: console.print(f"[green]Dividend #{eid}: {ticker.upper()} {amount - tax:,.2f} net[/green]"))


@event_app.command("list")
def event_list(ticker: Annotated[Optional[str], typer.Option("--ticker", "-t")] = None):
    """List splits and dividends."""
    conn = _get_conn()
    evs = db.get_events(conn, ticker)

    def human():
        if not evs:
            console.print("[yellow]No events.[/yellow]")
            return
        table = Table(title="Events")
        for c in ("ID", "Date", "Ticker", "Kind", "Ratio", "Amount", "Tax", "CCY"):
            table.add_column(c)
        for e in evs:
            table.add_row(str(e["id"]), e["timestamp"][:10], e["ticker"], e["kind"],
                          str(e["ratio"] or "-"), str(e["amount"] if e["amount"] is not None else "-"),
                          str(e["tax"] or 0), e.get("currency") or "-")
        console.print(table)

    _emit({"events": evs}, human)


@event_app.command("delete")
def event_delete(event_id: int):
    """Delete an event."""
    conn = _get_conn()
    if not db.delete_event(conn, event_id):
        _fail(f"Event #{event_id} not found.")
    _emit({"deleted": event_id}, lambda: console.print(f"[green]Event #{event_id} deleted.[/green]"))


# ── journal ──────────────────────────────────────────────────────────────────


@journal_app.command("add")
def journal_add(
    text: str,
    ticker: Annotated[Optional[str], typer.Option("--ticker", "-t")] = None,
    mood: Annotated[Optional[int], typer.Option("--mood", min=1, max=5, help="1 = awful, 5 = great")] = None,
    kind: Annotated[str, typer.Option("--kind", "-k", help="note | plan | lesson | mistake | review")] = "note",
    tags: Annotated[Optional[str], typer.Option("--tags")] = None,
    day: Annotated[Optional[str], typer.Option("--date")] = None,
):
    """Write a journal entry (plans, emotions, lessons, market notes)."""
    conn = _get_conn()
    try:
        eid = db.add_journal_entry(conn, text, date=day, ticker=ticker, kind=kind, mood=mood,
                                   tags=_tags(tags))
    except ValueError as exc:
        _fail(str(exc))
        return
    _emit({"entry_id": eid}, lambda: console.print(f"[green]Journal entry #{eid} saved.[/green]"))


@journal_app.command("list")
def journal_list(
    ticker: Annotated[Optional[str], typer.Option("--ticker", "-t")] = None,
    limit: Annotated[int, typer.Option("--limit", "-l")] = 20,
):
    """Show recent journal entries."""
    conn = _get_conn()
    entries = db.get_journal(conn, ticker=ticker, limit=limit)

    def human():
        if not entries:
            console.print("[yellow]No journal entries.[/yellow]")
            return
        for e in entries:
            meta = " · ".join(x for x in (e["kind"], e.get("ticker") or "",
                                          f"mood {e['mood']}" if e.get("mood") else "") if x)
            console.print(f"[cyan]#{e['id']} {e['date']}[/cyan] [dim]{meta}[/dim]\n  {e['text']}")

    _emit({"entries": entries}, human)


@journal_app.command("delete")
def journal_delete(entry_id: int):
    """Delete a journal entry."""
    conn = _get_conn()
    if not db.delete_journal_entry(conn, entry_id):
        _fail(f"Journal entry #{entry_id} not found.")
    _emit({"deleted": entry_id}, lambda: console.print(f"[green]Entry #{entry_id} deleted.[/green]"))


# ── report & dashboard ───────────────────────────────────────────────────────


@app.command("report")
def report_cmd(
    from_date: Annotated[Optional[str], typer.Option("--from")] = None,
    to_date: Annotated[Optional[str], typer.Option("--to")] = None,
    ticker: Annotated[Optional[str], typer.Option("--ticker", "-t")] = None,
    strategy: Annotated[Optional[str], typer.Option("--strategy", "-s")] = None,
    price_source: Annotated[str, typer.Option("--prices", help="cache | yahoo | none")] = "cache",
    output: Annotated[Optional[str], typer.Option("--output", "-o", help="Write JSON to a file")] = None,
):
    """Full journal snapshot as JSON: summary, equity curve, breakdowns, trades, journal."""
    conn = _get_conn()
    rep = report.build_report(conn, start=from_date, end=to_date, ticker=ticker,
                              strategy=strategy, price_provider=price_source)
    if output:
        Path(output).write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        _emit({"path": str(Path(output).resolve())},
              lambda: console.print(f"[green]Report written to {output}[/green]"))
        return
    if STATE.json:
        _emit(rep)
    else:
        print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))


@app.command("dashboard")
def dashboard_cmd(
    output: Annotated[str, typer.Option("--output", "-o", help="HTML file to write")] = "trading-dashboard.html",
    from_date: Annotated[Optional[str], typer.Option("--from")] = None,
    to_date: Annotated[Optional[str], typer.Option("--to")] = None,
    ticker: Annotated[Optional[str], typer.Option("--ticker", "-t")] = None,
    strategy: Annotated[Optional[str], typer.Option("--strategy", "-s")] = None,
    title: Annotated[str, typer.Option("--title")] = "Trading Journal",
    price_source: Annotated[str, typer.Option("--prices", help="cache | yahoo | none")] = "cache",
    open_browser: Annotated[bool, typer.Option("--open", help="Open in the default browser")] = False,
):
    """Render an interactive, self-contained HTML dashboard (works offline)."""
    conn = _get_conn()
    rep = report.build_report(conn, start=from_date, end=to_date, ticker=ticker,
                              strategy=strategy, price_provider=price_source)
    path = Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dashboard.render(rep, title=title), encoding="utf-8")
    if open_browser:
        webbrowser.open(path.as_uri())
    _emit({"path": str(path), "closed_trades": len(rep["closed_trades"]),
           "open_positions": len(rep["open_positions"]), "currencies": rep["currencies"]},
          lambda: console.print(f"[green]Dashboard written to {path}[/green]"))


# ── stats ────────────────────────────────────────────────────────────────────


@app.command()
def stats(
    from_date: Annotated[Optional[str], typer.Option("--from")] = None,
    to_date: Annotated[Optional[str], typer.Option("--to")] = None,
    ticker: Annotated[Optional[str], typer.Option("--ticker", "-t")] = None,
    strategy: Annotated[Optional[str], typer.Option("--strategy", "-s")] = None,
):
    """Trading statistics (per currency; nothing is silently converted)."""
    cfg = _cfg()
    conn = db.init_db(cfg.db_path)
    if STATE.json:
        rep = report.build_report(conn, start=from_date, end=to_date, ticker=ticker,
                                  strategy=strategy, price_provider="cache")
        _emit({ccy: {k: v for k, v in block.items() if k != "equity_curve"}
               for ccy, block in rep["by_currency"].items()})
        return
    closed = db.get_closed_trades(conn)
    if from_date or to_date or ticker or strategy:
        rep = report.build_report(conn, start=from_date, end=to_date, ticker=ticker,
                                  strategy=strategy, price_provider="none")
        keep = {c["exit_trade_id"] for c in rep["closed_trades"]}
        closed = [c for c in closed if c["exit_trade_id"] in keep]
    overall = analytics.compute_stats(closed)

    if overall["total_trades"] == 0:
        console.print("[yellow]No closed trades yet.[/yellow]")
        return

    # Detect currencies in use
    currencies_in_use = {t.get("currency", "USD") for t in closed}
    multi_ccy = len(currencies_in_use) > 1
    single_ccy = next(iter(currencies_in_use)) if not multi_ccy else None

    table = Table(title="Trading Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total Trades", str(overall["total_trades"]))
    table.add_row("Wins / Losses", f"{overall['wins']} / {overall['losses']}")
    table.add_row("Win Rate", f"{overall['win_rate']}%")

    if multi_ccy:
        base_ccy = cfg.fx.base_currency
        # Pre-fetch all FX rates in one batch to avoid sequential requests
        other_ccys = sorted(currencies_in_use - {base_ccy})
        rates = (analytics.fetch_fx_matrix([base_ccy] + other_ccys)
                 if other_ccys and prices.yahoo_available() else None)
        ccy_stats = analytics.compute_stats_by_currency(closed, base_ccy, rates=rates)

        for ccy, cs in ccy_stats["by_currency"].items():
            pf = cs.get("profit_factor")
            if pf is not None:
                pf_color = "green" if pf >= 1.5 else ("yellow" if pf >= 1.0 else "red")
                table.add_row(f"Profit Factor ({ccy})", f"[{pf_color}]{pf:.2f}[/{pf_color}]")
            else:
                table.add_row(f"Profit Factor ({ccy})", "[green]inf[/green]")

            exp = cs.get("expectancy", 0.0)
            ec = "green" if exp >= 0 else "red"
            table.add_row(f"Expectancy ({ccy})", f"[{ec}]{_fmt(exp, ccy)} per trade[/{ec}]")

        for ccy, cs in ccy_stats["by_currency"].items():
            color = "green" if cs["total_pnl"] >= 0 else "red"
            line = f"[{color}]{_fmt(cs['total_pnl'], ccy)}[/{color}]"
            if ccy != base_ccy and cs["pnl_in_base"] is not None:
                base_sym = currency_symbol(base_ccy)
                line += f" ({base_sym}{cs['pnl_in_base']:+,.2f})"
            table.add_row(f"P&L ({ccy})", line)
        total_color = "green" if ccy_stats["total_pnl_base"] >= 0 else "red"
        table.add_row(
            f"Total P&L ({base_ccy})",
            f"[{total_color}]{_fmt(ccy_stats['total_pnl_base'], base_ccy)}[/{total_color}]",
        )

        for ccy, cs in ccy_stats["by_currency"].items():
            table.add_row(f"Avg P&L ({ccy})", _fmt(cs["avg_pnl"], ccy))
            table.add_row(f"Best Trade ({ccy})", f"[green]{_fmt(cs['best_trade'], ccy)}[/green]")
            table.add_row(f"Worst Trade ({ccy})", f"[red]{_fmt(cs['worst_trade'], ccy)}[/red]")
            table.add_row(f"Commission ({ccy})", _fmt(cs["total_commission"], ccy))
    else:
        ccy = single_ccy
        pf = overall.get("profit_factor")
        if pf is not None:
            pf_color = "green" if pf >= 1.5 else ("yellow" if pf >= 1.0 else "red")
            table.add_row("Profit Factor", f"[{pf_color}]{pf:.2f}[/{pf_color}]")
        else:
            table.add_row("Profit Factor", "[green]inf (no losses)[/green]")

        exp = overall.get("expectancy", 0.0)
        exp_color = "green" if exp >= 0 else "red"
        table.add_row("Expectancy", f"[{exp_color}]{_fmt(exp, ccy)} per trade[/{exp_color}]")

        pnl_color = "green" if overall["total_pnl"] >= 0 else "red"
        table.add_row("Total P&L", f"[{pnl_color}]{_fmt(overall['total_pnl'], ccy)}[/{pnl_color}]")
        table.add_row("Avg P&L", _fmt(overall["avg_pnl"], ccy))
        table.add_row("Best Trade", f"[green]{_fmt(overall['best_trade'], ccy)}[/green]")
        table.add_row("Worst Trade", f"[red]{_fmt(overall['worst_trade'], ccy)}[/red]")
        table.add_row("Total Commission", _fmt(overall["total_commission"], ccy))

    # Holding analysis
    holding = analytics.compute_holding_analysis(closed)
    if holding["avg_hold_winners"] > 0 or holding["avg_hold_losers"] > 0:
        table.add_row("Avg Hold (winners)", f"{holding['avg_hold_winners']:.1f} days")
        table.add_row("Avg Hold (losers)", f"{holding['avg_hold_losers']:.1f} days")
    else:
        table.add_row("Avg Hold (days)", f"{overall['avg_hold_days']:.1f}")

    console.print(table)

    # Drawdown & Streaks — per-currency when multi_ccy to avoid mixing
    if multi_ccy:
        for sccy in sorted(currencies_in_use):
            ccy_closed = [t for t in closed if t.get("currency", "USD") == sccy]
            dd = analytics.compute_max_drawdown(ccy_closed)
            streaks = analytics.compute_streaks(ccy_closed)
            if dd["max_drawdown_count"] > 0 or streaks["longest_win_streak"] > 0:
                _print_streaks_table(dd, streaks, sccy, f"Drawdown & Streaks ({sccy})")
    else:
        dd = analytics.compute_max_drawdown(closed)
        streaks = analytics.compute_streaks(closed)
        if dd["max_drawdown_count"] > 0 or streaks["longest_win_streak"] > 0:
            _print_streaks_table(dd, streaks, single_ccy)

    # Instrument breakdown
    instr_breakdown = analytics.instrument_breakdown(closed)
    if len(instr_breakdown) > 1:
        instr_table = Table(title="By Instrument")
        instr_table.add_column("Instrument", style="cyan")
        instr_table.add_column("Trades", justify="right")
        instr_table.add_column("Win Rate", justify="right")
        if not multi_ccy:
            instr_table.add_column("Total P&L", justify="right")
            instr_table.add_column("Avg P&L", justify="right")

        for s in instr_breakdown:
            row = [s["instrument"], str(s["total_trades"]), f"{s['win_rate']}%"]
            if not multi_ccy:
                sc = "green" if s["total_pnl"] >= 0 else "red"
                row.extend([
                    f"[{sc}]{_fmt(s['total_pnl'], single_ccy)}[/{sc}]",
                    _fmt(s["avg_pnl"], single_ccy),
                ])
            instr_table.add_row(*row)
        console.print(instr_table)

    # Strategy breakdown
    breakdown = analytics.strategy_breakdown(closed)
    if len(breakdown) > 1:
        strat_table = Table(title="By Strategy")
        strat_table.add_column("Strategy", style="cyan")
        strat_table.add_column("Trades", justify="right")
        strat_table.add_column("Win Rate", justify="right")
        if not multi_ccy:
            strat_table.add_column("Total P&L", justify="right")
            strat_table.add_column("Avg P&L", justify="right")

        for s in breakdown:
            row = [s["strategy"], str(s["total_trades"]), f"{s['win_rate']}%"]
            if not multi_ccy:
                sc = "green" if s["total_pnl"] >= 0 else "red"
                row.extend([
                    f"[{sc}]{_fmt(s['total_pnl'], single_ccy)}[/{sc}]",
                    _fmt(s["avg_pnl"], single_ccy),
                ])
            strat_table.add_row(*row)
        console.print(strat_table)

    # Monthly breakdown
    monthly = analytics.monthly_breakdown(closed)
    if monthly["total_months"] > 0:
        month_table = Table(title="By Month")
        month_table.add_column("Period", style="cyan")
        month_table.add_column("Trades", justify="right")
        month_table.add_column("Win Rate", justify="right")
        if not multi_ccy:
            month_table.add_column("Total P&L", justify="right")

        # Show last 6 months
        for m in monthly["months"][-6:]:
            row = [m["period"], str(m["total_trades"]), f"{m['win_rate']}%"]
            if not multi_ccy:
                mc = "green" if m["total_pnl"] >= 0 else "red"
                row.append(f"[{mc}]{_fmt(m['total_pnl'], single_ccy)}[/{mc}]")
            month_table.add_row(*row)
        console.print(month_table)
        console.print(
            f"  Profitable months: {monthly['profitable_months']}/{monthly['total_months']} "
            f"({100 * monthly['profitable_months'] / monthly['total_months']:.0f}%)"
        )


# ── sync commands ────────────────────────────────────────────────────────────


@sync_app.command("export")
def sync_export(
    vault: Annotated[Optional[str], typer.Option("--vault", help="Obsidian vault path (overrides config)")] = None,
):
    """Export daily logs and position notes (Dataview-friendly) to an Obsidian vault."""
    cfg = _cfg()
    if vault:
        cfg.obsidian.vault_path = vault
    if not cfg.obsidian.vault_path:
        _fail("Obsidian vault_path not configured in .traderc.toml (or pass --vault)")
    conn = db.init_db(cfg.db_path)
    count = sync.export_to_obsidian(conn, cfg)
    _emit({"files": count, "vault": cfg.obsidian.vault_path},
          lambda: console.print(f"[green]Exported {count} files to Obsidian vault.[/green]"))


# ── fx commands ──────────────────────────────────────────────────────────────


def _require_yahoo() -> None:
    if not prices.yahoo_available():
        _fail("FX rates need yfinance: pip install 'obsidian-trading-tracker[yahoo]'")

DEFAULT_CURRENCIES = ["USD", "EUR", "RUB", "KZT"]


@fx_app.command("rate")
def fx_rate(
    base: str,
    quote: str,
    amount: Annotated[Optional[float], typer.Option("--amount", "-a")] = None,
):
    """Get exchange rate for a currency pair (e.g. trade fx rate USD KZT)."""
    _require_yahoo()
    rate = analytics.fetch_fx_rate(base, quote)

    if rate is None:
        _fail(f"Could not fetch rate for {base.upper()}/{quote.upper()}")
    data = {"base": base.upper(), "quote": quote.upper(), "rate": rate, "source": "yahoo"}
    if amount is not None:
        data["amount"], data["converted"] = amount, round(amount * rate, 2)

    def human() -> None:
        console.print(f"  [cyan]{base.upper()}/{quote.upper()}[/cyan] = [bold]{rate:.4f}[/bold]")
        if amount is not None:
            console.print(f"  {amount:.2f} {base.upper()} = [bold]{data['converted']:.2f} {quote.upper()}[/bold]")
    _emit(data, human)


@fx_app.command("matrix")
def fx_matrix(
    currencies: Annotated[
        Optional[str], typer.Argument(help="Comma-separated currencies (default: USD,EUR,RUB,KZT)")
    ] = None,
):
    """Show cross-rate matrix for multiple currencies."""
    if currencies:
        ccy_list = [c.strip().upper() for c in currencies.split(",")]
    else:
        cfg = _cfg()
        ccy_list = cfg.fx.currencies if cfg.fx.currencies else DEFAULT_CURRENCIES

    if len(ccy_list) < 2:
        _fail("Need at least 2 currencies.")

    _require_yahoo()
    rates = analytics.fetch_fx_matrix(ccy_list)
    if STATE.json:
        _emit({"currencies": ccy_list, "source": "yahoo",
               "rates": [{"base": b, "quote": q, "rate": r} for (b, q), r in rates.items()]})
        return

    table = Table(title="FX Cross Rates")
    table.add_column("", style="cyan bold")
    for c in ccy_list:
        table.add_column(c, justify="right")

    for base in ccy_list:
        row = [base]
        for quote in ccy_list:
            rate = rates.get((base, quote))
            if rate is None:
                row.append("-")
            elif base == quote:
                row.append("[dim]1[/dim]")
            elif rate >= 100:
                row.append(f"{rate:.2f}")
            elif rate >= 1:
                row.append(f"{rate:.4f}")
            else:
                row.append(f"{rate:.6f}")
        table.add_row(*row)
    console.print(table)


@fx_app.command("convert")
def fx_convert(
    amount: float,
    from_currency: str,
    to_currency: str,
):
    """Convert an amount between currencies (e.g. trade fx convert 1000 USD KZT)."""
    _require_yahoo()
    rate = analytics.fetch_fx_rate(from_currency, to_currency)

    if rate is None:
        _fail(f"Could not fetch rate for {from_currency.upper()}/{to_currency.upper()}")

    result = round(amount * rate, 2)
    _emit({"amount": amount, "from": from_currency.upper(), "to": to_currency.upper(), "rate": rate,
           "converted": result, "source": "yahoo"},
          lambda: console.print(
              f"  {amount:,.2f} {from_currency.upper()} = "
              f"[bold]{result:,.2f} {to_currency.upper()}[/bold]"
              f"  [dim](rate: {rate:.4f})[/dim]"))




def run() -> None:
    """Console entry point. In --json mode, argument errors are JSON too."""
    import click

    argv = sys.argv[1:]
    if "--json" not in argv:
        app()
        return
    try:
        code = app(args=argv, prog_name="trade", standalone_mode=False)
    except click.ClickException as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": exc.format_message(),
                                     "usage_error": True}, ensure_ascii=False) + "\n")
        sys.exit(1)
    except click.exceptions.Abort:
        sys.stdout.write(json.dumps({"ok": False, "error": "aborted"}) + "\n")
        sys.exit(1)
    except Exception as exc:  # never print a traceback to an agent
        sys.stdout.write(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                                    ensure_ascii=False) + "\n")
        sys.exit(1)
    sys.exit(code if isinstance(code, int) else 0)


if __name__ == "__main__":
    run()
