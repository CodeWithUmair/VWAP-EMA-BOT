"""
CLI Backtest Runner & Noise Control Statistical Significance Gate.
Runs causal backtest on historical/simulated XAU/USD 1m bars and reports:
- In-Sample (IS) vs Out-of-Sample (OOS) splits
- Win Rate, Profit Factor, Expectancy (R & $), Max Drawdown, Trade Count
- Noise-Control Monte Carlo reshuffle p-value and Z-score
"""

import os
import sys
import json
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.strategy import StrategyParameters
from trading_bot.data_feed import generate_realistic_gold_data
from trading_bot.backtest import run_causal_backtest, BacktestResult


def format_metrics_table(metrics, title):
    p_val_str = f"{metrics.noise_p_value:.4f}"
    gate_status = "PASSED (p <= 0.05)" if metrics.noise_gate_passed else f"FAILED (p = {p_val_str} > 0.05)"
    
    return f"""
+-----------------------------------------------------------------------+
|  {title.center(67)}  |
+-----------------------------------------------------------------------+
|  Total Trades:           {str(metrics.total_trades).ljust(15)} | Win Rate:             {f'{metrics.win_rate_pct:.1f}%'.ljust(15)} |
|  Winning Trades:         {str(metrics.winning_trades).ljust(15)} | Losing Trades:        {str(metrics.losing_trades).ljust(15)} |
|  Profit Factor:          {str(metrics.profit_factor).ljust(15)} | Net PnL (USD):        {f'${metrics.total_net_pnl_usd:+,.2f}'.ljust(15)} |
|  Expectancy (R):         {f'{metrics.expectancy_r:+.2f}R'.ljust(15)} | Expectancy (USD):     {f'${metrics.expectancy_usd:+,.2f}'.ljust(15)} |
|  Max Drawdown:           {f'${metrics.max_drawdown_usd:,.2f} ({metrics.max_drawdown_pct:.1f}%)'.ljust(15)} | Payoff Ratio:         {str(metrics.payoff_ratio).ljust(15)} |
|  Avg Trade Duration:     {f'{metrics.average_trade_bars:.1f} bars'.ljust(15)} | Max Consec Losses:    {str(metrics.max_consecutive_losses).ljust(15)} |
|  Sharpe Ratio (est):     {str(metrics.sharpe_ratio).ljust(15)} | Z-Score vs Null:      {str(metrics.z_score).ljust(15)} |
+-----------------------------------------------------------------------+
|  Noise-Control Gate:     {gate_status.ljust(43)} |
+-----------------------------------------------------------------------+"""


def run_full_backtest_cli(bars_count: int = 3000, num_shuffles: int = 100):
    print("=" * 73)
    print("  XAU/USD TRIPLE FILTER EMA 9/21 + VWAP + ORDER BLOCK BACKTEST GATE")
    print("=" * 73)
    print(f"Loading {bars_count} bars of 1-minute XAU/USD data...")
    data = generate_realistic_gold_data(num_bars=bars_count, seed=101)
    
    params = StrategyParameters(
        ema_fast_period=9,
        ema_slow_period=21,
        vwap_anchor_hour_utc=0,
        ob_swing_lookback=5,
        ob_max_age_bars=40,
        max_pullback_bars=15,
        pullback_atr_mult=1.0,
        rr_ratio=2.0,
        sl_lookback_bars=10,
        sl_buffer_atr=0.25,
        min_sl_distance_points=1.0,
        max_sl_distance_points=8.0
    )
    
    print("Executing causal backtest (Zero Lookahead, Next-bar Open fills)...")
    result = run_causal_backtest(
        opens=data["opens"],
        highs=data["highs"],
        lows=data["lows"],
        closes=data["closes"],
        times=data["times"],
        volumes=data["volumes"],
        params=params,
        initial_balance=10000.0,
        split_ratio=0.75,
        spread_points=0.25,
        commission_per_lot_usd=7.0,
        fixed_lot_size=0.1,
        num_noise_shuffles=num_shuffles
    )
    
    print(format_metrics_table(result.in_sample_metrics, "IN-SAMPLE METRICS (75% Training)"))
    print(format_metrics_table(result.out_of_sample_metrics, "OUT-OF-SAMPLE METRICS (25% Holdout)"))
    print(format_metrics_table(result.overall_metrics, "OVERALL DATASET METRICS"))
    
    overall_passed = result.overall_metrics.noise_gate_passed and result.out_of_sample_metrics.expectancy_r > 0
    print("\n" + "=" * 73)
    if overall_passed:
        print(">>> FINAL GATE DECISION: STRATEGY PASSED NOISE-CONTROL GATE <<<")
        print(f"    Empirical p-value: {result.overall_metrics.noise_p_value:.4f} <= 0.05")
        print(f"    Out-of-Sample Expectancy: {result.out_of_sample_metrics.expectancy_r:+.2f}R")
        print("    Live dashboard auto-trading gate: CLEARED (Demo Only)")
    else:
        print(">>> FINAL GATE DECISION: STRATEGY DID NOT CLEAR NOISE GATE <<<")
        print(f"    Empirical p-value: {result.overall_metrics.noise_p_value:.4f}")
        print("    Live dashboard auto-trading gate: BLOCKED")
    print("=" * 73 + "\n")
    return result

if __name__ == "__main__":
    run_full_backtest_cli()
