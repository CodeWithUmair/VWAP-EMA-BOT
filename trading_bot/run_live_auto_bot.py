"""
Autonomous live trading engine for MT5 (XAUUSD).

Runs whichever strategy is selected in :mod:`trading_bot.strategies` - the M1
VWAP/EMA scalper or the CRT + TBS liquidity-sweep swing setup - against the same
M1 feed. Pick one with ``--strategy``, or leave it off and the engine follows the
dashboard sidebar (both processes read ``active_strategy`` from SQLite).

Shared risk machinery, applied whatever the strategy:
1. Multi-Timeframe Trend Filter (M15 EMA 50 alignment; scalper only by default)
2. Institutional Killzone Tracker (London & New York high-volume momentum)
3. Smart Dynamic ATR-Buffered SL & TP (Minimum $1.80 breathing room prevents stop-hunting)
4. Dynamic Auto Break-Even Shield (locks +$2.50, capped at 40% of target, once 60% of the way to TP)
5. TP1 handling for two-target strategies (half off at CRT equilibrium, stop to entry)
6. Single Active Position Enforcement (Prevents dangerous stacking/over-leveraging)
7. Post-Loss Cooling Guard (3-minute circuit pause prevents revenge whipsaws)
8. Economic-calendar blackout around high-impact releases (ForexFactory, free JSON),
   plus a manual "HH:MM-HH:MM" UTC window list for releases the calendar misses
9. Circuit breakers scaled to the live account balance, not fixed dollar amounts,
   and their day's tally (consec losses, daily P&L) survives an engine restart
10. Max-spread and stale-feed gates refuse new entries into a widened spread or a
    frozen price feed
11. Per-fill spread / slippage / latency logged and stored on every trade

Usage:
    python -m trading_bot.run_live_auto_bot
    python -m trading_bot.run_live_auto_bot --strategy crt_tbs
"""

import time
import sys
import os
from dataclasses import asdict
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# This engine logs with emoji status markers. On Windows the console defaults to
# cp1252, which cannot encode them - and the crash only appears once output is
# piped to a file, i.e. exactly how a long-running bot is usually launched.
# Force UTF-8 and degrade unencodable characters rather than dying mid-session.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from trading_bot.mt5_bridge import MT5Bridge
from trading_bot.strategies import DEFAULT_STRATEGY_KEY, REGISTRY, get_strategy
from trading_bot.strategy import (
    StrategyParameters,
    evaluate_checklist_at_bar,
    calculate_sl_tp,
    calculate_atr,
    evaluate_htf_trend,
    is_in_killzone
)
from trading_bot.circuit_breakers import CircuitBreakerConfig, CircuitBreakerManager
from trading_bot.news_filter import build_filter
from trading_bot.storage import BotStorage


# Per-strategy live tuning, layered over each strategy's shipped defaults.
LIVE_PARAM_OVERRIDES = {
    "vwap_ema_scalper": {
        "max_pullback_bars": 35,         # Realistic pullback window
        "ob_buffer_atr": 0.35,           # Clean Order Block retest zone
        "pullback_atr_mult": 1.8,        # Proximity to EMA 9/21 zone
        "rr_ratio": 2.0,                 # 1:2.0 Risk:Reward (raised from 1.5 - spread/commission cost was eating the edge at 1.5)
        "sl_buffer_atr": 0.50,           # 0.5 ATR cushion beyond swing pivots
        "min_sl_distance_points": 1.8,   # Minimum $1.80 SL on Gold to survive wicks
        "max_sl_distance_points": 6.0,   # Max $6.00 SL on Gold
        "enable_htf_filter": True,       # Strictly trade with M15 macro trend
        "enable_session_filter": False,  # Set True to ONLY trade London/NY Killzones
    },
    # CRT + TBS ships its own killzone and range filters, so its defaults stand.
    "crt_tbs": {},
    "crt_body_soup": {
        # Bias ON is the only configuration that tested above break-even on the
        # broker's own bars (PF 1.15, ~1.8 trades/day). It did NOT hold on the
        # independent Dukascopy set, which is exactly why this is a demo
        # forward-test and not a funded strategy - see docs/MT5_BACKTEST_GUIDE.md.
        "enable_htf_bias": True,
        "htf_bias_tf": "H4",
    },
}


def _in_news_blackout(windows, now_utc=None):
    """True if now_utc falls inside any manually-entered "HH:MM-HH:MM" UTC window
    (no midnight wrap). This is a hand-typed supplement to the ForexFactory-driven
    ``NewsFilter`` above - useful for a release the calendar doesn't carry, or a
    window the trader wants blocked on their own judgement. Merged in from a
    parallel session's exec-safety pack (commit cfabc94).
    """
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


def load_strategy_settings(storage, strategy):
    """Resolve this strategy's parameters: defaults, then live tuning, then the
    dashboard's saved values.

    The sidebar writes every control to the SQLite settings table, so whatever
    the user last set there is what the engine trades with. Without this last
    layer the dashboard would look like it configured the bot while the engine
    quietly ran its own hard-coded numbers.
    """
    values = {spec.key: spec.default for spec in strategy.param_specs()}
    values.update(LIVE_PARAM_OVERRIDES.get(strategy.key, {}))
    for spec in strategy.param_specs():
        saved = storage.get_setting(f"param.{strategy.key}.{spec.key}", None)
        if saved is not None:
            values[spec.key] = saved
    return values


def resolve_strategy(storage, cli_key=None):
    """Pick the strategy this run trades: CLI flag, else the dashboard's choice.

    The dashboard writes ``active_strategy`` to SQLite when you switch strategies
    in the sidebar, so the two processes stay in step without any IPC. A CLI flag
    wins, and pins the engine regardless of what the dashboard says.
    """
    if cli_key:
        return get_strategy(cli_key), "command line"
    try:
        saved = storage.get_setting("active_strategy", DEFAULT_STRATEGY_KEY)
    except Exception:
        saved = DEFAULT_STRATEGY_KEY
    return get_strategy(saved), "dashboard sidebar"


def run_live_auto_trading(strategy_key: str = None):
    storage = BotStorage()
    strategy, chosen_via = resolve_strategy(storage, strategy_key)

    params = strategy.build_params(load_strategy_settings(storage, strategy))

    print("=" * 85, flush=True)
    print(f"🚀 STARTING LIVE ENGINE — {strategy.label.upper()} ({strategy.timeframe})", flush=True)
    print(f"   {strategy.description}", flush=True)
    print(f"   Strategy selected via: {chosen_via}", flush=True)
    print("🛡️ ACTIVE SHIELDS: Single Position | Break-Even Shield | Post-Loss Cooldown", flush=True)
    print("=" * 85, flush=True)

    # Trading Volume & Risk Settings
    trade_lot_size = 0.01                # Micro-lot 0.01 for safe scaling and testing

    # Circuit breakers are sized from the LIVE balance, not a fixed dollar
    # figure. A $500 daily-loss ceiling on a $100 account is not a safety net -
    # it lets the account reach zero five times over before it trips. These
    # scale, so the same numbers stay meaningful whatever the balance is.
    cb_config = CircuitBreakerConfig(
        bypass_noise_gate_for_demo=True,
        max_consecutive_losses=3,
        max_daily_loss_usd=50.0,         # replaced below once the balance is known
        cooldown_after_loss_minutes=3
    )
    cb_manager = CircuitBreakerManager(config=cb_config)
    mt5_bridge = MT5Bridge(symbol="XAUUSDm")

    if not mt5_bridge.connect():
        print("❌ Could not connect to MetaTrader 5 terminal. Exiting.", flush=True)
        return

    acc = mt5_bridge.get_account_info()
    algo_allowed = mt5_bridge.is_algo_trading_enabled()

    # Risk caps: whatever the dashboard last saved, else 10% of balance floored
    # at $5 so a small account still trades but is never uncapped.
    cb_config.max_daily_loss_usd = float(storage.get_setting(
        "risk.max_daily_loss_usd", max(round(acc.balance * 0.10, 2), 5.0)))
    cb_config.max_consecutive_losses = int(storage.get_setting(
        "risk.max_consecutive_losses", cb_config.max_consecutive_losses))
    cb_config.magic_number = int(storage.get_setting(
        "risk.magic_number", cb_config.magic_number))

    print(f"✅ Connected to MT5 Account: {acc.login} | Mode: {acc.trade_mode} | Balance: ${acc.balance:,.2f}", flush=True)
    print(f"🧯 Risk caps: max daily loss ${cb_config.max_daily_loss_usd:,.2f} | "
          f"max {cb_config.max_consecutive_losses} consecutive losses | "
          f"magic {cb_config.magic_number}", flush=True)

    # Restore circuit-breaker state so a restart does not wipe the day's loss
    # tally or the consecutive-loss count. Without this, every engine restart
    # (a redeploy, a crash, `start_bot.ps1` re-run) silently reset both counters
    # to zero - a real hole, since a losing streak split across two process
    # lifetimes would never trip the breaker. Only restored when it is still the
    # same UTC day; a new day is meant to reset the tally anyway.
    _saved_cb = storage.get_setting("cb_state", {}) or {}
    if _saved_cb.get("current_date") == datetime.now(timezone.utc).strftime("%Y-%m-%d"):
        for _k, _v in _saved_cb.items():
            if hasattr(cb_manager.state, _k):
                setattr(cb_manager.state, _k, _v)
        print(f"↻ Restored circuit-breaker state — consec losses "
              f"{cb_manager.state.consecutive_losses}, daily P&L "
              f"${cb_manager.state.daily_pnl_usd:+.2f}, "
              f"daily-loss tripped={cb_manager.state.is_daily_loss_tripped}", flush=True)

    # Execution-safety gates (max spread, stale feed, manual news windows).
    # 0 / empty disables a gate. Read once at startup - like every other risk
    # setting, changing it in the sidebar takes effect on the engine's next
    # restart, not this instant, since these are read from the same "risk.*"
    # settings namespace the rest of the risk caps use.
    max_spread_usd = float(storage.get_setting("risk.max_spread_usd", 0.60))
    stale_bar_secs = float(storage.get_setting("risk.stale_bar_secs", 180))
    manual_news_windows = storage.get_setting("risk.news_blackout_windows", []) or []
    print(f"🛡️ Exec guards — MaxSpread ${max_spread_usd:g} (0=off) | "
          f"StaleBar {stale_bar_secs:g}s (0=off) | "
          f"Manual news windows: {manual_news_windows or 'none'}", flush=True)

    # Sanity-check position sizing against the strategy's own stop distances.
    # At the 0.01 broker minimum, gold risks $1 per $1 of stop, so a wide stop
    # on a small account is a large percentage loss - say so plainly at startup.
    worst_sl = getattr(params, "max_sl_distance_points", 0) or 0
    if worst_sl and acc.balance > 0:
        worst_pct = (worst_sl * trade_lot_size * 100.0) / acc.balance * 100.0
        if worst_pct > 3.0:
            print(f"⚠️  SIZING WARNING: a worst-case ${worst_sl:.2f} stop at {trade_lot_size} lots "
                  f"risks {worst_pct:.1f}% of this balance on ONE trade (1-2% is normal). "
                  f"This account is small for this strategy at the minimum lot size.",
                  flush=True)
    print(f"⚡ Target Symbol: {mt5_bridge.symbol} | Default Lot Size: {trade_lot_size} | Algo Allowed: {algo_allowed}", flush=True)
    print("⚡ Auto-Scanner active. Streaming live ticks every 3 seconds...\n", flush=True)

    last_evaluated_time = 0
    last_news_log = 0
    last_loss_time = 0
    start_session_time = int(time.time())
    processed_deal_tickets = set()
    be_moved_tickets = set()
    pos_tp = {}       # ticket -> take-profit price, snapshotted while the position is open
    be_lock = {}      # ticket -> shield stop price, set when the profit shield arms
    pos_tp1 = {}      # ticket -> TP1 price for two-target strategies (CRT equilibrium)
    tp1_done = set()  # tickets whose TP1 has already been banked / shielded

    # Economic-calendar blackout. Gold's worst fills happen in the minutes around
    # NFP/CPI/FOMC: spreads gap, stops slip, and a clean-looking sweep is really
    # just the release. Fetched from ForexFactory's free public JSON (no API key
    # exists or is needed) and refreshed periodically; a fetch failure falls back
    # to the on-disk cache rather than dropping the guard silently.
    NEWS_REFRESH_SECONDS = 6 * 3600
    news = build_filter(fetch_live=True, minutes_before=30, minutes_after=30)
    last_news_refresh = time.time()
    if news.is_active:
        print(f"📰 News filter ON: {len(news)} high-impact events loaded "
              f"(blocking +/-30 min around each).", flush=True)
    else:
        print("📰 News filter: NO events loaded - trading unguarded against releases. "
              "Run `python -m trading_bot.news_filter` to populate the cache.", flush=True)

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

            # Keep the calendar current. Never let a network hiccup stop trading:
            # on failure the previous event list stays in force.
            if time.time() - last_news_refresh > NEWS_REFRESH_SECONDS:
                last_news_refresh = time.time()
                try:
                    refreshed = build_filter(fetch_live=True, minutes_before=30, minutes_after=30)
                    if refreshed.is_active:
                        news = refreshed
                        print(f"📰 News calendar refreshed: {len(news)} events.", flush=True)
                except Exception as exc:
                    print(f"📰 News refresh failed ({exc}); keeping the existing calendar.",
                          flush=True)

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

                # TP1 management for two-target strategies (CRT + TBS): at the CRT
                # equilibrium, bank half and pull the stop to entry. At 0.01 lots
                # there is nothing to halve, so the bridge says so and the trade
                # simply goes risk-free on the full size instead.
                tp1 = pos_tp1.get(ticket)
                if tp1 and ticket not in tp1_done:
                    reached = (current_p >= tp1) if direction == "BUY" else (current_p <= tp1)
                    if reached:
                        tp1_done.add(ticket)
                        ok, why = mt5_bridge.close_position_partial(ticket, fraction=0.5)
                        if ok:
                            print(f"💰 [TP1 BANKED] {direction} {ticket} took half off at "
                                  f"${tp1:.2f} (CRT equilibrium). {why}", flush=True)
                        else:
                            print(f"ℹ️ [TP1 REACHED] {direction} {ticket} at ${tp1:.2f} — "
                                  f"no partial taken ({why}).", flush=True)
                        if mt5_bridge.modify_position_sl(ticket, entry_p):
                            be_moved_tickets.add(ticket)
                            be_lock[ticket] = entry_p
                            print(f"🔒 [BREAK-EVEN] {ticket} stop moved to entry ${entry_p:.2f}; "
                                  f"runner rides to TP2 ${tp:.2f}.", flush=True)

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
                        try:  # persist so a restart keeps today's tally
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

            # 6. Evaluate the active strategy's checklist on the latest closed bar.
            #    CRT + TBS folds these M1 bars into M5/H1 internally, so the same
            #    feed serves both strategies without the engine knowing the difference.
            live_data = {"opens": opens, "highs": highs, "lows": lows,
                         "closes": closes, "times": times, "volumes": volumes}
            checklist = strategy.evaluate(live_data, curr_idx, params,
                                          strategy.prepare(live_data, params))
            long_st = checklist["LONG"]
            short_st = checklist["SHORT"]

            atr_vals = calculate_atr(highs, lows, closes, period=14)
            curr_atr = atr_vals[-1] if atr_vals else 1.0

            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

            # 7. Print Diagnostic Output on M1 Candle Close
            if latest_bar.time != last_evaluated_time:
                last_evaluated_time = latest_bar.time

                def _summarise(ev):
                    """One line per side, naming whatever checks this strategy defines."""
                    return " | ".join(f"{s.name.split('(')[0].strip()}={s.passed}"
                                      for s in ev.steps)

                pos_status = f"{len(open_positions)} OPEN ({open_positions[0]['direction']})" if open_positions else "0 OPEN"

                print(
                    f"\n🕯️ [{now_str} UTC | M1 CLOSE] Price: ${closes[-1]:.2f} | ATR: ${curr_atr:.2f} | Session: {killzone_name}\n"
                    f"   ├─ 🧭 M15 Macro Trend: {htf_trend} ({htf_reason})\n"
                    f"   ├─ 🟢 BUY  ({long_st.passed_count}/{long_st.step_count}): {_summarise(long_st)}\n"
                    f"   ├─ 🔴 SELL ({short_st.passed_count}/{short_st.step_count}): {_summarise(short_st)}\n"
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

            # Shield 3: Dead-Market Anti-Chop Filter. Scalping a flat M1 range is
            # a losing game, but a sweep strategy is *supposed* to fire into quiet
            # ranges, so this only guards the M1 scalper.
            if strategy.key == "vwap_ema_scalper" and curr_atr < 0.45:
                continue

            # Shield 4: Session Killzone Filter (optional; CRT + TBS enforces its
            # own killzone windows as a checklist step instead).
            if getattr(params, "enable_session_filter", False) and not in_killzone:
                continue

            # Shield 4b: Economic-calendar blackout. Skip the minutes either side
            # of a high-impact release - that is when spreads gap and stops slip,
            # and a "sweep" is usually just the news, not a liquidity trap.
            blocking = news.blocking_event(datetime.now(timezone.utc))
            if blocking is not None:
                if latest_bar.time != last_news_log:
                    last_news_log = latest_bar.time
                    print(f"📰 [NEWS BLACKOUT] Standing aside: {blocking.currency} "
                          f"{blocking.title} at {blocking.when:%H:%M} UTC", flush=True)
                continue

            # Shield 4c: Manual news-blackout windows - a hand-typed supplement to
            # the automated calendar above, for a release the calendar doesn't
            # carry or a window the trader wants blocked on judgement alone.
            _blk, _win = _in_news_blackout(manual_news_windows)
            if _blk:
                if latest_bar.time != last_news_log:
                    last_news_log = latest_bar.time
                    print(f"📰 [MANUAL NEWS WINDOW] Inside {_win} UTC — no new entries.", flush=True)
                continue

            # Shield 4d: Max-Spread Gate. A live spread this wide means a news spike
            # or thin liquidity - not the setup the checklist priced the trade on.
            if max_spread_usd > 0 and sym_info.spread_usd > max_spread_usd:
                if latest_bar.time != last_evaluated_time:
                    print(f"⏸️  [SPREAD GUARD] Spread ${sym_info.spread_usd:.2f} > "
                          f"${max_spread_usd:.2f} limit — no new entries.", flush=True)
                continue

            # Shield 4e: Stale-Feed Gate. If the newest bar has stopped advancing,
            # the price feed is frozen (terminal disconnect, symbol delisted for
            # the session) - trading on a stale price is trading blind.
            _bar_age = _bar_age_seconds(latest_bar.time)
            if stale_bar_secs > 0 and _bar_age is not None and _bar_age > stale_bar_secs:
                if latest_bar.time != last_evaluated_time:
                    print(f"⏸️  [STALE FEED] Newest M1 bar is {_bar_age:.0f}s old "
                          f"(> {stale_bar_secs:.0f}s) — halting new entries.", flush=True)
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
                if getattr(params, "enable_htf_filter", False) and htf_trend == "BEARISH":
                    print(f"⚠️ [FILTER BLOCKED] M1 BUY Signal skipped: M15 Macro Trend is BEARISH (Counter-trend protection)", flush=True)
                    continue

                print(f"\n🎯 >>> ALL CONFLUENCES ALIGNED: EXECUTING BUY ORDER (Lot: {trade_lot_size}) AT ${sym_info.ask:.2f} <<<", flush=True)
                tp1_note = f" | TP1: ${long_st.suggested_tp1:.2f}" if long_st.suggested_tp1 else ""
                print(f"   SL: ${long_st.suggested_sl:.2f} (Risk: ${long_st.risk_points:.2f}){tp1_note} | TP: ${long_st.suggested_tp:.2f} (Reward: ${long_st.reward_points:.2f})", flush=True)
                
                res = mt5_bridge.send_order(
                    direction="BUY",
                    volume=trade_lot_size,
                    sl_price=long_st.suggested_sl,
                    tp_price=long_st.suggested_tp,
                    magic_number=cb_config.magic_number,
                    comment=f"{strategy.key[:12]}_BUY"
                )
                ok, ticket, msg = res if (isinstance(res, tuple) and len(res) == 3) else (False, 0, str(res))
                if ok:
                    _le = dict(getattr(mt5_bridge, "last_exec", {}) or {})
                    print(f"✅ {msg}", flush=True)
                    print(f"   📏 spread ${_le.get('spread_paid_usd', 0):.2f} | "
                          f"slippage ${_le.get('entry_slippage_usd', 0):+.2f} | "
                          f"latency {_le.get('entry_latency_ms', 0):.0f}ms\n", flush=True)
                    if long_st.suggested_tp1:
                        pos_tp1[ticket] = long_st.suggested_tp1
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
                if getattr(params, "enable_htf_filter", False) and htf_trend == "BULLISH":
                    print(f"⚠️ [FILTER BLOCKED] M1 SELL Signal skipped: M15 Macro Trend is BULLISH (Counter-trend protection)", flush=True)
                    continue

                print(f"\n🎯 >>> ALL CONFLUENCES ALIGNED: EXECUTING SELL ORDER (Lot: {trade_lot_size}) AT ${sym_info.bid:.2f} <<<", flush=True)
                tp1_note = f" | TP1: ${short_st.suggested_tp1:.2f}" if short_st.suggested_tp1 else ""
                print(f"   SL: ${short_st.suggested_sl:.2f} (Risk: ${short_st.risk_points:.2f}){tp1_note} | TP: ${short_st.suggested_tp:.2f} (Reward: ${short_st.reward_points:.2f})", flush=True)

                res = mt5_bridge.send_order(
                    direction="SELL",
                    volume=trade_lot_size,
                    sl_price=short_st.suggested_sl,
                    tp_price=short_st.suggested_tp,
                    magic_number=cb_config.magic_number,
                    comment=f"{strategy.key[:12]}_SELL"
                )
                ok, ticket, msg = res if (isinstance(res, tuple) and len(res) == 3) else (False, 0, str(res))
                if ok:
                    _le = dict(getattr(mt5_bridge, "last_exec", {}) or {})
                    print(f"✅ {msg}", flush=True)
                    print(f"   📏 spread ${_le.get('spread_paid_usd', 0):.2f} | "
                          f"slippage ${_le.get('entry_slippage_usd', 0):+.2f} | "
                          f"latency {_le.get('entry_latency_ms', 0):.0f}ms\n", flush=True)
                    if short_st.suggested_tp1:
                        pos_tp1[ticket] = short_st.suggested_tp1
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
    import argparse

    ap = argparse.ArgumentParser(description="Run the headless MT5 auto-trading engine.")
    ap.add_argument("--strategy", choices=sorted(REGISTRY), default=None,
                    help="pin a strategy for this run; omit to follow the dashboard sidebar")
    run_live_auto_trading(strategy_key=ap.parse_args().strategy)