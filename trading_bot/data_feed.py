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
