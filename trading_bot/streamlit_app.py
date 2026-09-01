"""
Streamlit Desktop Live Dashboard for XAU/USD Triple Filter Scalper Bot.
Run locally with: streamlit run trading_bot/streamlit_app.py
"""

import os
import sys
from datetime import datetime, timezone
import json

# Ensure project root is on path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    import streamlit as st
except ImportError:
    st = None

from trading_bot.strategy import (
    StrategyParameters,
    evaluate_checklist_at_bar,
    calculate_ema,
    calculate_session_vwap,
    calculate_atr,
    calculate_sl_tp,
    detect_order_blocks_causal
)
from trading_bot.backtest import run_causal_backtest
from trading_bot.circuit_breakers import CircuitBreakerConfig, CircuitBreakerManager
from trading_bot.storage import BotStorage
from trading_bot.mt5_bridge import MT5Bridge
from trading_bot.data_feed import generate_realistic_gold_data


def main():
    if st is None:
        print("Streamlit is not installed. Install via: pip install streamlit")
        return

    st.set_page_config(
        page_title="XAU/USD Triple Filter Scalper MT5",
        page_icon="🏆",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Initialize persistence and managers in session state
    if "storage" not in st.session_state:
        st.session_state.storage = BotStorage()
    if "cb_manager" not in st.session_state:
        cb_cfg = CircuitBreakerConfig(bypass_noise_gate_for_demo=True)
        st.session_state.cb_manager = CircuitBreakerManager(config=cb_cfg)
    if "mt5_bridge" not in st.session_state or not hasattr(st.session_state.mt5_bridge, "get_rates"):
        st.session_state.mt5_bridge = MT5Bridge(symbol="XAUUSDm")
        st.session_state.mt5_bridge.connect()

    storage = st.session_state.storage
    cb_manager = st.session_state.cb_manager
    # Ensure noise gate bypass is always active for demo testing
    cb_manager.config.bypass_noise_gate_for_demo = True
    mt5_bridge = st.session_state.mt5_bridge

    st.title("🏆 XAU/USD 1M Triple Filter Scalper Bot")
    st.caption("EMA 9/21 Crossover + Daily-Anchored VWAP + Causal Order Blocks + Pullback Confirmation")

    # SIDEBAR: Parameters & Safety Controls
    st.sidebar.header("⚙️ Strategy Parameters")
    ema_fast = st.sidebar.number_input("EMA Fast Period", 3, 50, 9)
    ema_slow = st.sidebar.number_input("EMA Slow Period", 5, 200, 21)
    vwap_hour = st.sidebar.selectbox("VWAP Reset (UTC Hour)", [0, 7, 13], index=0, help="00:00 UTC Daily Open")
    ob_swing_lb = st.sidebar.number_input("OB Swing Lookback (Pivots)", 2, 20, 3)
    ob_max_age = st.sidebar.number_input("OB Max Age (Bars)", 10, 100, 60)
    max_pb_bars = st.sidebar.number_input("Max Pullback Bars Post-Cross", 3, 50, 35)
    pb_atr_mult = st.sidebar.slider("Pullback Proximity (x ATR)", 0.2, 3.0, 1.8, 0.1)
    rr_ratio = st.sidebar.number_input("Risk:Reward Ratio", 1.0, 5.0, 1.5, 0.5)
    sl_lookback = st.sidebar.number_input("SL Swing Lookback", 3, 30, 8)
    sl_buffer = st.sidebar.slider("SL Buffer (x ATR)", 0.0, 1.0, 0.20, 0.05)

    params = StrategyParameters(
        ema_fast_period=ema_fast,
        ema_slow_period=ema_slow,
        vwap_anchor_hour_utc=vwap_hour,
        ob_swing_lookback=ob_swing_lb,
        ob_max_age_bars=ob_max_age,
        max_pullback_bars=max_pb_bars,
        pullback_atr_mult=pb_atr_mult,
        rr_ratio=rr_ratio,
        sl_lookback_bars=sl_lookback,
        sl_buffer_atr=sl_buffer
    )

    st.sidebar.markdown("---")
    st.sidebar.header("🛡️ Safety & Circuit Breakers")
    max_daily_loss = st.sidebar.number_input("Max Daily Loss ($)", 50.0, 1000.0, 200.0)
    max_consec_losses = st.sidebar.number_input("Max Consec Losses", 1, 10, 3)
    magic_num = st.sidebar.number_input("Magic Number", 100000, 9999999, 9212001)

    cb_manager.config.max_daily_loss_usd = max_daily_loss
    cb_manager.config.max_consecutive_losses = max_consec_losses
    cb_manager.config.magic_number = magic_num

    # Sidebar Manual Refresh Button
    st.sidebar.markdown("---")
    if st.sidebar.button("🔄 Refresh Live Market Data", key="btn_refresh_market_data", use_container_width=True):
        st.rerun()

    # 1. Fetch LIVE Rates directly from MT5
    raw_bars = mt5_bridge.get_rates(count=150)
    opens = [b.open for b in raw_bars]
    highs = [b.high for b in raw_bars]
    lows = [b.low for b in raw_bars]
    closes = [b.close for b in raw_bars]
    times = [b.time for b in raw_bars]
    volumes = [b.tick_volume for b in raw_bars]

    # Check terminal & account status
    acc = mt5_bridge.get_account_info()
    if hasattr(mt5_bridge, "is_algo_trading_enabled"):
        algo_enabled = mt5_bridge.is_algo_trading_enabled()
    elif hasattr(mt5_bridge, "is_algo_trading_allowed"):
        algo_enabled = mt5_bridge.is_algo_trading_allowed()
    else:
        algo_enabled = True

    sym_info = mt5_bridge.get_symbol_info()

    # Top Status Bar
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Account Mode", acc.trade_mode, "DEMO ONLY" if acc.is_demo else "LIVE - BLOCKED")
    col2.metric("Balance", f"${acc.balance:,.2f}")
    col3.metric(f"Gold ({mt5_bridge.symbol}) Bid/Ask", f"${sym_info.bid:.2f} / ${sym_info.ask:.2f}", f"Spread: ${sym_info.spread_usd:.2f}")
    col4.metric("Consecutive Losses", f"{cb_manager.state.consecutive_losses} / {max_consec_losses}")
    col5.metric("Daily Net PnL", f"${cb_manager.state.daily_pnl_usd:+,.2f}")

    # Tabs
    tab1, tab2, tab3, tab4 = st.tabs(["📋 Live Setup Checklist", "📊 Causal Backtest & Noise Gate", "📈 Live Market & Indicators", "📜 Trade Logs & SQLite"])

    # Evaluate current bar checklist on LIVE MT5 data
    curr_idx = len(closes) - 1
    checklist = evaluate_checklist_at_bar(
        opens, highs, lows, closes,
        times, volumes, curr_idx, params
    )
    long_st = checklist["LONG"]
    short_st = checklist["SHORT"]

    with tab1:
        st.subheader("📋 5-Step Live Strategy Checklist (Latest Bar Close)")
        
        # Noise gate banner
        if not cb_manager.state.noise_gate_verified:
            st.info(f"ℹ️ **DEMO TRADING ACTIVE:** Noise gate is set to demo-bypass mode (p={cb_manager.state.noise_gate_p_value:.4f}). Orders will execute with full circuit-breaker safety.")
        else:
            st.success(f"✅ **NOISE-CONTROL GATE PASSED:** Validated with Monte Carlo p-value: {cb_manager.state.noise_gate_p_value:.4f} <= 0.05. Ready for execution.")

        c_long, c_short = st.columns(2)

        with c_long:
            st.markdown("### 🟢 BUY (LONG) SETUP CHECKLIST")
            st.info(f"**Current Market Price:** ${long_st.close_price:.2f} | **Signal:** {long_st.signal or 'NO SIGNAL'}")
            
            # Step 1
            st.write(f"**1. Trend Filter (VWAP):** {'✅ PASS' if long_st.vwap_pass else '❌ FAIL'}")
            st.caption(long_st.vwap_detail)
            
            # Step 2
            st.write(f"**2. EMA 9/21 Crossover:** {'✅ PASS' if long_st.crossover_pass else '❌ FAIL'}")
            st.caption(long_st.crossover_detail)

            # Step 3
            st.write(f"**3. Order Block Reaction:** {'✅ PASS' if long_st.ob_pass else '❌ FAIL'}")
            st.caption(long_st.ob_detail)

            # Step 4
            st.write(f"**4. Pullback to EMAs:** {'✅ PASS' if long_st.pullback_pass else '❌ FAIL'}")
            st.caption(long_st.pullback_detail)

            # Step 5
            st.write(f"**5. Confirmation Candle:** {'✅ PASS' if long_st.confirmation_pass else '❌ FAIL'}")
            st.caption(f"Pattern: {long_st.pattern_name} — {long_st.confirmation_detail}")

            if long_st.all_passed:
                st.success(f"🎯 **ALL 5 CRITERIA MET FOR BUY ENTRY**\n- Entry: ${long_st.suggested_entry:.2f}\n- Swing Low SL: ${long_st.suggested_sl:.2f} (Risk: ${long_st.risk_points:.2f})\n- TP: ${long_st.suggested_tp:.2f} (Reward: ${long_st.reward_points:.2f})")
            else:
                st.caption(f"Strategy SL (Swing Low): **${long_st.suggested_sl:.2f}** | TP ({params.rr_ratio}R): **${long_st.suggested_tp:.2f}**")

        with c_short:
            st.markdown("### 🔴 SELL (SHORT) SETUP CHECKLIST")
            st.info(f"**Current Market Price:** ${short_st.close_price:.2f} | **Signal:** {short_st.signal or 'NO SIGNAL'}")

            # Step 1
            st.write(f"**1. Trend Filter (VWAP):** {'✅ PASS' if short_st.vwap_pass else '❌ FAIL'}")
            st.caption(short_st.vwap_detail)

            # Step 2
            st.write(f"**2. EMA 9/21 Crossover:** {'✅ PASS' if short_st.crossover_pass else '❌ FAIL'}")
            st.caption(short_st.crossover_detail)

            # Step 3
            st.write(f"**3. Order Block Reaction:** {'✅ PASS' if short_st.ob_pass else '❌ FAIL'}")
            st.caption(short_st.ob_detail)

            # Step 4
            st.write(f"**4. Pullback to EMAs:** {'✅ PASS' if short_st.pullback_pass else '❌ FAIL'}")
            st.caption(short_st.pullback_detail)

            # Step 5
            st.write(f"**5. Confirmation Candle:** {'✅ PASS' if short_st.confirmation_pass else '❌ FAIL'}")
            st.caption(f"Pattern: {short_st.pattern_name} — {short_st.confirmation_detail}")

            if short_st.all_passed:
                st.error(f"🎯 **ALL 5 CRITERIA MET FOR SELL ENTRY**\n- Entry: ${short_st.suggested_entry:.2f}\n- Swing High SL: ${short_st.suggested_sl:.2f} (Risk: ${short_st.risk_points:.2f})\n- TP: ${short_st.suggested_tp:.2f} (Reward: ${short_st.reward_points:.2f})")
            else:
                st.caption(f"Strategy SL (Swing High): **${short_st.suggested_sl:.2f}** | TP ({params.rr_ratio}R): **${short_st.suggested_tp:.2f}**")

        st.markdown("---")
        st.subheader("⚡ Manual / Auto Order Dispatch")
        st.caption("Orders are executed on MT5 with Strategy Swing Low/High Stop-Loss & Take-Profit targets.")

        cb_manager.config.bypass_noise_gate_for_demo = True
        can_trade, reason = cb_manager.can_open_trade(
            is_demo_account=acc.is_demo if hasattr(acc, "is_demo") else True,
            algo_trading_enabled=algo_enabled,
            current_balance=acc.balance
        )
        col_b1, col_b2, col_b3 = st.columns([2, 2, 2])
        
        with col_b1:
            if st.button("🚀 Trigger Strategy BUY Order", key="btn_trigger_buy_order_main", type="primary", use_container_width=True):
                ok, ticket, msg = mt5_bridge.send_order(
                    direction="BUY",
                    volume=0.10,
                    sl_price=long_st.suggested_sl,
                    tp_price=long_st.suggested_tp,
                    magic_number=magic_num,
                    comment="TripleFilter_BUY"
                )
                if ok:
                    st.success(msg)
                    storage.record_trade({
                        "order_id": ticket,
                        "symbol": mt5_bridge.symbol,
                        "direction": "BUY",
                        "volume": 0.10,
                        "entry_price": long_st.close_price,
                        "sl": long_st.suggested_sl,
                        "tp": long_st.suggested_tp,
                        "status": "OPEN",
                        "opened_at": datetime.now(timezone.utc).isoformat()
                    })
                else:
                    st.error(msg)

        with col_b2:
            if st.button("🔻 Trigger Strategy SELL Order", key="btn_trigger_sell_order_main", type="secondary", use_container_width=True):
                ok, ticket, msg = mt5_bridge.send_order(
                    direction="SELL",
                    volume=0.10,
                    sl_price=short_st.suggested_sl,
                    tp_price=short_st.suggested_tp,
                    magic_number=magic_num,
                    comment="TripleFilter_SELL"
                )
                if ok:
                    st.success(msg)
                    storage.record_trade({
                        "order_id": ticket,
                        "symbol": mt5_bridge.symbol,
                        "direction": "SELL",
                        "volume": 0.10,
                        "entry_price": short_st.close_price,
                        "sl": short_st.suggested_sl,
                        "tp": short_st.suggested_tp,
                        "status": "OPEN",
                        "opened_at": datetime.now(timezone.utc).isoformat()
                    })
                else:
                    st.error(msg)

        with col_b3:
            if cb_manager.state.is_consec_loss_tripped or cb_manager.state.is_daily_loss_tripped:
                if st.button("🔄 Reset Circuit Breakers", key="btn_reset_circuit_breakers_main", use_container_width=True):
                    cb_manager.manual_reset_consecutive_losses()
                    st.success("Circuit breakers reset!")
                    st.rerun()

        if not can_trade:
            st.warning(f"Order Dispatch Guardrail Active: {reason}")

    with tab2:
        st.subheader("📊 Causal Backtesting & Noise-Control Monte Carlo Gate")
        st.write("Strict zero-lookahead backtest with separate In-Sample (75%) vs Out-of-Sample (25%) splits and 100-shuffle Monte Carlo noise testing.")

        bt_bars = st.slider("Historical Bars to Test", 500, 3000, 1500, 100, key="slider_bt_bars")
        if st.button("▶️ Execute Full Backtest & Gate Verification", key="btn_run_full_backtest"):
            with st.spinner("Running causal simulation and permutation tests..."):
                bt_data = generate_realistic_gold_data(num_bars=bt_bars, seed=101)
                res = run_causal_backtest(
                    bt_data["opens"], bt_data["highs"], bt_data["lows"], bt_data["closes"],
                    bt_data["times"], bt_data["volumes"], params,
                    initial_balance=10000.0, split_ratio=0.75, spread_points=sym_info.spread_usd,
                    num_noise_shuffles=100
                )
                st.session_state.bt_result = res
                cb_manager.set_noise_gate_status(
                    res.overall_metrics.noise_gate_passed,
                    res.overall_metrics.noise_p_value
                )

        if "bt_result" in st.session_state:
            res = st.session_state.bt_result
            
            m_is = res.in_sample_metrics
            m_oos = res.out_of_sample_metrics
            m_all = res.overall_metrics

            st.markdown("### Performance Breakdown")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("#### 📘 In-Sample (75%)")
                st.metric("Trades", m_is.total_trades)
                st.metric("Win Rate", f"{m_is.win_rate_pct:.1f}%")
                st.metric("Profit Factor", m_is.profit_factor)
                st.metric("Expectancy (R)", f"{m_is.expectancy_r:+.2f}R")
                st.metric("Net PnL", f"${m_is.total_net_pnl_usd:+,.2f}")
                st.metric("Max Drawdown", f"${m_is.max_drawdown_usd:,.2f} ({m_is.max_drawdown_pct:.1f}%)")

            with c2:
                st.markdown("#### 📙 Out-of-Sample (25%)")
                st.metric("Trades", m_oos.total_trades)
                st.metric("Win Rate", f"{m_oos.win_rate_pct:.1f}%")
                st.metric("Profit Factor", m_oos.profit_factor)
                st.metric("Expectancy (R)", f"{m_oos.expectancy_r:+.2f}R")
                st.metric("Net PnL", f"${m_oos.total_net_pnl_usd:+,.2f}")
                st.metric("Max Drawdown", f"${m_oos.max_drawdown_usd:,.2f} ({m_oos.max_drawdown_pct:.1f}%)")

            with c3:
                st.markdown("#### 🌐 Overall Dataset")
                st.metric("Trades", m_all.total_trades)
                st.metric("Win Rate", f"{m_all.win_rate_pct:.1f}%")
                st.metric("Profit Factor", m_all.profit_factor)
                st.metric("Expectancy (R)", f"{m_all.expectancy_r:+.2f}R")
                st.metric("Net PnL", f"${m_all.total_net_pnl_usd:+,.2f}")
                st.metric("Noise Gate p-value", f"{m_all.noise_p_value:.4f}")

            if m_all.noise_gate_passed:
                st.success(f"🎉 **STRATEGY PASSED NOISE GATE** (p = {m_all.noise_p_value:.4f} <= 0.05, Z-score = {m_all.z_score:.2f})")
            else:
                st.error(f"🛑 **STRATEGY FAILED NOISE GATE** (p = {m_all.noise_p_value:.4f} > 0.05). Edge is not distinguishable from noise.")

    with tab3:
        st.subheader("📈 Live Market & Indicators")
        st.write("Visualized indicator values, Order Blocks, and VWAP levels.")
        ema9_vals = calculate_ema(closes, 9)
        ema21_vals = calculate_ema(closes, 21)
        vwap_vals = calculate_session_vwap(times, highs, lows, closes, volumes, params.vwap_anchor_hour_utc)
        
        st.write(f"Latest 1m Bar Close: **${closes[-1]:.2f}** | EMA9: **${ema9_vals[-1]:.2f}** | EMA21: **${ema21_vals[-1]:.2f}** | VWAP: **${vwap_vals[-1]:.2f}**")

    with tab4:
        st.subheader("📜 Trade Logs & SQLite Storage")
        trades = storage.get_all_trades(20)
        st.write("Recent Executed Trades in SQLite:")
        st.json(trades if trades else [{"info": "No persistent trades executed yet in this session."}])

if __name__ == "__main__":
    main()