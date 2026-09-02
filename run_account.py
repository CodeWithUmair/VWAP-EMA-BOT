"""
Thin multi-account launcher for the VWAP/EMA scalper.

Why this file exists
--------------------
`trading_bot/run_live_auto_bot.py` calls `MT5Bridge.connect()` with no arguments,
so `mt5.initialize()` attaches to whatever single MT5 terminal Windows finds and
writes to `./trading_bot_data.sqlite`. That is fine for ONE account. To run this
bot on a second Exness demo account at the same time as the sibling `scalping_bot`
(which owns the default terminal / account 472544446), each instance must:

  * attach to its OWN MT5 terminal install (by explicit `path`, in portable mode), and
  * write to its OWN SQLite file.

This launcher does exactly that and NOTHING else. It imports the strategy, bridge,
circuit-breaker and storage modules unchanged -- it does not modify the bot. The
scan/execute loop below is copied verbatim from `run_live_auto_bot.py` (same
params, same 0.10 lot default, same magic 9212001, same
`bypass_noise_gate_for_demo=True`), with only the connection, the DB path, the
symbol, the volume and the poll interval made into CLI / .env arguments.

Usage
-----
    python run_account.py --mt5-path "D:\\MT5-VWAP\\terminal64.exe" --portable \
        --db vwap_acct2.sqlite

    # full login (first launch of a fresh portable copy, or to force an account):
    python run_account.py --mt5-path "D:\\MT5-VWAP\\terminal64.exe" --portable \
        --login 474438988 --password "<mt5 password>" --server "Exness-MT5Trial16" \
        --db vwap_acct2.sqlite

    # watch the 5/5 diagnostic on the new account without ever placing an order:
    python run_account.py --mt5-path "D:\\MT5-VWAP\\terminal64.exe" --portable --dry-run

Any flag can instead come from the environment (or a .env file in the repo root):
    MT5_PATH, MT5_PORTABLE, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER,
    MT5_SYMBOL, MT5_VOLUME, MT5_DB, MT5_MAGIC, MT5_POLL
CLI flags win over the environment. Credentials belong in .env (gitignored) or on
the command line -- never commit them.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except Exception:
    pass

import MetaTrader5 as mt5

from trading_bot.mt5_bridge import MT5Bridge
from trading_bot.strategy import StrategyParameters, evaluate_checklist_at_bar
from trading_bot.circuit_breakers import CircuitBreakerConfig, CircuitBreakerManager
from trading_bot.storage import BotStorage


def _env_bool(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in ("1", "true", "yes", "on")


def parse_args():
    p = argparse.ArgumentParser(description="Run the VWAP/EMA scalper on one specific MT5 terminal / account.")
    p.add_argument("--mt5-path", default=os.getenv("MT5_PATH"),
                   help="Full path to that account's terminal64.exe. Required for concurrency.")
    p.add_argument("--portable", action="store_true", default=_env_bool("MT5_PORTABLE"),
                   help="Launch the terminal in /portable mode (keeps its data beside the exe).")
    p.add_argument("--login", type=int, default=(int(os.getenv("MT5_LOGIN")) if os.getenv("MT5_LOGIN") else None),
                   help="MT5 account number. Optional if the terminal is already logged in.")
    p.add_argument("--password", default=os.getenv("MT5_PASSWORD"), help="MT5 password (use .env).")
    p.add_argument("--server", default=os.getenv("MT5_SERVER"), help="MT5 server, e.g. Exness-MT5Trial16.")
    p.add_argument("--symbol", default=os.getenv("MT5_SYMBOL", "XAUUSDm"))
    p.add_argument("--volume", type=float, default=float(os.getenv("MT5_VOLUME", "0.10")))
    p.add_argument("--db", default=os.getenv("MT5_DB", "trading_bot_data.sqlite"),
                   help="SQLite file for THIS account. Must differ per account.")
    p.add_argument("--magic", type=int, default=int(os.getenv("MT5_MAGIC", "9212001")))
    p.add_argument("--poll", type=float, default=float(os.getenv("MT5_POLL", "3")))
    p.add_argument("--dry-run", action="store_true", help="Scan and print diagnostics, but never send an order.")
    return p.parse_args()


def connect(args) -> MT5Bridge:
    init_kwargs = {}
    if args.mt5_path:
        init_kwargs["path"] = args.mt5_path
    if args.portable:
        init_kwargs["portable"] = True
    if args.login:
        init_kwargs.update(login=args.login, password=args.password or "", server=args.server or "")

    if not init_kwargs.get("path"):
        print("[WARN] No --mt5-path given. mt5.initialize() will attach to whatever terminal "
              "Windows finds -- that is almost certainly the one scalping_bot is using. "
              "Pass --mt5-path for a second account.", flush=True)

    if not mt5.initialize(**init_kwargs):
        print(f"[FATAL] mt5.initialize failed: {mt5.last_error()}", flush=True)
        sys.exit(1)

    bridge = MT5Bridge(symbol=args.symbol, magic_number=args.magic)
    # We already hold a live connection via mt5.initialize() above; tell the bridge
    # to use it instead of calling its own path-less connect().
    bridge.is_connected = True
    bridge.is_simulation = False
    return bridge


def run(args):
    print("=" * 75, flush=True)
    print("VWAP/EMA SCALPER -- per-account launcher (XAUUSDm M1)", flush=True)
    print("=" * 75, flush=True)

    params = StrategyParameters(
        max_pullback_bars=35,
        ob_buffer_atr=0.35,
        pullback_atr_mult=1.8,
    )
    cb_config = CircuitBreakerConfig(bypass_noise_gate_for_demo=True, magic_number=args.magic)
    cb_manager = CircuitBreakerManager(config=cb_config)
    storage = BotStorage(db_path=args.db)
    bridge = connect(args)

    acc = bridge.get_account_info()
    if not acc.is_demo:
        print(f"[FATAL] Account {acc.login} on {acc.server} is not DEMO. Refusing to run.", flush=True)
        bridge.disconnect()
        sys.exit(1)

    algo_allowed = bridge.is_algo_trading_enabled()
    print(f"[OK] Connected to MT5 Account: {acc.login} | Mode: {acc.trade_mode} | "
          f"Balance: ${acc.balance:,.2f} | Server: {acc.server}", flush=True)
    print(f"[OK] Symbol: {bridge.symbol} | Volume: {args.volume} | Magic: {args.magic} | "
          f"DB: {args.db} | Algo Allowed: {algo_allowed}"
          + ("  [DRY RUN - no orders]" if args.dry_run else ""), flush=True)
    if not algo_allowed and not args.dry_run:
        print("[WARN] AlgoTrading is OFF in this terminal (Tools > Options > Expert Advisors). "
              "Orders will be refused until you enable it.", flush=True)
    print("Scanning...\n", flush=True)

    last_evaluated_time = 0

    try:
        while True:
            time.sleep(args.poll)

            bars = bridge.get_rates(count=150)
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

            checklist = evaluate_checklist_at_bar(
                opens, highs, lows, closes, times, volumes, curr_idx, params
            )
            long_st = checklist["LONG"]
            short_st = checklist["SHORT"]

            sym_info = bridge.get_symbol_info()
            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

            if latest_bar.time != last_evaluated_time:
                last_evaluated_time = latest_bar.time
                buy_n = sum([long_st.vwap_pass, long_st.crossover_pass, long_st.ob_pass,
                             long_st.pullback_pass, long_st.confirmation_pass])
                sell_n = sum([short_st.vwap_pass, short_st.crossover_pass, short_st.ob_pass,
                              short_st.pullback_pass, short_st.confirmation_pass])
                print(
                    f"[{now_str} UTC | M1 CLOSE] {acc.login} Price: ${closes[-1]:.2f} "
                    f"(Spread: ${sym_info.spread_usd:.2f})\n"
                    f"   BUY  ({buy_n}/5): VWAP={long_st.vwap_pass} Cross={long_st.crossover_pass} "
                    f"OB={long_st.ob_pass} Pullback={long_st.pullback_pass} Candle={long_st.confirmation_pass}\n"
                    f"   SELL ({sell_n}/5): VWAP={short_st.vwap_pass} Cross={short_st.crossover_pass} "
                    f"OB={short_st.ob_pass} Pullback={short_st.pullback_pass} Candle={short_st.confirmation_pass}",
                    flush=True,
                )

            if hasattr(bridge, "get_closed_deals"):
                closed_deals = bridge.get_closed_deals(from_timestamp=int(time.time()) - 3600)
                for deal in closed_deals:
                    storage.update_closed_trade(deal["ticket"], deal["close_price"], deal["profit"],
                                                exit_reason="MT5 Closed Deal")
                    cb_manager.record_trade_outcome(net_pnl_usd=deal["profit"], current_balance=acc.balance)

            can_trade, reason = cb_manager.can_open_trade(
                is_demo_account=acc.is_demo,
                algo_trading_enabled=bridge.is_algo_trading_enabled(),
                current_balance=acc.balance,
            )
            if not can_trade:
                continue

            if long_st.all_passed or short_st.all_passed:
                direction = "BUY" if long_st.all_passed else "SELL"
                st = long_st if long_st.all_passed else short_st
                px = sym_info.ask if direction == "BUY" else sym_info.bid
                if args.dry_run:
                    print(f"\n[DRY RUN] Would {direction} {args.volume} @ ${px:.2f} "
                          f"SL ${st.suggested_sl:.2f} TP ${st.suggested_tp:.2f}\n", flush=True)
                    time.sleep(60)
                    continue

                print(f"\n>>> ALL 5 CONDITIONS MET: EXECUTING {direction} @ ${px:.2f} <<<", flush=True)
                res = bridge.send_order(
                    direction=direction,
                    volume=args.volume,
                    sl_price=st.suggested_sl,
                    tp_price=st.suggested_tp,
                    magic_number=args.magic,
                    comment=f"Auto_TripleFilter_{direction}",
                )
                ok, ticket, msg = res if (isinstance(res, tuple) and len(res) == 3) else (False, 0, str(res))
                if ok:
                    print(f"[OK] {msg}\n", flush=True)
                    storage.record_trade({
                        "order_id": ticket,
                        "symbol": bridge.symbol,
                        "direction": direction,
                        "volume": args.volume,
                        "entry_price": st.close_price,
                        "sl": st.suggested_sl,
                        "tp": st.suggested_tp,
                        "status": "OPEN",
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "magic_number": args.magic,
                    })
                    time.sleep(60)
                else:
                    print(f"[FAIL] Order rejected: {msg}\n", flush=True)

    except KeyboardInterrupt:
        print("\n[STOP] Stopped by user.", flush=True)
        bridge.disconnect()


if __name__ == "__main__":
    run(parse_args())
