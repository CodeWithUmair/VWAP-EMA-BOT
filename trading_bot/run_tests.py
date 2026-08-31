"""
Unified Unit Test Runner for XAU/USD Trading Bot.
Runs all test suites across Strategy, Indicators, Backtest, and Circuit Breakers.
"""

import os
import sys
import unittest

# Ensure current workspace directory is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

def run_all_tests():
    print("=" * 70)
    print("RUNNING TRADING BOT UNIT TESTS")
    print("=" * 70)
    loader = unittest.TestLoader()
    suite = loader.discover('trading_bot/tests', pattern='test_*.py')
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    print("=" * 70)
    if result.wasSuccessful():
        print(f"ALL {result.testsRun} TESTS PASSED SUCCESSFULLY!")
        return 0
    else:
        print(f"FAILED: {len(result.failures)} failures, {len(result.errors)} errors out of {result.testsRun} tests.")
        return 1

if __name__ == '__main__':
    sys.exit(run_all_tests())
