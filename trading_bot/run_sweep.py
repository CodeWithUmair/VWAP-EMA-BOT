"""
Parameter sweep: run one strategy across a grid of settings on the same bars.

A single backtest tells you whether one configuration made money. A sweep tells
you whether the *idea* holds up — if only one corner of the grid works, you have
found noise; if a whole region works, you may have found something.

    python -m trading_bot.run_sweep --strategy crt_tbs --real --bars 50000
    python -m trading_bot.run_sweep --strategy crt_tbs --csv data/XAUUSD_M1.csv

Read the output as a whole, not as a leaderboard: the top row of a 64-row sweep
is the best of 64 draws, and some of that is luck. Trust patterns that hold
across many rows.
"""

import argparse
import itertools
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.backtest import run_causal_backtest
from trading_bot.data_feed import generate_realistic_gold_data
from trading_bot.strategies import DEFAULT_STRATEGY_KEY, REGISTRY, get_strategy


# The axes worth varying per strategy. Keep these small — a grid of 6 binary
# knobs is already 64 backtests, and every extra axis makes the best row less
# meaningful, not more.
SWEEP_GRIDS = {
    "crt_tbs": {
        "reference_tf": ["H1", "H4"],
        "execution_tf": ["M5", "M15"],
        "min_sweep_points": [0.30, 1.00],
        "sl_buffer_points": [1.20, 2.50],
        "use_tp1_partial": [True, False],
        "enable_killzone_filter": [True, False],
    },
    "vwap_ema_scalper": {
        "rr_ratio": [1.5, 2.0],
        "max_pullback_bars": [15, 35],
        "pullback_atr_mult": [1.0, 1.8],
        "sl_buffer_atr": [0.20, 0.50],
        "ob_max_age_bars": [40, 60],
    },
}


def load_data(source, bars, csv_path, symbol):
    if source == "csv":
        from trading_bot.data_feed import load_gold_csv
        return load_gold_csv(csv_path)
    if source == "real":
        from trading_bot.data_feed import fetch_real_gold_data
        return fetch_real_gold_data(count=bars, symbol=symbol)
    return generate_realistic_gold_data(num_bars=bars, seed=101)


def run_sweep(strategy_key, data, lot_size=0.1, spread=0.25, commission=7.0):
    strategy = get_strategy(strategy_key)
    grid = SWEEP_GRIDS.get(strategy.key, {})
    if not grid:
        raise SystemExit(f"No sweep grid defined for {strategy.key}")

    base = {s.key: s.default for s in strategy.param_specs()}
    keys = list(grid)
    combos = list(itertools.product(*(grid[k] for k in keys)))

    print(f"Sweeping {strategy.label}: {len(combos)} configurations "
          f"over {len(data['closes']):,} bars")
    print(f"  {data['times'][0]}  ->  {data['times'][-1]}\n")
    width = max(len(", ".join(f"{k}={v}" for k, v in zip(keys, c))) for c in combos)
    print(f"  {'configuration':{width}} {'n':>4} {'win%':>6} {'PF':>5} "
          f"{'E(R)':>7} {'net $':>10} {'DD%':>5} {'hold':>6} {'OOS E':>7}")
    print("  " + "-" * (width + 56))

    rows = []
    for combo in combos:
        cfg = dict(zip(keys, combo))
        params = strategy.build_params(dict(base, **cfg))
        result = run_causal_backtest(
            data["opens"], data["highs"], data["lows"], data["closes"],
            data["times"], data["volumes"], params,
            strategy=strategy, fixed_lot_size=lot_size,
            spread_points=spread, commission_per_lot_usd=commission,
            num_noise_shuffles=1,
        )
        m, oos = result.overall_metrics, result.out_of_sample_metrics
        rows.append({"cfg": cfg, "metrics": m, "oos": oos})

        label = ", ".join(f"{k}={v}" for k, v in cfg.items())
        print(f"  {label:{width}} {m.total_trades:4} {m.win_rate_pct:6.1f} "
              f"{m.profit_factor:5.2f} {m.expectancy_r:+7.3f} {m.total_net_pnl_usd:+10.2f} "
              f"{m.max_drawdown_pct:5.1f} {m.average_trade_bars:5.0f}b {oos.expectancy_r:+7.2f}",
              flush=True)

    rows.sort(key=lambda r: -r["metrics"].expectancy_r)
    positive = [r for r in rows if r["metrics"].expectancy_r > 0]

    print(f"\n  {len(positive)}/{len(rows)} configurations had positive expectancy.")
    print("\n  Top 5 by expectancy — remember these are the best of "
          f"{len(rows)} draws, so treat a lone winner as luck until it repeats:")
    for r in rows[:5]:
        m = r["metrics"]
        print(f"    E={m.expectancy_r:+.3f}R  PF={m.profit_factor:.2f}  n={m.total_trades:4}  "
              f"win={m.win_rate_pct:.1f}%  net=${m.total_net_pnl_usd:+,.2f}  "
              f"OOS={r['oos'].expectancy_r:+.2f}R")
        print(f"      {r['cfg']}")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Sweep a strategy's parameters over one dataset.")
    ap.add_argument("--strategy", default=DEFAULT_STRATEGY_KEY, choices=sorted(REGISTRY))
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--real", action="store_true", help="real M1 history from the MT5 terminal")
    src.add_argument("--csv", metavar="PATH", help="real M1 history from a CSV export")
    ap.add_argument("--symbol", default="XAUUSDm")
    ap.add_argument("--bars", type=int, default=50000)
    ap.add_argument("--lot", type=float, default=0.1)
    ap.add_argument("--json", metavar="PATH", help="also write the raw rows here")
    a = ap.parse_args()

    source = "csv" if a.csv else "real" if a.real else "synthetic"
    dataset = load_data(source, a.bars, a.csv, a.symbol)
    results = run_sweep(a.strategy, dataset, lot_size=a.lot)

    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump([{
                "cfg": r["cfg"],
                "trades": r["metrics"].total_trades,
                "win_rate_pct": r["metrics"].win_rate_pct,
                "profit_factor": r["metrics"].profit_factor,
                "expectancy_r": r["metrics"].expectancy_r,
                "net_pnl_usd": r["metrics"].total_net_pnl_usd,
                "max_dd_pct": r["metrics"].max_drawdown_pct,
                "oos_expectancy_r": r["oos"].expectancy_r,
            } for r in results], fh, indent=1)
        print(f"\n  Raw rows written to {a.json}")
