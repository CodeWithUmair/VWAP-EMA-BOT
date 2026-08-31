"""
Unit tests for Backtest Engine, Causal Execution, and Noise Control Gate.
"""

import unittest
from trading_bot.strategy import StrategyParameters
from trading_bot.backtest import (
    Trade,
    run_causal_backtest,
    run_noise_control_gate,
    calculate_metrics_from_trades
)


class TestBacktestEngine(unittest.TestCase):
    """Test suite for causal backtest execution and noise gate."""

    def test_noise_control_gate_random_baseline(self):
        """
        A random coin flip strategy with 50% win rate on 1:1 R:R should FAIL the noise gate (p-value high).
        """
        trades = []
        for i in range(50):
            # Alternating +1R and -1R -> 0 net expectancy
            r = 1.0 if i % 2 == 0 else -1.0
            trades.append(Trade(
                id=i, direction="BUY", signal_bar=i, signal_time=str(i),
                entry_bar=i+1, entry_time=str(i+1), entry_price=2000.0,
                stop_loss=1995.0, take_profit=2005.0, risk_points=5.0, reward_points=5.0,
                net_pnl_usd=r * 50.0, pnl_r_multiple=r
            ))

        passed, p_val, z_score, _ = run_noise_control_gate(trades, num_shuffles=50)
        self.assertFalse(passed)
        self.assertEqual(p_val, 1.0)  # Real expectancy is 0, so cannot pass

    def test_noise_control_gate_significant_edge(self):
        """
        A genuinely strong strategy with consistent +2R wins and low losses should PASS the noise gate (p <= 0.05).
        """
        trades = []
        # 35 trades with +2.0R win, 15 trades with -1.0R loss (70% win rate on 1:2 R:R -> massive edge)
        for i in range(50):
            r = 2.0 if i < 35 else -1.0
            trades.append(Trade(
                id=i, direction="BUY", signal_bar=i, signal_time=str(i),
                entry_bar=i+1, entry_time=str(i+1), entry_price=2000.0,
                stop_loss=1995.0, take_profit=2010.0, risk_points=5.0, reward_points=10.0,
                net_pnl_usd=r * 100.0, pnl_r_multiple=r
            ))

        passed, p_val, z_score, _ = run_noise_control_gate(trades, num_shuffles=100)
        self.assertTrue(passed)
        self.assertLessEqual(p_val, 0.05)
        self.assertGreater(z_score, 2.0)

    def test_causal_fill_never_on_signal_bar(self):
        """
        Verify that in backtesting, no trade ever enters at signal_bar. entry_bar must be >= signal_bar + 1.
        """
        # Create a synthetic series of 200 bars
        n = 200
        opens = [2000.0 + (i * 0.1) for i in range(n)]
        highs = [o + 1.5 for o in opens]
        lows = [o - 1.5 for o in opens]
        closes = [o + 0.5 for o in opens]
        times = [f"2026-08-31T{i//60:02d}:{i%60:02d}:00Z" for i in range(n)]
        volumes = [100.0] * n

        params = StrategyParameters()
        result = run_causal_backtest(opens, highs, lows, closes, times, volumes, params)

        for trade in result.trades:
            self.assertGreaterEqual(
                trade.entry_bar,
                trade.signal_bar + 1,
                f"Lookahead violation! Trade {trade.id} entered at bar {trade.entry_bar} with signal at {trade.signal_bar}"
            )


if __name__ == '__main__':
    unittest.main()
