"""
Sidebar settings must survive a page refresh, and must reach the engine.

Streamlit rebuilds widgets on every rerun, so anything seeded from a literal
default silently reverts - which is exactly what happened to Max Daily Loss.
These tests pin both halves of the fix: the value is written to SQLite, and
the headless engine resolves that value rather than its own default.
"""

import os
import unittest

from trading_bot.run_live_auto_bot import load_strategy_settings
from trading_bot.storage import BotStorage
from trading_bot.strategies import get_strategy


class TestSettingsPersistence(unittest.TestCase):
    def setUp(self):
        self.storage = BotStorage()
        self.strategy = get_strategy("crt_body_soup")
        self._saved = {}
        for key in ("risk.max_daily_loss_usd", "param.crt_body_soup.min_rr_tp2"):
            self._saved[key] = self.storage.get_setting(key, None)

    def tearDown(self):
        # Leave the user's real settings exactly as they were.
        for key, value in self._saved.items():
            if value is None:
                self.storage.set_setting(key, None)
            else:
                self.storage.set_setting(key, value)

    def test_setting_round_trips_through_sqlite(self):
        self.storage.set_setting("risk.max_daily_loss_usd", 50.0)
        self.assertEqual(float(self.storage.get_setting("risk.max_daily_loss_usd")), 50.0)

    def test_engine_uses_saved_param_over_its_default(self):
        spec = next(s for s in self.strategy.param_specs() if s.key == "min_rr_tp2")
        changed = float(spec.default) + 0.5

        self.storage.set_setting("param.crt_body_soup.min_rr_tp2", changed)
        resolved = load_strategy_settings(self.storage, self.strategy)

        self.assertEqual(float(resolved["min_rr_tp2"]), changed,
                         "engine ignored the dashboard's saved value")
        params = self.strategy.build_params(resolved)
        self.assertEqual(float(params.min_rr_tp2), changed)

    def test_unset_param_falls_back_to_default(self):
        self.storage.set_setting("param.crt_body_soup.min_rr_tp2", None)
        resolved = load_strategy_settings(self.storage, self.strategy)
        spec = next(s for s in self.strategy.param_specs() if s.key == "min_rr_tp2")
        self.assertEqual(float(resolved["min_rr_tp2"]), float(spec.default))


if __name__ == "__main__":
    unittest.main()
