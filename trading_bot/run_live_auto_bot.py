"""
Autonomous Live Execution Engine for MT5.
Scans market continuously on every 3 seconds and executes trades autonomously.
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
    print("=" * 70)
    print("🚀 STARTING AUTONOMOUS LIVE SCALPER ENGINE (XAUUSDm M1)")
    print("=" * 70)

    # 1. Initialize Modules
    params = StrategyParameters()
    cb_config = CircuitBreakerConfig(bypass_noise_gate_for_demo=True)
    cb_manager = CircuitBreakerManager(config=cb_config)
    storage = BotStorage()
    mt5_bridge = MT5Bridge(symbol="XAUUSDm")

    # 2. Connect to MT5
    if not mt5_bridge.connect():
        print("❌ Could not connect to MetaTrader 5 terminal. Exiting.")
        return

    acc = mt5_bridge.get_account_info()
    print(f"✅ Connected to MT5 Account: {acc.login} | Mode: {acc.trade_mode} | Balance: ${acc.balance:,.2f}")
    print("⚡ Auto-Scanner active. Monitoring live market tick every 3 seconds...\n")

    last_evaluated_time = 0

    try:
        while True:
            time.sleep(3)  # Har 3 second baad live scan

            # 1. Fetch live rates
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

            # 2. Real-time checklist evaluation on every 3s loop
            checklist = evaluate_checklist_at_bar(
                opens, highs, lows, closes, times, volumes, curr_idx, params
            )
            long_st = checklist["LONG"]
            short_st = checklist["SHORT"]

            sym_info = mt5_bridge.get_symbol_info()
            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

            # 3. Print live heartbeat in terminal (updates in place)
            print(
                f"[{now_str} UTC] Gold: ${sym_info.bid:.2f}/${sym_info.ask:.2f} (Spread: ${sym_info.spread_usd:.2f}) | "
                f"BUY Setup: {'🎯 YES' if long_st.all_passed else '❌ NO'} | "
                f"SELL Setup: {'🎯 YES' if short_st.all_passed else '❌ NO'}",
                end="\r"
            )

            # Check for recently closed trades in MT5 history
            closed_deals = mt5_bridge.get_closed_deals(from_timestamp=int(time.time()) - 3600)
            for deal in closed_deals:
                ticket = deal["ticket"]
                pnl = deal["profit"]
                exit_p = deal["close_price"]

                # Update SQLite & Circuit Breakers
                storage.update_closed_trade(ticket, exit_p, pnl, exit_reason="MT5 Closed Deal")
                cb_manager.record_trade_outcome(net_pnl_usd=pnl, current_balance=acc.balance)
                
                print(f"\n📊 [TRADE CLOSED] Ticket #{ticket} | Net PnL: ${pnl:+.2f} | Exit Price: ${exit_p:.2f}", flush=True)

            # 4. Print detailed log on every new candle close
            if latest_bar.time != last_evaluated_time:
                last_evaluated_time = latest_bar.time
                print(
                    f"\n[CANDLE CLOSE {now_str} UTC] Price: ${closes[-1]:.2f} | "
                    f"EMA9: ${long_st.ema_fast:.2f} | EMA21: ${long_st.ema_slow:.2f} | "
                    f"VWAP: ${long_st.vwap_value:.2f}"
                )

            # 5. Check Pre-Trade Safety Guardrails
            can_trade, reason = cb_manager.can_open_trade(
                is_demo_account=acc.is_demo,
                algo_trading_enabled=mt5_bridge.is_algo_trading_allowed(),
                current_balance=acc.balance
            )

            if not can_trade:
                continue

            # ================= EXECUTE BUY ORDER =================
            if long_st.all_passed:
                print(f"\n🎯 >>> ALL 5 CONDITIONS MET: EXECUTING BUY ORDER AT ${sym_info.ask:.2f} <<<")
                ok, ticket, msg = mt5_bridge.send_order(
                    direction="BUY",
                    volume=0.10,
                    sl_price=long_st.suggested_sl,
                    tp_price=long_st.suggested_tp,
                    magic_number=cb_config.magic_number,
                    comment="Auto_TripleFilter_BUY"
                )
                if ok:
                    print(f"✅ {msg}\n")
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
                    time.sleep(60)  # Cooldown taake same candle par multiple orders na lagein
                else:
                    print(f"❌ Order Failed: {msg}\n")

            # ================= EXECUTE SELL ORDER =================
            elif short_st.all_passed:
                print(f"\n🎯 >>> ALL 5 CONDITIONS MET: EXECUTING SELL ORDER AT ${sym_info.bid:.2f} <<<")
                ok, ticket, msg = mt5_bridge.send_order(
                    direction="SELL",
                    volume=0.10,
                    sl_price=short_st.suggested_sl,
                    tp_price=short_st.suggested_tp,
                    magic_number=cb_config.magic_number,
                    comment="Auto_TripleFilter_SELL"
                )
                if ok:
                    print(f"✅ {msg}\n")
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
                    time.sleep(60)  # Cooldown taake same candle par multiple orders na lagein
                else:
                    print(f"❌ Order Failed: {msg}\n")

    except KeyboardInterrupt:
        print("\n🛑 Auto-trading engine stopped by user.")
        mt5_bridge.disconnect()


if __name__ == "__main__":
    run_live_auto_trading()