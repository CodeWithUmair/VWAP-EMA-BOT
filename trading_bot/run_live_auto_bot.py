"""
Autonomous Live Execution Engine for MT5 with Real-Time Filter Diagnostics.
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
    calculate_sl_tp
)
from trading_bot.circuit_breakers import CircuitBreakerConfig, CircuitBreakerManager
from trading_bot.storage import BotStorage


def run_live_auto_trading():
    print("=" * 75, flush=True)
    print("🚀 STARTING AUTONOMOUS LIVE SCALPER ENGINE (XAUUSDm M1)", flush=True)
    print("=" * 75, flush=True)

    # Balanced Strategy Parameters for Realistic M1 Scalping
    params = StrategyParameters(
        max_pullback_bars=35,       # Pullback window increased to 35 bars
        ob_buffer_atr=0.35,         # Buffer to catch OB zone retests
        pullback_atr_mult=1.8       # Realistic distance to EMA zone
    )

    cb_config = CircuitBreakerConfig(bypass_noise_gate_for_demo=True)
    cb_manager = CircuitBreakerManager(config=cb_config)
    storage = BotStorage()
    mt5_bridge = MT5Bridge(symbol="XAUUSDm")

    if not mt5_bridge.connect():
        print("❌ Could not connect to MetaTrader 5 terminal. Exiting.", flush=True)
        return

    acc = mt5_bridge.get_account_info()
    print(f"✅ Connected to MT5 Account: {acc.login} | Mode: {acc.trade_mode} | Balance: ${acc.balance:,.2f}", flush=True)
    print(f"⚡ Target Symbol: {mt5_bridge.symbol} | Algo Allowed: {mt5_bridge.is_algo_trading_allowed()}", flush=True)
    print("⚡ Auto-Scanner active. Streaming live ticks every 3 seconds...\n", flush=True)

    last_evaluated_time = 0
    iteration = 0

    try:
        while True:
            time.sleep(3)
            iteration += 1

            # 1. Fetch live rates from MT5
            bars = mt5_bridge.get_rates(count=150)
            if not bars or len(bars) < 30:
                continue

            latest_bar = bars[-1]
            opens = [b.open for b in bars]
            highs = [b.high for b in bars]
            lows = [b.low for b in bars]
            closes = [b.close for b in bars]
            times = [b.time for b in bars]
            volumes = [b.tick_volume for b in bars]

            curr_idx = len(closes) - 1

            # 2. Evaluate Strategy Checklist
            checklist = evaluate_checklist_at_bar(
                opens, highs, lows, closes, times, volumes, curr_idx, params
            )
            long_st = checklist["LONG"]
            short_st = checklist["SHORT"]

            sym_info = mt5_bridge.get_symbol_info()
            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

            # 3. Print Detailed Diagnostics on every 1M Candle Close
            if latest_bar.time != last_evaluated_time:
                last_evaluated_time = latest_bar.time

                buy_passed_count = sum([long_st.vwap_pass, long_st.crossover_pass, long_st.ob_pass, long_st.pullback_pass, long_st.confirmation_pass])
                sell_passed_count = sum([short_st.vwap_pass, short_st.crossover_pass, short_st.ob_pass, short_st.pullback_pass, short_st.confirmation_pass])

                print(
                    f"\n🕯️ [{now_str} UTC | M1 CLOSE] Price: ${closes[-1]:.2f} (Spread: ${sym_info.spread_usd:.2f})\n"
                    f"   ├─ 🟢 BUY Setup ({buy_passed_count}/5): VWAP={long_st.vwap_pass} | Cross={long_st.crossover_pass} | OB={long_st.ob_pass} | Pullback={long_st.pullback_pass} | Candle={long_st.confirmation_pass}\n"
                    f"   └─ 🔴 SELL Setup ({sell_passed_count}/5): VWAP={short_st.vwap_pass} | Cross={short_st.crossover_pass} | OB={short_st.ob_pass} | Pullback={short_st.pullback_pass} | Candle={short_st.confirmation_pass}",
                    flush=True
                )

            # 4. Check Closed Deals in MT5 History and Record PnL
            closed_deals = mt5_bridge.get_closed_deals(from_timestamp=int(time.time()) - 3600)
            for deal in closed_deals:
                ticket = deal["ticket"]
                pnl = deal["profit"]
                exit_p = deal["close_price"]
                storage.update_closed_trade(ticket, exit_p, pnl, exit_reason="MT5 Closed Deal")
                cb_manager.record_trade_outcome(net_pnl_usd=pnl, current_balance=acc.balance)

            # 5. Check Safety Guardrails
            can_trade, reason = cb_manager.can_open_trade(
                is_demo_account=acc.is_demo,
                algo_trading_enabled=mt5_bridge.is_algo_trading_allowed(),
                current_balance=acc.balance
            )

            if not can_trade:
                continue

            # ================= EXECUTE BUY ORDER =================
            if long_st.all_passed:
                print(f"\n🎯 >>> ALL 5 CONDITIONS MET: EXECUTING BUY ORDER AT ${sym_info.ask:.2f} <<<", flush=True)
                ok, ticket, msg = mt5_bridge.send_order(
                    direction="BUY",
                    volume=0.10,
                    sl_price=long_st.suggested_sl,
                    tp_price=long_st.suggested_tp,
                    magic_number=cb_config.magic_number,
                    comment="Auto_TripleFilter_BUY"
                )
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
                ok, ticket, msg = mt5_bridge.send_order(
                    direction="SELL",
                    volume=0.10,
                    sl_price=short_st.suggested_sl,
                    tp_price=short_st.suggested_tp,
                    magic_number=cb_config.magic_number,
                    comment="Auto_TripleFilter_SELL"
                )
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