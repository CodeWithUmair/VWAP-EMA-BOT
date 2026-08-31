"""
Unit tests for Circuit Breakers and Safety Logic.
"""

import unittest
from trading_bot.circuit_breakers import (
    CircuitBreakerConfig,
    CircuitBreakerManager
)


class TestCircuitBreakers(unittest.TestCase):
    """Test suite for safety guardrails and circuit breaker triggers."""

    def test_demo_account_refusal(self):
        """Live accounts must be strictly blocked on every attempt."""
        config = CircuitBreakerConfig(enforce_demo_only=True)
        manager = CircuitBreakerManager(config)
        manager.set_noise_gate_status(passed=True, p_value=0.01)

        # Attempt with live account (is_demo=False)
        allowed, reason = manager.can_open_trade(
            is_demo_account=False,
            algo_trading_enabled=True,
            current_balance=10000.0
        )
        self.assertFalse(allowed)
        self.assertIn("SAFETY REFUSAL", reason)
        self.assertIn("LIVE", reason)

    def test_noise_gate_blocked_before_verification(self):
        """Cannot trade if noise gate is not verified or failed."""
        config = CircuitBreakerConfig()
        manager = CircuitBreakerManager(config)
        # Default noise gate is not passed
        allowed, reason = manager.can_open_trade(
            is_demo_account=True,
            algo_trading_enabled=True,
            current_balance=10000.0
        )
        self.assertFalse(allowed)
        self.assertIn("noise-control gate", reason)

    def test_daily_loss_circuit_breaker(self):
        """Daily loss limit trips breaker and blocks subsequent trades."""
        config = CircuitBreakerConfig(max_daily_loss_usd=150.0)
        manager = CircuitBreakerManager(config)
        manager.set_noise_gate_status(passed=True, p_value=0.02)

        # Trade 1: -$80
        manager.record_trade_outcome(net_pnl_usd=-80.0, current_balance=9920.0)
        allowed, _ = manager.can_open_trade(is_demo_account=True, algo_trading_enabled=True, current_balance=9920.0)
        self.assertTrue(allowed)

        # Trade 2: -$80 (Total daily loss = -$160 > $150 limit)
        manager.record_trade_outcome(net_pnl_usd=-80.0, current_balance=9840.0)
        allowed, reason = manager.can_open_trade(is_demo_account=True, algo_trading_enabled=True, current_balance=9840.0)
        self.assertFalse(allowed)
        self.assertIn("CIRCUIT BREAKER TRIPPED", reason)
        self.assertIn("Daily Loss", reason)

    def test_consecutive_losses_circuit_breaker(self):
        """Max consecutive losses limit trips breaker."""
        config = CircuitBreakerConfig(max_consecutive_losses=3, max_daily_loss_usd=1000.0)
        manager = CircuitBreakerManager(config)
        manager.set_noise_gate_status(passed=True, p_value=0.01)

        manager.record_trade_outcome(net_pnl_usd=-20.0, current_balance=9980.0)
        manager.record_trade_outcome(net_pnl_usd=-20.0, current_balance=9960.0)
        allowed, _ = manager.can_open_trade(is_demo_account=True, algo_trading_enabled=True, current_balance=9960.0)
        self.assertTrue(allowed)

        # 3rd consecutive loss
        manager.record_trade_outcome(net_pnl_usd=-20.0, current_balance=9940.0)
        allowed, reason = manager.can_open_trade(is_demo_account=True, algo_trading_enabled=True, current_balance=9940.0)
        self.assertFalse(allowed)
        self.assertIn("Consecutive Losses Limit Hit", reason)

        # Manual reset restores capability
        manager.manual_reset_consecutive_losses()
        allowed, _ = manager.can_open_trade(is_demo_account=True, algo_trading_enabled=True, current_balance=9940.0)
        self.assertTrue(allowed)


if __name__ == '__main__':
    unittest.main()
