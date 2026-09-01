# Decisions log

Durable "why is it built this way" notes. Read before changing something so you know
whether there's a reason it isn't already that way.

Most entries below are **reverse-engineered** from the code and the upstream `README.md`
(this handoff was started by an outside session that did not write the bot). Where a
reason is stated by the upstream author it's marked *(upstream)*; where it's inferred
it's marked *(inferred)*. Correct these as you learn more.

## Strategy / signal

- **5 filters, all-or-nothing.** *(upstream)* VWAP trend + EMA9/21 crossover + causal
  order block/BOS + pullback-to-EMA + candlestick confirmation. A signal fires only
  when all five pass at bar close; fill is next bar's open + spread. The upstream
  README frames this as strict causality / zero-lookahead, and the unit tests
  (`tests/test_strategy.py`) check the causality of each indicator.
- **The EMA crossover bar is not the entry.** *(upstream)* Price must pull back toward
  the EMAs after the crossover, within `max_pullback_bars` (15 in `StrategyParameters`,
  but **35** as set in `run_live_auto_bot.py`). Rationale given: avoid chasing the
  impulse candle.
- **Order blocks are confirmed with a lag.** *(upstream)* A pivot at bar `k` is only
  visible at `k + ob_swing_lookback` (5). OB stays active `ob_max_age_bars` (40) unless
  invalidated. This is the zero-lookahead guarantee applied to structure.
- **Live params differ from defaults.** *(inferred)* `run_live_auto_bot.py` overrides
  `max_pullback_bars=35`, `ob_buffer_atr=0.35`, `pullback_atr_mult=1.8` with a comment
  "Balanced Strategy Parameters for Realistic M1 Scalping" — i.e. the dataclass
  defaults were found too strict to ever fire live, and these were loosened by hand.
  There's no record of *how* they were chosen; treat them as untuned-by-us.

## Risk / execution

- **Fixed 0.10 lot, every trade.** *(inferred)* Hardcoded in `run_live_auto_bot.py`
  (`volume=0.10`). No position sizing, no risk-per-trade calc. The sibling repo learned
  the hard way that "trade bigger once it looks good" is a real hazard — see
  `scalping_bot/docs/handoff/decisions.md` — so if this ever moves off demo, revisit.
- **RR 1:2, SL behind the last 10 bars, clamped $1–$8.** *(upstream)* The clamp exists
  so the stop can't be tighter than spread absorbs, nor absurdly wide on a spike.
- **Magic number `9212001`.** *(upstream)* Tags every order so the bot never adopts or
  closes manual trades or another system's trades. Distinct from `scalping_bot`'s
  `990101`, so the two bots coexist on one account without fighting over positions.
- **Demo-only, re-checked every order.** *(upstream)* `mt5_bridge.send_order` and
  `circuit_breakers.can_open_trade` both call `account_info().trade_mode` on every
  attempt and hard-refuse a live account. Not a startup check — every order.
- **`bypass_noise_gate_for_demo=True`.** *(inferred)* Both the live runner and the
  dashboard set this. So the "strategy must pass the noise gate before auto-trading is
  unlocked" rule from the upstream README is **off on demo** — the bot trades on demo
  even though its own backtest gate reports FAILED. This looks deliberate (a demo is
  for gathering live behaviour, not for proving edge first), but it means the gate is
  not actually protecting anything in the current setup.

## Infrastructure

- **SQLite, relative path.** *(inferred)* `BotStorage(db_path="trading_bot_data.sqlite")`
  — opened relative to the process's working directory. Run the bot from the repo root
  or the DB will be created somewhere else. A committed `trading_bot_data.sqlite` is in
  the repo (from the upstream author's own runs).
- **MT5 connection takes no account/path.** *(inferred)* `MT5Bridge.connect()` accepts
  an optional `path=` but `run_live_auto_bot.py` calls it with nothing, so
  `mt5.initialize()` attaches to whatever terminal Windows finds. Fine for one account;
  the blocker for multi-account concurrency — see `MULTI-ACCOUNT.md`.
- **Simulation fallback.** *(upstream)* If the `MetaTrader5` package can't import (non-
  Windows), `MT5Bridge` runs a synthetic simulation instead of failing. Handy for CI /
  Linux, but means "it ran" doesn't prove "it talked to a broker" — check the log for
  `Connected to MetaTrader 5 Terminal` vs `Simulation Mode`.

## This handoff session's own choices (2026-09-01)

- **Added `docs/handoff/` only.** No bot code touched — the owner's explicit constraint.
- **`PYTHONUTF8=1` documented, not patched.** The headless runner crashes on its emoji
  banner when stdout isn't a console. The fix in the owner's other repo for the same
  class of bug was a code change; here the code is off-limits, so it's an env var.
- **Own `venv/`.** Kept separate from `scalping_bot` (system Python) so dependency
  versions (pandas 3.x, numpy 2.x, MetaTrader5 5.0.6147) don't collide.
