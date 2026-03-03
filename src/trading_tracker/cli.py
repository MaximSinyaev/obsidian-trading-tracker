"""CLI interface — Typer app with trade management commands."""

from __future__ import annotations

from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from trading_tracker import analytics, db, sync
from trading_tracker.config import load_config

app = typer.Typer(name="trade", help="Personal trading journal CLI.")
db_app = typer.Typer(name="db", help="Database management commands.")
sync_app = typer.Typer(name="sync", help="Obsidian sync commands.")
app.add_typer(db_app)
app.add_typer(sync_app)

console = Console()


def _get_conn():
    cfg = load_config()
    return db.init_db(cfg.db_path)


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
):
    """Add a new trade (BUY or SELL)."""
    cfg = load_config()
    conn = db.init_db(cfg.db_path)
    tag_list = [t.strip() for t in tags.split(",")] if tags else []

    trade_id = db.add_trade(
        conn,
        ticker,
        action,
        shares,
        price,
        commission=commission or cfg.defaults.commission,
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
    )
    console.print(
        f"[green]Trade #{trade_id}: {action.upper()} {shares} {ticker.upper()} @ ${price:.2f}[/green]"
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
def close(
    ticker: str,
    shares: float,
    price: float,
    commission: Annotated[float, typer.Option("--commission", "-c")] = 0,
    strategy: Annotated[Optional[str], typer.Option("--strategy", "-s")] = None,
    what_worked: Annotated[Optional[str], typer.Option("--what-worked")] = None,
    what_failed: Annotated[Optional[str], typer.Option("--what-failed")] = None,
    lesson: Annotated[Optional[str], typer.Option("--lesson")] = None,
    rating: Annotated[Optional[int], typer.Option("--rating")] = None,
):
    """Close (fully or partially) a position."""
    conn = _get_conn()
    try:
        result = db.close_position(
            conn,
            ticker,
            shares,
            price,
            commission=commission,
            strategy=strategy,
            what_worked=what_worked,
            what_failed=what_failed,
            lesson=lesson,
            rating=rating,
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None

    color = "green" if result["net_pnl"] >= 0 else "red"
    console.print(
        f"[{color}]Closed {result['shares']} {result['ticker']} | "
        f"Entry: ${result['avg_entry']:.2f} → Exit: ${result['exit_price']:.2f} | "
        f"P&L: ${result['net_pnl']:.2f} ({result['pnl_percent']:.1f}%)[/{color}]"
    )
    if result["remaining_shares"] > 0:
        console.print(
            f"  Remaining: {result['remaining_shares']} shares @ ${result['remaining_avg_cost']:.2f}"
        )


@app.command()
def positions():
    """Show all open positions."""
    conn = _get_conn()
    pos = db.get_positions(conn)
    if not pos:
        console.print("[yellow]No open positions.[/yellow]")
        return

    table = Table(title="Open Positions")
    table.add_column("Ticker", style="cyan bold")
    table.add_column("Shares", justify="right")
    table.add_column("Avg Cost", justify="right")
    table.add_column("Cost Basis", justify="right")
    table.add_column("First Trade")
    table.add_column("Trades", justify="right")

    for p in pos:
        cost_basis = p["net_shares"] * p["avg_cost"]
        table.add_row(
            p["ticker"],
            f"{p['net_shares']:.2f}",
            f"${p['avg_cost']:.2f}",
            f"${cost_basis:.2f}",
            p["first_trade"][:10],
            str(p["trade_count"]),
        )
    console.print(table)


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
            pnl_color = "green" if t["net_pnl"] >= 0 else "red"
            table.add_row(
                str(t["id"]),
                t["ticker"],
                f"{t['shares']:.2f}",
                f"${t['avg_entry_price']:.2f}",
                f"${t['avg_exit_price']:.2f}",
                f"[{pnl_color}]${t['net_pnl']:.2f}[/{pnl_color}]",
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
            action_color = "green" if t["action"] == "BUY" else "red"
            table.add_row(
                str(t["id"]),
                t["ticker"],
                f"[{action_color}]{t['action']}[/{action_color}]",
                f"{t['shares']:.2f}",
                f"${t['price']:.2f}",
                f"${t['commission']:.2f}",
                t.get("strategy") or "-",
                t["timestamp"][:16],
            )
    console.print(table)


@app.command()
def stats():
    """Show trading statistics."""
    conn = _get_conn()
    closed = db.get_closed_trades(conn)
    overall = analytics.compute_stats(closed)

    if overall["total_trades"] == 0:
        console.print("[yellow]No closed trades yet.[/yellow]")
        return

    table = Table(title="Trading Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    pnl_color = "green" if overall["total_pnl"] >= 0 else "red"
    table.add_row("Total Trades", str(overall["total_trades"]))
    table.add_row("Wins / Losses", f"{overall['wins']} / {overall['losses']}")
    table.add_row("Win Rate", f"{overall['win_rate']}%")
    table.add_row("Total P&L", f"[{pnl_color}]${overall['total_pnl']:.2f}[/{pnl_color}]")
    table.add_row("Avg P&L", f"${overall['avg_pnl']:.2f}")
    table.add_row("Best Trade", f"[green]${overall['best_trade']:.2f}[/green]")
    table.add_row("Worst Trade", f"[red]${overall['worst_trade']:.2f}[/red]")
    table.add_row("Total Commission", f"${overall['total_commission']:.2f}")
    table.add_row("Avg Hold (days)", f"{overall['avg_hold_days']:.1f}")
    console.print(table)

    # Strategy breakdown
    breakdown = analytics.strategy_breakdown(closed)
    if len(breakdown) > 1:
        strat_table = Table(title="By Strategy")
        strat_table.add_column("Strategy", style="cyan")
        strat_table.add_column("Trades", justify="right")
        strat_table.add_column("Win Rate", justify="right")
        strat_table.add_column("Total P&L", justify="right")
        strat_table.add_column("Avg P&L", justify="right")

        for s in breakdown:
            sc = "green" if s["total_pnl"] >= 0 else "red"
            strat_table.add_row(
                s["strategy"],
                str(s["total_trades"]),
                f"{s['win_rate']}%",
                f"[{sc}]${s['total_pnl']:.2f}[/{sc}]",
                f"${s['avg_pnl']:.2f}",
            )
        console.print(strat_table)


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


if __name__ == "__main__":
    app()
