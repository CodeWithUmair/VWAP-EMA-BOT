"""
Autonomous Live Execution Engine for MT5 with High-Precision Filters & Risk Controls.
Includes:
- Single-Position Enforcement (No stacking/over-leveraging)
- Dynamic Auto Break-Even (Moves SL to entry once in profit)
- Anti-Chop & ATR Minimum Filter (Prevents trading during dead/flat consolidation)
- Post-Loss 5-Minute Cooling Period (Prevents whipsaw repeat losses)
- Active Circuit Breakers & Auto PnL Sync
"""

import time
import sys
import os
from datetime import datetime, timezone

# Ensure path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.mt5_bridge import MT5Bridge
from trading_bot.strategy import (
    StrategyParameters,
    evaluate_checklist_at_bar,
    calculate_sl_tp,
    calculate_atr
)
from trading_bot.circuit_breakers import CircuitBreakerConfig, CircuitBreakerManager
from trading_bot.storage import BotStorage


def run_live_auto_trading():
    print("=" * 80, flush=True)
    print("🚀 STARTING AUTONOMOUS PRO SCALPER ENGINE (XAUUSDm M1)", flush=True)
    print("🛡️ ACTIVE SHIELDS: Break-Even Auto-Lock | Single Position | Anti-Chop Filter", flush=True)
    print("=" * 80, flush=True)

    # Optimized Quantitative Strategy Parameters
    params = StrategyParameters(
        max_pullback_bars=25,       # Valid pullback window
        ob_buffer_atr=0.25,         # Clean OB zone retest
        pullback_atr_mult=1.5,      # Proximity to EMA zone
        rr_ratio=1.5,               # 1:1.5 Risk:Reward
        min_sl_distance_points=1.2, # Minimum $1.20 SL on Gold
        max_sl_distance_points=5.0  # Max $5.00 SL on Gold
    )

    cb_config = CircuitBreakerConfig(
        bypass_noise_gate_for_demo=True,
        max_consecutive_losses=3,
        max_daily_loss_usd=150.0,
        cooldown_after_loss_minutes=5
    )
    cb_manager = CircuitBreakerManager(config=cb_config)
    storage = BotStorage()
    mt5_bridge = MT5Bridge(symbol="XAUUSDm")

    if not mt5_bridge.connect():
        print("❌ Could not connect to MetaTrader 5 terminal. Exiting.", flush=True)
        return

    acc = mt5_bridge.get_account_info()
    algo_allowed = mt5_bridge.is_algo_trading_enabled()

    print(f"✅ Connected to MT5 Account: {acc.login} | Mode: {acc.trade_mode} | Balance: ${acc.balance:,.2f}", flush=True)
    print(f"⚡ Target Symbol: {mt5_bridge.symbol} | Algo Allowed: {algo_allowed}", flush=True)
    print("⚡ Auto-Scanner active. Streaming live ticks every 3 seconds...\n", flush=True)

    last_evaluated_time = 0
    last_loss_time = 0
    processed_deal_tickets = set()
    be_moved_tickets = set()

    try:
        while True:
            time.sleep(3)

            # 1. Check & Manage Active Open Positions
            open_positions = mt5_bridge.get_open_positions()
            sym_info = mt5_bridge.get_symbol_info()

            # --- AUTO BREAK-EVEN & PROFIT LOCK LOGIC ---
            for pos in open_positions:
                ticket = pos["ticket"]
                direction = pos["direction"]
                entry_p = pos["entry_price"]
                current_p = pos["current_price"]
                sl = pos["sl"]
                tp = pos["tp"]

                # If position is in profit by +1.0R (or 50% toward TP), lock Break-Even
                if ticket not in be_moved_tickets and tp > 0 and sl > 0:
                    if direction == "BUY":
                        target_dist = tp - entry_p
                        current_gain = current_p - entry_p
                        if current_gain >= target_dist * 0.5 and sl < entry_p:
                            new_sl = entry_p + (sym_info.spread_usd or 0.15)
                            if mt5_bridge.modify_position_sl(ticket, new_sl):
                                be_moved_tickets.add(ticket)
                                print(f"🔒 [PROFIT SHIELD] BUY Order {ticket} moved to Break-Even at ${new_sl:.2f}!", flush=True)

                    elif direction == "SELL":
                        target_dist = entry_p - tp
                        current_gain = entry_p - current_p
                        if current_gain >= target_dist * 0.5 and sl > entry_p:
                            new_sl = entry_p - (sym_info.spread_usd or 0.15)
                            if mt5_bridge.modify_position_sl(ticket, new_sl):
                                be_moved_tickets.add(ticket)
                                print(f"🔒 [PROFIT SHIELD] SELL Order {ticket} moved to Break-Even at ${new_sl:.2f}!", flush=True)

            # 2. Check Closed Deals in MT5 History and Update Circuit Breaker
            closed_deals = mt5_bridge.get_closed_deals(from_timestamp=int(time.time()) - 86400)
            for deal in closed_deals:
                ticket = deal["ticket"]
                if ticket not in processed_deal_tickets:
                    processed_deal_tickets.add(ticket)
                    pnl = deal["profit"]
                    exit_p = deal["close_price"]
                    storage.update_closed_trade(ticket, exit_p, pnl, exit_reason="MT5 Deal Closed")
                    cb_manager.record_trade_outcome(net_pnl_usd=pnl, current_balance=acc.balance)
                    
                    if pnl < 0:
                        last_loss_time = time.time()
                        print(f"⚠️ [TRADE CLOSED - LOSS] Deal {ticket} closed at -${abs(pnl):.2f}. Activating 5-min cooldown.", flush=True)
                    else:
                        print(f"🎉 [TRADE CLOSED - WIN] Deal {ticket} closed at +${pnl:.2f} profit!", flush=True)

            # 3. Fetch live rates from MT5
            bars = mt5_bridge.get_rates(count=150)
            if not bars or len(bars) < 35:
                continue

            latest_bar = bars[-1]
            opens = [b.open for b in bars]
            highs = [b.high for b in bars]
            lows = [b.low for b in bars]
            closes = [b.close for b in bars]
            times = [b.time for b in bars]
            volumes = [b.tick_volume for b in bars]

            curr_idx = len(closes) - 1

            # 4. Evaluate Strategy Checklist
            checklist = evaluate_checklist_at_bar(
                opens, highs, lows, closes, times, volumes, curr_idx, params
            )
            long_st = checklist["LONG"]
            short_st = checklist["SHORT"]

            # Calculate current ATR for chop filter
            atr_vals = calculate_atr(highs, lows, closes, period=14)
            curr_atr = atr_vals[-1] if atr_vals else 1.0

            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

            # 5. Print Diagnostics on 1M Candle Close
            if latest_bar.time != last_evaluated_time:
                last_evaluated_time = latest_bar.time

                buy_passed_count = sum([long_st.vwap_pass, long_st.crossover_pass, long_st.ob_pass, long_st.pullback_pass, long_st.confirmation_pass])
                sell_passed_count = sum([short_st.vwap_pass, short_st.crossover_pass, short_st.ob_pass, short_st.pullback_pass, short_st.confirmation_pass])

                pos_status = f"{len(open_positions)} OPEN ({open_positions[0]['direction']})" if open_positions else "0 OPEN"

                print(
                    f"\n🕯️ [{now_str} UTC | M1 CLOSE] Price: ${closes[-1]:.2f} | ATR: ${curr_atr:.2f} | Positions: {pos_status}\n"
                    f"   ├─ 🟢 BUY Setup ({buy_passed_count}/5): VWAP={long_st.vwap_pass} | Cross={long_st.crossover_pass} | OB={long_st.ob_pass} | Pullback={long_st.pullback_pass} | Candle={long_st.confirmation_pass}\n"
                    f"   └─ 🔴 SELL Setup ({sell_passed_count}/5): VWAP={short_st.vwap_pass} | Cross={short_st.crossover_pass} | OB={short_st.ob_pass} | Pullback={short_st.pullback_pass} | Candle={short_st.confirmation_pass}",
                    flush=True
                )

            # 6. Safety & Single-Position Checks
            # Guard A: Do NOT open new trade if one is already running
            if len(open_positions) >= 1:
                continue

            # Guard B: Post-Loss 5-Minute Cooldown
            if (time.time() - last_loss_time) < (cb_config.cooldown_after_loss_minutes * 60):
                continue

            # Guard C: Dead-Market / Flat Volatility Chop Filter
            if curr_atr < 0.70:  # If Gold 1M range is under $0.70, market is in a dead flat consolidation trap
                continue

            # Guard D: Circuit Breakers (Max 3 losses / Daily loss)
            can_trade, reason = cb_manager.can_open_trade(
                is_demo_account=acc.is_demo,
                algo_trading_enabled=mt5_bridge.is_algo_trading_enabled(),
                current_balance=acc.balance
            )

            if not can_trade:
                print(f"🛑 [SAFETY HALT] Trading blocked: {reason}", flush=True)
                time.sleep(30)
                continue

            # ================= EXECUTE BUY ORDER =================
            if long_st.all_passed:
                print(f"\n🎯 >>> ALL 5 CONDITIONS MET: EXECUTING BUY ORDER AT ${sym_info.ask:.2f} <<<", flush=True)
                res = mt5_bridge.send_order(
                    direction="BUY",
                    volume=0.10,
                    sl_price=long_st.suggested_sl,
                    tp_price=long_st.suggested_tp,
                    magic_number=cb_config.magic_number,
                    comment="ProScalper_BUY"
                )
                ok, ticket, msg = res if (isinstance(res, tuple) and len(res) == 3) else (False, 0, str(res))
                if ok:
                    print(f"✅ {msg}\n", flush=True)
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
                    time.sleep(60)
                else:
                    print(f"❌ Order Failed: {msg}\n", flush=True)

            # ================= EXECUTE SELL ORDER =================
            elif short_st.all_passed:
                print(f"\n🎯 >>> ALL 5 CONDITIONS MET: EXECUTING SELL ORDER AT ${sym_info.bid:.2f} <<<", flush=True)
                res = mt5_bridge.send_order(
                    direction="SELL",
                    volume=0.10,
                    sl_price=short_st.suggested_sl,
                    tp_price=short_st.suggested_tp,
                    magic_number=cb_config.magic_number,
                    comment="ProScalper_SELL"
                )
                ok, ticket, msg = res if (isinstance(res, tuple) and len(res) == 3) else (False, 0, str(res))
                if ok:
                    print(f"✅ {msg}\n", flush=True)
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
                    time.sleep(60)
                else:
                    print(f"❌ Order Failed: {msg}\n", flush=True)

    except KeyboardInterrupt:
        print("\n🛑 Auto-trading engine stopped by user.", flush=True)
        mt5_bridge.disconnect()


if __name__ == "__main__":
    run_live_auto_trading()
