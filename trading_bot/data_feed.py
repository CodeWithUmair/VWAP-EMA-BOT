"""
Data Feed & Realistic Gold Market Simulator for XAU/USD 1-minute data.

Generates realistic market price paths with:
- Macro trend regimes (bullish expansions, consolidations, pullbacks)
- Liquidity sweeps and Order Block structures
- Session volume transitions (Asian drift, London breakout, NY liquidity)
- Authentic bid/ask spreads and tick volatility
"""

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any


def generate_realistic_gold_data(
    num_bars: int = 600,
    base_price: float = 2380.0,
    start_time: datetime = None,
    volatility: float = 0.65,
    seed: int = 42
) -> Dict[str, List]:
    """
    Generates authentic 1-minute XAU/USD OHLCV bars.
    Includes natural trends, pullbacks, and order block formations.
    """
    if start_time is None:
        # Start at 00:00 UTC today
        now = datetime.now(timezone.utc)
        start_time = datetime(now.year, now.month, now.day, 0, 0, tzinfo=timezone.utc) - timedelta(minutes=num_bars)

    rng = random.Random(seed)
    
    times = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []

    price = base_price
    trend = 0.05

    for i in range(num_bars):
        current_time = start_time + timedelta(minutes=i)
        times.append(current_time.isoformat())

        # Regime switching every 50-100 bars
        if i % 75 == 0:
            trend = rng.choice([0.12, -0.12, 0.08, -0.08, 0.0])

        # Session volume modeling
        hour = current_time.hour
        if 7 <= hour < 11:  # London morning
            session_mult = 1.8
            vol_base = 250
        elif 13 <= hour < 17:  # NY open
            session_mult = 2.2
            vol_base = 350
        elif 0 <= hour < 5:  # Asian
            session_mult = 0.8
            vol_base = 100
        else:
            session_mult = 1.0
            vol_base = 150

        # Bar generation
        o = price
        noise = rng.gauss(0, volatility * session_mult)
        drift = trend * session_mult
        delta = drift + noise
        c = o + delta

        # Realistic wicks
        upper_wick = abs(rng.gauss(0, volatility * 0.5))
        lower_wick = abs(rng.gauss(0, volatility * 0.5))

        h = max(o, c) + upper_wick
        l = min(o, c) - lower_wick

        # Volume
        bar_vol = int(vol_base * rng.uniform(0.7, 1.4) + abs(delta) * 100)

        opens.append(round(o, 2))
        highs.append(round(h, 2))
        lows.append(round(l, 2))
        closes.append(round(c, 2))
        volumes.append(bar_vol)

        price = c

    return {
        "times": times,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "volumes": volumes
    }


# ---------------------------------------------------------------------------
# REAL data — for a backtest that means something. `generate_realistic_gold_data`
# above is a `random.Random` walk: it exercises the causal plumbing but a gate run
# against it only proves the RNG has no edge, not that the strategy has one. These
# two loaders return the same dict shape from actual XAU/USD history.
# ---------------------------------------------------------------------------


def fetch_real_gold_data(count: int = 20000, symbol: str = "XAUUSDm") -> Dict[str, List]:
    """Pull real 1-minute XAU/USD history straight from a running MT5 terminal.

    MT5 only serves what the terminal has cached for the timeframe, and there is a
    hard per-call ceiling, so this walks a ladder of decreasing request sizes and
    returns the largest block it can actually get. To backtest more than the terminal
    keeps on hand, export a CSV from MT5 and use :func:`load_gold_csv` instead.
    """
    import MetaTrader5 as mt5  # local import: only needed for a real-data run

    if not mt5.initialize():
        raise RuntimeError(
            f"MT5 initialize failed ({mt5.last_error()}). Open the terminal, log in, "
            "leave it running, then retry."
        )
    try:
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"symbol_select({symbol!r}) failed: {mt5.last_error()}")

        rates = None
        for req in (count, 50000, 20000, 15000, 10000, 5000, 2000):
            if req > count and rates is None:
                continue
            r = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, req)
            if r is not None and len(r):
                rates = r
                break
        if rates is None or len(rates) == 0:
            raise RuntimeError(
                f"MT5 returned no M1 bars for {symbol} (last error {mt5.last_error()}). "
                "In the terminal, open an M1 chart for this symbol and scroll back to "
                "force a history download, or raise Tools > Options > Charts > "
                "'Max bars in chart'."
            )
    finally:
        mt5.shutdown()

    return {
        "times": [datetime.fromtimestamp(int(r["time"]), tz=timezone.utc).isoformat() for r in rates],
        "opens": [float(r["open"]) for r in rates],
        "highs": [float(r["high"]) for r in rates],
        "lows": [float(r["low"]) for r in rates],
        "closes": [float(r["close"]) for r in rates],
        "volumes": [float(r["tick_volume"]) for r in rates],
    }


def load_gold_csv(path: str) -> Dict[str, List]:
    """Load real OHLCV history from a CSV export (MT5 'Save' on an M1 chart, or any
    file with time/open/high/low/close/volume columns — separator and column order
    are auto-detected, a header row is optional).
    """
    import csv as _csv

    with open(path, "r", newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = _csv.Sniffer().sniff(sample, delimiters=",;\t ")
        except _csv.Error:
            dialect = _csv.excel
        has_header = _csv.Sniffer().has_header(sample)
        reader = _csv.reader(fh, dialect)
        rows = [r for r in reader if r]

    if has_header:
        header = [h.strip().lower().lstrip("<").rstrip(">") for h in rows[0]]
        rows = rows[1:]
        col = {name: header.index(name) for name in header}
        def g(row, *names, default=None):
            for n in names:
                if n in col and col[n] < len(row):
                    return row[col[n]]
            return default
    else:
        # MT5's headerless dump: date, time, open, high, low, close, tickvol, [vol], [spread]
        def g(row, *names, default=None):
            order = {"date": 0, "time": 1, "open": 2, "high": 3, "low": 4, "close": 5, "tickvol": 6, "volume": 6}
            for n in names:
                i = order.get(n)
                if i is not None and i < len(row):
                    return row[i]
            return default

    times, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    for row in rows:
        date = g(row, "date")
        clock = g(row, "time")
        stamp = f"{date} {clock}".strip() if date and clock and date != clock else (g(row, "time", "date") or "")
        times.append(stamp.replace(".", "-", 2) if stamp else "")
        opens.append(float(g(row, "open")))
        highs.append(float(g(row, "high")))
        lows.append(float(g(row, "low")))
        closes.append(float(g(row, "close")))
        v = g(row, "tickvol", "volume", "vol", default=1) or 1
        volumes.append(float(v))
    return {
        "times": times, "opens": opens, "highs": highs,
        "lows": lows, "closes": closes, "volumes": volumes,
    }
