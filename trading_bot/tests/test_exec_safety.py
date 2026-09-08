"""
Checks for the 2026-09 execution-safety additions (dev questionnaire sections D/K/N/Q8):
  - news-blackout window parsing
  - stale-feed bar-age detection
  - circuit-breaker state survives a save/restore round-trip
  - trades table stores spread / slippage / latency and MT5Bridge exposes last_exec
"""

import os
import sys
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.run_live_auto_bot import _in_news_blackout, _bar_age_seconds
from trading_bot.circuit_breakers import CircuitBreakerManager, CircuitBreakerState
from trading_bot.storage import BotStorage
from trading_bot.mt5_bridge import MT5Bridge


class TestNewsBlackout(unittest.TestCase):
    def test_inside_window(self):
        noon = datetime(2026, 1, 2, 12, 30, tzinfo=timezone.utc)
        hit, win = _in_news_blackout(["12:25-12:45"], noon)
        self.assertTrue(hit)
        self.assertEqual(win, "12:25-12:45")

    def test_outside_window(self):
        t = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
        hit, _ = _in_news_blackout(["12:25-12:45", "13:55-14:15"], t)
        self.assertFalse(hit)

    def test_empty_and_garbage_are_safe(self):
        now = datetime(2026, 1, 2, 12, 30, tzinfo=timezone.utc)
        self.assertFalse(_in_news_blackout([], now)[0])
        self.assertFalse(_in_news_blackout(["not-a-window", ""], now)[0])


class TestBarAge(unittest.TestCase):
    def test_fresh_bar_small_age(self):
        recent = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
        age = _bar_age_seconds(recent)
        self.assertIsNotNone(age)
        self.assertLess(age, 60)

    def test_stale_bar_large_age(self):
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        self.assertGreater(_bar_age_seconds(old), 300)

    def test_unparseable_returns_none(self):
        self.assertIsNone(_bar_age_seconds("garbage"))
        self.assertIsNone(_bar_age_seconds(0))


class TestCircuitBreakerPersistence(unittest.TestCase):
    def test_state_round_trips_through_storage(self):
        tmp = os.path.join(tempfile.mkdtemp(), "cb.sqlite")
        store = BotStorage(db_path=tmp)

        cb = CircuitBreakerManager()
        cb.record_trade_outcome(net_pnl_usd=-40.0, current_balance=1000.0)
        cb.record_trade_outcome(net_pnl_usd=-40.0, current_balance=960.0)
        store.set_setting("cb_state", asdict(cb.state))

        restored = CircuitBreakerManager()
        saved = store.get_setting("cb_state", {})
        self.assertEqual(saved.get("current_date"), cb.state.current_date)
        for k, v in saved.items():
            if hasattr(restored.state, k):
                setattr(restored.state, k, v)

        self.assertEqual(restored.state.consecutive_losses, 2)
        self.assertAlmostEqual(restored.state.daily_pnl_usd, -80.0)


class TestExecutionLogging(unittest.TestCase):
    def test_bridge_records_last_exec_and_storage_persists_it(self):
        tmp = os.path.join(tempfile.mkdtemp(), "trades.sqlite")
        store = BotStorage(db_path=tmp)

        bridge = MT5Bridge(symbol="XAUUSDm")   # no MT5 -> simulation fill
        ok, ticket, _ = bridge.send_order(direction="BUY", volume=0.01,
                                          sl_price=3990.0, tp_price=4020.0)
        self.assertTrue(ok)
        self.assertIn("fill_price", bridge.last_exec)
        self.assertIn("entry_slippage_usd", bridge.last_exec)

        store.record_trade({
            "order_id": ticket, "direction": "BUY", "volume": 0.01,
            "entry_price": 4000.0, "sl": 3990.0, "tp": 4020.0,
            "opened_at": datetime.now(timezone.utc).isoformat(),
            **bridge.last_exec,
        })
        row = store.get_all_trades(1)[0]
        self.assertIn("entry_slippage_usd", row)
        self.assertIn("fill_price", row)
        self.assertEqual(row["fill_price"], bridge.last_exec["fill_price"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
