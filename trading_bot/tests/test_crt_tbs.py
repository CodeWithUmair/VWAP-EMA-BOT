"""
Unit tests for the CRT + TBS liquidity-sweep strategy and its M1 -> HTF folding.
"""

import unittest
from datetime import datetime, timedelta, timezone

from trading_bot.backtest import run_causal_backtest
from trading_bot.strategies import get_strategy
from trading_bot.strategies.base import build_htf_series
from trading_bot.strategies.crt_tbs import CRTParams, CRTTurtleSoupStrategy, in_killzone


START = datetime(2024, 6, 3, 6, 0, tzinfo=timezone.utc)  # a Monday, before London


def make_bars(specs):
    """Build an M1 dataset from (open, high, low, close) tuples, one per minute."""
    data = {"times": [], "opens": [], "highs": [], "lows": [], "closes": [], "volumes": []}
    for i, (o, h, l, c) in enumerate(specs):
        data["times"].append((START + timedelta(minutes=i)).isoformat())
        data["opens"].append(o)
        data["highs"].append(h)
        data["lows"].append(l)
        data["closes"].append(c)
        data["volumes"].append(100.0)
    return data


def flat_bars(count, price, start_offset=0):
    """`count` featureless bars at `price`, for padding out a reference candle."""
    return [(price, price + 0.10, price - 0.10, price) for _ in range(count)]


class TestHTFAggregation(unittest.TestCase):
    """M1 bars folded onto the wall clock, without peeking at the next bar."""

    def test_m5_candles_group_by_clock(self):
        data = make_bars(flat_bars(20, 2400.0))
        series = build_htf_series(data, 5)
        # 06:00 start, 20 M1 bars -> exactly four M5 candles.
        self.assertEqual(len(series.candles), 4)
        self.assertEqual([c.first_idx for c in series.candles], [0, 5, 10, 15])

    def test_bucket_close_flag_is_clock_based(self):
        data = make_bars(flat_bars(10, 2400.0))
        series = build_htf_series(data, 5)
        # Bars at :04 and :09 close their M5 candle; nothing else does.
        self.assertEqual([i for i, f in enumerate(series.is_bucket_close) if f], [4, 9])

    def test_ohlc_of_aggregate_candle(self):
        specs = [
            (2400.0, 2402.0, 2399.0, 2401.0),
            (2401.0, 2405.0, 2400.5, 2404.0),
            (2404.0, 2404.5, 2396.0, 2397.0),
            (2397.0, 2398.0, 2396.5, 2397.5),
            (2397.5, 2399.0, 2397.0, 2398.0),
        ]
        candle = build_htf_series(make_bars(specs), 5).candles[0]
        self.assertEqual(candle.open, 2400.0)
        self.assertEqual(candle.high, 2405.0)
        self.assertEqual(candle.low, 2396.0)
        self.assertEqual(candle.close, 2398.0)
        self.assertAlmostEqual(candle.midpoint, 2400.5)

    def test_previous_completed_never_reads_the_current_candle(self):
        data = make_bars(flat_bars(125, 2400.0))
        series = build_htf_series(data, 60)
        # Bar 90 sits in the 07:00 hour; its reference is the completed 06:00 hour,
        # which is built only from bars 0..59.
        crt = series.previous_completed(90)
        self.assertIsNotNone(crt)
        self.assertEqual(crt.first_idx, 0)
        self.assertLess(crt.last_idx, 90)


class TestKillzones(unittest.TestCase):
    def test_spec_windows(self):
        p = CRTParams()
        self.assertTrue(in_killzone(8 * 60, p)[0])          # 08:00 London
        self.assertTrue(in_killzone(13 * 60, p)[0])         # 13:00 New York
        self.assertFalse(in_killzone(11 * 60, p)[0])        # 11:00 lunch gap
        self.assertFalse(in_killzone(2 * 60, p)[0])         # 02:00 Asian session


class TestSweepDetection(unittest.TestCase):
    """The Turtle Soup trigger: run the level, fail, close back inside."""

    def build_short_setup(self, sweep_high=2412.0, close_back=2405.0):
        """An H1 candle ranging 2390-2410, then an M5 that sweeps its high and rejects.

        The reference hour is 06:00-06:59, so the trigger lands at 07:00+ inside
        the London killzone. The trigger M5 candle is 07:00-07:04, and the sweep
        happens on its own bars, so the signal fires on the 07:04 close.
        """
        specs = []
        # 06:00 reference hour: build a 2390-2410 range, close mid.
        specs += [(2400.0, 2410.0, 2390.0, 2400.0)]          # sets the H1 high/low
        specs += flat_bars(59, 2400.0)
        # 07:00-07:04 execution candle: poke above 2410 then close back inside.
        specs += [
            (2400.0, 2405.0, 2399.5, 2404.0),
            (2404.0, sweep_high, 2403.0, 2408.0),
            (2408.0, 2409.0, 2404.0, 2406.0),
            (2406.0, 2407.0, 2404.0, 2405.5),
            (2405.5, 2406.0, 2404.0, close_back),
        ]
        return make_bars(specs)

    def setUp(self):
        self.strategy = CRTTurtleSoupStrategy()
        self.params = CRTParams()

    def evaluate_last(self, data, params=None):
        params = params or self.params
        idx = len(data["closes"]) - 1
        ctx = self.strategy.prepare(data, params)
        return self.strategy.evaluate(data, idx, params, ctx)

    def test_short_signal_on_sweep_and_reentry(self):
        result = self.evaluate_last(self.build_short_setup())["SHORT"]
        self.assertTrue(result.all_passed, [(s.name, s.detail) for s in result.steps])
        self.assertEqual(result.signal, "SELL")
        # SL beyond the sweep wick, TP1 at equilibrium, TP2 at the CRT low.
        self.assertGreater(result.suggested_sl, 2412.0)
        self.assertAlmostEqual(result.suggested_tp1, 2400.0, places=1)
        self.assertAlmostEqual(result.suggested_tp, 2390.0, places=1)

    def test_no_signal_when_close_stays_outside(self):
        # Same sweep, but the candle closes above the CRT high: a break, not a fade.
        result = self.evaluate_last(self.build_short_setup(close_back=2411.5))["SHORT"]
        self.assertFalse(result.all_passed)
        step = next(s for s in result.steps if s.name == "Close back inside range")
        self.assertFalse(step.passed)

    def test_no_signal_without_a_sweep(self):
        # Never reaches the CRT high, so there is no liquidity to grab.
        result = self.evaluate_last(self.build_short_setup(sweep_high=2408.0))["SHORT"]
        self.assertFalse(result.all_passed)
        step = next(s for s in result.steps if s.name == "Liquidity sweep (TBS)")
        self.assertFalse(step.passed)

    def test_deep_run_is_displacement_not_a_sweep(self):
        params = CRTParams(max_sweep_points=3.0)
        result = self.evaluate_last(self.build_short_setup(sweep_high=2425.0), params)["SHORT"]
        step = next(s for s in result.steps if s.name == "Liquidity sweep (TBS)")
        self.assertFalse(step.passed)
        self.assertIn("displacement", step.detail)

    def test_killzone_filter_blocks_off_session_setups(self):
        # Same structure, shifted to 02:00 UTC (Asian session, outside both windows).
        global START
        original = START
        try:
            START = datetime(2024, 6, 3, 1, 0, tzinfo=timezone.utc)
            result = self.evaluate_last(self.build_short_setup())["SHORT"]
        finally:
            START = original
        self.assertFalse(result.all_passed)
        step = next(s for s in result.steps if s.name == "Killzone / news window")
        self.assertFalse(step.passed)

    def test_signal_only_fires_on_an_execution_candle_close(self):
        data = self.build_short_setup()
        # Bar 62 is 07:02 — mid-M5, so the trigger must stay silent there.
        ctx = self.strategy.prepare(data, self.params)
        mid = self.strategy.evaluate(data, 62, self.params, ctx)["SHORT"]
        self.assertFalse(mid.all_passed)
        step = next(s for s in mid.steps if s.name.endswith("candle closed"))
        self.assertFalse(step.passed)


class TestCRTThroughBacktester(unittest.TestCase):
    """The strategy has to survive the causal engine, not just direct calls."""

    def test_backtest_runs_and_respects_next_bar_fills(self):
        from trading_bot.data_feed import generate_realistic_gold_data

        strategy = get_strategy("crt_tbs")
        data = generate_realistic_gold_data(num_bars=4000, seed=7)
        result = run_causal_backtest(
            data["opens"], data["highs"], data["lows"], data["closes"],
            data["times"], data["volumes"], strategy.default_params(),
            strategy=strategy, num_noise_shuffles=10
        )
        self.assertEqual(result.strategy_key, "crt_tbs")
        for trade in result.trades:
            self.assertGreaterEqual(trade.entry_bar, trade.signal_bar + 1)

    def test_tp1_partial_leaves_a_break_even_runner(self):
        """A trade that tags TP1 and then stops out must not book a full loss."""
        from trading_bot.backtest import Trade, _finalize_trade

        trade = Trade(
            id=1, direction="BUY", signal_bar=0, signal_time="0",
            entry_bar=1, entry_time="1", entry_price=2400.0,
            stop_loss=2400.0, take_profit=2420.0, risk_points=5.0, reward_points=20.0,
            lot_size=0.1, take_profit_1=2410.0,
        )
        # TP1 banked half at +$10, then the runner stops at break-even.
        trade.tp1_hit = True
        trade.partial_pnl_usd = 10.0 * (0.1 * 100.0) * 0.5   # +$50
        _finalize_trade(trade, 9, "9", 2400.0, "BREAK_EVEN", 0.25, 7.0)

        self.assertAlmostEqual(trade.gross_pnl_usd, 50.0)
        self.assertAlmostEqual(trade.net_pnl_usd, 50.0 - 0.7)
        self.assertGreater(trade.net_pnl_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
