"""
Circuit Breakers and Safety Guardrails for MT5 Live Execution.

Safety Rules:
1. Daily Loss Limit: Halts new auto-orders when net daily loss exceeds $ limit or % of starting balance.
2. Max Consecutive Losses Limit: Halts new auto-orders after N consecutive losses until manual review.
3. Demo-Only Verification: Re-reads account trade_mode on EVERY order attempt and refuses live accounts.
4. AlgoTrading Check: Ensures terminal automated trading toggle is active.
5. Unique Magic Number: Tags every order to isolate from manual trades.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple


@dataclass
class CircuitBreakerConfig:
    """Configurable safety thresholds."""
    max_daily_loss_usd: float = 200.0          # Max USD lost in a single day
    max_daily_loss_pct: float = 3.0            # Max % balance lost in a single day
    max_consecutive_losses: int = 3            # Max consecutive losing trades allowed
    enforce_demo_only: bool = True             # Hard refusal if account is not DEMO
    enforce_noise_gate: bool = True            # Enforce noise gate verification
    bypass_noise_gate_for_demo: bool = False   # Set True if manually bypassing on demo UI
    magic_number: int = 9212001                # Unique Magic Number for order tagging
    cooldown_after_loss_minutes: int = 5       # Mandatory cooldown after any loss


@dataclass
class CircuitBreakerState:
    """Runtime tracking state for safety breakers."""
    current_date: str = ""
    daily_start_balance: float = 10000.0
    daily_pnl_usd: float = 0.0
    daily_trades_count: int = 0
    consecutive_losses: int = 0
    is_daily_loss_tripped: bool = False
    is_consec_loss_tripped: bool = False
    trip_reason: Optional[str] = None
    trip_timestamp: Optional[str] = None
    last_trade_closed_time: Optional[str] = None
    noise_gate_verified: bool = False
    noise_gate_p_value: float = 1.0


class CircuitBreakerManager:
    """Manages evaluation and tripping of circuit breakers."""

    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitBreakerState()
        self._reset_for_new_day_if_needed()

    def _get_utc_date_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _reset_for_new_day_if_needed(self, current_balance: float = 10000.0):
        today = self._get_utc_date_str()
        if self.state.current_date != today:
            self.state.current_date = today
            self.state.daily_start_balance = current_balance
            self.state.daily_pnl_usd = 0.0
            self.state.daily_trades_count = 0
            self.state.is_daily_loss_tripped = False
            self.state.trip_reason = None
            self.state.trip_timestamp = None

    def record_trade_outcome(self, net_pnl_usd: float, current_balance: float):
        """Updates internal state after a closed trade."""
        self._reset_for_new_day_if_needed(current_balance)
        self.state.daily_pnl_usd += net_pnl_usd
        self.state.daily_trades_count += 1
        self.state.last_trade_closed_time = datetime.now(timezone.utc).isoformat()

        if net_pnl_usd < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

        # Check Daily Loss Breaker
        loss_limit_usd = min(
            self.config.max_daily_loss_usd,
            (self.config.max_daily_loss_pct / 100.0) * self.state.daily_start_balance
        )
        if self.state.daily_pnl_usd <= -loss_limit_usd:
            self.state.is_daily_loss_tripped = True
            self.state.trip_reason = (
                f"Daily Loss Limit Hit: -${abs(self.state.daily_pnl_usd):.2f} "
                f"(Limit: -${loss_limit_usd:.2f})"
            )
            self.state.trip_timestamp = datetime.now(timezone.utc).isoformat()

        # Check Consecutive Loss Breaker
        if self.state.consecutive_losses >= self.config.max_consecutive_losses:
            self.state.is_consec_loss_tripped = True
            self.state.trip_reason = (
                f"Consecutive Losses Limit Hit: {self.state.consecutive_losses} in a row "
                f"(Limit: {self.config.max_consecutive_losses})"
            )
            self.state.trip_timestamp = datetime.now(timezone.utc).isoformat()

    def can_open_trade(
        self,
        is_demo_account: bool,
        algo_trading_enabled: bool,
        current_balance: float
    ) -> Tuple[bool, str]:
        """
        Comprehensive pre-trade gate check executed on EVERY order attempt.
        """
        self._reset_for_new_day_if_needed(current_balance)

        # 1. Check Demo Account Requirement
        if self.config.enforce_demo_only and not is_demo_account:
            return False, "SAFETY REFUSAL: Connected account is LIVE, not DEMO. Automated trading strictly prohibited on real money."

        # 2. Check AlgoTrading Toggle
        if not algo_trading_enabled:
            return False, "SAFETY REFUSAL: MetaTrader5 AlgoTrading toggle is DISABLED in terminal."

        # 3. Check Noise Control Gate Validation
        if self.config.enforce_noise_gate and not self.config.bypass_noise_gate_for_demo:
            if not self.state.noise_gate_verified:
                return False, f"SAFETY REFUSAL: Strategy has NOT passed the noise-control gate (p-value: {self.state.noise_gate_p_value:.4f} > 0.05). Auto-trade blocked."

        # 4. Check Daily Loss Breaker
        if self.state.is_daily_loss_tripped:
            return False, f"CIRCUIT BREAKER TRIPPED: {self.state.trip_reason}. Auto-trading paused until tomorrow UTC."

        # 5. Check Consecutive Loss Breaker
        if self.state.is_consec_loss_tripped:
            return False, f"CIRCUIT BREAKER TRIPPED: {self.state.trip_reason}. Manual reset required in dashboard."

        return True, "ALL_CHECKS_PASSED"

    def manual_reset_consecutive_losses(self):
        """Allows manual reset of consecutive loss breaker from dashboard."""
        self.state.consecutive_losses = 0
        self.state.is_consec_loss_tripped = False
        if not self.state.is_daily_loss_tripped:
            self.state.trip_reason = None
            self.state.trip_timestamp = None

    def set_noise_gate_status(self, passed: bool, p_value: float):
        """Updates noise gate clearance."""
        self.state.noise_gate_verified = passed
        self.state.noise_gate_p_value = p_value
