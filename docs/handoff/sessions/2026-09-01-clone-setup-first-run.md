# Session — 2026-09-01 — Clone, environment setup, first run, handoff created

**Who:** a Claude Code session running out of the sibling repo
`d:/mine/Bots/scalping_bot`, at the owner's request. The owner forked
`uzairshaikh346/VWAP-EMA-BOT` to `github.com/CodeWithUmair/VWAP-EMA-BOT` after seeing
strong live results from the upstream author's demo account, and wanted it cloned next
to `scalping_bot` and run alongside it.

**Hard constraint from the owner:** do not change anything in the bot's code — "that
board is running amazingly". Only additions (tooling, docs) alongside it. This session
added `docs/handoff/` and nothing else.

## What was done

1. **Cloned** to `d:/mine/Bots/VWAP-EMA-BOT` (sibling of `scalping_bot`, `aureum`,
   `gold_bot`).
2. **Read the whole codebase** — `trading_bot/*.py` (strategy, backtest, circuit
   breakers, mt5_bridge, storage, data_feed, the two runners, streamlit app),
   `requirements.txt`, and the upstream `README.md`. Summary is in
   `docs/handoff/README.md` and `decisions.md`.
3. **Created `venv/`** and `pip install -r requirements.txt` (pandas 3.0.5, numpy
   2.5.2, streamlit 1.63.0, MetaTrader5 5.0.6147, scipy 1.18.1, plotly 7.0.0, pytest
   9.1.1, requests, python-dotenv). Kept separate from `scalping_bot`'s system Python.
4. **`python trading_bot/run_tests.py` → 14/14 pass** (indicator causality,
   candlestick patterns, circuit breakers, VWAP day-boundary reset, causal swings).
5. **`python trading_bot/run_backtest.py` → runs.** Findings worth carrying forward:
   - It backtests on **synthetic data** (`data_feed.generate_realistic_gold_data()`),
     not real MT5 history. The numbers are not evidence about live edge.
   - In-sample: 14 trades, +0.64R, noise gate **p = 1.0 FAILED**.
     Out-of-sample: 6 trades, −0.52R, noise gate **p = 1.0 FAILED**.
     Overall (pooled): p = 0.0099 "PASSED" — a pooling artifact, both halves fail.
   - Final line: `STRATEGY DID NOT CLEAR NOISE GATE … dashboard auto-trading gate:
     BLOCKED`. **But** `run_live_auto_bot.py` and `streamlit_app.py` both set
     `bypass_noise_gate_for_demo=True`, so on a demo account the bot trades anyway.
6. **`python trading_bot/run_live_auto_bot.py` → verified live** (needs `PYTHONUTF8=1`
   first — see below). Output:
   ```
   Connected to MT5 Account: 472544446 | Mode: DEMO | Balance: $9,909.53
   Target Symbol: XAUUSDm | Algo Allowed: True
   [18:13:18 UTC | M1 CLOSE] Price: $4335.08 (Spread: $0.26)
     BUY  (0/5): VWAP=F Cross=F OB=F Pullback=F Candle=F
     SELL (3/5): VWAP=T Cross=T OB=F Pullback=T Candle=F
   ```
   Stopped after ~25 s. **No order was placed by this session.** Account 472544446 is
   the **same Exness demo account `scalping_bot` is using** — one MT5 terminal, one
   account, shared by both bots.
7. **Wrote this handoff** — `README.md`, `decisions.md`, `MULTI-ACCOUNT.md`, this file.

## Gotcha found: headless run needs `PYTHONUTF8=1`

`run_live_auto_bot.py`'s first `print()` contains a 🚀 emoji. When stdout is not a real
console (pipe, log file, background process), Windows uses cp1252 and it crashes:
`UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f680'`. Fix without
touching the code: `set PYTHONUTF8=1` (or `PYTHONIOENCODING=utf-8`) before launching.
The owner's other repo fixed the same class of bug in code; here the code is off-limits.

## Running it alongside `scalping_bot`

Both attach to the one running MT5 terminal → same account. Different magic numbers
(`9212001` here vs `990101` there) so neither adopts/closes the other's trades, but
they share balance and margin, and both trade `XAUUSDm`. `scalping_bot`'s dashboard
holds port 8501, so run this one's dashboard on `--server.port 8502`. Full notes in
`README.md`. Multi-**account** (the owner has several) is not solved — options in
`MULTI-ACCOUNT.md`.

## For the next session (owner will start it in this repo directly)

- Pick a multi-account approach from `MULTI-ACCOUNT.md` and build it (a thin
  `run_account.py` wrapper is the recommended no-edit path; a small argparse addition
  to `run_live_auto_bot.py` is the clean path if the owner allows that edit).
- If an honest edge read is wanted, add a **real-data** backtest (pull M1 history from
  MT5 — `scalping_bot/scalping_bot/data/mt5_source.py` is a working reference for the
  fetch+cache pattern; reimplement, don't cross-import).
- Decide whether circuit-breaker state should persist across restarts (currently
  in-memory, resets every launch).
