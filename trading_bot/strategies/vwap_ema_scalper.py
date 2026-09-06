"""
Triple Filter EMA 9/21 + VWAP + Order Block scalper.

The logic itself still lives in :mod:`trading_bot.strategy` - this module only
dresses it in the :class:`~trading_bot.strategies.base.BaseStrategy` interface so
the dashboard, the live engine and the backtester can treat it exactly like any
other strategy in this folder. Nothing here changes a single entry rule.
"""

from typing import Any, Dict, List, Optional

from trading_bot.strategies.base import (
    BaseStrategy,
    Evaluation,
    ParamSpec,
    StepResult,
)
from trading_bot.strategy import (
    ChecklistStatus,
    StrategyParameters,
    calculate_atr,
    calculate_ema,
    calculate_session_vwap,
    evaluate_checklist_at_bar,
)


class VwapEmaScalperStrategy(BaseStrategy):
    """M1 trend-continuation scalper: VWAP bias, EMA cross, OB retest, pullback, trigger."""

    key = "vwap_ema_scalper"
    label = "VWAP + EMA 9/21 Scalper"
    description = (
        "The original engine. On every M1 close it scores five confluences - VWAP "
        "bias, a recent EMA 9/21 cross, an unmitigated order block, a pullback into "
        "the EMA zone and a confirmation candle - and takes the trade only at 5/5. "
        "Many trades, tight stops, R-based targets."
    )
    timeframe = "M1"
    doc_file = "VWAP_EMA_Scalper_Strategy.md"

    def param_specs(self) -> List[ParamSpec]:
        d = StrategyParameters()
        return [
            ParamSpec("ema_fast_period", "EMA Fast Period", "number", d.ema_fast_period, 3, 50, 1),
            ParamSpec("ema_slow_period", "EMA Slow Period", "number", d.ema_slow_period, 5, 200, 1),
            ParamSpec("vwap_anchor_hour_utc", "VWAP Reset (UTC Hour)", "select",
                      d.vwap_anchor_hour_utc, options=[0, 7, 13],
                      help="00:00 UTC daily open by default."),
            ParamSpec("ob_swing_lookback", "OB Swing Lookback (Pivots)", "number",
                      d.ob_swing_lookback, 2, 20, 1),
            ParamSpec("ob_max_age_bars", "OB Max Age (Bars)", "number", d.ob_max_age_bars, 10, 100, 1),
            ParamSpec("max_pullback_bars", "Max Pullback Bars Post-Cross", "number", 35, 3, 50, 1),
            ParamSpec("pullback_atr_mult", "Pullback Proximity (x ATR)", "slider", 1.8, 0.2, 3.0, 0.1),
            ParamSpec("rr_ratio", "Risk:Reward Ratio", "number", d.rr_ratio, 1.0, 5.0, 0.5),
            ParamSpec("sl_lookback_bars", "SL Swing Lookback", "number", d.sl_lookback_bars, 3, 30, 1),
            ParamSpec("sl_buffer_atr", "SL Buffer (x ATR)", "slider", 0.20, 0.0, 1.0, 0.05),
        ]

    def build_params(self, values: Dict[str, Any]) -> StrategyParameters:
        params = StrategyParameters()
        for spec in self.param_specs():
            if spec.key in values and values[spec.key] is not None:
                setattr(params, spec.key, values[spec.key])
        # Anything not on the sidebar (min/max SL, HTF filter, ...) stays overridable.
        for key, value in values.items():
            if hasattr(params, key) and value is not None:
                setattr(params, key, value)
        return params

    def warmup_bars(self, params: StrategyParameters) -> int:
        return max(params.ema_slow_period, params.atr_period,
                   params.ob_swing_lookback * 3) + 5

    def prepare(self, data: Dict[str, List], params: StrategyParameters) -> Dict[str, Any]:
        closes, highs, lows = data["closes"], data["highs"], data["lows"]
        return {
            "cached_indicators": {
                "ema9": calculate_ema(closes, params.ema_fast_period),
                "ema21": calculate_ema(closes, params.ema_slow_period),
                "vwap": calculate_session_vwap(data["times"], highs, lows, closes,
                                               data["volumes"], params.vwap_anchor_hour_utc),
                "atr": calculate_atr(highs, lows, closes, params.atr_period),
            }
        }

    def evaluate(self, data: Dict[str, List], idx: int, params: StrategyParameters,
                 ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Evaluation]:
        checklist = evaluate_checklist_at_bar(
            data["opens"], data["highs"], data["lows"], data["closes"],
            data["times"], data["volumes"], idx, params,
            cached_indicators=(ctx or {}).get("cached_indicators"),
        )
        return {side: _to_evaluation(status, params)
                for side, status in checklist.items()}


def _to_evaluation(status: ChecklistStatus, params: StrategyParameters) -> Evaluation:
    """Flatten the scalper's five named checks into the generic step list."""
    return Evaluation(
        direction=status.direction,
        timestamp=status.timestamp,
        bar_index=status.bar_index,
        close_price=status.close_price,
        steps=[
            StepResult("Trend filter (VWAP)", status.vwap_pass, status.vwap_detail),
            StepResult("EMA 9/21 crossover", status.crossover_pass, status.crossover_detail),
            StepResult("Order block reaction", status.ob_pass, status.ob_detail),
            StepResult("Pullback to EMAs", status.pullback_pass, status.pullback_detail),
            StepResult("Confirmation candle", status.confirmation_pass,
                       f"Pattern: {status.pattern_name} - {status.confirmation_detail}"),
        ],
        all_passed=status.all_passed,
        signal=status.signal,
        suggested_entry=status.suggested_entry,
        suggested_sl=status.suggested_sl,
        suggested_tp=status.suggested_tp,
        suggested_tp1=None,
        risk_points=status.risk_points,
        reward_points=status.reward_points,
        pattern_name=status.pattern_name,
        context={
            "vwap": round(status.vwap_value, 2),
            "ema_fast": round(status.ema_fast, 2),
            "ema_slow": round(status.ema_slow, 2),
            "rr_ratio": params.rr_ratio,
            "active_ob": status.active_ob.id if status.active_ob else None,
        },
    )
