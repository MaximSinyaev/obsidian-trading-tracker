"""CLI interface — Typer app with trade management commands."""

from __future__ import annotations

from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from importlib.metadata import version as pkg_version

from trading_tracker import analytics, db, sync
from trading_tracker.config import load_config
from trading_tracker.models import currency_symbol


def _version_callback(value: bool):
    if value:
        v = pkg_version("obsidian-trading-tracker")
        print(f"trade {v}")
        raise typer.Exit()


app = typer.Typer(name="trade")
db_app = typer.Typer(name="db", help="Database management commands.")
sync_app = typer.Typer(name="sync", help="Obsidian sync commands.")
fx_app = typer.Typer(name="fx", help="Currency exchange rates.")
app.add_typer(db_app)
app.add_typer(sync_app)
app.add_typer(fx_app)

console = Console()


@app.callback()
def main(
    version: Annotated[
        Optional[bool], typer.Option("--version", "-v", callback=_version_callback, is_eager=True)
    ] = None,
):
    """Personal trading journal CLI."""


def _get_conn():
    cfg = load_config()
    return db.init_db(cfg.db_path)


def _fmt(amount: float, ccy: str = "USD") -> str:
    """Format amount with currency symbol: '$1,234.56' or '₸9,092.00'."""
    sym = currency_symbol(ccy)
    return f"{sym}{amount:,.2f}"


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
    """Initialize the trading database."""
    cfg = load_config()
    db.init_db(cfg.db_path)
    console.print(f"[green]Database initialized at {cfg.db_path}[/green]")


@db_app.command("validate")
def db_validate():
    """Check database consistency."""
    conn = _get_conn()
    issues = db.validate_db(conn)
    if issues:
        console.print("[red]Issues found:[/red]")
        for issue in issues:
            console.print(f"  - {issue}")
    else:
        console.print("[green]Database is consistent.[/green]")


# ── trade commands ───────────────────────────────────────────────────────────


@app.command()
def add(
    ticker: str,
    action: str,
    shares: float,
    price: float,
    commission: Annotated[float, typer.Option("--commission", "-c")] = 0,
    strategy: Annotated[Optional[str], typer.Option("--strategy", "-s")] = None,
    sl: Annotated[Optional[float], typer.Option("--sl")] = None,
    tp1: Annotated[Optional[float], typer.Option("--tp1")] = None,
    tp2: Annotated[Optional[float], typer.Option("--tp2")] = None,
    confidence: Annotated[Optional[int], typer.Option("--confidence")] = None,
    notes: Annotated[Optional[str], typer.Option("--notes", "-n")] = None,
    tags: Annotated[Optional[str], typer.Option("--tags", "-t")] = None,
    source: Annotated[Optional[str], typer.Option("--source")] = None,
    group: Annotated[Optional[str], typer.Option("--group")] = None,
    timestamp: Annotated[Optional[str], typer.Option("--date", "--timestamp")] = None,
    instrument: Annotated[Optional[str], typer.Option("--instrument", "-i")] = None,
    leverage: Annotated[float, typer.Option("--leverage")] = 1.0,
    currency: Annotated[str, typer.Option("--currency", "--ccy")] = "USD",
):
    """Add a new trade (BUY or SELL)."""
    cfg = load_config()
    conn = db.init_db(cfg.db_path)
    tag_list = [t.strip() for t in tags.split(",")] if tags else []

    # Snapshot position before to detect auto-close
    pos_before = db.get_position(conn, ticker)

    trade_id = db.add_trade(
        conn,
        ticker,
        action,
        shares,
        price,
        commission=commission or cfg.defaults.commission,
        timestamp=timestamp,
        strategy=strategy,
        stop_loss=sl,
        target_1=tp1,
        target_2=tp2,
        confidence=confidence,
        notes=notes,
        tags=tag_list,
        source=source or cfg.defaults.source,
        position_group=group,
        asset_type=cfg.defaults.asset_type,
        instrument=instrument or cfg.defaults.asset_type,
        currency=currency,
        leverage=leverage,
    )
    sym = currency_symbol(currency)
    console.print(
        f"[green]Trade #{trade_id}: {action.upper()} {shares} {ticker.upper()} @ {sym}{price:.2f}[/green]"
    )

    # Show auto-close info if position was reduced
    if pos_before is not None:
        net_before = pos_before["net_shares"]
        is_long = net_before > 0
        is_counter = (is_long and action.upper() == "SELL") or (
            not is_long and action.upper() == "BUY"
        )
        if is_counter:
            ct = conn.execute(
                "SELECT * FROM closed_trades WHERE ticker = ? ORDER BY id DESC LIMIT 1",
                (ticker.upper(),),
            ).fetchone()
            if ct:
                dir_label = "SHORT" if ct["direction"] == "short" else "LONG"
                color = "green" if ct["net_pnl"] >= 0 else "red"
                ct_ccy = ct["currency"] if "currency" in ct.keys() else currency
                console.print(
                    f"[{color}]  ↳ Closed {dir_label}: "
                    f"{_fmt(ct['avg_entry_price'], ct_ccy)} → {_fmt(ct['avg_exit_price'], ct_ccy)} | "
                    f"P&L: {_fmt(ct['net_pnl'], ct_ccy)} ({ct['pnl_percent']:.1f}%)[/{color}]"
                )
                pos_after = db.get_position(conn, ticker)
                if pos_after is None:
                    console.print("  Position fully closed.")
                else:
                    remaining = abs(pos_after["net_shares"])
                    console.print(
                        f"  Remaining: {remaining:.2f} shares @ {_fmt(pos_after['avg_cost'], ct_ccy)}"
                    )


@app.command()
def edit(
    trade_id: int,
    ticker: Annotated[Optional[str], typer.Option("--ticker")] = None,
    action: Annotated[Optional[str], typer.Option("--action")] = None,
    shares: Annotated[Optional[float], typer.Option("--shares")] = None,
    price: Annotated[Optional[float], typer.Option("--price")] = None,
    commission: Annotated[Optional[float], typer.Option("--commission", "-c")] = None,
    strategy: Annotated[Optional[str], typer.Option("--strategy", "-s")] = None,
    sl: Annotated[Optional[float], typer.Option("--sl")] = None,
    tp1: Annotated[Optional[float], typer.Option("--tp1")] = None,
    tp2: Annotated[Optional[float], typer.Option("--tp2")] = None,
    confidence: Annotated[Optional[int], typer.Option("--confidence")] = None,
    notes: Annotated[Optional[str], typer.Option("--notes", "-n")] = None,
    tags: Annotated[Optional[str], typer.Option("--tags", "-t")] = None,
):
    """Edit an existing trade by ID."""
    conn = _get_conn()
    existing = db.get_trade(conn, trade_id)
    if not existing:
        console.print(f"[red]Trade #{trade_id} not found.[/red]")
        raise typer.Exit(1)

    fields: dict = {}
    if ticker is not None:
        fields["ticker"] = ticker
    if action is not None:
        fields["action"] = action
    if shares is not None:
        fields["shares"] = shares
    if price is not None:
        fields["price"] = price
    if commission is not None:
        fields["commission"] = commission
    if strategy is not None:
        fields["strategy"] = strategy
    if sl is not None:
        fields["stop_loss"] = sl
    if tp1 is not None:
        fields["target_1"] = tp1
    if tp2 is not None:
        fields["target_2"] = tp2
    if confidence is not None:
        fields["confidence"] = confidence
    if notes is not None:
        fields["notes"] = notes
    if tags is not None:
        fields["tags"] = [t.strip() for t in tags.split(",")]

    if not fields:
        console.print("[yellow]No fields to update.[/yellow]")
        raise typer.Exit(0)

    updated = db.edit_trade(conn, trade_id, **fields)
    if updated:
        console.print(f"[green]Trade #{trade_id} updated: {', '.join(fields.keys())}[/green]")
    else:
        console.print(f"[red]Failed to update trade #{trade_id}.[/red]")


@app.command()
def show(trade_id: int):
    """Show full details of a single trade."""
    conn = _get_conn()
    trade = db.get_trade(conn, trade_id)
    if not trade:
        console.print(f"[red]Trade #{trade_id} not found.[/red]")
        raise typer.Exit(1)

    import json as _json

    table = Table(title=f"Trade #{trade_id}", show_header=False)
    table.add_column("Field", style="cyan")
    table.add_column("Value")

    skip = {"created_at", "updated_at"}
    for key, value in dict(trade).items():
        if key in skip:
            continue
        if key == "tags":
            try:
                value = ", ".join(_json.loads(value)) or "-"
            except (TypeError, _json.JSONDecodeError):
                pass
        display = str(value) if value is not None else "-"
        table.add_row(key, display)

    table.add_row("created_at", trade["created_at"])
    table.add_row("updated_at", trade["updated_at"])
    console.print(table)


@app.command()
def delete(
    trade_id: int,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
):
    """Delete a trade by ID."""
    conn = _get_conn()
    trade = db.get_trade(conn, trade_id)
    if not trade:
        console.print(f"[red]Trade #{trade_id} not found.[/red]")
        raise typer.Exit(1)

    trade_ccy = trade.get("currency", "USD")
    console.print(
        f"  Trade #{trade_id}: {trade['action']} {trade['shares']} {trade['ticker']} "
        f"@ {_fmt(trade['price'], trade_ccy)} ({trade['timestamp'][:10]})"
    )
    if not yes:
        confirm = typer.confirm("Delete this trade?")
        if not confirm:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)

    db.delete_trade(conn, trade_id)
    console.print(f"[green]Trade #{trade_id} deleted.[/green]")


@app.command(deprecated=True)
def close(
    ticker: str = typer.Argument(default=""),
    shares: float = typer.Argument(default=0),
    price: float = typer.Argument(default=0),
):
    """Deprecated: use 'trade add <ticker> sell <shares> <price>' instead."""
    console.print(
        "[yellow]The 'close' command is deprecated.[/yellow]\n"
        "Positions are now closed automatically when you add a counter-trade:\n"
        "  [cyan]trade add TICKER sell SHARES PRICE[/cyan]  (to close a long)\n"
        "  [cyan]trade add TICKER buy SHARES PRICE[/cyan]   (to close a short)"
    )
    raise typer.Exit(1)


@app.command()
def positions(
    no_live: Annotated[bool, typer.Option("--no-live", help="Skip live price fetching")] = False,
):
    """Show all open positions with live prices."""
    conn = _get_conn()
    pos = db.get_positions(conn)
    if not pos:
        console.print("[yellow]No open positions.[/yellow]")
        return

    if not no_live:
        with console.status("Fetching live prices..."):
            pos = analytics.enrich_positions_with_prices(pos, live=True)
    else:
        pos = analytics.enrich_positions_with_prices(pos, live=False)

    has_prices = any(p.get("current_price") is not None for p in pos)

    currencies_in_use = {p.get("currency", "USD") for p in pos}
    show_ccy_col = len(currencies_in_use) > 1

    table = Table(title="Open Positions")
    table.add_column("Ticker", style="cyan bold")
    if show_ccy_col:
        table.add_column("CCY")
    table.add_column("Dir")
    table.add_column("Shares", justify="right")
    table.add_column("Avg Cost", justify="right")
    table.add_column("Cost Basis", justify="right")
    if has_prices:
        table.add_column("Price", justify="right")
        table.add_column("Mkt Value", justify="right")
        table.add_column("P&L", justify="right")
        table.add_column("P&L %", justify="right")
    table.add_column("First Trade")
    table.add_column("Trades", justify="right")

    total_cost = 0.0
    total_value = 0.0
    total_pnl = 0.0

    for p in pos:
        net = p["net_shares"]
        is_long = net > 0
        abs_shares = abs(net)
        ccy = p.get("currency", "USD")
        cost_basis = abs_shares * p["avg_cost"]
        total_cost += cost_basis
        dir_label = "[green]LONG[/green]" if is_long else "[red]SHORT[/red]"
        row = [p["ticker"]]
        if show_ccy_col:
            row.append(ccy)
        row.extend([
            dir_label,
            f"{abs_shares:.2f}",
            _fmt(p["avg_cost"], ccy),
            _fmt(cost_basis, ccy),
        ])
        if has_prices:
            price = p.get("current_price")
            if price is not None:
                mkt_val = abs_shares * price
                pnl = p["unrealized_pnl"]
                pnl_pct = p["unrealized_pnl_pct"]
                total_value += mkt_val
                total_pnl += pnl
                color = "green" if pnl >= 0 else "red"
                row.extend([
                    _fmt(price, ccy),
                    _fmt(mkt_val, ccy),
                    f"[{color}]{_fmt(pnl, ccy)}[/{color}]",
                    f"[{color}]{pnl_pct:+.1f}%[/{color}]",
                ])
            else:
                row.extend(["-", "-", "-", "-"])
        row.extend([p["first_trade"][:10], str(p["trade_count"])])
        table.add_row(*row)

    console.print(table)

    if has_prices and total_cost > 0:
        color = "green" if total_pnl >= 0 else "red"
        pct = (total_pnl / total_cost) * 100
        console.print(
            f"\n  Total: cost ${total_cost:.2f} → value ${total_value:.2f} | "
            f"[{color}]P&L ${total_pnl:+.2f} ({pct:+.1f}%)[/{color}]"
        )


@app.command()
def history(
    limit: Annotated[int, typer.Option("--limit", "-l")] = 20,
    ticker: Annotated[Optional[str], typer.Option("--ticker", "-t")] = None,
    from_date: Annotated[Optional[str], typer.Option("--from")] = None,
    to_date: Annotated[Optional[str], typer.Option("--to")] = None,
    closed: Annotated[bool, typer.Option("--closed")] = False,
):
    """Show trade history."""
    conn = _get_conn()
    trades = db.get_history(
        conn, limit=limit, ticker=ticker, from_date=from_date, to_date=to_date, closed_only=closed
    )
    if not trades:
        console.print("[yellow]No trades found.[/yellow]")
        return

    if closed:
        table = Table(title="Closed Trades")
        table.add_column("ID", justify="right")
        table.add_column("Ticker", style="cyan bold")
        table.add_column("Shares", justify="right")
        table.add_column("Entry", justify="right")
        table.add_column("Exit", justify="right")
        table.add_column("Net P&L", justify="right")
        table.add_column("P&L %", justify="right")
        table.add_column("Closed At")

        for t in trades:
            ccy = t.get("currency", "USD")
            pnl_color = "green" if t["net_pnl"] >= 0 else "red"
            table.add_row(
                str(t["id"]),
                t["ticker"],
                f"{t['shares']:.2f}",
                _fmt(t["avg_entry_price"], ccy),
                _fmt(t["avg_exit_price"], ccy),
                f"[{pnl_color}]{_fmt(t['net_pnl'], ccy)}[/{pnl_color}]",
                f"[{pnl_color}]{t['pnl_percent']:.1f}%[/{pnl_color}]",
                t["closed_at"][:10],
            )
    else:
        table = Table(title="Trade History")
        table.add_column("ID", justify="right")
        table.add_column("Ticker", style="cyan bold")
        table.add_column("Action")
        table.add_column("Shares", justify="right")
        table.add_column("Price", justify="right")
        table.add_column("Commission", justify="right")
        table.add_column("Strategy")
        table.add_column("Time")

        for t in trades:
            ccy = t.get("currency", "USD")
            action_color = "green" if t["action"] == "BUY" else "red"
            table.add_row(
                str(t["id"]),
                t["ticker"],
                f"[{action_color}]{t['action']}[/{action_color}]",
                f"{t['shares']:.2f}",
                _fmt(t["price"], ccy),
                _fmt(t["commission"], ccy),
                t.get("strategy") or "-",
                t["timestamp"][:16],
            )
    console.print(table)


@app.command()
def stats():
    """Show trading statistics."""
    cfg = load_config()
    conn = db.init_db(cfg.db_path)
    closed = db.get_closed_trades(conn)
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
        rates = analytics.fetch_fx_matrix([base_ccy] + other_ccys) if other_ccys else None
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
def sync_export():
    """Export trades to Obsidian vault as markdown."""
    cfg = load_config()
    if not cfg.obsidian.vault_path:
        console.print(
            "[red]Obsidian vault_path not configured in .traderc.toml[/red]"
        )
        raise typer.Exit(1)
    conn = db.init_db(cfg.db_path)
    count = sync.export_to_obsidian(conn, cfg)
    console.print(f"[green]Exported {count} files to Obsidian vault.[/green]")


# ── fx commands ──────────────────────────────────────────────────────────────

DEFAULT_CURRENCIES = ["USD", "EUR", "RUB", "KZT"]


@fx_app.command("rate")
def fx_rate(
    base: str,
    quote: str,
    amount: Annotated[Optional[float], typer.Option("--amount", "-a")] = None,
):
    """Get exchange rate for a currency pair (e.g. trade fx rate USD KZT)."""
    with console.status(f"Fetching {base.upper()}/{quote.upper()}..."):
        rate = analytics.fetch_fx_rate(base, quote)

    if rate is None:
        console.print(f"[red]Could not fetch rate for {base.upper()}/{quote.upper()}[/red]")
        raise typer.Exit(1)

    console.print(f"  [cyan]{base.upper()}/{quote.upper()}[/cyan] = [bold]{rate:.4f}[/bold]")
    if amount is not None:
        converted = round(amount * rate, 2)
        console.print(f"  {amount:.2f} {base.upper()} = [bold]{converted:.2f} {quote.upper()}[/bold]")


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
        cfg = load_config()
        ccy_list = cfg.fx.currencies if cfg.fx.currencies else DEFAULT_CURRENCIES

    if len(ccy_list) < 2:
        console.print("[red]Need at least 2 currencies.[/red]")
        raise typer.Exit(1)

    with console.status(f"Fetching rates for {', '.join(ccy_list)}..."):
        rates = analytics.fetch_fx_matrix(ccy_list)

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
    with console.status(f"Converting {from_currency.upper()} → {to_currency.upper()}..."):
        rate = analytics.fetch_fx_rate(from_currency, to_currency)

    if rate is None:
        console.print(f"[red]Could not fetch rate for {from_currency.upper()}/{to_currency.upper()}[/red]")
        raise typer.Exit(1)

    result = round(amount * rate, 2)
    console.print(
        f"  {amount:,.2f} {from_currency.upper()} = "
        f"[bold]{result:,.2f} {to_currency.upper()}[/bold]"
        f"  [dim](rate: {rate:.4f})[/dim]"
    )


if __name__ == "__main__":
    app()
