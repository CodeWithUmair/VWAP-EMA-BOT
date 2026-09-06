"""
Render the Streamlit dashboard for every strategy and fail on any exception.

These exist because three separate dashboard crashes shipped in a row - an
AttributeError on scalper-only params, then a NameError on a variable used
before assignment inside a fragment. Every one of them passed ``py_compile``:
the dashboard is a script whose bugs only appear when it actually runs, so
compiling it proves nothing. This runs it for real.
"""

import os
import unittest

try:
    from streamlit.testing.v1 import AppTest
    HAVE_APPTEST = True
except ImportError:                     # streamlit not installed in this env
    HAVE_APPTEST = False

from trading_bot.strategies import list_strategies

# Absolute, so the test passes regardless of pytest's working directory.
APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "streamlit_app.py")


@unittest.skipUnless(HAVE_APPTEST, "streamlit not installed")
class TestDashboardRenders(unittest.TestCase):
    """Every selectable strategy must render, on first run and on rerun."""

    def _run_for(self, strategy_key):
        from trading_bot.storage import BotStorage
        BotStorage().set_setting("active_strategy", strategy_key)
        app = AppTest.from_file(APP_PATH, default_timeout=180).run()
        return app

    def _assert_clean(self, app, key, phase):
        if app.exception:
            details = "; ".join(str(e.value)[:200] for e in app.exception)
            self.fail(f"dashboard raised for {key} on {phase}: {details}")

    def test_every_strategy_renders(self):
        for strategy in list_strategies():
            with self.subTest(strategy=strategy.key):
                app = self._run_for(strategy.key)
                self._assert_clean(app, strategy.key, "first run")

                # Rerun matters on its own: fragments re-execute independently,
                # which is how the "used before assignment" bug slipped through.
                app.run()
                self._assert_clean(app, strategy.key, "rerun")


if __name__ == "__main__":
    unittest.main()
