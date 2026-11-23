"""Build a demo journal (random but reproducible) and render the dashboard.

    uv run python examples/make_demo.py /tmp/demo.db /tmp/demo.html
"""

import random
import sys
from datetime import datetime, timedelta

from trading_tracker import dashboard, db, report

path, out = sys.argv[1], sys.argv[2]
conn = db.init_db(path)
rng = random.Random(7)
tickers = {"NVDA": 120, "AAPL": 190, "MSFT": 410, "TSLA": 240, "AMD": 160, "XOM": 110, "SPY": 560}
strategies = ["swing", "breakout", "mean-reversion", "earnings"]
day = datetime(2025, 1, 6, 15, 30)
for _ in range(70):
    tk = rng.choice(list(tickers))
    px = tickers[tk] = round(tickers[tk] * rng.uniform(0.95, 1.06), 2)
    side = "buy" if rng.random() < 0.8 else "sell"
    qty = rng.choice([2, 5, 10, 20])
    stop = round(px * (0.94 if side == "buy" else 1.06), 2)
    tags = ["ai-idea"] if rng.random() < 0.4 else ["manual"]
    strat = rng.choice(strategies)
    db.add_trade(conn, tk, side, qty, px, commission=round(qty * px * 0.0012, 2), stop_loss=stop,
                 strategy=strat, tags=tags, timestamp=day.isoformat(), notes=f"{strat} setup")
    hold = rng.randint(1, 15)
    exit_px = round(px * rng.gauss(1.012 if side == "buy" else 0.99, 0.045), 2)
    db.add_trade(conn, tk, "sell" if side == "buy" else "buy", qty, exit_px,
                 commission=round(qty * exit_px * 0.0012, 2), timestamp=(day + timedelta(days=hold)).isoformat())
    day += timedelta(days=rng.randint(1, 5))
db.add_trade(conn, "NVDA", "buy", 10, 118.4, commission=1.42, stop_loss=109, tags=["ai-idea"],
             strategy="swing", notes="AI capex thesis", timestamp="2025-11-03T15:40")
db.add_trade(conn, "KMGZ", "buy", 20, 9050, currency="KZT", timestamp="2025-10-01T10:00", stop_loss=8500)
db.set_price(conn, "NVDA", 131.05, source="demo")
db.add_event(conn, "XOM", "dividend", amount=9.9, tax=1.49, timestamp="2025-06-10")
db.add_journal_entry(conn, "Chased a gap at the open again. Wait 15 minutes.", kind="mistake", mood=2, ticker="TSLA", date="2025-05-14")
db.add_journal_entry(conn, "Plan for the week: only A+ setups, max 3 positions.", kind="plan", mood=4, date="2025-06-02")
open(out, "w").write(dashboard.render(report.build_report(conn), title="Trading Journal (demo)"))
print(out)
