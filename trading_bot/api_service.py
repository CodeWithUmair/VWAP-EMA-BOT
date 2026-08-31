"""
JSON API Service helper for web dashboard integration.
"""

import sys
import os
import json
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.strategy import (
    StrategyParameters,
    evaluate_checklist_at_bar,
    calculate_ema,
    calculate_session_vwap,
    calculate_atr,
    detect_order_blocks_causal
)
from trading_bot.backtest import run_causal_backtest
from trading_bot.data_feed import generate_realistic_gold_data
from trading_bot.circuit_breakers import CircuitBreakerConfig, CircuitBreakerManager
from trading_bot.storage import BotStorage
from trading_bot.mt5_bridge import MT5Bridge


def handle_get_market_data(num_bars=200):
    bridge = MT5Bridge()
    bridge.connect()
    data = bridge.fetch_recent_bars(count=num_bars)
    params = StrategyParameters()

    ema9 = calculate_ema(data["closes"], params.ema_fast_period)
    ema21 = calculate_ema(data["closes"], params.ema_slow_period)
    vwap = calculate_session_vwap(data["times"], data["highs"], data["lows"], data["closes"], data["volumes"])
    atr = calculate_atr(data["highs"], data["lows"], data["closes"], params.atr_period)
    obs = detect_order_blocks_causal(data["opens"], data["highs"], data["lows"], data["closes"], data["times"], len(data["closes"]) - 1, params)

    obs_json = [{
        "id": ob.id,
        "direction": ob.direction,
        "bar_index": ob.bar_index,
        "zone_low": ob.zone_low,
        "zone_high": ob.zone_high,
        "is_active": ob.is_active,
        "is_mitigated": ob.is_mitigated
    } for ob in obs]

    curr_idx = len(data["closes"]) - 1
    checklist = evaluate_checklist_at_bar(
        data["opens"], data["highs"], data["lows"], data["closes"], data["times"], data["volumes"], curr_idx, params
    )

    long_st = checklist["LONG"]
    short_st = checklist["SHORT"]

    return {
        "bars": [{
            "time": data["times"][i],
            "open": data["opens"][i],
            "high": data["highs"][i],
            "low": data["lows"][i],
            "close": data["closes"][i],
            "volume": data["volumes"][i],
            "ema9": round(ema9[i], 2),
            "ema21": round(ema21[i], 2),
            "vwap": round(vwap[i], 2),
            "atr": round(atr[i], 2)
        } for i in range(len(data["closes"]))],
        "order_blocks": obs_json,
        "checklist": {
            "LONG": {
                "close_price": long_st.close_price,
                "vwap_value": long_st.vwap_value,
                "vwap_pass": long_st.vwap_pass,
                "vwap_detail": long_st.vwap_detail,
                "ema_fast": long_st.ema_fast,
                "ema_slow": long_st.ema_slow,
                "crossover_pass": long_st.crossover_pass,
                "crossover_detail": long_st.crossover_detail,
                "ob_pass": long_st.ob_pass,
                "ob_detail": long_st.ob_detail,
                "pullback_pass": long_st.pullback_pass,
                "pullback_detail": long_st.pullback_detail,
                "confirmation_pass": long_st.confirmation_pass,
                "pattern_name": long_st.pattern_name,
                "confirmation_detail": long_st.confirmation_detail,
                "all_passed": long_st.all_passed,
                "signal": long_st.signal,
                "suggested_entry": long_st.suggested_entry,
                "suggested_sl": long_st.suggested_sl,
                "suggested_tp": long_st.suggested_tp,
                "risk_points": long_st.risk_points,
                "reward_points": long_st.reward_points
            },
            "SHORT": {
                "close_price": short_st.close_price,
                "vwap_value": short_st.vwap_value,
                "vwap_pass": short_st.vwap_pass,
                "vwap_detail": short_st.vwap_detail,
                "ema_fast": short_st.ema_fast,
                "ema_slow": short_st.ema_slow,
                "crossover_pass": short_st.crossover_pass,
                "crossover_detail": short_st.crossover_detail,
                "ob_pass": short_st.ob_pass,
                "ob_detail": short_st.ob_detail,
                "pullback_pass": short_st.pullback_pass,
                "pullback_detail": short_st.pullback_detail,
                "confirmation_pass": short_st.confirmation_pass,
                "pattern_name": short_st.pattern_name,
                "confirmation_detail": short_st.confirmation_detail,
                "all_passed": short_st.all_passed,
                "signal": short_st.signal,
                "suggested_entry": short_st.suggested_entry,
                "suggested_sl": short_st.suggested_sl,
                "suggested_tp": short_st.suggested_tp,
                "risk_points": short_st.risk_points,
                "reward_points": short_st.reward_points
            }
        },
        "account": {
            "mode": "DEMO",
            "is_demo": True,
            "balance": 10000.0,
            "equity": 10000.0,
            "bid": round(data["closes"][-1] - 0.12, 2),
            "ask": round(data["closes"][-1] + 0.13, 2),
            "spread_usd": 0.25,
            "algo_trading": True
        }
    }


def handle_run_backtest(params_dict):
    p = StrategyParameters(
        ema_fast_period=int(params_dict.get("ema_fast_period", 9)),
        ema_slow_period=int(params_dict.get("ema_slow_period", 21)),
        vwap_anchor_hour_utc=int(params_dict.get("vwap_anchor_hour_utc", 0)),
        ob_swing_lookback=int(params_dict.get("ob_swing_lookback", 5)),
        ob_max_age_bars=int(params_dict.get("ob_max_age_bars", 40)),
        max_pullback_bars=int(params_dict.get("max_pullback_bars", 15)),
        pullback_atr_mult=float(params_dict.get("pullback_atr_mult", 1.0)),
        rr_ratio=float(params_dict.get("rr_ratio", 2.0)),
        sl_lookback_bars=int(params_dict.get("sl_lookback_bars", 10)),
        sl_buffer_atr=float(params_dict.get("sl_buffer_atr", 0.25))
    )

    bars_count = int(params_dict.get("bars_count", 1500))
    seed = int(params_dict.get("seed", 101))
    data = generate_realistic_gold_data(num_bars=bars_count, seed=seed)

    res = run_causal_backtest(
        opens=data["opens"],
        highs=data["highs"],
        lows=data["lows"],
        closes=data["closes"],
        times=data["times"],
        volumes=data["volumes"],
        params=p,
        initial_balance=10000.0,
        split_ratio=0.75,
        spread_points=float(params_dict.get("spread_points", 0.25)),
        commission_per_lot_usd=7.0,
        num_noise_shuffles=100
    )

    def metrics_to_dict(m):
        return {
            "segment_name": m.segment_name,
            "total_trades": m.total_trades,
            "winning_trades": m.winning_trades,
            "losing_trades": m.losing_trades,
            "win_rate_pct": m.win_rate_pct,
            "profit_factor": m.profit_factor,
            "expectancy_r": m.expectancy_r,
            "expectancy_usd": m.expectancy_usd,
            "total_net_pnl_usd": m.total_net_pnl_usd,
            "max_drawdown_usd": m.max_drawdown_usd,
            "max_drawdown_pct": m.max_drawdown_pct,
            "average_trade_bars": m.average_trade_bars,
            "avg_win_usd": m.avg_win_usd,
            "avg_loss_usd": m.avg_loss_usd,
            "payoff_ratio": m.payoff_ratio,
            "max_consecutive_losses": m.max_consecutive_losses,
            "sharpe_ratio": m.sharpe_ratio,
            "noise_gate_passed": m.noise_gate_passed,
            "noise_p_value": m.noise_p_value,
            "z_score": m.z_score,
            "shuffled_expectancies": m.shuffled_expectancies[:30]
        }

    trades_list = [{
        "id": t.id,
        "direction": t.direction,
        "signal_bar": t.signal_bar,
        "signal_time": t.signal_time,
        "entry_bar": t.entry_bar,
        "entry_time": t.entry_time,
        "entry_price": t.entry_price,
        "stop_loss": t.stop_loss,
        "take_profit": t.take_profit,
        "exit_bar": t.exit_bar,
        "exit_time": t.exit_time,
        "exit_price": t.exit_price,
        "exit_reason": t.exit_reason,
        "net_pnl_usd": t.net_pnl_usd,
        "pnl_r_multiple": t.pnl_r_multiple,
        "duration_bars": t.duration_bars,
        "pattern_name": t.pattern_name,
        "is_out_of_sample": t.is_out_of_sample
    } for t in res.trades]

    return {
        "in_sample": metrics_to_dict(res.in_sample_metrics),
        "out_of_sample": metrics_to_dict(res.out_of_sample_metrics),
        "overall": metrics_to_dict(res.overall_metrics),
        "trades": trades_list,
        "equity_curve": res.equity_curve,
        "split_index": res.split_index,
        "initial_balance": res.initial_balance,
        "final_balance": res.final_balance
    }


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "market_data"
    if action == "market_data":
        print(json.dumps(handle_get_market_data()))
    elif action == "run_backtest":
        payload = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        print(json.dumps(handle_run_backtest(payload)))
    elif action == "run_tests":
        import unittest
        loader = unittest.TestLoader()
        suite = loader.discover('trading_bot/tests', pattern='test_*.py')
        runner = unittest.TextTestRunner(verbosity=1)
        res = runner.run(suite)
        print(json.dumps({
            "success": res.wasSuccessful(),
            "testsRun": res.testsRun,
            "failures": len(res.failures),
            "errors": len(res.errors)
        }))
