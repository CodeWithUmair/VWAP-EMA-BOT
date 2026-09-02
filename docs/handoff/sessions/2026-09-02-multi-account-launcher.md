# Session — 2026-09-02 — Multi-account launcher + backtest re-read

**Who:** Claude Code session in `d:/mine/Bots/VWAP-EMA-BOT`, at the owner's request.
The owner wants **this** bot running on a **second Exness demo account** at the same
time as the sibling `scalping_bot`, which currently owns the one MT5 terminal
(account **472544446**, balance ~$9,902, server Exness-MT5Trial16). The owner has
three more fresh demo accounts available: **472611148 / 474438988 / 474438985**
(all "Demo · MT5 · Standard", $10,000). Pick any one for this bot.

**Constraint still in force:** don't edit the bot's code. This session added one
NEW file (`run_account.py`) and docs — nothing under `trading_bot/` was touched.

## What was done

1. **Re-ran the test suite** — `./venv/Scripts/python trading_bot/run_tests.py` →
   **14/14 pass**.
2. **Re-ran the backtest** — `run_backtest.py` (synthetic data, unchanged from the
   first session):
   - In-sample: 14 trades, WR 57.1%, PF 2.85, **+0.64R**, noise gate **p=1.0 FAILED**.
   - Out-of-sample: 6 trades, WR 16.7%, PF 0.19, **−0.52R**, noise gate **p=1.0 FAILED**.
   - Overall pooled: 20 trades, +0.29R, "p=0.0099 PASSED" — pooling artifact.
   - Final line: **STRATEGY DID NOT CLEAR NOISE GATE / auto-trading gate BLOCKED**.
   - Live runners set `bypass_noise_gate_for_demo=True`, so on demo the bot trades
     anyway. **Still no real-data backtest exists** — these numbers are not
     evidence about live edge. Unchanged from 2026-09-01.
3. **Built `run_account.py`** at the repo root — the Option C launcher from
   `MULTI-ACCOUNT.md`. It imports `MT5Bridge`, the strategy, the circuit breakers
   and `BotStorage` unchanged, and re-implements the ~40-line scan/execute loop
   from `run_live_auto_bot.py` verbatim (same params `max_pullback_bars=35`,
   `ob_buffer_atr=0.35`, `pullback_atr_mult=1.8`; same 0.10 lot; same magic
   9212001; same `bypass_noise_gate_for_demo=True`). What it adds:
   - `--mt5-path` (+ `--portable`) → `mt5.initialize(path=..., portable=True)` so
     the process attaches to a **specific** terminal, not "whatever Windows finds".
   - `--login/--password/--server` → optional full login.
   - `--db` → per-account SQLite (default still `trading_bot_data.sqlite`).
   - `--symbol`, `--volume`, `--magic`, `--poll`.
   - `--dry-run` → scan + print the 5/5 diagnostic, never send an order.
   - All flags also read from env / `.env` (`MT5_PATH`, `MT5_LOGIN`, …).
   - ASCII-only prints — no `PYTHONUTF8=1` gotcha (unlike `run_live_auto_bot.py`).
4. **Smoke-tested** `python run_account.py --dry-run` (no `--mt5-path`, so it
   attached to the running terminal / 472544446): connected, printed
   `SELL (4/5)` diagnostics, placed nothing. Loop logic verified.

## Not done — needs the owner (one manual step, then it runs)

`run_account.py` is ready but a second account needs a **second MT5 terminal
install**, because the `MetaTrader5` Python package binds one process to one
terminal, and two terminals can't share one install folder.

1. **Make a second terminal.** Either copy `C:\Program Files\MetaTrader 5 EXNESS`
   to e.g. `D:\MT5-VWAP\`, or run the Exness MT5 installer into a new folder.
2. **Launch it in portable mode:** `D:\MT5-VWAP\terminal64.exe /portable`.
3. **Log in** to the chosen demo account (472611148 / 474438988 / 474438985) —
   needs that account's **MT5 password** (Exness "Set MT5 password" on the account
   card) and server (**Exness-MT5Trial16**, same as 472544446 unless the account
   card says otherwise).
4. **Tools → Options → Expert Advisors → Allow Algorithmic Trading** — on.
5. Put the password in `.env` (gitignored) — see `.env.example` — or pass it once
   on the CLI.
6. Run:
   ```
   ./venv/Scripts/python run_account.py --mt5-path "D:\MT5-VWAP\terminal64.exe" \
       --portable --login <acct> --server Exness-MT5Trial16 --db vwap_acct2.sqlite --dry-run
   ```
   Confirm it prints that account's login + balance, then drop `--dry-run` to arm it.

`scalping_bot` keeps running untouched on 472544446. The two bots are now on
different accounts / different terminals / different SQLite files — fully isolated.
