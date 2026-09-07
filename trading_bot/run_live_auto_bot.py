"""
Autonomous Institutional-Grade Live Scalper Engine for MT5 (XAUUSD M1).
Features:
1. Multi-Timeframe Trend Filter (M15 EMA 50 alignment prevents counter-trend traps)
2. Institutional Killzone Tracker (London & New York high-volume momentum)
3. Smart Dynamic ATR-Buffered SL & TP (Minimum $1.80 breathing room prevents stop-hunting)
4. Dynamic Auto Break-Even Shield (locks +$2.50, capped at 40% of target, once 60% of the way to TP)
5. Single Active Position Enforcement (Prevents dangerous stacking/over-leveraging)
6. Post-Loss Cooling Guard (3-minute circuit pause prevents revenge whipsaws)
7. Configurable 0.01 Micro-Lot Size & Expanded $500 Daily Loss Limit
"""

import time
import sys
import os
from dataclasses import asdict
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from trading_bot.mt5_bridge import MT5Bridge
from trading_bot.strategy import (
    StrategyParameters,
    evaluate_checklist_at_bar,
    calculate_sl_tp,
    calculate_atr,
    evaluate_htf_trend,
    is_in_killzone
)
from trading_bot.circuit_breakers import CircuitBreakerConfig, CircuitBreakerManager
from trading_bot.storage import BotStorage


def _in_news_blackout(windows, now_utc=None):
    """True if now_utc falls inside any "HH:MM-HH:MM" UTC window (no midnight wrap)."""
    now = now_utc or datetime.now(timezone.utc)
    mins = now.hour * 60 + now.minute
    for w in windows or []:
        try:
            a, b = str(w).split("-")
            ah, am = (int(x) for x in a.split(":"))
            bh, bm = (int(x) for x in b.split(":"))
            if ah * 60 + am <= mins <= bh * 60 + bm:
                return True, w
        except Exception:
            continue
    return False, None


def _bar_age_seconds(bar_time):
    """Seconds between a bar's ISO-8601 timestamp and now (UTC). None if unparseable."""
    try:
        t = datetime.fromisoformat(str(bar_time))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return None


def run_live_auto_trading():
    print("=" * 85, flush=True)
    print("🚀 STARTING INSTITUTIONAL PRO SCALPER ENGINE (XAUUSD M1)", flush=True)
    print("🛡️ ACTIVE CONFLUENCES: M15 Trend Align | Break-Even Shield | Smart ATR Buffer", flush=True)
    print("=" * 85, flush=True)

    # Strategy Parameters
    params = StrategyParameters()
    params.max_pullback_bars = 35        # Realistic pullback window
    params.ob_buffer_atr = 0.35          # Clean Order Block retest zone
    params.pullback_atr_mult = 1.8       # Proximity to EMA 9/21 zone
    params.rr_ratio = 1.5                # 1:1.5 Risk:Reward
    params.sl_buffer_atr = 0.50          # 0.5 ATR cushion beyond swing pivots
    params.min_sl_distance_points = 1.8  # Minimum $1.80 SL on Gold to survive wicks
    params.max_sl_distance_points = 6.0  # Max $6.00 SL on Gold
    params.enable_htf_filter = True      # Strictly trade with M15 macro trend
    params.enable_session_filter = False # Set True to ONLY trade London/NY Killzones

    # Trading Volume & Risk Settings
    trade_lot_size = 0.01                # Micro-lot 0.01 for safe scaling and testing

    cb_config = CircuitBreakerConfig(
        bypass_noise_gate_for_demo=True,
        max_consecutive_losses=6,        # Relaxed consecutive loss count
        max_daily_loss_usd=500.0,        # Increased daily loss ceiling ($500.00)
        cooldown_after_loss_minutes=3
    )
    cb_manager = CircuitBreakerManager(config=cb_config)
    storage = BotStorage()

    # Honour the dashboard's "Safety & Circuit Breakers" panel (SQLite "bot_config" row).
    # cb_manager holds cb_config by reference, so mutating it here takes effect live.
    _cfg = storage.get_setting("bot_config", {}) or {}
    if _cfg:
        cb_config.max_daily_loss_usd = float(_cfg.get("max_daily_loss", cb_config.max_daily_loss_usd))
        cb_config.max_consecutive_losses = int(_cfg.get("max_consec_losses", cb_config.max_consecutive_losses))
        cb_config.magic_number = int(_cfg.get("magic_num", cb_config.magic_number))
        print(f"⚙️ bot_config applied — MaxDailyLoss ${cb_config.max_daily_loss_usd:g} | "
              f"MaxConsecLosses {cb_config.max_consecutive_losses} | Magic {cb_config.magic_number}", flush=True)

    # Execution-safety knobs (questionnaire sections D / K / N). 0 disables a gate.
    MAX_SPREAD_USD = float(_cfg.get("max_spread_usd", 0.60))     # refuse entry above this live spread
    STALE_BAR_SECS = float(_cfg.get("stale_bar_secs", 180))      # halt entries if the feed freezes
    NEWS_BLACKOUT = _cfg.get("news_blackout", []) or []          # list of "HH:MM-HH:MM" UTC windows
    print(f"🛡️ Exec guards — MaxSpread ${MAX_SPREAD_USD:g} | StaleBar {STALE_BAR_SECS:g}s | "
          f"NewsBlackout {NEWS_BLACKOUT or 'none'}", flush=True)

    # Restore circuit-breaker state so a restart does not wipe the day's loss tally
    # or the consecutive-loss count (questionnaire Q8 / Q53).
    _saved_cb = storage.get_setting("cb_state", {}) or {}
    if _saved_cb.get("current_date") == datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        for _k, _v in _saved_cb.items():
            if hasattr(cb_manager.state, _k):
                setattr(cb_manager.state, _k, _v)
        print(f"↻ Restored circuit-breaker state — consec losses {cb_manager.state.consecutive_losses}, "
              f"daily P&L ${cb_manager.state.daily_pnl_usd:+.2f}, "
              f"daily-loss tripped={cb_manager.state.is_daily_loss_tripped}", flush=True)

    mt5_bridge = MT5Bridge(symbol="XAUUSDm")

    if not mt5_bridge.connect():
        print("❌ Could not connect to MetaTrader 5 terminal. Exiting.", flush=True)
        return

    acc = mt5_bridge.get_account_info()
    algo_allowed = mt5_bridge.is_algo_trading_enabled()

    print(f"✅ Connected to MT5 Account: {acc.login} | Mode: {acc.trade_mode} | Balance: ${acc.balance:,.2f}", flush=True)
    print(f"⚡ Target Symbol: {mt5_bridge.symbol} | Default Lot Size: {trade_lot_size} | Algo Allowed: {algo_allowed}", flush=True)
    print("⚡ Auto-Scanner active. Streaming live ticks every 3 seconds...\n", flush=True)

    last_evaluated_time = 0
    last_loss_time = 0
    start_session_time = int(time.time())
    processed_deal_tickets = set()
    be_moved_tickets = set()
    pos_tp = {}       # ticket -> take-profit price, snapshotted while the position is open
    be_lock = {}      # ticket -> shield stop price, set when the profit shield arms

    # Profit-shield tuning: arm once 60% of the way to TP, lock in up to $2.50 of price
    # (capped at 40% of the target so tight-target trades keep a buffer to current price).
    SHIELD_ARM_FRAC = 0.60
    SHIELD_LOCK_USD = 2.50
    SHIELD_LOCK_CAP_FRAC = 0.40

    # Heartbeat for the dashboard's AUTO-ENGINE indicator (read from SQLite settings).
    try:
        storage.set_setting("engine_started_at", datetime.now(timezone.utc).isoformat())
        storage.set_setting("engine_pid", os.getpid())
    except Exception:
        pass

    try:
        while True:
            time.sleep(3)

            # Refresh the heartbeat every loop; a transient DB lock must never stop trading.
            try:
                storage.set_setting("engine_heartbeat", datetime.now(timezone.utc).isoformat())
            except Exception:
                pass

            # 1. Fetch live open positions and apply the profit shield

            open_positions = mt5_bridge.get_open_positions()
            sym_info = mt5_bridge.get_symbol_info()

            for pos in open_positions:
                ticket = pos["ticket"]
                direction = pos["direction"]
                entry_p = pos["entry_price"]
                current_p = pos["current_price"]
                sl = pos["sl"]
                tp = pos["tp"]
                pos_tp[ticket] = tp

                # Arm the profit shield once the trade is SHIELD_ARM_FRAC of the way to TP
                if ticket not in be_moved_tickets and tp > 0 and sl > 0:
                    if direction == "BUY":
                        target_dist = tp - entry_p
                        current_gain = current_p - entry_p
                        if current_gain >= target_dist * SHIELD_ARM_FRAC and sl < entry_p:
                            lock = min(SHIELD_LOCK_USD, target_dist * SHIELD_LOCK_CAP_FRAC)
                            new_sl = entry_p + lock
                            if mt5_bridge.modify_position_sl(ticket, new_sl):
                                be_moved_tickets.add(ticket)
                                be_lock[ticket] = new_sl
                                print(f"🔒 [PROFIT SHIELD] BUY Order {ticket} locked +${lock:.2f} at ${new_sl:.2f}!", flush=True)

                    elif direction == "SELL":
                        target_dist = entry_p - tp
                        current_gain = entry_p - current_p
                        if current_gain >= target_dist * SHIELD_ARM_FRAC and sl > entry_p:
                            lock = min(SHIELD_LOCK_USD, target_dist * SHIELD_LOCK_CAP_FRAC)
                            new_sl = entry_p - lock
                            if mt5_bridge.modify_position_sl(ticket, new_sl):
                                be_moved_tickets.add(ticket)
                                be_lock[ticket] = new_sl
                                print(f"🔒 [PROFIT SHIELD] SELL Order {ticket} locked +${lock:.2f} at ${new_sl:.2f}!", flush=True)

            # 2. Check deals closed during this live session
            if hasattr(mt5_bridge, "get_closed_deals"):
                closed_deals = mt5_bridge.get_closed_deals(from_timestamp=start_session_time)
                for deal in closed_deals:
                    ticket = deal["ticket"]
                    if ticket not in processed_deal_tickets:
                        processed_deal_tickets.add(ticket)
                        pnl = deal["profit"]
                        exit_p = deal["close_price"]

                        # Classify the exit: shield stop (scratch), take-profit, or original stop.
                        lock_ref = be_lock.get(ticket)
                        tp_ref = pos_tp.get(ticket)
                        if lock_ref is not None and abs(exit_p - lock_ref) <= 0.40:
                            exit_reason = "Break-Even Shield"
                        elif tp_ref and abs(exit_p - tp_ref) <= 0.40:
                            exit_reason = "TP Hit"
                        elif pnl > 0:
                            exit_reason = "TP Hit"
                        else:
                            exit_reason = "SL Hit"

                        storage.update_closed_trade(ticket, exit_p, pnl, exit_reason=exit_reason)
                        cb_manager.record_trade_outcome(net_pnl_usd=pnl, current_balance=acc.balance)
                        try:  # persist so a restart keeps the day's tally (Q8)
                            storage.set_setting("cb_state", asdict(cb_manager.state))
                        except Exception:
                            pass

                        if exit_reason == "Break-Even Shield":
                            sign = "+" if pnl >= 0 else "-"
                            print(f"🛡️ [TRADE CLOSED - SCRATCH] Deal {ticket} shielded at {sign}${abs(pnl):.2f} (break-even).", flush=True)
                        elif pnl < 0:
                            last_loss_time = time.time()
                            print(f"⚠️ [TRADE CLOSED - LOSS] Deal #{ticket} closed at -${abs(pnl):.2f}. Cooling down for 3 mins.", flush=True)
                        else:
                            print(f"🎉 [TRADE CLOSED - WIN] Deal #{ticket} closed at +${pnl:.2f} profit!", flush=True)

            # 3. Fetch live M1 bars
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

            # 4. Fetch M15 bars for Macro Trend Alignment
            htf_trend = "NEUTRAL"
            htf_reason = "HTF Filter Disabled"
            try:
                htf_data = mt5_bridge.fetch_htf_bars(count=80, timeframe="M15")
                if htf_data and len(htf_data["closes"]) >= 50:
                    htf_trend, htf_ema, htf_reason = evaluate_htf_trend(htf_data["closes"], period=50)
            except Exception as e:
                htf_trend = "NEUTRAL"
                htf_reason = f"HTF fetch error: {str(e)}"

            # 5. Session Killzone Status
            in_killzone, killzone_name = is_in_killzone()

            # 6. Evaluate 5-Step Checklist on M1
            checklist = evaluate_checklist_at_bar(
                opens, highs, lows, closes, times, volumes, curr_idx, params
            )
            long_st = checklist["LONG"]
            short_st = checklist["SHORT"]

            atr_vals = calculate_atr(highs, lows, closes, period=14)
            curr_atr = atr_vals[-1] if atr_vals else 1.0

            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

            # 7. Print Diagnostic Output on M1 Candle Close
            _new_bar = latest_bar.time != last_evaluated_time
            if _new_bar:
                last_evaluated_time = latest_bar.time

                buy_passed_count = sum([long_st.vwap_pass, long_st.crossover_pass, long_st.ob_pass, long_st.pullback_pass, long_st.confirmation_pass])
                sell_passed_count = sum([short_st.vwap_pass, short_st.crossover_pass, short_st.ob_pass, short_st.pullback_pass, short_st.confirmation_pass])

                pos_status = f"{len(open_positions)} OPEN ({open_positions[0]['direction']})" if open_positions else "0 OPEN"

                print(
                    f"\n🕯️ [{now_str} UTC | M1 CLOSE] Price: ${closes[-1]:.2f} | ATR: ${curr_atr:.2f} | Session: {killzone_name}\n"
                    f"   ├─ 🧭 M15 Macro Trend: {htf_trend} ({htf_reason})\n"
                    f"   ├─ 🟢 BUY Setup ({buy_passed_count}/5): VWAP={long_st.vwap_pass} | Cross={long_st.crossover_pass} | OB={long_st.ob_pass} | Pullback={long_st.pullback_pass} | Candle={long_st.confirmation_pass}\n"
                    f"   ├─ 🔴 SELL Setup ({sell_passed_count}/5): VWAP={short_st.vwap_pass} | Cross={short_st.crossover_pass} | OB={short_st.ob_pass} | Pullback={short_st.pullback_pass} | Candle={short_st.confirmation_pass}\n"
                    f"   └─ 🛡️ Active Positions: {pos_status}",
                    flush=True
                )

            # ================= STRICT RISK SHIELDS =================

            # Shield 1: Single Active Position Guard
            if len(open_positions) >= 1:
                continue

            # Shield 2: 3-Minute Post-Loss Cooling Period
            if (time.time() - last_loss_time) < (cb_config.cooldown_after_loss_minutes * 60):
                continue

            # Shield 3: Dead-Market Anti-Chop Filter
            if curr_atr < 0.45:  # Gold M1 ATR < $0.45 indicates flat range trap
                continue

            # Shield 4: Session Killzone Filter (Optional)
            if params.enable_session_filter and not in_killzone:
                continue

            # Shield 4b: Max-Spread Gate — refuse 1-minute scalps when the spread is
            # abnormally wide (news spikes, thin liquidity). Questionnaire D19/D20.
            if MAX_SPREAD_USD > 0 and sym_info.spread_usd > MAX_SPREAD_USD:
                if _new_bar:
                    print(f"⏸️  [SPREAD GUARD] Spread ${sym_info.spread_usd:.2f} > "
                          f"${MAX_SPREAD_USD:.2f} limit — no new entries.", flush=True)
                continue

            # Shield 4c: Stale-Feed Gate — if the newest bar stopped advancing the
            # price feed is frozen; do not trade on stale data. Questionnaire N77/N78.
            _age = _bar_age_seconds(latest_bar.time)
            if STALE_BAR_SECS > 0 and _age is not None and _age > STALE_BAR_SECS:
                if _new_bar:
                    print(f"⏸️  [STALE FEED] Newest M1 bar is {_age:.0f}s old "
                          f"(> {STALE_BAR_SECS:.0f}s) — halting new entries.", flush=True)
                continue

            # Shield 4d: News Blackout — skip new entries inside configured
            # high-impact-news windows (CPI / NFP / FOMC). Questionnaire C14/C15.
            _blk, _win = _in_news_blackout(NEWS_BLACKOUT)
            if _blk:
                if _new_bar:
                    print(f"⏸️  [NEWS BLACKOUT] Inside window {_win} UTC — no new entries.", flush=True)
                continue

            # Shield 5: Circuit Breakers (Max consecutive losses / daily loss)
            can_trade, reason = cb_manager.can_open_trade(
                is_demo_account=acc.is_demo,
                algo_trading_enabled=mt5_bridge.is_algo_trading_enabled(),
                current_balance=acc.balance
            )
            if not can_trade:
                continue

            # Dashboard "Auto-Trade" toggle: when off, the engine still manages
            # open positions (break-even, closed-deal tracking) above but won't
            # open new ones — manual dispatch from the dashboard still works.
            if not storage.get_setting("auto_trade_enabled", True):
                continue

            # ================= EXECUTE BUY ORDER =================
            if long_st.all_passed:
                if params.enable_htf_filter and htf_trend == "BEARISH":
                    print(f"⚠️ [FILTER BLOCKED] M1 BUY Signal skipped: M15 Macro Trend is BEARISH (Counter-trend protection)", flush=True)
                    continue

                print(f"\n🎯 >>> ALL CONFLUENCES ALIGNED: EXECUTING BUY ORDER (Lot: {trade_lot_size}) AT ${sym_info.ask:.2f} <<<", flush=True)
                print(f"   SL: ${long_st.suggested_sl:.2f} (Risk: ${long_st.risk_points:.2f}) | TP: ${long_st.suggested_tp:.2f} (Reward: ${long_st.reward_points:.2f})", flush=True)
                
                res = mt5_bridge.send_order(
                    direction="BUY",
                    volume=trade_lot_size,
                    sl_price=long_st.suggested_sl,
                    tp_price=long_st.suggested_tp,
                    magic_number=cb_config.magic_number,
                    comment="ProScalper_BUY"
                )
                ok, ticket, msg = res if (isinstance(res, tuple) and len(res) == 3) else (False, 0, str(res))
                if ok:
                    _le = dict(getattr(mt5_bridge, "last_exec", {}) or {})
                    print(f"✅ {msg}", flush=True)
                    print(f"   📏 spread ${_le.get('spread_paid_usd', 0):.2f} | "
                          f"slippage ${_le.get('entry_slippage_usd', 0):+.2f} | "
                          f"latency {_le.get('entry_latency_ms', 0):.0f}ms\n", flush=True)
                    storage.record_trade({
                        "order_id": ticket,
                        "symbol": mt5_bridge.symbol,
                        "direction": "BUY",
                        "volume": trade_lot_size,
                        "entry_price": long_st.close_price,
                        "sl": long_st.suggested_sl,
                        "tp": long_st.suggested_tp,
                        "status": "OPEN",
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        **_le,
                    })
                    time.sleep(60)
                else:
                    print(f"❌ Order Failed: {msg}\n", flush=True)

            # ================= EXECUTE SELL ORDER =================
            elif short_st.all_passed:
                if params.enable_htf_filter and htf_trend == "BULLISH":
                    print(f"⚠️ [FILTER BLOCKED] M1 SELL Signal skipped: M15 Macro Trend is BULLISH (Counter-trend protection)", flush=True)
                    continue

                print(f"\n🎯 >>> ALL CONFLUENCES ALIGNED: EXECUTING SELL ORDER (Lot: {trade_lot_size}) AT ${sym_info.bid:.2f} <<<", flush=True)
                print(f"   SL: ${short_st.suggested_sl:.2f} (Risk: ${short_st.risk_points:.2f}) | TP: ${short_st.suggested_tp:.2f} (Reward: ${short_st.reward_points:.2f})", flush=True)

                res = mt5_bridge.send_order(
                    direction="SELL",
                    volume=trade_lot_size,
                    sl_price=short_st.suggested_sl,
                    tp_price=short_st.suggested_tp,
                    magic_number=cb_config.magic_number,
                    comment="ProScalper_SELL"
                )
                ok, ticket, msg = res if (isinstance(res, tuple) and len(res) == 3) else (False, 0, str(res))
                if ok:
                    _le = dict(getattr(mt5_bridge, "last_exec", {}) or {})
                    print(f"✅ {msg}", flush=True)
                    print(f"   📏 spread ${_le.get('spread_paid_usd', 0):.2f} | "
                          f"slippage ${_le.get('entry_slippage_usd', 0):+.2f} | "
                          f"latency {_le.get('entry_latency_ms', 0):.0f}ms\n", flush=True)
                    storage.record_trade({
                        "order_id": ticket,
                        "symbol": mt5_bridge.symbol,
                        "direction": "SELL",
                        "volume": trade_lot_size,
                        "entry_price": short_st.close_price,
                        "sl": short_st.suggested_sl,
                        "tp": short_st.suggested_tp,
                        "status": "OPEN",
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        **_le,
                    })
                    time.sleep(60)
                else:
                    print(f"❌ Order Failed: {msg}\n", flush=True)

    except KeyboardInterrupt:
        print("\n🛑 Auto-trading engine stopped by user.", flush=True)
        mt5_bridge.disconnect()


if __name__ == "__main__":
    run_live_auto_trading()