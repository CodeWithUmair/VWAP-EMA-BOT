"""
Unit tests for Strategy & Indicator functions using hand-crafted OHLC values.
"""

import unittest
from trading_bot.strategy import (
    calculate_ema,
    calculate_atr,
    calculate_session_vwap,
    find_causal_swings,
    check_confirmation_candle,
    detect_order_blocks_causal,
    evaluate_checklist_at_bar,
    StrategyParameters,
    OrderBlock
)


class TestStrategyAndIndicators(unittest.TestCase):
    """Test suite for causal strategy calculations."""

    def test_ema_calculation(self):
        """Test EMA calculation matches mathematical recurrence."""
        prices = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0]
        ema_3 = calculate_ema(prices, 3)
        self.assertEqual(len(ema_3), len(prices))
        # Initial 3 SMA is (10 + 11 + 12)/3 = 11.0
        self.assertAlmostEqual(ema_3[0], 11.0, places=2)
        self.assertAlmostEqual(ema_3[1], 11.0, places=2)
        self.assertAlmostEqual(ema_3[2], 11.0, places=2)
        # Mult = 2 / (3 + 1) = 0.5
        # Bar 3: (13 - 11)*0.5 + 11 = 12.0
        self.assertAlmostEqual(ema_3[3], 12.0, places=2)
        # Bar 4: (14 - 12)*0.5 + 12 = 13.0
        self.assertAlmostEqual(ema_3[4], 13.0, places=2)

    def test_atr_calculation(self):
        """Test Wilder's ATR calculation on hand-crafted bars."""
        highs = [105.0, 106.0, 108.0, 107.0]
        lows =  [100.0, 101.0, 103.0, 102.0]
        closes = [103.0, 104.0, 106.0, 105.0]
        atr = calculate_atr(highs, lows, closes, 3)
        self.assertEqual(len(atr), 4)
        # TRs: bar 0: 5.0, bar 1: max(5, 3, 2)=5.0, bar 2: max(5, 4, 1)=5.0
        self.assertAlmostEqual(atr[2], 5.0, places=2)

    def test_session_vwap_reset(self):
        """Test that VWAP resets across UTC day boundaries."""
        times = [
            "2026-08-30T23:58:00Z",
            "2026-08-30T23:59:00Z",
            "2026-08-31T00:00:00Z",  # New day
            "2026-08-31T00:01:00Z"
        ]
        highs = [2000.0, 2000.0, 2010.0, 2012.0]
        lows =  [1998.0, 1998.0, 2008.0, 2008.0]
        closes = [1999.0, 1999.0, 2009.0, 2010.0]
        volumes = [100.0, 100.0, 50.0, 50.0]

        vwap = calculate_session_vwap(times, highs, lows, closes, volumes, anchor_hour_utc=0)
        self.assertEqual(len(vwap), 4)
        # Bar 2 (00:00) must reset: typical price = (2010+2008+2009)/3 = 2009.0
        self.assertAlmostEqual(vwap[2], 2009.0, places=2)

    def test_causal_swing_pivots(self):
        """Test swing pivots are only confirmed lookback bars to the right."""
        # V-shape swing low at index 3
        highs = [10.0, 9.0, 8.0, 5.0, 8.0, 9.0, 10.0]
        lows =  [ 9.0, 8.0, 7.0, 4.0, 7.0, 8.0,  9.0]
        # Lookback = 2
        sh, sl = find_causal_swings(highs, lows, lookback=2)
        self.assertEqual(len(sl), 1)
        self.assertEqual(sl[0]['index'], 3)
        self.assertEqual(sl[0]['price'], 4.0)
        self.assertEqual(sl[0]['confirmed_at'], 5)  # Confirmed at 3 + 2 = 5

    def test_bullish_engulfing_confirmation(self):
        """Test candlestick pattern recognition for Bullish Engulfing."""
        params = StrategyParameters()
        # Bar 1: Red candle Open 100, High 101, Low 98, Close 99
        # Bar 2: Green candle Open 98.5, High 102, Low 98.0, Close 101.5 (Engulfs Bar 1)
        passed, name, detail = check_confirmation_candle(
            open_val=98.5, high_val=102.0, low_val=98.0, close_val=101.5,
            prev_open=100.0, prev_high=101.0, prev_low=98.0, prev_close=99.0,
            atr=2.0, direction="LONG", params=params
        )
        self.assertTrue(passed)
        self.assertEqual(name, "Bullish Engulfing")

    def test_hammer_pinbar_confirmation(self):
        """Test candlestick pattern recognition for Bullish Hammer / Pinbar."""
        params = StrategyParameters()
        # Open 100, High 100.5, Low 95.0, Close 100.2 -> Lower wick = 5.0, Body = 0.2
        passed, name, detail = check_confirmation_candle(
            open_val=100.0, high_val=100.5, low_val=95.0, close_val=100.2,
            prev_open=102.0, prev_high=102.5, prev_low=99.0, prev_close=100.0,
            atr=2.0, direction="LONG", params=params
        )
        self.assertTrue(passed)
        self.assertIn("Hammer", name)

    def test_bearish_engulfing_confirmation(self):
        """Test candlestick pattern recognition for Bearish Engulfing."""
        params = StrategyParameters()
        # Bar 1: Green candle Open 100, Close 101
        # Bar 2: Red candle Open 101.5, Close 99.0 (Engulfs Bar 1)
        passed, name, detail = check_confirmation_candle(
            open_val=101.5, high_val=102.0, low_val=98.5, close_val=99.0,
            prev_open=100.0, prev_high=101.2, prev_low=99.8, prev_close=101.0,
            atr=2.0, direction="SHORT", params=params
        )
        self.assertTrue(passed)
        self.assertEqual(name, "Bearish Engulfing")


if __name__ == '__main__':
    unittest.main()
